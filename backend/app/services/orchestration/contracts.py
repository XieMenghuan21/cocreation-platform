"""Typed contracts shared by orchestration runtime and agent executors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias
from uuid import UUID

from sqlalchemy.orm import Session

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

AgentType: TypeAlias = Literal[
    "requirement",
    "project",
    "design",
    "render",
    "three_d",
    "cad",
    "quote",
    "engineering_package",
]
AgentRunStatus: TypeAlias = Literal[
    "queued",
    "running",
    "waiting_user",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
]


@dataclass(frozen=True)
class AgentExecutionContext:
    db: Session
    workflow_id: UUID
    project_id: str
    conversation_id: UUID | None
    user_id: str
    input_snapshot: dict[str, JsonValue]


@dataclass(frozen=True)
class AgentExecutionResult:
    status: AgentRunStatus
    output_snapshot: dict[str, JsonValue]
    artifact_ids: tuple[str, ...] = ()
    next_agents: tuple[AgentType, ...] = ()
    message: str | None = None
