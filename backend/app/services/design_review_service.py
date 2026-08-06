"""设计审查服务：build123d 几何规则检查 + LLM 生成审查报告。"""
from __future__ import annotations

import io
import logging
import math
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.services.asset_blob_service import AssetBlobService
from app.services.workflow_task_repository import WorkflowTaskRepository

logger = logging.getLogger(__name__)

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

_REVIEW_SYSTEM_PROMPT = """你是资深机械设计制造专家，负责对 AI 生成的 3D 模型进行设计审查。
根据给定的几何分析数据和设计需求，输出中文设计审查报告，指出可制造性风险、结构问题与改进建议。

输出格式要求：
1. 总体评价（一句话）
2. 风险清单（每条：风险项 - 严重程度(高/中/低) - 说明与建议）
3. 结构合理性分析
4. 可制造性建议
5. 尺寸/材料建议

只输出报告正文，不要 markdown 标题，用自然段落和编号列表。"""

_REVIEW_FONT = "NotoSansCJK"

_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]


def _resolve_cjk_font() -> Path | None:
    configured = os.getenv("KNOWLEDGE_FONT_PATH", "").strip()
    if configured:
        return Path(configured)
    for candidate in _CJK_FONT_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


class DesignReviewServiceError(Exception):
    """设计审查服务可预期错误。"""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class DesignReviewService:
    """对 build123d 模型做几何规则检查并生成 PDF 审查报告。"""

    def __init__(
        self,
        *,
        asset_service: AssetBlobService | None = None,
        repository_factory=WorkflowTaskRepository,
        runtime_temp_root: Path | None = None,
    ) -> None:
        self.asset_service = asset_service or AssetBlobService(
            chunk_size=settings.ASSET_CHUNK_SIZE_BYTES
        )
        self.repository_factory = repository_factory
        self.runtime_temp_root = runtime_temp_root
        self.base_url = (
            settings.QWEN_BASE_URL
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.api_key = (settings.QWEN_API_KEY or settings.DASHSCOPE_API_KEY or "").strip()
        self.model = "qwen-plus"
        self.request_timeout = 180.0

    @property
    def available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("build123d") is not None

    @staticmethod
    def _extract_asset_id(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        if DesignReviewService._parse_asset_uuid(value) is not None:
            return value
        match = re.match(r".*?/([0-9a-fA-F-]{36})/download", value)
        return match.group(1) if match else None

    @staticmethod
    def _parse_asset_uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except ValueError:
            return None

    def _read_asset(self, db: Session, user_id: str, asset_id: str) -> bytes:
        from sqlalchemy import select

        from app.models.persistence import AssetBlobChunk

        asset = self.asset_service._get_owned_asset(
            db,
            UUID(asset_id),
            user_id,
            require_available=False,
        )
        chunks = list(
            db.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset.id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        if len(chunks) != asset.chunk_count:
            raise DesignReviewServiceError(
                f"资产数据不完整：{asset_id}",
                "REVIEW_ASSET_INCOMPLETE",
                status_code=502,
            )
        content = bytearray()
        for chunk in chunks:
            content.extend(chunk.content)
        return bytes(content)

    def _analyze_step(self, step_bytes: bytes) -> dict[str, object]:
        try:
            from build123d import import_step
            from OCP.GProp import GProp_GProps
            from OCP.BRepGProp import BRepGProp
        except ImportError as exc:
            raise DesignReviewServiceError(
                "build123d 未安装，无法执行几何审查",
                "REVIEW_IMPORT_UNAVAILABLE",
                status_code=503,
            ) from exc

        with tempfile.TemporaryDirectory(
            prefix="design-review-",
            dir=self.runtime_temp_root,
        ) as temporary_directory:
            step_path = Path(temporary_directory) / "model.step"
            step_path.write_bytes(step_bytes)
            try:
                part = import_step(str(step_path))
            except Exception as exc:
                raise DesignReviewServiceError(
                    f"STEP 解析失败：{exc}",
                    "REVIEW_STEP_PARSE_FAILED",
                    status_code=502,
                ) from exc

        bb = part.bounding_box()
        size = (
            round(float(bb.max.X - bb.min.X), 2),
            round(float(bb.max.Y - bb.min.Y), 2),
            round(float(bb.max.Z - bb.min.Z), 2),
        )
        volume = float(part.volume)
        edges = [float(e.length) for e in part.edges()]
        min_edge = min(edges) if edges else 0.0

        hole_radii: list[float] = []
        for face in part.faces():
            geom_type = str(getattr(face, "geom_type", ""))
            if "CYLINDER" in geom_type.upper():
                try:
                    radius = float(face.radius)
                    hole_radii.append(radius)
                except Exception:
                    continue

        checks: dict[str, object] = {
            "sizeMm": list(size),
            "volumeMm3": volume,
            "minEdgeMm": round(min_edge, 3),
            "holeRadiiMm": sorted({round(r, 3) for r in hole_radii}),
            "edgeCount": len(edges),
            "solidCount": len(part.solids()),
        }

        findings: list[dict[str, object]] = []
        if min_edge < 0.2:
            findings.append({
                "level": "high",
                "item": "过小特征",
                "detail": f"最小边长度仅 {min_edge:.3f} mm，可能难以加工或易断裂，建议放大至 ≥0.5mm。",
            })
        elif min_edge < 0.5:
            findings.append({
                "level": "medium",
                "item": "细小特征",
                "detail": f"最小边长度 {min_edge:.3f} mm，接近加工极限，建议确认是否必要。",
            })
        if hole_radii:
            min_hole = min(hole_radii)
            if min_hole < 1.5:
                findings.append({
                    "level": "medium",
                    "item": "小孔",
                    "detail": f"最小孔径 {min_hole * 2:.2f} mm，普通钻削可加工，但长径比大时建议电火花。",
                })
        if volume <= 0:
            findings.append({
                "level": "high",
                "item": "实体异常",
                "detail": "模型体积非正，可能存在拓扑错误，请检查建模。",
            })
        if len(part.solids()) > 1:
            findings.append({
                "level": "medium",
                "item": "多实体",
                "detail": f"模型包含 {len(part.solids())} 个实体，若非装配体建议合并为单一实体。",
            })
        checks["findings"] = findings
        return checks

    async def _request_llm(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise DesignReviewServiceError(
                f"调用 LLM 生成审查报告失败：{exc}",
                "REVIEW_LLM_REQUEST_FAILED",
                status_code=502,
            ) from exc
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise DesignReviewServiceError(
                "LLM 审查响应为空",
                "REVIEW_LLM_EMPTY",
                status_code=502,
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise DesignReviewServiceError(
                "LLM 审查响应为空",
                "REVIEW_LLM_EMPTY",
                status_code=502,
            )
        return content

    def _build_review_pdf(
        self,
        *,
        project_name: str,
        design_spec: Mapping[str, object] | None,
        analysis: Mapping[str, object],
        review_text: str,
    ) -> bytes:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        except ImportError as exc:
            raise DesignReviewServiceError(
                "报告生成组件不可用：缺少 reportlab",
                "REVIEW_PDF_DEP_UNAVAILABLE",
                status_code=503,
            ) from exc

        font_path = _resolve_cjk_font()
        if font_path is None:
            raise DesignReviewServiceError(
                "审查报告生成失败：未找到系统中文字体",
                "REVIEW_PDF_FONT_UNAVAILABLE",
                status_code=503,
            )
        try:
            if _REVIEW_FONT not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(_REVIEW_FONT, str(font_path)))
        except Exception as exc:
            raise DesignReviewServiceError(
                f"审查报告生成失败：无法加载中文字体 {font_path}",
                "REVIEW_PDF_FONT_UNAVAILABLE",
                status_code=503,
            ) from exc

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
        )
        title_style = ParagraphStyle(
            "title", fontName=_REVIEW_FONT, fontSize=18, leading=24, spaceAfter=12
        )
        heading_style = ParagraphStyle(
            "heading", fontName=_REVIEW_FONT, fontSize=12, leading=16, spaceBefore=10, spaceAfter=4
        )
        body_style = ParagraphStyle(
            "body", fontName=_REVIEW_FONT, fontSize=9.5, leading=15, spaceAfter=4
        )

        requirement = design_spec.get("requirementText") if design_spec else None
        story: list[object] = [
            Paragraph("设计审查报告", title_style),
            Paragraph(f"项目名称：{project_name}", body_style),
            Paragraph("一、几何参数", heading_style),
            Paragraph(
                f"外形尺寸：{analysis.get('sizeMm')} mm；体积：{analysis.get('volumeMm3')} mm³；"
                f"最小边：{analysis.get('minEdgeMm')} mm；孔数：{len(analysis.get('holeRadiiMm') or [])}。",
                body_style,
            ),
        ]
        findings = analysis.get("findings")
        if isinstance(findings, list) and findings:
            story.append(Paragraph("二、规则检查发现", heading_style))
            for finding in findings:
                level = finding.get("level")
                level_label = {"high": "高", "medium": "中", "low": "低"}.get(str(level), str(level))
                story.append(
                    Paragraph(
                        f"[{level_label}] {finding.get('item')}：{finding.get('detail')}",
                        body_style,
                    )
                )
        if requirement:
            story.append(Paragraph("三、设计需求回顾", heading_style))
            story.append(Paragraph(str(requirement), body_style))
        story.append(Paragraph("四、AI 审查意见", heading_style))
        for paragraph in str(review_text).split("\n"):
            text = paragraph.strip()
            if text:
                story.append(Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), body_style))
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                f"审查时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
                body_style,
            )
        )
        doc.build(story)
        return buffer.getvalue()

    async def create_review(
        self,
        *,
        db: Session,
        user_id: str,
        task_id: str | None = None,
        step_asset_id: str | None = None,
        project_name: str | None = None,
        requirement: str | None = None,
        publish_assets: bool = True,
    ) -> dict[str, JsonValue]:
        """对 3D 模型生成设计审查报告并入库。

        提供 task_id 时从任务 outputs 读取 STEP；否则需直接传 step_asset_id。
        """
        if not self.available:
            raise DesignReviewServiceError(
                "build123d 未安装，无法执行设计审查",
                "REVIEW_IMPORT_UNAVAILABLE",
                status_code=503,
            )
        if task_id:
            task = self.repository_factory(db).get_internal(task_id)
            if task is None:
                raise DesignReviewServiceError(
                    "工业品设计任务不存在",
                    "REVIEW_TASK_NOT_FOUND",
                    status_code=404,
                )
            outputs = task.get("outputs") or {}
            if not isinstance(outputs, Mapping):
                raise DesignReviewServiceError(
                    "任务产物数据异常",
                    "REVIEW_OUTPUTS_INVALID",
                    status_code=500,
                )
            step_asset_id = self._extract_asset_id(outputs.get("modelStepAssetId"))
            if step_asset_id is None:
                raise DesignReviewServiceError(
                    "该任务尚未产出 STEP 模型，无法审查",
                    "REVIEW_NO_MODEL",
                    status_code=400,
                )
            project_name = str(task.get("projectId") or "未命名项目")
            design_spec = task.get("designSpec")
            if not isinstance(design_spec, Mapping):
                design_spec = {}
            requirement = design_spec.get("requirementText") or (task.get("inputPayload") or {}).get("text") or ""
        if not step_asset_id:
            raise DesignReviewServiceError(
                "缺少可审查的 STEP 模型",
                "REVIEW_NO_MODEL",
                status_code=400,
            )
        project_name = str(project_name or "未命名项目")
        requirement = str(requirement or "")
        design_spec: Mapping[str, object] = {"requirementText": requirement} if requirement else {}

        step_bytes = self._read_asset(db, user_id, step_asset_id)
        analysis = self._analyze_step(step_bytes)

        findings = analysis.get("findings")
        finding_text = "\n".join(
            f"- [{f.get('level')}] {f.get('item')}: {f.get('detail')}"
            for f in findings
            if isinstance(findings, list) and isinstance(f, Mapping)
        ) or "规则检查未发现明显问题。"

        review_text = ""
        knowledge_context = ""
        try:
            from app.services.knowledge_base_service import knowledge_base_service

            context = knowledge_base_service.build_context(str(requirement or project_name), top_k=3)
            if context:
                knowledge_context = (
                    "\n\n【工业知识库参考（来自产业共享平台，辅助审查判断）】\n"
                    f"{context}"
                )
        except Exception:
            logger.debug("知识库检索不可用，跳过注入", exc_info=True)
        try:
            review_text = await self._request_llm([
                {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"设计需求：\n{requirement}\n\n"
                        f"几何分析数据：\n"
                        f"外形尺寸 {analysis.get('sizeMm')} mm；体积 {analysis.get('volumeMm3')} mm³；"
                        f"最小边 {analysis.get('minEdgeMm')} mm；孔径 {analysis.get('holeRadiiMm')} mm。\n"
                        f"规则检查结果：\n{finding_text}\n"
                        f"{knowledge_context}\n\n"
                        f"请给出设计审查报告。"
                    ),
                },
            ])
        except DesignReviewServiceError as exc:
            logger.warning("LLM 审查失败，降级仅输出规则检查：%s", exc.error_code)
            review_text = "（AI 审查不可用，以下仅含规则检查结果。）\n" + finding_text

        pdf_bytes = self._build_review_pdf(
            project_name=project_name,
            design_spec=design_spec,
            analysis=analysis,
            review_text=review_text,
        )
        review_task_id = f"design_review_{uuid.uuid4().hex[:16]}"
        asset = self.asset_service.store_bytes(
            db=db,
            user_id=user_id,
            filename=f"{review_task_id}.pdf",
            content_type="application/pdf",
            kind="document",
            source="generated",
            content=pdf_bytes,
            task_id=task_id,
            metadata={
                "designReviewTaskId": review_task_id,
                "format": "pdf",
            },
            publish=publish_assets,
        )
        return {
            "taskId": review_task_id,
            "status": "completed",
            "reviewAssetId": str(asset.id),
            "reviewDownloadUrl": f"{settings.API_V1_PREFIX}/assets/{asset.id}/download",
            "analysis": analysis,
            "reviewText": review_text,
        }


design_review_service = DesignReviewService()
