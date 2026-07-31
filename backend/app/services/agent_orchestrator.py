"""工业设计总师 Agent：基于 LangGraph 的编排层，复用现有 build123d/工程包/审查/知识库服务。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, TypedDict, cast

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖可选
    LANGGRAPH_AVAILABLE = False

from app.config.settings import settings
from app.services.knowledge_base_service import knowledge_base_service

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class AgentOrchestratorError(Exception):
    """总师编排器可预期错误。"""

    def __init__(self, message: str, error_code: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code


class AgentState(TypedDict):
    """跨节点共享状态。"""

    request: dict[str, object]
    project_name: str
    industry: str
    requirement: str
    knowledge_context: str
    plan_line: dict[str, object] | None
    cad_model: dict[str, object] | None
    review: dict[str, object] | None
    package: dict[str, object] | None
    diagnostics: list[dict[str, str]]
    errors: list[str]
    db: object
    user_id: str


def _node_understand_requirement(state: AgentState) -> dict[str, object]:
    """需求理解 Agent：提炼项目名/行业/需求文本，检索知识库上下文。"""
    request = state["request"]
    text = str(request.get("text") or "").strip()
    industry = str(request.get("industry") or "装备制造")
    project_name = str(request.get("projectName") or "工业品智能设计项目")
    requirement = text

    knowledge_context = ""
    if knowledge_base_service.enabled:
        try:
            knowledge_context = knowledge_base_service.build_context(
                requirement or industry,
                top_k=3,
            )
        except Exception as exc:
            logger.debug("知识库检索跳过: %s", exc)
    return {
        "project_name": project_name,
        "industry": industry,
        "requirement": requirement,
        "knowledge_context": knowledge_context,
    }


async def _node_build123d_cad(state: AgentState) -> dict[str, object]:
    """CAD Agent：build123d 生成 3D 模型（复用现有服务）。"""
    from app.services.cad_build123d_service import Build123dServiceError, build123d_service

    request = state["request"]
    options = request.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    need_cad = bool(options.get("generateCad") or options.get("generatePlanLine"))
    if not need_cad or not build123d_service.available:
        return {"cad_model": None}
    prompt = (
        f"项目名称：{state['project_name']}\n"
        f"所属行业：{state['industry']}\n"
        f"设计需求：{state['requirement']}\n"
        f"{state['knowledge_context']}"
    )
    db = cast(Session, state["db"])
    user_id = state["user_id"]
    try:
        result = await build123d_service.generate_model(
            prompt=prompt,
            db=db,
            user_id=user_id,
            task_id=None,
            publish_assets=True,
            render_views=bool(options.get("generateRenderViews")),
        )
        return {"cad_model": result}
    except Build123dServiceError as exc:
        return {
            "cad_model": None,
            "errors": [f"CAD 生成失败: {exc.error_code}"],
        }


async def _node_design_review(state: AgentState) -> dict[str, object]:
    """审查 Agent：几何规则检查 + LLM 审查报告。"""
    from app.services.design_review_service import (
        DesignReviewServiceError,
        design_review_service,
    )

    model = state.get("cad_model")
    step_asset_id = model.get("modelStepAssetId") if model else None
    if not step_asset_id or not design_review_service.available:
        return {"review": None}
    try:
        review = await design_review_service.create_review(
            db=cast(Session, state["db"]),
            user_id=state["user_id"],
            step_asset_id=str(step_asset_id),
            project_name=state["project_name"],
            requirement=state["requirement"],
            publish_assets=True,
        )
        return {"review": review}
    except DesignReviewServiceError as exc:
        return {"review": None, "errors": [f"审查失败: {exc.error_code}"]}


class AgentOrchestrator:
    """LangGraph 工业设计总师编排器。"""

    def __init__(self) -> None:
        self._app = None
        if LANGGRAPH_AVAILABLE:
            self._build_graph()

    def _build_graph(self) -> None:
        graph = StateGraph(AgentState)
        graph.add_node("understand", _node_understand_requirement)
        graph.add_node("cad", _node_build123d_cad)
        graph.add_node("review", _node_design_review)
        graph.add_edge(START, "understand")
        graph.add_edge("understand", "cad")
        graph.add_edge("cad", "review")
        graph.add_edge("review", END)
        self._app = graph.compile()

    @property
    def available(self) -> bool:
        return self._app is not None

    async def orchestrate(
        self,
        *,
        request: dict[str, object],
        db: Session,
        user_id: str,
    ) -> dict[str, object]:
        """执行编排，返回统一结果（与 workflow 服务 outputs 兼容）。"""
        if not self.available:
            raise AgentOrchestratorError(
                "LangGraph 未安装，无法执行总师编排",
                "AGENT_ORCHESTRATOR_UNAVAILABLE",
                status_code=503,
            )
        initial_state: AgentState = {
            "request": request,
            "project_name": "",
            "industry": "",
            "requirement": "",
            "knowledge_context": "",
            "plan_line": None,
            "cad_model": None,
            "review": None,
            "package": None,
            "diagnostics": [],
            "errors": [],
            "db": db,
            "user_id": user_id,
        }
        result = await self._app.ainvoke(initial_state)
        return self._to_result(result)

    @staticmethod
    def _to_result(state: AgentState) -> dict[str, object]:
        outputs: dict[str, object] = {}
        model = state.get("cad_model") or {}
        if model:
            for key in (
                "modelScriptAssetId",
                "modelStepAssetId",
                "modelStlAssetId",
                "modelGlbAssetId",
                "modelStep",
                "modelStl",
                "modelGlb",
                "modelDownloadUrl",
                "renderViews",
                "renderViewsPreview",
            ):
                if model.get(key):
                    outputs[key] = model[key]
        review = state.get("review") or {}
        if review:
            for key in ("reviewAssetId", "reviewDownloadUrl", "analysis", "reviewText"):
                if review.get(key):
                    outputs[key] = review[key]
        errors = state.get("errors") or []
        return {
            "status": "completed" if not errors else "completed_with_errors",
            "outputs": outputs,
            "diagnostics": [{"level": "warning", "title": e, "detail": e} for e in errors],
            "agentGraph": "industrial-design-total-engineer",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }


agent_orchestrator = AgentOrchestrator()
