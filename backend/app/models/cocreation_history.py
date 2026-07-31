"""共创项目历史 ORM 模型。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CocreationProjectHistory(Base):
    __tablename__ = "cocreation_project_histories"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_cocreation_project_histories_user_project"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(160), index=True)
    project_name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_task_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_image_asset_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    versions: Mapped[list["CocreationProjectVersionHistory"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="desc(CocreationProjectVersionHistory.created_at)",
    )


class CocreationProjectVersionHistory(Base):
    __tablename__ = "cocreation_project_version_histories"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "project_history_id",
            "version_id",
            name="uq_cocreation_project_version_histories_user_project_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_history_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cocreation_project_histories.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    version_id: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    source_project_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    optimized_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_image_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    change_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_object: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    script_asset_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    output_asset_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cli_executed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    export_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_objects: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    parameters: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    generated_assets: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    diagnostics: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    snapshot_data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped[CocreationProjectHistory] = relationship(back_populates="versions")
    library_entries: Mapped[list["CocreationAssetLibraryEntry"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    asset_entries: Mapped[list["CocreationVersionAssetEntry"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CocreationVersionAssetEntry(Base):
    """项目版本与数据库资产的规范化关系。"""

    __tablename__ = "cocreation_version_asset_entries"
    __table_args__ = (
        UniqueConstraint(
            "version_history_id",
            "asset_id",
            "role",
            name="uq_cocreation_version_asset_entries_version_asset_role",
        ),
        Index(
            "ix_cocreation_version_asset_entries_user_version",
            "user_id",
            "version_history_id",
        ),
        Index(
            "ix_cocreation_version_asset_entries_asset",
            "asset_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    version_history_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cocreation_project_version_histories.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    version: Mapped[CocreationProjectVersionHistory] = relationship(
        back_populates="asset_entries"
    )


class CocreationAssetLibraryEntry(Base):
    """发布版本与数据库资产之间的可查询关系。"""

    __tablename__ = "cocreation_asset_library_entries"
    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_cocreation_asset_library_entries_asset"),
        Index(
            "ix_cocreation_asset_library_entries_user_version",
            "user_id",
            "version_history_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    version_history_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cocreation_project_version_histories.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    version: Mapped[CocreationProjectVersionHistory] = relationship(
        back_populates="library_entries"
    )
