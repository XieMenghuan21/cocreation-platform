from __future__ import annotations

from collections.abc import Generator
from importlib import import_module
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.conversation import Conversation
from app.models.orchestration import AgentRun
from app.models.persistence import WorkflowTask
from app.models.workspace_node import WorkspaceNode
from app.services.agents.render_agent import build_render_request, render_agent
from app.services.agents.render_agent import sync_node_from_task
from app.services.workspace_graph_service import workspace_graph_service

render_agent_module = import_module("app.services.agents.render_agent")


@pytest.fixture()
def factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'render-agent-reference.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


def test_render_request_carries_reference_image_urls() -> None:
    asset_id = "11111111-1111-4111-8111-111111111111"
    request = build_render_request(
        project_name="布艺沙发",
        requirement_text="白色三人位布艺沙发",
        direction_image_prompt="place the same sofa into a warm living room",
        industry="家具",
        reference_image_urls=[f"/api/v1/assets/{asset_id}/download"],
    )

    assert request.asset_ids == [asset_id]
    assert request.asset_urls == [f"/api/v1/assets/{asset_id}/download"]
    assert request.options.generate_render is True
    assert request.options.generate_drawing is False
    assert request.options.enhance_image is True
    assert request.context["imageEditMode"] == "poster"


@pytest.mark.asyncio()
async def test_render_agent_fails_without_reference_image(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_create_workflow(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {"taskId": "should-not-run"}

    monkeypatch.setattr(
        render_agent_module.industrial_design_workflow_service,
        "create_workflow",
        fake_create_workflow,
    )

    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.flush()
        node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="render",
            status="queued",
            title="宣发图",
            summary="融合客厅场景",
            agent_key="render_agent",
            input_data={"imagePrompt": "living room scene"},
        )

        result = await render_agent.launch_render(
            db=db,
            auth_user={"sub": "alice"},
            user_id="alice",
            node=node,
            project_name="布艺沙发",
            requirement_text="白色三人位布艺沙发",
            direction_image_prompt="living room scene",
            industry="家具",
        )
        db.commit()
        run_id = result.ui_data["agentRunId"]
        run = db.get(AgentRun, UUID(str(run_id)))

    assert called is False
    assert result.status == "failed"
    assert result.output_data["errorCode"] == "RENDER_REFERENCE_IMAGE_REQUIRED"
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "RENDER_REFERENCE_IMAGE_REQUIRED"


@pytest.mark.asyncio()
async def test_render_agent_persists_source_image_metadata(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_asset_urls: list[str] = []

    async def fake_create_workflow(request: object, **kwargs: object) -> dict[str, object]:
        received_asset_urls.extend(getattr(request, "asset_urls"))
        return {"taskId": "promo-edit-task", "currentStep": "图片编辑中"}

    monkeypatch.setattr(
        render_agent_module.industrial_design_workflow_service,
        "create_workflow",
        fake_create_workflow,
    )

    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.flush()
        source_node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="render",
            status="completed",
            title="设计图 v1.2",
            summary="白色布艺沙发设计图",
            agent_key="render_agent",
            output_data={"renderImageUrl": "/api/v1/assets/source/download"},
        )
        promo_node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            parent_id=source_node.id,
            node_type="render",
            status="queued",
            title="宣发图",
            summary="融合客厅场景",
            agent_key="render_agent",
            input_data={"imagePrompt": "living room scene"},
        )

        result = await render_agent.launch_render(
            db=db,
            auth_user={"sub": "alice"},
            user_id="alice",
            node=promo_node,
            project_name="布艺沙发",
            requirement_text="白色三人位布艺沙发",
            direction_image_prompt="living room scene",
            industry="家具",
        )
        db.commit()

    assert received_asset_urls == ["/api/v1/assets/source/download"]
    assert result.status == "running"
    assert result.ui_data["renderMode"] == "promotion"
    assert result.ui_data["sourceNodeTitle"] == "设计图 v1.2"
    assert result.ui_data["sourceImageUrl"] == "/api/v1/assets/source/download"
    assert result.output_data["renderMode"] == "promotion"
    assert result.output_data["sourceNodeTitle"] == "设计图 v1.2"
    assert result.output_data["sourceImageUrl"] == "/api/v1/assets/source/download"


def test_promotion_render_next_action_recommends_promo_edits(
    factory: sessionmaker[Session],
) -> None:
    with factory() as db:
        conversation = Conversation(user_id="alice", project_id="project-alpha", title="sofa")
        db.add(conversation)
        db.flush()
        node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="render",
            status="running",
            title="宣发图",
            summary="客厅场景融合",
            agent_key="render_agent",
            output_data={
                "renderMode": "promotion",
                "sourceNodeTitle": "设计图 v1.2",
                "sourceImageUrl": "/api/v1/assets/source/download",
            },
        )
        task = WorkflowTask(
            id="promo-render-task",
            user_id="alice",
            project_id="project-alpha",
            version_id=None,
            conversation_id=conversation.id,
            workspace_node_id=node.id,
            status="completed",
            progress=100,
            current_step="done",
            input_payload={},
            design_spec={},
            outputs={"renderImageUrl": "/api/v1/assets/promo/download"},
            diagnostics=[],
        )
        db.add(task)
        db.flush()

        sync_node_from_task(db, "promo-render-task")
        next_action = db.scalars(
            select(WorkspaceNode).where(WorkspaceNode.node_type == "next_action")
        ).one()

    recommendations = next_action.output_data["recommendations"]
    assert [item["label"] for item in recommendations] == [
        "换一个宣发场景",
        "调整宣发风格",
        "生成海报文案",
    ]
