"""资产接口 Schema。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def to_camel(field_name: str) -> str:
    first, *rest = field_name.split("_")
    return first + "".join(word.capitalize() for word in rest)


class AssetResponse(BaseModel):
    """持久化资产响应。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
    )

    id: UUID
    user_id: str
    project_id: str | None
    version_id: str | None
    source_version_id: str | None = None
    task_id: str | None
    kind: str
    filename: str
    extension: str | None
    content_type: str
    size_bytes: int
    sha256: str
    chunk_size: int
    chunk_count: int
    status: str
    source: str
    metadata: dict[str, object] = Field(validation_alias="asset_metadata")
    created_at: datetime
    updated_at: datetime


class AssetListResponse(BaseModel):
    """资产分页列表响应。"""

    items: list[AssetResponse] = Field(default_factory=list)
    total: int
