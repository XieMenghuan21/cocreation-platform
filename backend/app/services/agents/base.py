"""Agent 统一协议。

核心思想：Agent 输出的不是「页面怎么显示」，而是「Workspace 应该发生什么变化」。
第一阶段的 Agent 只是 Python service class，不拆微服务。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class NodeCreateCommand:
    node_type: str
    status: str
    title: str
    summary: str = ""
    parent_id: UUID | None = None
    project_id: str | None = None
    agent_key: str | None = None
    input_data: dict[str, object] = field(default_factory=dict)
    output_data: dict[str, object] = field(default_factory=dict)
    ui_data: dict[str, object] = field(default_factory=dict)


@dataclass
class NodeUpdateCommand:
    node_id: UUID
    status: str | None = None
    title: str | None = None
    summary: str | None = None
    task_id: str | None = None
    version_id: str | None = None
    output_data: dict[str, object] | None = None
    ui_data: dict[str, object] | None = None


@dataclass
class AgentResult:
    assistant_text: str
    create_nodes: list[NodeCreateCommand] = field(default_factory=list)
    update_nodes: list[NodeUpdateCommand] = field(default_factory=list)
    next_actions: list[dict[str, object]] = field(default_factory=list)
    requires_user_input: bool = False
    active_node_id: UUID | None = None
    preview_node_id: UUID | None = None