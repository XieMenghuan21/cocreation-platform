"""Workspace Agent 模块。

Agent 只负责「Workspace 应该发生什么变化」，不决定产品下一步页面。
Orchestrator（workspace_turn_service）根据节点状态决定调用哪个 Agent。
"""
from app.services.agents.base import (
    AgentResult,
    NodeCreateCommand,
    NodeUpdateCommand,
)
from app.services.agents.cad_agent import CadAgentError, cad_agent
from app.services.agents.design_agent import DesignAgentError, design_agent
from app.services.agents.engineering_agent import (
    EngineeringAgentError,
    engineering_agent,
)
from app.services.agents.model_agent import ModelAgentError, model_agent
from app.services.agents.next_action_agent import next_action_agent
from app.services.agents.quote_agent import QuoteAgentError, quote_agent
from app.services.agents.render_agent import RenderAgentError, render_agent

__all__ = [
    "AgentResult",
    "CadAgentError",
    "DesignAgentError",
    "EngineeringAgentError",
    "ModelAgentError",
    "NodeCreateCommand",
    "NodeUpdateCommand",
    "QuoteAgentError",
    "RenderAgentError",
    "cad_agent",
    "design_agent",
    "engineering_agent",
    "model_agent",
    "next_action_agent",
    "quote_agent",
    "render_agent",
]