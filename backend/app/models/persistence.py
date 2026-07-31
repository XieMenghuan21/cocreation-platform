"""数据库持久化 ORM 模型。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


sqlite_compatible_bigint = BigInteger().with_variant(Integer, "sqlite")

# MutableDict/MutableList 只跟踪顶层原地修改；嵌套对象由仓储层整值替换。


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_metadata: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )


class SsoAuthorizationState(Base):
    __tablename__ = "sso_authorization_states"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    browser_binding_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceState(Base):
    __tablename__ = "workspace_states"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    selected_project_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    selected_reference_version_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    selected_reference_version_history_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "cocreation_project_version_histories.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    selected_reference_asset_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    active_scenario: Mapped[str] = mapped_column(String(120), default="")
    active_workflow_stage: Mapped[str] = mapped_column(String(120), default="")
    view_mode: Mapped[str] = mapped_column(String(64), default="")
    scene_mode: Mapped[str] = mapped_column(String(64), default="")
    selected_industry: Mapped[str] = mapped_column(String(120), default="")
    generation_prompt: Mapped[str] = mapped_column(Text, default="")
    active_step_index: Mapped[int] = mapped_column(Integer, default=0)
    state_data: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str] = mapped_column(String(120), default="")
    input_payload: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    design_spec: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    outputs: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    diagnostics: Mapped[list[dict[str, object]]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    recoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    history_persisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )

    events: Mapped[list["WorkflowTaskEvent"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkflowTaskEvent.sequence",
    )
    assets: Mapped[list["Asset"]] = relationship(back_populates="task")


class WorkflowTaskEvent(Base):
    __tablename__ = "workflow_task_events"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "sequence",
            name="uq_workflow_task_events_task_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(
        sqlite_compatible_bigint,
        primary_key=True,
        autoincrement=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    event_data: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[WorkflowTask] = relationship(back_populates="events")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        Index("ix_assets_user_created_id", "user_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(120))
    project_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    task_id: Mapped[str | None] = mapped_column(
        String(160),
        ForeignKey("workflow_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content_type: Mapped[str] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    chunk_size: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64))
    asset_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )

    task: Mapped[WorkflowTask | None] = relationship(back_populates="assets")
    chunks: Mapped[list["AssetBlobChunk"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssetBlobChunk.chunk_index",
    )


class AssetBlobChunk(Base):
    __tablename__ = "asset_blob_chunks"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "chunk_index",
            name="uq_asset_blob_chunks_asset_chunk",
        ),
    )

    id: Mapped[int] = mapped_column(
        sqlite_compatible_bigint,
        primary_key=True,
        autoincrement=True,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="CASCADE"),
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[Asset] = relationship(back_populates="chunks")
