"""工业品设计统一工作流 Schema。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.json_shape import validate_json_shape


IndustrialDesignInputType = Literal["text", "voice", "drawing", "cad", "image", "pdf"]


class IndustrialDesignAssetMeta(BaseModel):
    """用户输入资产元信息。"""

    model_config = ConfigDict(populate_by_name=True)

    asset_id: str = Field(alias="assetId", max_length=160)
    filename: str = Field(max_length=255)
    extension: str = Field(max_length=32)
    content_type: str = Field(default="application/octet-stream", alias="contentType", max_length=160)
    size_bytes: int = Field(default=0, alias="sizeBytes")
    parse_status: str = Field(default="stored", alias="parseStatus", max_length=64)
    parse_message: str = Field(default="", alias="parseMessage", max_length=1000)
    preview_asset_url: str | None = Field(default=None, alias="previewAssetUrl", max_length=2048)


class IndustrialDesignWorkflowOptions(BaseModel):
    """统一工作流生成选项。"""

    model_config = ConfigDict(populate_by_name=True)

    generate_cad: bool = Field(default=True, alias="generateCad")
    generate_drawing: bool = Field(default=True, alias="generateDrawing")
    generate_three_preview: bool = Field(default=True, alias="generateThreePreview")
    generate_render: bool = Field(default=True, alias="generateRender")
    generate_explosion: bool = Field(default=True, alias="generateExplosion")
    enhance_image: bool = Field(default=True, alias="enhanceImage")
    generate_trellis_asset: bool = Field(default=False, alias="generateTrellisAsset")
    optimize_prompt: bool = Field(default=True, alias="optimizePrompt")
    image_model: str | None = Field(default=None, alias="imageModel")
    image_provider: str | None = Field(default=None, alias="imageProvider")


class IndustrialDesignWorkflowRequest(BaseModel):
    """工业品设计统一工作流请求。"""

    model_config = ConfigDict(populate_by_name=True)

    input_type: IndustrialDesignInputType = Field(alias="inputType")
    text: str | None = Field(default=None, max_length=12000)
    asset_ids: list[str] = Field(default_factory=list, alias="assetIds", max_length=20)
    asset_urls: list[str] = Field(default_factory=list, alias="assetUrls", max_length=20)
    asset_metas: list[IndustrialDesignAssetMeta] = Field(default_factory=list, alias="assetMetas", max_length=20)
    project_name: str | None = Field(default=None, alias="projectName", max_length=120)
    industry: str | None = Field(default=None, max_length=80)
    mode: Literal["create", "redesign"] = "create"
    options: IndustrialDesignWorkflowOptions = Field(default_factory=IndustrialDesignWorkflowOptions)
    context: dict[str, object] = Field(default_factory=dict, max_length=200)

    @field_validator("asset_ids", "asset_urls")
    @classmethod
    def validate_string_lists(cls, value: list[str]) -> list[str]:
        if any(len(item) > 2048 for item in value):
            raise ValueError("资产标识或 URL 长度超过限制")
        return value

    @field_validator("context")
    @classmethod
    def validate_context_shape(
        cls,
        value: dict[str, object],
    ) -> dict[str, object]:
        validate_json_shape(value)
        return value


class IndustrialDesignImageEditRequest(BaseModel):
    """工业设计图片精修请求。"""

    model_config = ConfigDict(populate_by_name=True)

    prompt: str = Field(min_length=1, max_length=4000)
    image_paths: list[str] = Field(alias="imagePaths", min_length=1, max_length=6)
    mask_path: str | None = Field(default=None, alias="maskPath", max_length=1000)
    size: str = Field(default="1536x1024", max_length=32)
    quality: Literal["low", "medium", "high"] = "medium"
    output_format: Literal["png", "jpeg", "webp"] = Field(default="png", alias="outputFormat")
    input_fidelity: str | None = Field(default="high", alias="inputFidelity", max_length=32)
