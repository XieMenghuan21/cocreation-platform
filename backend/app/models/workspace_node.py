"""Workspace Graph ORM 模型。

Conversation → WorkspaceNode → Artifact 的产品主干。
每个 WorkspaceNode 是一个结构化工作状态、决策或成果；聊天卡片、项目树、Preview 都是它的投影。
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkspaceNode(Base):
    """对话持续生成的结构化工作节点。"""

    __tablename__ = "workspace_nodes"
    __table_args__ = (
        Index("ix_workspace_nodes_conv_status", "conversation_id", "status"),
        Index("ix_workspace_nodes_project_created", "project_id", "created_at"),
        Index("ix_workspace_nodes_parent", "parent_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("workspace_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    branch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    node_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    agent_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    input_data: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    output_data: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    ui_data: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )

    assets: Mapped[list["WorkspaceNodeAsset"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkspaceNodeAsset.created_at",
    )


class WorkspaceNodeAsset(Base):
    """Node ↔ Asset 关联，role 标记用途（preview / render / cad / step / stl / glb / bom 等）。"""

    __tablename__ = "workspace_node_assets"
    __table_args__ = (
        UniqueConstraint(
            "node_id",
            "asset_id",
            "role",
            name="uq_workspace_node_assets_node_asset_role",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    node_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workspace_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(64), default="reference")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    node: Mapped[WorkspaceNode] = relationship(back_populates="assets")
