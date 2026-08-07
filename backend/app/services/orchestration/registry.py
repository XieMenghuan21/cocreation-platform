"""Agent executor registry."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from app.services.orchestration.contracts import (
    AgentExecutionContext,
    AgentExecutionResult,
    AgentType,
)


class AgentExecutor(Protocol):
    def execute(self, context: AgentExecutionContext) -> AgentExecutionResult:
        """Run one agent with a persisted execution context."""


class AgentRegistry:
    def __init__(self, executors: Mapping[AgentType, AgentExecutor]) -> None:
        self._executors = dict(executors)

    def get(self, agent_type: AgentType) -> AgentExecutor:
        executor = self._executors.get(agent_type)
        if executor is None:
            raise KeyError(f"agent executor not registered: {agent_type}")
        return executor
