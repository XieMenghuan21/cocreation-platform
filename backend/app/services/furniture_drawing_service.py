"""家具工程图生成服务。"""
from __future__ import annotations

import html
import uuid
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.schemas.furniture_drawing import (
    FurnitureDrawingBomItem,
    FurnitureDrawingViewMeta,
    WardrobeDrawingRequest,
    WardrobeDrawingResult,
    WardrobeSectionInput,
)
from app.services.asset_blob_service import AssetBlobService


@dataclass(frozen=True)
class _Rect:
    x: float
    y: float
    width: float
    height: float


class FurnitureDrawingService:
    """根据多行业模板参数生成可预览的 SVG 工程图，支持导出 SVG/PDF/DXF。"""

    def __init__(self, asset_service: AssetBlobService | None = None) -> None:
        self.asset_service = asset_service or AssetBlobService(
            chunk_size=settings.ASSET_CHUNK_SIZE_BYTES
        )

    # ── 公共入口 ──────────────────────────────────────────────

    def render_wardrobe_drawing(self, request: WardrobeDrawingRequest) -> WardrobeDrawingResult:
        self._validate_sections(request)
        drawing_id = f"{request.template_type}_{uuid.uuid4().hex[:16]}"
        bom_items = self._build_bom_items(request)
        svg_content = self._build_svg(request, bom_items)
        total_sections = len(request.sections)
        summary = (
            f"已生成 {request.industry} · {self._template_name(request)} 工程图，"
            f"共 {total_sections} 个模块，输出正立面/侧立面/顶视图。"
        )
        return WardrobeDrawingResult(
            drawingId=drawing_id,
            svgContent=svg_content,
            summary=summary,
            views=[
                FurnitureDrawingViewMeta(key="front", title="正立面图", scale=self._compute_scale(request), description="用于查看整体分仓、门型和功能模块"),
                FurnitureDrawingViewMeta(key="side", title="侧立面图", scale=self._compute_scale(request), description="用于查看深度、上部储物和底部功能关系"),
                FurnitureDrawingViewMeta(key="top", title="顶视平面图", scale=self._compute_scale(request), description="用于查看深度、开门范围和模块投影"),
            ],
            bomItems=bom_items,
            generatedAt=datetime.now(timezone.utc).isoformat(),
        )

    def render_and_store(
        self,
        *,
        db: Session,
        user_id: str,
        request: WardrobeDrawingRequest,
        task_id: str | None = None,
        project_id: str | None = None,
        version_id: str | None = None,
        publish_assets: bool = True,
    ) -> WardrobeDrawingResult:
        """生成三种工程图格式，并在调用方事务内原子写入资产表。"""
        result = self.render_wardrobe_drawing(request)
        common = {
            "db": db,
            "user_id": user_id,
            "kind": "drawing",
            "source": "generated",
            "task_id": task_id,
            "project_id": project_id,
            "version_id": version_id,
            "metadata": {"drawingId": result.drawing_id},
            "publish": publish_assets,
        }
        svg_asset = self.asset_service.store_bytes(
            **common,
            filename=f"{result.drawing_id}.svg",
            content_type="image/svg+xml",
            content=result.svg_content.encode("utf-8"),
        )
        pdf_asset = self.asset_service.store_bytes(
            **common,
            filename=f"{result.drawing_id}.pdf",
            content_type="application/pdf",
            content=self._render_pdf_bytes(result.svg_content),
        )
        dxf_asset = self.asset_service.store_bytes(
            **common,
            filename=f"{result.drawing_id}.dxf",
            content_type="application/dxf",
            content=self._render_dxf_bytes(result.svg_content),
        )
        return result.model_copy(
            update={
                "svg_asset_id": str(svg_asset.id),
                "svg_url": self._asset_url(svg_asset.id),
                "pdf_asset_id": str(pdf_asset.id),
                "pdf_url": self._asset_url(pdf_asset.id),
                "dxf_asset_id": str(dxf_asset.id),
                "dxf_url": self._asset_url(dxf_asset.id),
            }
        )

    @staticmethod
    def _asset_url(asset_id: object) -> str:
        return f"{settings.API_V1_PREFIX}/assets/{asset_id}/download"

    def _render_pdf_bytes(self, svg_text: str) -> bytes:
        buffer = BytesIO()
        try:
            from reportlab.graphics import renderPDF
            from reportlab.lib.pagesizes import landscape, A4
            from reportlab.pdfgen import canvas as pdf_canvas

            del renderPDF
            page_w, page_h = landscape(A4)
            c = pdf_canvas.Canvas(buffer, pagesize=landscape(A4))
            c.setStrokeColorRGB(0.12, 0.21, 0.35)
            c.setLineWidth(0.5)
            c.rect(20, 20, page_w - 40, page_h - 40)
            c.setFont("Helvetica", 10)
            c.setFillColorRGB(0.1, 0.15, 0.25)
            c.drawString(30, page_h - 35, "Engineering Drawing - SVG source available separately")
            lines = self._extract_svg_lines(svg_text)
            scale_x = (page_w - 80) / 1500
            scale_y = (page_h - 80) / 1080
            scale = min(scale_x, scale_y)
            offset_x = 40
            offset_y = 40
            for x1, y1, x2, y2 in lines:
                c.line(
                    offset_x + x1 * scale,
                    page_h - offset_y - y1 * scale,
                    offset_x + x2 * scale,
                    page_h - offset_y - y2 * scale,
                )
            rects = self._extract_svg_rects(svg_text)
            for rx, ry, rw, rh in rects:
                c.rect(
                    offset_x + rx * scale,
                    page_h - offset_y - ry * scale - rh * scale,
                    rw * scale,
                    rh * scale,
                )
            c.showPage()
            c.save()
            return buffer.getvalue()
        except ImportError:
            objects = (
                b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
                (
                    b"3 0 obj<</Type/Page/MediaBox[0 0 842 595]"
                    b"/Parent 2 0 R/Resources<<>>>>endobj\n"
                ),
            )
            document = bytearray(b"%PDF-1.0\n")
            offsets: list[int] = []
            for item in objects:
                offsets.append(len(document))
                document.extend(item)
            xref_offset = len(document)
            document.extend(b"xref\n0 4\n0000000000 65535 f \n")
            for offset in offsets:
                document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
            document.extend(
                (
                    "trailer<</Size 4/Root 1 0 R>>\n"
                    f"startxref\n{xref_offset}\n%%EOF"
                ).encode("ascii")
            )
            return bytes(document)

    def _render_dxf_bytes(self, svg_text: str) -> bytes:
        lines = self._extract_svg_lines(svg_text)
        rects = self._extract_svg_rects(svg_text)
        parts: list[str] = []
        parts.append("0\nSECTION\n2\nHEADER\n0\nENDSEC")
        parts.append("0\nSECTION\n2\nTABLES\n0\nENDSEC")
        parts.append("0\nSECTION\n2\nBLOCKS\n0\nENDSEC")
        parts.append("0\nSECTION\n2\nENTITIES")
        for x1, y1, x2, y2 in lines:
            parts.append(
                f"0\nLINE\n8\ndrawing\n10\n{x1:.2f}\n20\n{-y1:.2f}\n30\n0\n11\n{x2:.2f}\n21\n{-y2:.2f}\n31\n0"
            )
        for rx, ry, rw, rh in rects:
            corners = [
                (rx, ry), (rx + rw, ry),
                (rx + rw, ry + rh), (rx, ry + rh),
                (rx, ry),
            ]
            for i in range(len(corners) - 1):
                x1, y1 = corners[i]
                x2, y2 = corners[i + 1]
                parts.append(
                    f"0\nLINE\n8\ndrawing\n10\n{x1:.2f}\n20\n{-y1:.2f}\n30\n0\n11\n{x2:.2f}\n21\n{-y2:.2f}\n31\n0"
                )
        parts.append("0\nENDSEC\n0\nEOF")
        return "\n".join(parts).encode("utf-8")

    @staticmethod
    def _extract_svg_lines(svg_text: str) -> list[tuple[float, float, float, float]]:
        import re
        results: list[tuple[float, float, float, float]] = []
        for m in re.finditer(
            r'<line[^>]*\bx1="([^"]*)"[^>]*\by1="([^"]*)"[^>]*\bx2="([^"]*)"[^>]*\by2="([^"]*)"',
            svg_text,
        ):
            try:
                results.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))))
            except ValueError:
                pass
        return results

    @staticmethod
    def _extract_svg_rects(svg_text: str) -> list[tuple[float, float, float, float]]:
        import re
        results: list[tuple[float, float, float, float]] = []
        for m in re.finditer(
            r'<rect[^>]*\bx="([^"]*)"[^>]*\by="([^"]*)"[^>]*\bwidth="([^"]*)"[^>]*\bheight="([^"]*)"',
            svg_text,
        ):
            try:
                results.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))))
            except ValueError:
                pass
        return results

    # ── 校验 ──────────────────────────────────────────────────

    def _validate_sections(self, request: WardrobeDrawingRequest) -> None:
        total_width = sum(section.width for section in request.sections)
        if total_width != request.width:
            raise ValueError(f"分区宽度合计为 {total_width}mm，与总宽 {request.width}mm 不一致")

    # ── 自适应比例 ────────────────────────────────────────────

    @staticmethod
    def _compute_scale(request: WardrobeDrawingRequest) -> str:
        max_dim = max(request.width, request.height)
        if max_dim <= 1500:
            return "1:10"
        if max_dim <= 3000:
            return "1:20"
        if max_dim <= 6000:
            return "1:50"
        return "1:100"

    def _auto_scales(self, request: WardrobeDrawingRequest) -> tuple[float, float]:
        scale_denom = float(self._compute_scale(request).split(":")[1])
        sheet_available_w = 1100
        sheet_available_h = 800
        w_scale = min(sheet_available_w / request.width, sheet_available_h / request.height)
        return w_scale, w_scale

    # ── BOM ───────────────────────────────────────────────────

    def _build_bom_items(self, request: WardrobeDrawingRequest) -> list[FurnitureDrawingBomItem]:
        material = request.material
        template_name = self._template_name(request)
        items: list[FurnitureDrawingBomItem] = [
            FurnitureDrawingBomItem(
                name=f"{template_name}侧板",
                material=material,
                quantity=2,
                size=f"{request.height} x {request.depth} mm",
                remark="左右侧板",
            ),
            FurnitureDrawingBomItem(
                name="顶板/底板",
                material=material,
                quantity=2,
                size=f"{request.width} x {request.depth} mm",
            ),
        ]
        for index, section in enumerate(request.sections, start=1):
            label = self._section_label(section)
            items.append(
                FurnitureDrawingBomItem(
                    name=f"{label}隔间",
                    material=material,
                    quantity=1,
                    size=f"{section.width} x {request.height} x {request.depth} mm",
                    remark=f"分区 {index:02d}",
                )
            )
            if section.drawer_count:
                items.append(
                    FurnitureDrawingBomItem(
                        name=f"{label}抽屉",
                        material=material,
                        quantity=section.drawer_count,
                        size=f"{max(section.width - 60, 200)} x 180 x {max(request.depth - 80, 220)} mm",
                    )
                )
            if section.shelf_count:
                items.append(
                    FurnitureDrawingBomItem(
                        name=f"{label}层板",
                        material=material,
                        quantity=section.shelf_count,
                        size=f"{max(section.width - 36, 180)} x {request.depth - 30} mm",
                    )
                )
        return items

    # ── SVG 构建 ──────────────────────────────────────────────

    def _build_svg(self, request: WardrobeDrawingRequest, bom_items: list[FurnitureDrawingBomItem]) -> str:
        width_scale, height_scale = self._auto_scales(request)
        sheet_width = 1500
        sheet_height = 1080
        front_rect = _Rect(70, 90, request.width * width_scale, request.height * height_scale)
        side_rect = _Rect(front_rect.x + front_rect.width + 120, 90, request.depth * width_scale, request.height * height_scale)
        top_rect = _Rect(70, front_rect.y + front_rect.height + 130, request.width * width_scale, request.depth * width_scale)
        bom_rect = _Rect(side_rect.x + side_rect.width + 70, 90, 250, 460)
        title_rect = _Rect(70, sheet_height - 180, sheet_width - 140, 120)

        renderer = self._get_template_renderer(request)
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {sheet_width} {sheet_height}" width="{sheet_width}" height="{sheet_height}">',
            '<defs><style><![CDATA['
            '.frame{fill:#fffdf8;stroke:#1f3559;stroke-width:1.2;}'
            '.main{fill:none;stroke:#223a5e;stroke-width:2.1;}'
            '.thin{fill:none;stroke:#36567e;stroke-width:1;}'
            '.dash{fill:none;stroke:#5f7a9c;stroke-width:1;stroke-dasharray:6 6;}'
            '.txt{fill:#1a2740;font-family:"PingFang SC","Microsoft YaHei",sans-serif;}'
            '.title{font-size:19px;font-weight:700;}'
            '.small{font-size:12px;}'
            '.label{font-size:14px;font-weight:600;}'
            '.dim{font-size:12px;}'
            '.chip{fill:#f2f6fb;stroke:#c3d2e6;stroke-width:1;}'
            ']]></style></defs>',
            f'<rect class="frame" x="10" y="10" width="{sheet_width - 20}" height="{sheet_height - 20}" rx="2" />',
            renderer.render_front(request, front_rect, width_scale, height_scale),
            self._render_side_view(request, side_rect, width_scale, height_scale),
            self._render_top_view(request, top_rect, width_scale),
            self._render_bom_block(request, bom_rect, bom_items),
            self._render_title_block(request, title_rect),
            self._render_sheet_header(request),
            '</svg>',
        ]
        return "".join(svg_parts)

    def _get_template_renderer(self, request: WardrobeDrawingRequest) -> "_TemplateRenderer":
        renderers: dict[str, type[_TemplateRenderer]] = {
            "wardrobe": _WardrobeRenderer,
            "bookcase": _BookcaseRenderer,
            "tv_cabinet": _TvCabinetRenderer,
        }
        renderer_cls = renderers.get(request.template_type, _GenericRenderer)
        return renderer_cls(self)

    # ── 图纸头 ────────────────────────────────────────────────

    def _render_sheet_header(self, request: WardrobeDrawingRequest) -> str:
        safe_project_name = html.escape(request.project_name)
        template_name = html.escape(self._template_name(request))
        scale = self._compute_scale(request)
        return (
            f'<text class="txt title" x="40" y="52">{safe_project_name}</text>'
            f'<text class="txt small" x="40" y="74">{template_name}工程图 · 正立面图 / 侧立面图 / 顶视平面图 · {scale}</text>'
        )

    # ── 衣柜正立面（默认渲染器也用这个）──────────────────────

    def _render_front_view(self, request: WardrobeDrawingRequest, rect: _Rect, width_scale: float, height_scale: float) -> str:
        parts = [
            f'<g><rect class="main" x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" />',
            f'<text class="txt label" x="{rect.x + rect.width / 2 - 46}" y="{rect.y + rect.height + 40}">① 正立面图</text>',
            f'<text class="txt small" x="{rect.x + rect.width / 2 - 14}" y="{rect.y + rect.height + 60}">{self._compute_scale(request)}</text>',
        ]
        section_x = rect.x
        for index, section in enumerate(request.sections):
            section_width = section.width * width_scale
            parts.append(f'<line class="thin" x1="{section_x}" y1="{rect.y}" x2="{section_x}" y2="{rect.y + rect.height}" />')
            renderer = self._get_template_renderer(request)
            parts.extend(renderer.render_section_front(section, section_x, rect.y, section_width, rect.height, index))
            parts.append(self._render_top_dimension(section_x, rect.y, section_width, str(section.width)))
            section_x += section_width
        parts.append(f'<line class="thin" x1="{rect.x + rect.width}" y1="{rect.y}" x2="{rect.x + rect.width}" y2="{rect.y + rect.height}" />')
        parts.append(self._render_total_dimension(rect.x, rect.y - 34, rect.width, str(request.width)))
        parts.append(self._render_side_dimension(rect.x - 34, rect.y, rect.height, str(request.height)))
        parts.append("</g>")
        return "".join(parts)

    def _render_section_front(self, section: WardrobeSectionInput, x: float, y: float, width: float, height: float, index: int) -> list[str]:
        label = html.escape(section.label or self._section_label(section))
        parts = [f'<text class="txt small" x="{x + width / 2 - 22}" y="{y + 28}">{label}</text>']
        if section.section_type == "mirror_door":
            parts.extend([
                f'<rect class="thin" x="{x + 8}" y="{y + 10}" width="{width - 16}" height="{height - 20}" rx="2" />',
                f'<line class="thin" x1="{x + 20}" y1="{y + 40}" x2="{x + width - 18}" y2="{y + height - 40}" />',
                f'<line class="thin" x1="{x + 30}" y1="{y + 70}" x2="{x + width - 26}" y2="{y + height - 120}" />',
            ])
            return parts

        top_storage_height = 400 if section.top_storage else 0
        if top_storage_height:
            y_split = y + top_storage_height * 0.28
            parts.append(f'<line class="thin" x1="{x}" y1="{y_split}" x2="{x + width}" y2="{y_split}" />')
            parts.append(f'<text class="txt small" x="{x + width / 2 - 26}" y="{y_split - 8}">顶部储物区</text>')
        if section.section_type in {"hanging", "hanging_shoe"}:
            usable_top = y + top_storage_height * 0.28 + 18
            usable_bottom = y + height - 60
            rod_y = usable_top + (usable_bottom - usable_top) * 0.22
            parts.append(f'<line class="main" x1="{x + 18}" y1="{rod_y}" x2="{x + width - 18}" y2="{rod_y}" />')
            for hanger_index in range(4):
                hanger_x = x + 36 + hanger_index * max((width - 72) / 4, 24)
                parts.extend([
                    f'<line class="thin" x1="{hanger_x}" y1="{rod_y}" x2="{hanger_x}" y2="{rod_y + 42}" />',
                    f'<path class="thin" d="M {hanger_x - 11} {rod_y + 56} Q {hanger_x} {rod_y + 36} {hanger_x + 11} {rod_y + 56}" />',
                    f'<line class="thin" x1="{hanger_x - 11}" y1="{rod_y + 56}" x2="{hanger_x + 11}" y2="{rod_y + 56}" />',
                ])
            if section.shoe_zone:
                shelf_y = y + height - 70
                parts.append(f'<line class="thin" x1="{x + 12}" y1="{shelf_y}" x2="{x + width - 12}" y2="{shelf_y}" />')
                for shoe_index in range(3):
                    shoe_x = x + 24 + shoe_index * max((width - 60) / 3, 20)
                    parts.append(f'<path class="thin" d="M {shoe_x} {shelf_y + 20} q 10 -16 26 0" />')
        if section.drawer_count:
            drawer_height = min(56, max(38, (height - top_storage_height * 0.28 - 40) / max(section.drawer_count + max(section.shelf_count, 1), 1)))
            current_y = y + height - 24
            for _ in range(section.drawer_count):
                current_y -= drawer_height
                parts.append(f'<rect class="thin" x="{x + 14}" y="{current_y}" width="{width - 28}" height="{drawer_height - 6}" />')
                parts.append(f'<line class="thin" x1="{x + width / 2 - 18}" y1="{current_y + drawer_height / 2}" x2="{x + width / 2 + 18}" y2="{current_y + drawer_height / 2}" />')
                current_y -= 8
        if section.shelf_count:
            shelf_top = y + 56
            spacing = max((height - 120) / (section.shelf_count + 1), 48)
            for shelf_index in range(section.shelf_count):
                shelf_y = shelf_top + spacing * shelf_index
                parts.append(f'<line class="thin" x1="{x + 14}" y1="{shelf_y}" x2="{x + width - 14}" y2="{shelf_y}" />')
        if section.section_type == "shelf":
            for shelf_index in range(4):
                shelf_y = y + 60 + shelf_index * max((height - 120) / 4, 46)
                parts.append(f'<line class="thin" x1="{x + 14}" y1="{shelf_y}" x2="{x + width - 14}" y2="{shelf_y}" />')
        if index == 0:
            parts.append(f'<text class="txt small" x="{x + 16}" y="{y + height - 16}">地脚 100</text>')
        return parts

    # ── 侧视图 ───────────────────────────────────────────────

    def _render_side_view(self, request: WardrobeDrawingRequest, rect: _Rect, width_scale: float, height_scale: float) -> str:
        top_storage_height = any(section.top_storage for section in request.sections)
        top_split = rect.y + 400 * height_scale if top_storage_height else rect.y
        shoe_split = rect.y + rect.height - 80
        rod_y = rect.y + 140
        return "".join([
            '<g>',
            f'<rect class="main" x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" />',
            f'<text class="txt label" x="{rect.x + rect.width / 2 - 40}" y="{rect.y + rect.height + 40}">② 侧立面图</text>',
            f'<text class="txt small" x="{rect.x + rect.width / 2 - 14}" y="{rect.y + rect.height + 60}">{self._compute_scale(request)}</text>',
            f'<line class="thin" x1="{rect.x}" y1="{top_split}" x2="{rect.x + rect.width}" y2="{top_split}" />' if top_storage_height else '',
            f'<line class="thin" x1="{rect.x}" y1="{shoe_split}" x2="{rect.x + rect.width}" y2="{shoe_split}" />',
            f'<line class="main" x1="{rect.x + 14}" y1="{rod_y}" x2="{rect.x + rect.width - 14}" y2="{rod_y}" />',
            f'<path class="thin" d="M {rect.x + rect.width / 2 - 12} {rod_y + 22} q 12 -18 24 0" />',
            self._render_total_dimension(rect.x, rect.y - 34, rect.width, str(request.depth)),
            self._render_side_dimension(rect.x + rect.width + 28, rect.y, rect.height, str(request.height)),
            '</g>',
        ])

    # ── 顶视图 ───────────────────────────────────────────────

    def _render_top_view(self, request: WardrobeDrawingRequest, rect: _Rect, width_scale: float) -> str:
        parts = [
            '<g>',
            f'<rect class="main" x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" />',
            f'<text class="txt label" x="{rect.x + rect.width / 2 - 52}" y="{rect.y + rect.height + 40}">③ 顶视平面图</text>',
            f'<text class="txt small" x="{rect.x + rect.width / 2 - 14}" y="{rect.y + rect.height + 60}">{self._compute_scale(request)}</text>',
        ]
        section_x = rect.x
        for section in request.sections:
            section_width = section.width * width_scale
            parts.append(f'<line class="thin" x1="{section_x}" y1="{rect.y}" x2="{section_x}" y2="{rect.y + rect.height}" />')
            label = html.escape(section.label or self._section_label(section))
            parts.append(f'<text class="txt small" x="{section_x + section_width / 2 - 24}" y="{rect.y - 10}">{label}</text>')
            section_x += section_width
        door_arc_x = rect.x + rect.width * 0.5
        parts.append(f'<path class="dash" d="M {door_arc_x - 80} {rect.y + rect.height + 10} A 80 80 0 0 1 {door_arc_x + 10} {rect.y + rect.height - 70}" />')
        parts.append(self._render_total_dimension(rect.x, rect.y - 34, rect.width, str(request.width)))
        parts.append(self._render_side_dimension(rect.x + rect.width + 28, rect.y, rect.height, str(request.depth)))
        parts.append('</g>')
        return "".join(parts)

    # ── BOM 块 ────────────────────────────────────────────────

    def _render_bom_block(
        self,
        request: WardrobeDrawingRequest,
        rect: _Rect,
        bom_items: list[FurnitureDrawingBomItem],
    ) -> str:
        parts = [
            '<g>',
            f'<rect class="main" x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" />',
            f'<text class="txt label" x="{rect.x + 16}" y="{rect.y + 28}">五金及材质说明</text>',
            f'<rect class="chip" x="{rect.x + 14}" y="{rect.y + 44}" width="{rect.width - 28}" height="72" />',
            f'<text class="txt small" x="{rect.x + 24}" y="{rect.y + 68}">门型：{html.escape(request.door_type)}</text>',
            f'<text class="txt small" x="{rect.x + 24}" y="{rect.y + 88}">材质：{html.escape(request.material)}</text>',
            f'<text class="txt small" x="{rect.x + 24}" y="{rect.y + 108}">单位：mm</text>',
        ]
        current_y = rect.y + 144
        for item in bom_items[:8]:
            parts.append(f'<line class="thin" x1="{rect.x}" y1="{current_y - 10}" x2="{rect.x + rect.width}" y2="{current_y - 10}" />')
            parts.append(f'<text class="txt small" x="{rect.x + 14}" y="{current_y}">{html.escape(item.name)}</text>')
            parts.append(f'<text class="txt small" x="{rect.x + 14}" y="{current_y + 18}">{html.escape(item.size)}</text>')
            parts.append(f'<text class="txt small" x="{rect.x + rect.width - 42}" y="{current_y + 18}">x{item.quantity}</text>')
            current_y += 42
        parts.append('</g>')
        return "".join(parts)

    # ── 标题栏 ────────────────────────────────────────────────

    def _render_title_block(self, request: WardrobeDrawingRequest, rect: _Rect) -> str:
        now_text = datetime.now().strftime("%Y.%m.%d")
        template_name = self._template_name(request)
        scale = self._compute_scale(request)
        rows = [
            ("项目名称", request.project_name),
            ("图纸名称", f"{template_name}工程图"),
            ("比例", scale),
            ("日期", now_text),
            ("单位", "mm"),
        ]
        row_height = rect.height / len(rows)
        parts = [f'<g><rect class="main" x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" />']
        split_x = rect.x + 150
        parts.append(f'<line class="thin" x1="{split_x}" y1="{rect.y}" x2="{split_x}" y2="{rect.y + rect.height}" />')
        for index, (label, value) in enumerate(rows):
            y = rect.y + row_height * index
            if index:
                parts.append(f'<line class="thin" x1="{rect.x}" y1="{y}" x2="{rect.x + rect.width}" y2="{y}" />')
            parts.append(f'<text class="txt small" x="{rect.x + 16}" y="{y + 24}">{html.escape(label)}</text>')
            parts.append(f'<text class="txt small" x="{split_x + 16}" y="{y + 24}">{html.escape(value)}</text>')
        parts.append('</g>')
        return "".join(parts)

    # ── 尺寸标注（带箭头）─────────────────────────────────────

    @staticmethod
    def _arrowhead_left(x: float, y: float, size: float = 5) -> str:
        return f'<polygon points="{x},{y} {x + size},{y - size * 0.5} {x + size},{y + size * 0.5}" fill="#36567e" />'

    @staticmethod
    def _arrowhead_right(x: float, y: float, size: float = 5) -> str:
        return f'<polygon points="{x},{y} {x - size},{y - size * 0.5} {x - size},{y + size * 0.5}" fill="#36567e" />'

    @staticmethod
    def _arrowhead_up(x: float, y: float, size: float = 5) -> str:
        return f'<polygon points="{x},{y} {x - size * 0.5},{y + size} {x + size * 0.5},{y + size}" fill="#36567e" />'

    @staticmethod
    def _arrowhead_down(x: float, y: float, size: float = 5) -> str:
        return f'<polygon points="{x},{y} {x - size * 0.5},{y - size} {x + size * 0.5},{y - size}" fill="#36567e" />'

    def _render_total_dimension(self, x: float, y: float, width: float, value: str) -> str:
        return (
            f'<g>'
            f'<line class="thin" x1="{x}" y1="{y}" x2="{x + width}" y2="{y}" />'
            f'<line class="thin" x1="{x}" y1="{y - 8}" x2="{x}" y2="{y + 8}" />'
            f'<line class="thin" x1="{x + width}" y1="{y - 8}" x2="{x + width}" y2="{y + 8}" />'
            f'{self._arrowhead_left(x, y)}'
            f'{self._arrowhead_right(x + width, y)}'
            f'<text class="txt dim" x="{x + width / 2 - 14}" y="{y - 10}">{html.escape(value)}</text></g>'
        )

    def _render_top_dimension(self, x: float, y: float, width: float, value: str) -> str:
        dim_y = y - 18
        return (
            f'<g>'
            f'<line class="thin" x1="{x}" y1="{dim_y}" x2="{x + width}" y2="{dim_y}" />'
            f'<line class="thin" x1="{x}" y1="{y - 26}" x2="{x}" y2="{y - 8}" />'
            f'<line class="thin" x1="{x + width}" y1="{y - 26}" x2="{x + width}" y2="{y - 8}" />'
            f'{self._arrowhead_left(x, dim_y)}'
            f'{self._arrowhead_right(x + width, dim_y)}'
            f'<text class="txt dim" x="{x + width / 2 - 12}" y="{dim_y - 8}">{html.escape(value)}</text></g>'
        )

    def _render_side_dimension(self, x: float, y: float, height: float, value: str) -> str:
        return (
            f'<g>'
            f'<line class="thin" x1="{x}" y1="{y}" x2="{x}" y2="{y + height}" />'
            f'<line class="thin" x1="{x - 8}" y1="{y}" x2="{x + 8}" y2="{y}" />'
            f'<line class="thin" x1="{x - 8}" y1="{y + height}" x2="{x + 8}" y2="{y + height}" />'
            f'{self._arrowhead_up(x, y)}'
            f'{self._arrowhead_down(x, y + height)}'
            f'<text class="txt dim" transform="translate({x + 18} {y + height / 2}) rotate(90)">{html.escape(value)}</text></g>'
        )

    # ── 辅助 ──────────────────────────────────────────────────

    @staticmethod
    def _section_label(section: WardrobeSectionInput) -> str:
        labels = {
            "hanging": "挂衣区",
            "drawer_shelf": "抽屉区",
            "mirror_door": "镜面门",
            "hanging_shoe": "挂衣鞋柜区",
            "shelf": "层板区",
        }
        return labels.get(section.section_type, "功能区")

    @staticmethod
    def _template_name(request: WardrobeDrawingRequest) -> str:
        template_names = {
            "wardrobe": "衣柜",
            "bookcase": "书柜",
            "tv_cabinet": "电视柜",
            "mounting_plate": "安装板",
            "control_cabinet": "控制机柜",
            "support_bracket": "支架",
            "medical_cart": "医疗推车",
            "device_shell": "设备外壳",
        }
        return template_names.get(request.template_type, "工业产品")


# ── 模板渲染器（策略模式）─────────────────────────────────────

class _TemplateRenderer:
    """模板渲染器基类。"""

    def __init__(self, service: FurnitureDrawingService) -> None:
        self.svc = service

    def render_front(self, request: WardrobeDrawingRequest, rect: _Rect, width_scale: float, height_scale: float) -> str:
        return self.svc._render_front_view(request, rect, width_scale, height_scale)

    def render_section_front(self, section: WardrobeSectionInput, x: float, y: float, width: float, height: float, index: int) -> list[str]:
        return self.svc._render_section_front(section, x, y, width, height, index)


class _WardrobeRenderer(_TemplateRenderer):
    pass


class _BookcaseRenderer(_TemplateRenderer):
    """书柜模板：对称分格、层板、玻璃门。"""

    def render_section_front(self, section: WardrobeSectionInput, x: float, y: float, width: float, height: float, index: int) -> list[str]:
        label = html.escape(section.label or self.svc._section_label(section))
        parts: list[str] = [f'<text class="txt small" x="{x + width / 2 - 22}" y="{y + 28}">{label}</text>']
        col_count = max(2, min(4, round(width / 200)))
        col_w = width / col_count
        shelf_count = section.shelf_count or 4
        shelf_spacing = max((height - 60) / (shelf_count + 1), 30)
        for col in range(col_count):
            cx = x + col * col_w
            if col > 0:
                parts.append(f'<line class="thin" x1="{cx}" y1="{y + 10}" x2="{cx}" y2="{y + height - 10}" />')
            for s in range(shelf_count):
                sy = y + 50 + s * shelf_spacing
                if sy < y + height - 20:
                    parts.append(f'<line class="thin" x1="{cx + 8}" y1="{sy}" x2="{cx + col_w - 8}" y2="{sy}" />')
        parts.append(f'<rect class="thin" x="{x + 4}" y="{y + 4}" width="{width - 8}" height="{height - 8}" rx="1" />')
        parts.append(f'<text class="txt small" x="{x + width / 2 - 16}" y="{y + height - 16}">玻璃门</text>')
        return parts


class _TvCabinetRenderer(_TemplateRenderer):
    """电视柜模板：地台、开放格、抽屉、背板孔位。"""

    def render_section_front(self, section: WardrobeSectionInput, x: float, y: float, width: float, height: float, index: int) -> list[str]:
        label = html.escape(section.label or self.svc._section_label(section))
        parts: list[str] = [f'<text class="txt small" x="{x + width / 2 - 22}" y="{y + 28}">{label}</text>']
        base_h = min(60, height * 0.15)
        parts.append(f'<rect class="main" x="{x + 4}" y="{y + height - base_h}" width="{width - 8}" height="{base_h - 4}" />')
        parts.append(f'<text class="txt small" x="{x + width / 2 - 16}" y="{y + height - base_h / 2 + 4}">地台</text>')
        mid_y = y + height * 0.3
        parts.append(f'<rect class="thin" x="{x + 8}" y="{y + 12}" width="{width - 16}" height="{mid_y - y - 16}" rx="2" />')
        parts.append(f'<text class="txt small" x="{x + width / 2 - 20}" y="{(y + mid_y) / 2 + 4}">开放格</text>')
        drawer_count = section.drawer_count or 2
        drawer_h = min(40, (y + height - base_h - mid_y - 10) / drawer_count)
        dy = mid_y + 6
        for _ in range(drawer_count):
            parts.append(f'<rect class="thin" x="{x + 12}" y="{dy}" width="{width - 24}" height="{drawer_h - 4}" />')
            parts.append(f'<line class="thin" x1="{x + width / 2 - 14}" y1="{dy + drawer_h / 2}" x2="{x + width / 2 + 14}" y2="{dy + drawer_h / 2}" />')
            dy += drawer_h + 2
        for hole_idx in range(3):
            hx = x + 20 + hole_idx * (width - 40) / 2
            parts.append(f'<circle cx="{hx}" cy="{y + height - base_h - 8}" r="3" class="thin" />')
        return parts


class _GenericRenderer(_TemplateRenderer):
    """通用工业产品模板：简化矩形 + 标注。"""

    def render_front(self, request: WardrobeDrawingRequest, rect: _Rect, width_scale: float, height_scale: float) -> str:
        parts = [
            f'<g><rect class="main" x="{rect.x}" y="{rect.y}" width="{rect.width}" height="{rect.height}" />',
            f'<text class="txt label" x="{rect.x + rect.width / 2 - 46}" y="{rect.y + rect.height + 40}">① 正立面图</text>',
            f'<text class="txt small" x="{rect.x + rect.width / 2 - 14}" y="{rect.y + rect.height + 60}">{self.svc._compute_scale(request)}</text>',
        ]
        cx = rect.x + rect.width / 2
        cy = rect.y + rect.height / 2
        parts.append(f'<line class="dash" x1="{cx}" y1="{rect.y}" x2="{cx}" y2="{rect.y + rect.height}" />')
        parts.append(f'<line class="dash" x1="{rect.x}" y1="{cy}" x2="{rect.x + rect.width}" y2="{cy}" />')
        parts.append(self.svc._render_total_dimension(rect.x, rect.y - 34, rect.width, str(request.width)))
        parts.append(self.svc._render_side_dimension(rect.x - 34, rect.y, rect.height, str(request.height)))
        parts.append("</g>")
        return "".join(parts)


furniture_drawing_service = FurnitureDrawingService()
