from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.conversation import Conversation
from app.models.orchestration import AgentRun
from app.models.workspace_node import WorkspaceNode
from app.schemas.workspace_graph import TurnRequest
from app.services.workspace_turn_service import workspace_turn_service


@pytest.fixture()
def factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace-turn-agent-runs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


@pytest.mark.asyncio()
async def test_turn_flow_persists_agent_runs(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_intent(text: str) -> dict[str, object]:
        return {
            "projectName": "布艺沙发",
            "requirementText": text,
            "industry": "家具",
        }

    async def fake_requirement(**kwargs: object) -> dict[str, object]:
        return {
            "summary": "白色三人位布艺沙发，圆润扶手，棉麻材质",
            "requirement": {"productCategory": "沙发"},
            "completeness": 80,
            "criticalUnknown": "",
            "question": "",
            "canProceed": True,
        }

    async def fake_directions(**kwargs: object) -> list[dict[str, str]]:
        return [
            {
                "key": "A",
                "name": "柔和家居",
                "summary": "圆润亲和",
                "styleKeywords": ["圆润", "温和"],
                "cmf": "白色棉麻",
                "imagePrompt": "白色三人位布艺沙发设计图",
            }
        ]

    def fake_create_project(*args: object, **kwargs: object) -> dict[str, object]:
        return {"id": "project-alpha", "name": "布艺沙发"}

    monkeypatch.setattr(
        "app.services.workspace_turn_service.intent_service.analyze",
        fake_intent,
    )
    monkeypatch.setattr(
        "app.services.workspace_turn_service.requirement_agent.analyze",
        fake_requirement,
    )
    monkeypatch.setattr(
        "app.services.workspace_turn_service.design_agent.generate_directions",
        fake_directions,
    )
    monkeypatch.setattr(
        "app.services.workspace_turn_service.cocreation_history_service.create_project",
        fake_create_project,
    )

    with factory() as db:
        first = await workspace_turn_service.handle_turn(
            db,
            auth_user={"sub": "alice"},
            user_id="alice",
            conversation_id=None,
            request=TurnRequest(text="设计一款白色三人位布艺沙发"),
        )
        requirement_node = next(
            node for node in first.nodes_created if node.node_type == "requirement"
        )

        await workspace_turn_service.handle_turn(
            db,
            auth_user={"sub": "alice"},
            user_id="alice",
            conversation_id=first.conversation_id,
            request=TurnRequest(
                text="确认",
                action={"nodeId": str(requirement_node.id), "type": "confirm"},
            ),
        )

        nodes = db.scalars(select(WorkspaceNode)).all()
        runs = db.scalars(select(AgentRun)).all()

    assert {node.node_type for node in nodes} >= {"project", "requirement", "design_direction"}
    assert {run.agent_type for run in runs} >= {"project", "requirement", "design"}
    assert {run.project_id for run in runs} == {"project-alpha"}


@pytest.mark.asyncio()
async def test_turn_action_regenerates_promotion_scene_from_original_source(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[object] = []

    def fake_create_task(coro: object) -> object:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        scheduled.append(coro)
        return object()

    monkeypatch.setattr(
        "app.services.workspace_turn_service.asyncio.create_task",
        fake_create_task,
    )

    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.flush()
        promo_node = WorkspaceNode(
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="render",
            status="completed",
            title="宣发图 v1",
            summary="客厅场景融合",
            agent_key="render_agent",
            input_data={},
            output_data={
                "renderMode": "promotion",
                "sourceImageUrl": "/api/v1/assets/design/download",
                "sourceNodeTitle": "设计图 v1.2",
                "renderImageUrl": "/api/v1/assets/promo-v1/download",
            },
            ui_data={
                "renderMode": "promotion",
                "sourceImageUrl": "/api/v1/assets/design/download",
                "sourceNodeTitle": "设计图 v1.2",
            },
        )
        db.add(promo_node)
        db.flush()

        result = await workspace_turn_service.handle_turn(
            db,
            auth_user={"sub": "alice"},
            user_id="alice",
            conversation_id=conversation.id,
            request=TurnRequest(
                text="",
                action={"nodeId": str(promo_node.id), "type": "regenerate_scene"},
            ),
        )

    created = result.nodes_created[0]
    assert scheduled
    assert created.node_type == "render"
    assert created.title.startswith("宣发图")
    assert created.input_data["referenceImageUrl"] == "/api/v1/assets/design/download"
    assert created.output_data["sourceImageUrl"] == "/api/v1/assets/design/download"
    assert created.ui_data["sourceNodeTitle"] == "设计图 v1.2"
    assert "同一张设计图" in result.message["text"]
