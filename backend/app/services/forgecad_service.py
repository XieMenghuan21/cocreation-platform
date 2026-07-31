"""Qwen3 + ForgeCAD 后端编排服务。"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import uuid
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import httpx
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config.settings import settings
from app.schemas.forgecad import (
    ForgeCadDiagnostic,
    ForgeCadErrorDetail,
    ForgeCadBomItem,
    ForgeCadExplosionStep,
    ForgeCadGeneratedAsset,
    ForgeCadImportAsset,
    ForgeCadImportFeature,
    ForgeCadPreviewEntity,
    ForgeCadGenerateRequest,
    ForgeCadGenerateResult,
    ForgeCadModelObject,
    ForgeCadParameter,
    ForgeCadTaskStatus,
    ForgeCadVersionSnapshot,
)
from app.services.asset_blob_service import AssetBlobService
from app.services.safe_content_validator import (
    is_valid_jpeg,
    is_valid_pdf,
    is_valid_png,
    is_valid_webp,
    trusted_image_decoder_available,
    trusted_pdf_parser_available,
)

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
ImportAnalysis = dict[
    str,
    str
    | None
    | list[ForgeCadImportFeature]
    | list[ForgeCadPreviewEntity]
    | list[ForgeCadBomItem]
    | list[ForgeCadExplosionStep],
]


@dataclass(frozen=True)
class CliRunResult:
    """ForgeCAD CLI 执行结果。"""

    logs: str
    output_path: str | None


class ForgeCadServiceError(Exception):
    """ForgeCAD 服务可预期错误。"""

    def __init__(
        self,
        message: str,
        error_code: str,
        status_code: int = 400,
        detail: ForgeCadErrorDetail | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.detail = detail


class ForgeCadService:
    """负责生成 ForgeCAD 脚本并按需调用 CLI。"""

    allowed_import_extensions = {
        ".step",
        ".stp",
        ".stl",
        ".dxf",
        ".dwg",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
        ".webm",
    }
    max_import_size_bytes = settings.FORGECAD_IMPORT_MAX_BYTES

    def __init__(
        self,
        *,
        asset_service: AssetBlobService | None = None,
        runtime_temp_root: Path | None = None,
    ) -> None:
        self.bridge_base_url = os.getenv("FORGECAD_BRIDGE_BASE_URL", "").rstrip("/")
        self.bridge_token = os.getenv("FORGECAD_BRIDGE_TOKEN", "").strip()
        self.bridge_token_file = os.getenv("FORGECAD_BRIDGE_TOKEN_FILE", "").strip()
        self.qwen_base_url = os.getenv("FORGECAD_QWEN_BASE_URL", "http://127.0.0.1:55904/v1").rstrip("/")
        self.qwen_api_key = os.getenv("FORGECAD_QWEN_API_KEY", "").strip()
        self.qwen_model = os.getenv("FORGECAD_QWEN_MODEL", "/data/models/Qwen3-32B-INT8").strip()
        self.cli_binary = os.getenv("FORGECAD_CLI_BIN", "forgecad").strip() or "forgecad"
        self.sandbox_wrapper = os.getenv("FORGECAD_SANDBOX_WRAPPER", "").strip()
        self.request_timeout = float(os.getenv("FORGECAD_QWEN_TIMEOUT_SECONDS", "180"))
        self.step_preview_format = os.getenv("FORGECAD_STEP_PREVIEW_FORMAT", "stl").strip().lower() or "stl"
        self.step_converter_command = os.getenv("FORGECAD_STEP_CONVERTER_CMD", "").strip()
        self.asset_service = asset_service or AssetBlobService(
            chunk_size=settings.ASSET_CHUNK_SIZE_BYTES
        )
        self.runtime_temp_root = runtime_temp_root

    def save_import_asset(
        self,
        *,
        db: Session,
        user_id: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> ForgeCadImportAsset:
        """保存用户导入的 CAD 或图纸文件，返回可用于生成上下文的真实资产信息。"""
        safe_filename = self._sanitize_filename(filename)
        extension = Path(safe_filename).suffix.lower()
        if extension not in self.allowed_import_extensions:
            raise ForgeCadServiceError(
                f"暂不支持该文件类型：{extension or '无扩展名'}",
                "FORGECAD_IMPORT_TYPE_UNSUPPORTED",
                status_code=400,
            )

        if not content:
            raise ForgeCadServiceError(
                "导入文件为空，请重新选择 CAD 或图纸文件",
                "FORGECAD_IMPORT_EMPTY",
                status_code=400,
            )

        if len(content) > self.max_import_size_bytes:
            raise ForgeCadServiceError(
                "导入文件超过 50MB，请压缩或拆分后再上传",
                "FORGECAD_IMPORT_TOO_LARGE",
                status_code=413,
            )

        detected_content_type = self._validate_import_content(extension, content)
        normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
        if (
            normalized_content_type
            and normalized_content_type != "application/octet-stream"
            and normalized_content_type != detected_content_type
            and not (
                extension in {".jpg", ".jpeg"}
                and normalized_content_type == "image/jpg"
            )
        ):
            raise ForgeCadServiceError(
                "文件内容与声明的媒体类型不一致",
                "FORGECAD_IMPORT_CONTENT_TYPE_MISMATCH",
                status_code=400,
            )

        analysis = self._analyze_import_content(
            extension=extension,
            content=content,
        )
        source_asset = self.asset_service.store_bytes(
            db=db,
            user_id=user_id,
            filename=safe_filename,
            content_type=detected_content_type,
            kind="source",
            source="upload",
            content=content,
            metadata={
                "parseStatus": str(analysis["parse_status"]),
                "parseMessage": str(analysis["parse_message"]),
            },
        )
        if extension in {".step", ".stp"}:
            with tempfile.TemporaryDirectory(
                prefix="forgecad-import-",
                dir=self.runtime_temp_root,
            ) as temporary_directory:
                input_path = Path(temporary_directory) / safe_filename
                input_path.write_bytes(content)
                analysis = self._attach_step_preview_conversion(
                    db=db,
                    user_id=user_id,
                    analysis=analysis,
                    source_asset_id=str(source_asset.id),
                    input_path=input_path,
                )

        return ForgeCadImportAsset(
            assetId=str(source_asset.id),
            filename=safe_filename,
            extension=extension.lstrip("."),
            contentType=detected_content_type,
            sizeBytes=len(content),
            storagePath=None,
            createdAt=datetime.now(timezone.utc).isoformat(),
            parseStatus=analysis["parse_status"],
            parseMessage=analysis["parse_message"],
            parseFeatures=analysis["parse_features"],
            previewKind=analysis["preview_kind"],
            previewAssetId=analysis.get("preview_asset_id"),
            previewAssetPath=analysis.get("preview_asset_path"),
            previewAssetFormat=analysis.get("preview_asset_format"),
            previewAssetUrl=analysis.get("preview_asset_url"),
            conversionStatus=analysis.get("conversion_status"),
            conversionMessage=analysis.get("conversion_message"),
            previewEntities=analysis["preview_entities"],
            bomItems=analysis["bom_items"],
            explosionSteps=analysis["explosion_steps"],
        )

    @staticmethod
    def _validate_import_content(extension: str, content: bytes) -> str:
        """Validate formats with stable signatures before persisting untrusted bytes."""
        if extension == ".dwg":
            raise ForgeCadServiceError(
                "DWG 导入已禁用，请转换为 STEP、STL、DXF 或 PDF 后上传",
                "FORGECAD_IMPORT_FORMAT_DISABLED",
                status_code=400,
            )
        if extension in {".png", ".jpg", ".jpeg", ".webp"} and (
            not trusted_image_decoder_available()
        ):
            raise ForgeCadServiceError(
                "图片导入已禁用：当前环境缺少受信图片解码器",
                "FORGECAD_IMPORT_FORMAT_DISABLED",
                status_code=503,
            )
        if extension == ".pdf" and not trusted_pdf_parser_available():
            raise ForgeCadServiceError(
                "PDF 导入已禁用：当前环境缺少受信 PDF 解析器",
                "FORGECAD_IMPORT_FORMAT_DISABLED",
                status_code=503,
            )

        def valid_stl() -> bool:
            stripped = content.strip()
            if (
                stripped.lower().startswith(b"solid ")
                and b"facet normal" in stripped.lower()
                and stripped.lower().endswith(b"endsolid")
            ):
                return True
            if len(content) < 84:
                return False
            triangle_count = int.from_bytes(content[80:84], "little")
            return triangle_count > 0 and len(content) == 84 + triangle_count * 50

        def valid_dxf() -> bool:
            try:
                normalized = content.decode("ascii").replace("\r\n", "\n").strip()
            except UnicodeDecodeError:
                return False
            return normalized.startswith("0\nSECTION") and normalized.endswith("0\nEOF")

        def valid_wav() -> bool:
            return (
                len(content) >= 44
                and content[:4] == b"RIFF"
                and content[8:12] == b"WAVE"
                and int.from_bytes(content[4:8], "little") + 8 == len(content)
                and b"fmt " in content[12:]
                and b"data" in content[12:]
            )

        def valid_mp3() -> bool:
            if len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0:
                return True
            if len(content) < 12 or not content.startswith(b"ID3"):
                return False
            tag_size = (
                (content[6] & 0x7F) << 21
                | (content[7] & 0x7F) << 14
                | (content[8] & 0x7F) << 7
                | (content[9] & 0x7F)
            )
            frame_offset = 10 + tag_size
            return (
                frame_offset + 2 <= len(content)
                and content[frame_offset] == 0xFF
                and content[frame_offset + 1] & 0xE0 == 0xE0
            )

        def valid_m4a() -> bool:
            if len(content) < 16 or content[4:8] != b"ftyp":
                return False
            box_size = int.from_bytes(content[:4], "big")
            brands = content[8:min(box_size, len(content))]
            return 16 <= box_size <= len(content) and any(
                brand in brands for brand in (b"M4A ", b"mp42", b"isom")
            )

        def valid_aac() -> bool:
            if len(content) < 7 or content[0] != 0xFF or content[1] & 0xF6 != 0xF0:
                return False
            frame_length = (
                (content[3] & 0x03) << 11
                | content[4] << 3
                | (content[5] & 0xE0) >> 5
            )
            return 7 <= frame_length <= len(content)

        def valid_ogg() -> bool:
            if len(content) < 27 or content[:5] != b"OggS\x00":
                return False
            segment_count = content[26]
            return 27 + segment_count <= len(content)

        def valid_flac() -> bool:
            if len(content) < 8 or content[:4] != b"fLaC":
                return False
            block_length = int.from_bytes(content[5:8], "big")
            return block_length > 0 and 8 + block_length <= len(content)

        def valid_webm() -> bool:
            return (
                len(content) >= 8
                and content.startswith(b"\x1a\x45\xdf\xa3")
                and content[4] != 0
                and b"webm" in content[:4096].lower()
            )

        signatures: dict[str, tuple[str, bool]] = {
            ".png": ("image/png", is_valid_png(content)),
            ".jpg": ("image/jpeg", is_valid_jpeg(content)),
            ".jpeg": ("image/jpeg", is_valid_jpeg(content)),
            ".webp": ("image/webp", is_valid_webp(content)),
            ".pdf": ("application/pdf", is_valid_pdf(content)),
            ".step": (
                "application/step",
                content.lstrip().upper().startswith(b"ISO-10303-21;")
                and b"END-ISO-10303-21;" in content.upper(),
            ),
            ".stp": (
                "application/step",
                content.lstrip().upper().startswith(b"ISO-10303-21;")
                and b"END-ISO-10303-21;" in content.upper(),
            ),
            ".stl": ("model/stl", valid_stl()),
            ".dxf": ("application/dxf", valid_dxf()),
            ".wav": ("audio/wav", valid_wav()),
            ".mp3": ("audio/mpeg", valid_mp3()),
            ".m4a": ("audio/mp4", valid_m4a()),
            ".aac": ("audio/aac", valid_aac()),
            ".ogg": ("audio/ogg", valid_ogg()),
            ".flac": ("audio/flac", valid_flac()),
            ".webm": ("audio/webm", valid_webm()),
        }
        media_type, valid = signatures.get(
            extension,
            (
                "application/octet-stream",
                False,
            ),
        )
        if not valid:
            raise ForgeCadServiceError(
                "文件扩展名与实际内容不匹配或文件结构不完整",
                "FORGECAD_IMPORT_CONTENT_INVALID",
                status_code=400,
            )
        return media_type

    async def generate(
        self,
        request: ForgeCadGenerateRequest,
        *,
        db: Session,
        user_id: str,
        task_id: str | None = None,
        publish_assets: bool = True,
    ) -> ForgeCadGenerateResult:
        """生成 ForgeCAD 脚本，必要时运行 CLI 校验。"""
        if self.bridge_base_url:
            return await self._generate_via_bridge(
                request,
                db=db,
                user_id=user_id,
                task_id=task_id,
                publish_assets=publish_assets,
            )

        raw_content = await self._request_qwen(request)
        script = self.extract_script(raw_content)
        if not script.strip():
            raise ForgeCadServiceError(
                "Qwen3 未返回可执行的 ForgeCAD 脚本",
                "FORGECAD_SCRIPT_EMPTY",
                status_code=502,
            )

        forgecad_task_id = f"forgecad_{uuid.uuid4().hex[:16]}"
        with tempfile.TemporaryDirectory(
            prefix="forgecad-task-",
            dir=self.runtime_temp_root,
        ) as temporary_directory:
            work_dir = Path(temporary_directory)
            script_path = work_dir / "model.forge.js"
            script_path.write_text(script, encoding="utf-8")
            logs = "已生成 ForgeCAD 脚本，未执行 CLI。"
            cli_executed = False
            output_path: Path | None = None
            task_status: ForgeCadTaskStatus = "script_generated"

            if request.run_cli:
                cli_result = await run_in_threadpool(
                    self._run_cli,
                    script_path,
                    request.export_format,
                )
                logs = cli_result.logs
                output_path = (
                    Path(cli_result.output_path)
                    if cli_result.output_path is not None
                    else None
                )
                cli_executed = True
                task_status = "completed"

            script_asset = self.asset_service.store_bytes(
                db=db,
                user_id=user_id,
                filename=f"{forgecad_task_id}.forge.js",
                content_type="text/javascript",
                kind="source",
                source="generated",
                content=script.encode("utf-8"),
                task_id=task_id,
                metadata={"forgecadTaskId": forgecad_task_id},
                publish=publish_assets,
            )
            output_asset = None
            if output_path is not None and output_path.is_file():
                output_asset = self.asset_service.store_bytes(
                    db=db,
                    user_id=user_id,
                    filename=f"{forgecad_task_id}.{request.export_format}",
                    content_type=self._export_content_type(request.export_format),
                    kind="cad",
                    source="generated",
                    content=output_path.read_bytes(),
                    task_id=task_id,
                    metadata={
                        "forgecadTaskId": forgecad_task_id,
                        "format": request.export_format,
                    },
                    publish=publish_assets,
                )

        download_asset = output_asset or script_asset
        download_url = self._asset_url(download_asset.id)
        model_objects = self._extract_model_objects(logs)
        parameters = self._extract_parameters(script=script, logs=logs)
        generated_assets = self._build_generated_assets(
            script_asset_id=str(script_asset.id),
            output_asset_id=str(output_asset.id) if output_asset is not None else None,
            export_format=request.export_format,
        )
        diagnostics = self._build_diagnostics(
            cli_executed=cli_executed,
            output_path=str(output_path) if output_path is not None else None,
            export_format=request.export_format,
            model_objects=model_objects,
        )

        return ForgeCadGenerateResult(
            taskId=forgecad_task_id,
            status=task_status,
            script=script,
            scriptAssetId=str(script_asset.id),
            outputAssetId=str(output_asset.id) if output_asset is not None else None,
            scriptPath=None,
            workDir=None,
            outputPath=None,
            downloadUrl=download_url,
            logs=logs,
            cliExecuted=cli_executed,
            exportFormat=request.export_format,
            modelObjects=model_objects,
            parameters=parameters,
            generatedAssets=generated_assets,
            diagnostics=diagnostics,
            snapshot=self._build_snapshot(
                request=request,
                task_id=forgecad_task_id,
                task_status=task_status,
                script_asset_id=str(script_asset.id),
                output_asset_id=str(output_asset.id) if output_asset is not None else None,
                logs=logs,
                cli_executed=cli_executed,
                download_url=download_url,
                model_objects=model_objects,
                parameters=parameters,
                generated_assets=generated_assets,
                diagnostics=diagnostics,
            ),
        )

    async def _generate_via_bridge(
        self,
        request: ForgeCadGenerateRequest,
        *,
        db: Session,
        user_id: str,
        task_id: str | None,
        publish_assets: bool,
    ) -> ForgeCadGenerateResult:
        """通过远端 ForgeCAD bridge 服务生成并执行建模。"""
        bridge_token = self._load_bridge_token()
        if not bridge_token:
            raise ForgeCadServiceError(
                "未配置 ForgeCAD bridge token，请设置 FORGECAD_BRIDGE_TOKEN 或 FORGECAD_BRIDGE_TOKEN_FILE",
                "FORGECAD_BRIDGE_TOKEN_MISSING",
                status_code=500,
            )

        payload = request.model_dump(by_alias=True)
        headers = {
            "Content-Type": "application/json",
            "X-ForgeCAD-Token": bridge_token,
        }

        remote_output: bytes | None = None
        remote_content_type = self._export_content_type(request.export_format)
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(
                    f"{self.bridge_base_url}/forgecad/generate",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict):
                    remote_task_id = data.get("taskId")
                    if (
                        isinstance(remote_task_id, str)
                        and request.export_format != "none"
                    ):
                        file_response = await client.get(
                            f"{self.bridge_base_url}/forgecad/file/{remote_task_id}",
                            headers=headers,
                        )
                        file_response.raise_for_status()
                        remote_output = file_response.content
                        remote_content_type = file_response.headers.get(
                            "content-type",
                            remote_content_type,
                        )
        except httpx.HTTPError as exc:
            raise ForgeCadServiceError(
                f"调用 ForgeCAD bridge 失败：{exc}",
                "FORGECAD_BRIDGE_REQUEST_FAILED",
                status_code=502,
            ) from exc

        if not isinstance(data, dict):
            raise ForgeCadServiceError(
                "ForgeCAD bridge 响应不是对象",
                "FORGECAD_BRIDGE_RESPONSE_INVALID",
                status_code=502,
            )
        normalized_data = dict(data)
        normalized_data.update(
            {
                "scriptPath": None,
                "workDir": None,
                "outputPath": None,
                "snapshot": None,
                "generatedAssets": [],
            }
        )
        try:
            result = ForgeCadGenerateResult.model_validate(normalized_data)
        except Exception as exc:
            raise ForgeCadServiceError(
                f"ForgeCAD bridge 响应解析失败：{exc}",
                "FORGECAD_BRIDGE_RESPONSE_INVALID",
                status_code=502,
            ) from exc

        script_asset = self.asset_service.store_bytes(
            db=db,
            user_id=user_id,
            filename=f"{result.task_id}.forge.js",
            content_type="text/javascript",
            kind="source",
            source="generated",
            content=result.script.encode("utf-8"),
            task_id=task_id,
            metadata={"forgecadTaskId": result.task_id, "provider": "bridge"},
            publish=publish_assets,
        )
        output_asset = None
        if remote_output is not None:
            output_asset = self.asset_service.store_bytes(
                db=db,
                user_id=user_id,
                filename=f"{result.task_id}.{result.export_format}",
                content_type=remote_content_type,
                kind="cad",
                source="generated",
                content=remote_output,
                task_id=task_id,
                metadata={
                    "forgecadTaskId": result.task_id,
                    "provider": "bridge",
                    "format": result.export_format,
                },
                publish=publish_assets,
            )
        download_url = self._asset_url(
            output_asset.id if output_asset is not None else script_asset.id
        )
        model_objects = result.model_objects or self._extract_model_objects(result.logs)
        parameters = result.parameters or self._extract_parameters(script=result.script, logs=result.logs)
        generated_assets = self._build_generated_assets(
            script_asset_id=str(script_asset.id),
            output_asset_id=str(output_asset.id) if output_asset is not None else None,
            export_format=result.export_format,
        )
        diagnostics = result.diagnostics or self._build_diagnostics(
            cli_executed=result.cli_executed,
            output_path="generated" if output_asset is not None else None,
            export_format=result.export_format,
            model_objects=model_objects,
        )

        return result.model_copy(
            update={
                "script_asset_id": str(script_asset.id),
                "output_asset_id": (
                    str(output_asset.id)
                    if output_asset is not None
                    else None
                ),
                "script_path": None,
                "work_dir": None,
                "output_path": None,
                "download_url": download_url,
                "model_objects": model_objects,
                "parameters": parameters,
                "generated_assets": generated_assets,
                "diagnostics": diagnostics,
                "snapshot": self._build_snapshot(
                    request=request,
                    task_id=result.task_id,
                    task_status=result.status,
                    script_asset_id=str(script_asset.id),
                    output_asset_id=(
                        str(output_asset.id)
                        if output_asset is not None
                        else None
                    ),
                    logs=result.logs,
                    cli_executed=result.cli_executed,
                    download_url=download_url,
                    model_objects=model_objects,
                    parameters=parameters,
                    generated_assets=generated_assets,
                    diagnostics=diagnostics,
                )
            }
        )

    def _build_snapshot(
        self,
        *,
        request: ForgeCadGenerateRequest,
        task_id: str,
        task_status: ForgeCadTaskStatus,
        script_asset_id: str,
        output_asset_id: str | None,
        logs: str,
        cli_executed: bool,
        download_url: str | None = None,
        model_objects: list[ForgeCadModelObject] | None = None,
        parameters: list[ForgeCadParameter] | None = None,
        generated_assets: list[ForgeCadGeneratedAsset] | None = None,
        diagnostics: list[ForgeCadDiagnostic] | None = None,
    ) -> ForgeCadVersionSnapshot:
        action_labels = {
            "create": "新建设计项目",
            "structure": "结构修改",
            "appearance": "外观调整",
            "derive": "派生新版本",
            "concept": "派生新版本",
        }
        summary = self._summarize_logs(logs)
        status_label = "执行完成" if task_status == "completed" else "脚本已生成"

        return ForgeCadVersionSnapshot(
            taskId=task_id,
            changeType=action_labels.get(request.action, "设计修改"),
            sourceObject=request.source_object.strip() or "当前设计项目",
            scriptAssetId=script_asset_id,
            outputAssetId=output_asset_id,
            scriptPath=None,
            workDir=None,
            outputPath=None,
            downloadUrl=download_url,
            executionSummary=summary,
            createdAt=datetime.now(timezone.utc).isoformat(),
            statusLabel=status_label,
            cliExecuted=cli_executed,
            exportFormat=request.export_format,
            modelObjects=model_objects or [],
            parameters=parameters or [],
            generatedAssets=generated_assets or [],
            diagnostics=diagnostics or [],
        )

    def _extract_model_objects(self, logs: str) -> list[ForgeCadModelObject]:
        objects: list[ForgeCadModelObject] = []
        object_pattern = re.compile(
            r"^\s*(?P<name>[^:]+):\s+vol=(?P<volume>\S+)\s+"
            r"bbox=\[(?P<bbox_min>[^\]]+)\]\s*→\s*\[(?P<bbox_max>[^\]]+)\]\s+"
            r"geom=(?P<geometry>.+)$"
        )
        for line in logs.splitlines():
            matched = object_pattern.match(line.strip())
            if not matched:
                continue
            objects.append(
                ForgeCadModelObject(
                    name=matched.group("name").strip(),
                    volume=matched.group("volume").strip(),
                    bbox=f"[{matched.group('bbox_min').strip()}] -> [{matched.group('bbox_max').strip()}]",
                    geometry=matched.group("geometry").strip(),
                )
            )
        return objects

    def _extract_parameters(self, *, script: str, logs: str) -> list[ForgeCadParameter]:
        params: dict[str, str | None] = {}
        for matched in re.finditer(r"param\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\)", script):
            params[matched.group(1).strip()] = matched.group(2).strip()

        for line in logs.splitlines():
            if "✓ Params:" not in line:
                continue
            _, raw_names = line.split(":", 1)
            for name in raw_names.split(","):
                normalized = name.strip()
                if normalized:
                    params.setdefault(normalized, None)

        return [
            ForgeCadParameter(name=name, defaultValue=default_value)
            for name, default_value in params.items()
        ]

    def _build_generated_assets(
        self,
        *,
        script_asset_id: str,
        output_asset_id: str | None,
        export_format: str,
    ) -> list[ForgeCadGeneratedAsset]:
        assets = [
            ForgeCadGeneratedAsset(
                name="ForgeCAD 脚本",
                assetId=script_asset_id,
                assetType="script",
                path=None,
                downloadUrl=self._asset_url(script_asset_id),
                status="已生成",
            )
        ]
        if output_asset_id:
            assets.append(
                ForgeCadGeneratedAsset(
                    name=f"{export_format.upper()} 导出文件",
                    assetId=output_asset_id,
                    assetType=export_format,
                    path=None,
                    downloadUrl=self._asset_url(output_asset_id),
                    status="已生成",
                )
            )
        return assets

    @staticmethod
    def _asset_url(asset_id: object) -> str:
        return f"{settings.API_V1_PREFIX}/assets/{asset_id}/download"

    @staticmethod
    def _export_content_type(export_format: str) -> str:
        return {
            "step": "application/step",
            "stl": "model/stl",
            "brep": "application/octet-stream",
        }.get(export_format, "application/octet-stream")

    def _build_diagnostics(
        self,
        *,
        cli_executed: bool,
        output_path: str | None,
        export_format: str,
        model_objects: list[ForgeCadModelObject],
    ) -> list[ForgeCadDiagnostic]:
        diagnostics: list[ForgeCadDiagnostic] = []
        diagnostics.append(
            ForgeCadDiagnostic(
                level="info",
                title="CLI 执行状态",
                detail="ForgeCAD CLI 已执行完成。" if cli_executed else "当前任务只生成脚本，未执行 ForgeCAD CLI。",
            )
        )
        diagnostics.append(
            ForgeCadDiagnostic(
                level="info" if model_objects else "warning",
                title="模型对象",
                detail=f"CLI 日志返回 {len(model_objects)} 个模型对象。" if model_objects else "CLI 日志未返回模型对象明细。",
            )
        )
        if export_format != "none" and not output_path:
            diagnostics.append(
                ForgeCadDiagnostic(
                    level="warning",
                    title="导出文件",
                    detail=f"请求了 {export_format.upper()} 导出，但 bridge 响应没有返回 outputPath。",
                )
            )
        diagnostics.append(
            ForgeCadDiagnostic(
                level="warning",
                title="BOM 数据",
                detail="ForgeCAD 当前响应没有材料、工艺、数量字段，无法生成真实 BOM。",
            )
        )
        return diagnostics

    def _summarize_logs(self, logs: str) -> str:
        compact_logs = "；".join(
            line.strip()
            for line in logs.splitlines()
            if line.strip()
        )
        if not compact_logs:
            return "ForgeCAD 已返回脚本与执行结果。"
        return compact_logs[:240]

    def _load_bridge_token(self) -> str:
        if self.bridge_token:
            return self.bridge_token

        if not self.bridge_token_file:
            return ""

        token_path = Path(self.bridge_token_file).expanduser()
        try:
            return token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _sanitize_filename(self, filename: str) -> str:
        normalized = Path(filename or "").name.strip()
        if not normalized:
            raise ForgeCadServiceError(
                "文件名不能为空",
                "FORGECAD_IMPORT_FILENAME_EMPTY",
                status_code=400,
            )
        if normalized in {".", ".."}:
            raise ForgeCadServiceError(
                "文件名不合法",
                "FORGECAD_IMPORT_FILENAME_INVALID",
                status_code=400,
            )
        return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", normalized)[:180]

    def _analyze_import_content(
        self,
        *,
        extension: str,
        content: bytes,
    ) -> ImportAnalysis:
        """对导入文件做轻量结构解析，解析不到时明确返回真实状态。"""
        if extension in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm"}:
            return self._build_import_analysis(
                parse_status="stored",
                parse_message="语音描述已保存；自动生成时将交给远端 CAD AI 转写后提取设计需求。",
                parse_features=[ForgeCadImportFeature(label="文件类型", value="语音描述")],
                preview_kind="audio_transcription_required",
            )
        if extension == ".stl":
            return self._analyze_stl_content(content)
        if extension == ".dxf":
            return self._analyze_dxf_content(content)
        if extension in {".step", ".stp"}:
            return self._analyze_step_content(content)
        if extension == ".dwg":
            return self._build_import_analysis(
                parse_status="stored",
                parse_message="DWG 属于专有二进制格式，文件已保存；需要接入专用转换器后才能拆解图层和几何。",
                parse_features=[ForgeCadImportFeature(label="文件类型", value="DWG")],
                preview_kind="converter_required",
            )
        if extension == ".pdf":
            return self._build_import_analysis(
                parse_status="stored",
                parse_message="PDF 图纸已保存；需要接入 OCR/图纸识别后才能提取尺寸和结构。",
                parse_features=[ForgeCadImportFeature(label="文件类型", value="PDF 图纸")],
                preview_kind="ocr_required",
            )
        return self._build_import_analysis(
            parse_status="stored",
            parse_message="图片图纸已保存；需要接入视觉识别后才能提取轮廓、尺寸和零部件。",
            parse_features=[ForgeCadImportFeature(label="文件类型", value="图片图纸")],
            preview_kind="vision_required",
        )

    def _build_import_analysis(
        self,
        *,
        parse_status: str,
        parse_message: str,
        parse_features: list[ForgeCadImportFeature],
        preview_kind: str,
        preview_asset_id: str | None = None,
        preview_asset_path: str | None = None,
        preview_asset_format: str | None = None,
        preview_asset_url: str | None = None,
        conversion_status: str | None = None,
        conversion_message: str | None = None,
        preview_entities: list[ForgeCadPreviewEntity] | None = None,
        bom_items: list[ForgeCadBomItem] | None = None,
        explosion_steps: list[ForgeCadExplosionStep] | None = None,
    ) -> ImportAnalysis:
        return {
            "parse_status": parse_status,
            "parse_message": parse_message,
            "parse_features": parse_features,
            "preview_kind": preview_kind,
            "preview_asset_id": preview_asset_id,
            "preview_asset_path": preview_asset_path,
            "preview_asset_format": preview_asset_format,
            "preview_asset_url": preview_asset_url,
            "conversion_status": conversion_status,
            "conversion_message": conversion_message,
            "preview_entities": preview_entities or [],
            "bom_items": bom_items or [],
            "explosion_steps": explosion_steps or [],
        }

    def _analyze_stl_content(self, content: bytes) -> ImportAnalysis:
        vertices = self._extract_stl_vertices(content)
        if not vertices:
            return self._build_import_analysis(
                parse_status="stored",
                parse_message="STL 文件已保存，但未解析到三角面片顶点；可继续作为生成参考。",
                parse_features=[ForgeCadImportFeature(label="文件类型", value="STL")],
                preview_kind="stl",
            )

        bbox = self._format_bbox(vertices)
        triangle_count = len(vertices) // 3
        return self._build_import_analysis(
            parse_status="parsed_lite",
            parse_message=f"已解析 STL 基础网格信息：约 {triangle_count} 个三角面片，包围盒 {bbox}。",
            parse_features=[
                ForgeCadImportFeature(label="文件类型", value="STL 网格"),
                ForgeCadImportFeature(label="三角面片", value=str(triangle_count)),
                ForgeCadImportFeature(label="包围盒", value=bbox),
            ],
            preview_kind="stl",
            bom_items=[
                ForgeCadBomItem(name="STL 网格模型", material="未识别", quantity=1, size=bbox, source="import"),
            ],
            explosion_steps=[
                ForgeCadExplosionStep(step=1, name="整体网格", offset=[0, 0, 0], description="STL 暂无装配层级，按整体模型展示。"),
            ],
        )

    def _extract_stl_vertices(self, content: bytes) -> list[tuple[float, float, float]]:
        if content[:5].lower() == b"solid" and b"facet" in content[:2048].lower():
            text = content.decode("utf-8", errors="ignore")
            return [
                (float(match.group(1)), float(match.group(2)), float(match.group(3)))
                for match in re.finditer(
                    r"\bvertex\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
                    r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+"
                    r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
                    text,
                )
            ]

        if len(content) < 84:
            return []
        triangle_count = struct.unpack("<I", content[80:84])[0]
        expected_size = 84 + triangle_count * 50
        if triangle_count <= 0 or len(content) < min(expected_size, 84 + 50):
            return []

        vertices: list[tuple[float, float, float]] = []
        max_triangles = min(triangle_count, 200000)
        for index in range(max_triangles):
            base = 84 + index * 50 + 12
            for vertex_index in range(3):
                offset = base + vertex_index * 12
                vertices.append(struct.unpack("<fff", content[offset:offset + 12]))
        return vertices

    def _analyze_dxf_content(self, content: bytes) -> ImportAnalysis:
        text = content.decode("utf-8", errors="ignore")
        if not text.strip():
            text = content.decode("gb18030", errors="ignore")
        lines = [line.strip() for line in text.splitlines()]
        entity_counts: dict[str, int] = {}
        tracked_entities = {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE", "TEXT", "MTEXT", "INSERT"}
        preview_entities = self._extract_dxf_preview_entities(lines)
        for index in range(0, max(len(lines) - 1, 0), 2):
            if lines[index] == "0":
                entity = lines[index + 1].upper()
                if entity in tracked_entities:
                    entity_counts[entity] = entity_counts.get(entity, 0) + 1

        features = [ForgeCadImportFeature(label=name, value=str(count)) for name, count in sorted(entity_counts.items())]
        if not features:
            return self._build_import_analysis(
                parse_status="stored",
                parse_message="DXF 文件已保存，但未识别到常见实体；可继续作为生成参考。",
                parse_features=[ForgeCadImportFeature(label="文件类型", value="DXF")],
                preview_kind="dxf",
            )

        summary = "，".join(f"{item.label}:{item.value}" for item in features[:6])
        return self._build_import_analysis(
            parse_status="parsed_lite",
            parse_message=f"已解析 DXF 基础实体：{summary}。",
            parse_features=[ForgeCadImportFeature(label="文件类型", value="DXF 图纸"), *features],
            preview_kind="dxf",
            preview_entities=preview_entities,
            bom_items=[
                ForgeCadBomItem(name=f"DXF {item.label}", material="未识别", quantity=int(item.value), size=None, source="import")
                for item in features
                if item.value.isdigit()
            ],
            explosion_steps=[
                ForgeCadExplosionStep(step=index + 1, name=item.label, offset=[index * 12, index * 5, 0], description=f"图纸实体 {item.label}，数量 {item.value}。")
                for index, item in enumerate(features[:8])
            ],
        )

    def _analyze_step_content(self, content: bytes) -> ImportAnalysis:
        text = content.decode("utf-8", errors="ignore")
        entity_count = len(re.findall(r"^#\d+\s*=", text, flags=re.MULTILINE))
        product_names = [
            name.strip()
            for name in re.findall(r"PRODUCT\s*\(\s*'([^']+)'", text, flags=re.IGNORECASE)
            if name.strip()
        ]
        features = [
            ForgeCadImportFeature(label="文件类型", value="STEP 装配/零件"),
            ForgeCadImportFeature(label="STEP 实体数", value=str(entity_count)),
        ]
        if product_names:
            features.append(ForgeCadImportFeature(label="产品名", value="、".join(product_names[:3])))

        names = product_names[:8] or ["STEP 模型"]
        return self._build_import_analysis(
            parse_status="parsed_lite" if entity_count else "stored",
            parse_message=(
                f"已解析 STEP 基础信息：约 {entity_count} 个实体。"
                if entity_count
                else "STEP 文件已保存，但未识别到实体声明；可继续作为生成参考。"
            ),
            parse_features=features,
            preview_kind="step_pending_conversion",
            bom_items=[
                ForgeCadBomItem(name=name, material="未识别", quantity=1, size=None, source="import")
                for name in names
            ],
            explosion_steps=[
                ForgeCadExplosionStep(step=index + 1, name=name, offset=[index * 18, index * 7, index * 4], description="STEP 已识别产品名，三维预览需转换为 STL/glTF 后加载。")
                for index, name in enumerate(names)
            ],
        )

    def _attach_step_preview_conversion(
        self,
        *,
        db: Session,
        user_id: str,
        analysis: ImportAnalysis,
        source_asset_id: str,
        input_path: Path,
    ) -> ImportAnalysis:
        if not self.step_converter_command:
            analysis["conversion_status"] = "converter_not_configured"
            analysis["conversion_message"] = "未配置 STEP 转换命令，当前仅返回装配元数据。"
            return analysis

        preview_format = self.step_preview_format if self.step_preview_format in {"stl", "glb", "gltf"} else "stl"
        preview_path = input_path.parent / f"preview.{preview_format}"
        conversion_logs = self._convert_step_preview(
            input_path=input_path,
            preview_path=preview_path,
        )

        if not preview_path.exists():
            analysis["conversion_status"] = "converter_failed"
            analysis["conversion_message"] = conversion_logs or "STEP 转换命令已执行，但未生成预览文件。"
            return analysis

        preview_content_type = {
            "stl": "model/stl",
            "glb": "model/gltf-binary",
            "gltf": "model/gltf+json",
        }[preview_format]
        preview_asset = self.asset_service.store_bytes(
            db=db,
            user_id=user_id,
            filename=preview_path.name,
            content_type=preview_content_type,
            kind="preview",
            source="generated",
            content=preview_path.read_bytes(),
            metadata={
                "sourceAssetId": source_asset_id,
                "format": preview_format,
            },
        )
        analysis["preview_kind"] = "stl" if preview_format == "stl" else preview_format
        analysis["preview_asset_id"] = str(preview_asset.id)
        analysis["preview_asset_path"] = None
        analysis["preview_asset_format"] = preview_format
        analysis["preview_asset_url"] = (
            f"{settings.API_V1_PREFIX}/assets/{preview_asset.id}/download"
        )
        analysis["conversion_status"] = "converted"
        analysis["conversion_message"] = conversion_logs or f"STEP 已转换为 {preview_format.upper()} 预览文件。"
        if analysis["parse_message"]:
            analysis["parse_message"] = f"{analysis['parse_message']} 已生成 {preview_format.upper()} 预览文件。"
        return analysis

    def _convert_step_preview(self, *, input_path: Path, preview_path: Path) -> str:
        command = shlex.split(
            self.step_converter_command.format(
                input=str(input_path),
                output=str(preview_path),
                output_dir=str(preview_path.parent),
            )
        )
        if not command:
            return "STEP 转换命令为空。"
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                env=self._subprocess_environment(),
            )
        except subprocess.TimeoutExpired:
            return "STEP 转换超时。"

        logs = "\n".join(
            part for part in [completed.stdout.strip(), completed.stderr.strip()] if part
        ).strip()
        if completed.returncode != 0:
            return logs or f"STEP 转换失败，退出码 {completed.returncode}。"
        return logs

    def _extract_dxf_preview_entities(self, lines: list[str]) -> list[ForgeCadPreviewEntity]:
        entities: list[ForgeCadPreviewEntity] = []
        index = 0
        while index < len(lines) - 1:
            if lines[index] != "0":
                index += 2
                continue
            entity_type = lines[index + 1].upper()
            index += 2
            values: dict[str, list[float]] = {}
            while index < len(lines) - 1 and lines[index] != "0":
                code = lines[index]
                raw_value = lines[index + 1]
                if code in {"10", "20", "11", "21", "40", "50", "51"}:
                    try:
                        values.setdefault(code, []).append(float(raw_value))
                    except ValueError:
                        pass
                index += 2

            if entity_type == "LINE" and values.get("10") and values.get("20") and values.get("11") and values.get("21"):
                entities.append(
                    ForgeCadPreviewEntity(
                        entityType="LINE",
                        points=[[values["10"][0], values["20"][0]], [values["11"][0], values["21"][0]]],
                    )
                )
            elif entity_type == "CIRCLE" and values.get("10") and values.get("20") and values.get("40"):
                entities.append(
                    ForgeCadPreviewEntity(
                        entityType="CIRCLE",
                        center=[values["10"][0], values["20"][0]],
                        radius=values["40"][0],
                    )
                )
            elif entity_type == "ARC" and values.get("10") and values.get("20") and values.get("40"):
                start_angle = values.get("50", [0.0])[0]
                end_angle = values.get("51", [360.0])[0]
                entities.append(
                    ForgeCadPreviewEntity(
                        entityType="ARC",
                        center=[values["10"][0], values["20"][0]],
                        radius=values["40"][0],
                        startAngle=start_angle,
                        endAngle=end_angle,
                        points=self._build_arc_points(values["10"][0], values["20"][0], values["40"][0], start_angle, end_angle),
                    )
                )
            elif entity_type == "LWPOLYLINE" and values.get("10") and values.get("20"):
                points = [
                    [x_value, values["20"][point_index]]
                    for point_index, x_value in enumerate(values["10"][: len(values["20"])])
                ]
                if len(points) >= 2:
                    entities.append(ForgeCadPreviewEntity(entityType="LWPOLYLINE", points=points))

            if len(entities) >= 500:
                break
        return entities

    def _build_arc_points(
        self,
        center_x: float,
        center_y: float,
        radius: float,
        start_angle: float,
        end_angle: float,
    ) -> list[list[float]]:
        if end_angle < start_angle:
            end_angle += 360
        step_count = max(8, min(48, int(abs(end_angle - start_angle) / 8) + 1))
        points: list[list[float]] = []
        for index in range(step_count + 1):
            angle = start_angle + (end_angle - start_angle) * index / step_count
            radians = math.radians(angle)
            points.append([center_x + radius * math.cos(radians), center_y + radius * math.sin(radians)])
        return points

    def _format_bbox(self, vertices: list[tuple[float, float, float]]) -> str:
        xs = [vertex[0] for vertex in vertices]
        ys = [vertex[1] for vertex in vertices]
        zs = [vertex[2] for vertex in vertices]
        return (
            f"[{min(xs):.2f},{min(ys):.2f},{min(zs):.2f}] -> "
            f"[{max(xs):.2f},{max(ys):.2f},{max(zs):.2f}]"
        )

    def extract_script(self, raw_content: str) -> str:
        """从 Markdown 或纯文本中提取 ForgeCAD JavaScript。"""
        content = raw_content.strip()
        if not content:
            return ""

        fenced = re.search(r"```(?:javascript|js|typescript|ts)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip() + "\n"

        lines = [line for line in content.splitlines() if not line.strip().startswith(("// 文件", "以下是"))]
        return "\n".join(lines).strip() + "\n"

    async def _request_qwen(self, request: ForgeCadGenerateRequest) -> str:
        payload: dict[str, JsonValue] = {
            "model": self.qwen_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.qwen_api_key:
            headers["Authorization"] = f"Bearer {self.qwen_api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(
                    f"{self.qwen_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ForgeCadServiceError(
                f"调用 Qwen3 生成 ForgeCAD 脚本失败：{exc}",
                "QWEN_REQUEST_FAILED",
                status_code=502,
            ) from exc

        content = self._extract_qwen_content(data)
        if not content.strip():
            raise ForgeCadServiceError(
                "Qwen3 响应中没有可用内容",
                "QWEN_RESPONSE_EMPTY",
                status_code=502,
            )
        return content

    def _extract_qwen_content(self, data: object) -> str:
        if not isinstance(data, Mapping):
            return ""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, Mapping):
            return ""
        message = first.get("message")
        if not isinstance(message, Mapping):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    def _run_cli(self, script_path: Path, export_format: str) -> CliRunResult:
        if not self.sandbox_wrapper:
            raise ForgeCadServiceError(
                "ForgeCAD CLI 执行已禁用：未配置受控沙箱包装器",
                "FORGECAD_SANDBOX_REQUIRED",
                status_code=503,
            )
        cli_path = shutil.which(self.cli_binary)
        if not cli_path:
            raise ForgeCadServiceError(
                "未检测到 ForgeCAD CLI，请先安装：npm install -g forgecad，或设置 FORGECAD_CLI_BIN",
                "FORGECAD_CLI_NOT_FOUND",
                status_code=424,
                detail=ForgeCadErrorDetail(scriptPath=str(script_path)),
            )

        wrapper = shlex.split(self.sandbox_wrapper)
        if not wrapper:
            raise ForgeCadServiceError(
                "ForgeCAD 沙箱包装器配置无效",
                "FORGECAD_SANDBOX_INVALID",
                status_code=500,
            )
        command = [*wrapper, cli_path, "run", str(script_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=str(script_path.parent),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env=self._subprocess_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ForgeCadServiceError(
                "ForgeCAD CLI 执行超时",
                "FORGECAD_CLI_TIMEOUT",
                status_code=504,
                detail=ForgeCadErrorDetail(scriptPath=str(script_path)),
            ) from exc
        logs = "\n".join(
            part for part in [completed.stdout.strip(), completed.stderr.strip()] if part
        )
        if completed.returncode != 0:
            raise ForgeCadServiceError(
                "ForgeCAD CLI 执行失败",
                "FORGECAD_CLI_FAILED",
                status_code=422,
                detail=ForgeCadErrorDetail(
                    scriptPath=str(script_path),
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                ),
            )

        output_path = self._guess_output_path(script_path, export_format)
        return CliRunResult(logs=logs or "ForgeCAD CLI 执行成功。", output_path=output_path)

    @staticmethod
    def _subprocess_environment() -> dict[str, str]:
        """Only pass non-secret runtime variables to converter/sandbox processes."""
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        }

    def _guess_output_path(self, script_path: Path, export_format: str) -> str | None:
        if export_format == "none":
            return None
        candidates = sorted(script_path.parent.glob(f"*.{export_format}"))
        if not candidates:
            return None
        return str(candidates[0])

    def _system_prompt(self) -> str:
        return (
            "你是 ForgeCAD 参数化建模工程师。只输出可保存为 model.forge.js 的 JavaScript 代码，"
            "不要输出解释、Markdown 标题或额外文本。代码应使用 ForgeCAD 的 Param、box、cylinder、"
            "translate、subtract、union、color 等常见 API，返回对象形如 return { \"零件名\": shape };。"
            "所有尺寸默认单位为 mm，参数命名清晰，避免外部依赖。"
        )


forgecad_service = ForgeCadService()
