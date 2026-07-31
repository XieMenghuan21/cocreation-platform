"""家具工程图出图 Schema。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WardrobeSectionType = Literal["hanging", "drawer_shelf", "mirror_door", "hanging_shoe", "shelf"]
IndustryCategory = Literal["家居智造", "装备制造", "医疗器械", "汽车零部件"]
DrawingTemplateType = Literal["wardrobe", "bookcase", "tv_cabinet", "mounting_plate", "control_cabinet", "support_bracket", "medical_cart", "device_shell"]


class WardrobeSectionInput(BaseModel):
    """衣柜分区定义。"""

    section_type: WardrobeSectionType = Field(alias="sectionType")
    width: int = Field(..., ge=200, le=2400)
    upper_height: int | None = Field(default=None, alias="upperHeight", ge=100, le=2200)
    lower_height: int | None = Field(default=None, alias="lowerHeight", ge=100, le=2200)
    drawer_count: int = Field(default=0, alias="drawerCount", ge=0, le=8)
    shelf_count: int = Field(default=0, alias="shelfCount", ge=0, le=12)
    top_storage: bool = Field(default=False, alias="topStorage")
    shoe_zone: bool = Field(default=False, alias="shoeZone")
    label: str | None = Field(default=None, max_length=60)


class WardrobeDrawingRequest(BaseModel):
    """多行业工程图生成请求。"""

    model_config = ConfigDict(populate_by_name=True)

    industry: IndustryCategory = Field(default="家居智造")
    template_type: DrawingTemplateType = Field(default="wardrobe", alias="templateType")
    project_name: str = Field(alias="projectName", min_length=2, max_length=120)
    width: int = Field(..., ge=1200, le=8000)
    height: int = Field(..., ge=1800, le=4000)
    depth: int = Field(..., ge=300, le=1200)
    door_type: str = Field(default="sliding_mixed", alias="doorType", max_length=60)
    material: str = Field(default="柜体板材 18mm", max_length=120)
    sections: list[WardrobeSectionInput] = Field(..., alias="modules", min_length=1, max_length=12)


class FurnitureDrawingBomItem(BaseModel):
    """工程图输出的 BOM 项。"""

    name: str
    material: str
    quantity: int
    size: str
    remark: str | None = None


class FurnitureDrawingViewMeta(BaseModel):
    """图纸视图元数据。"""

    key: str
    title: str
    scale: str
    description: str


class WardrobeDrawingResult(BaseModel):
    """衣柜工程图生成结果。"""

    model_config = ConfigDict(populate_by_name=True)

    drawing_id: str = Field(alias="drawingId")
    svg_content: str = Field(alias="svgContent")
    svg_asset_id: str | None = Field(default=None, alias="svgAssetId")
    svg_url: str | None = Field(default=None, alias="svgUrl")
    pdf_asset_id: str | None = Field(default=None, alias="pdfAssetId")
    pdf_url: str | None = Field(default=None, alias="pdfUrl")
    dxf_asset_id: str | None = Field(default=None, alias="dxfAssetId")
    dxf_url: str | None = Field(default=None, alias="dxfUrl")
    summary: str
    views: list[FurnitureDrawingViewMeta]
    bom_items: list[FurnitureDrawingBomItem] = Field(alias="bomItems")
    generated_at: str = Field(alias="generatedAt")
