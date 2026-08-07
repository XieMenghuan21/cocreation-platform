"""Conversation-created workflow orchestration ORM models."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowInstance(Base):
    """One persisted workspace workflow for one project."""

    __tablename__ = "workflow_instances"
    __table_args__ = (
        Index("ix_workflow_instances_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    output_snapshot: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentRun.created_at",
    )


class AgentRun(Base):
    """One persisted execution record for one backend agent."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_workflow_created", "workflow_id", "created_at"),
        Index("ix_agent_runs_project_created", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    conversation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    agent_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    output_snapshot: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )

    workflow: Mapped[WorkflowInstance] = relationship(back_populates="agent_runs")
    events: Mapped[list["AgentRunEvent"]] = relationship(
        back_populates="agent_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentRunEvent.sequence",
    )
    artifacts: Mapped[list["AgentArtifactLink"]] = relationship(
        back_populates="agent_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AgentRunEvent(Base):
    """Append-only status/progress event for an agent run."""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        Index("ix_agent_run_events_agent_sequence", "agent_run_id", "sequence"),
        Index("ix_agent_run_events_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    event_data: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent_run: Mapped[AgentRun] = relationship(back_populates="events")


class AgentArtifactLink(Base):
    """Relation between an agent run and a persisted database asset."""

    __tablename__ = "agent_artifact_links"
    __table_args__ = (
        Index("ix_agent_artifact_links_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(120), index=True)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), default="output")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent_run: Mapped[AgentRun] = relationship(back_populates="artifacts")
