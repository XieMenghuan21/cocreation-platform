from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.conversation import Conversation
from app.models.orchestration import AgentRun, WorkflowInstance
from app.services.workspace_graph_service import workspace_graph_service


@pytest.fixture()
def factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace-graph-bridge.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


def test_agent_backed_workspace_node_creates_agent_run(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        conversation = Conversation(
            user_id="alice",
            project_id="project-alpha",
            title="sofa",
        )
        db.add(conversation)
        db.flush()

        node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-alpha",
            node_type="render",
            status="queued",
            title="Render",
            summary="Render the confirmed sofa design",
            agent_key="render_agent",
            input_data={"prompt": "white sofa"},
        )
        db.commit()

        workflows = db.scalars(select(WorkflowInstance)).all()
        runs = db.scalars(select(AgentRun)).all()

    assert len(workflows) == 1
    assert workflows[0].project_id == "project-alpha"
    assert len(runs) == 1
    assert runs[0].agent_type == "render"
    assert runs[0].status == "queued"
    assert runs[0].project_id == "project-alpha"
    assert node.ui_data["workflowId"] == str(workflows[0].id)
    assert node.ui_data["agentRunId"] == str(runs[0].id)
    assert runs[0].input_snapshot["workspaceNodeId"] == str(node.id)


@pytest.mark.parametrize(
    ("agent_key", "agent_type"),
    (
        ("requirement_agent", "requirement"),
        ("project_agent", "project"),
        ("design_agent", "design"),
        ("render_agent", "render"),
        ("model_agent", "three_d"),
        ("cad_agent", "cad"),
        ("quote_agent", "quote"),
        ("engineering_agent", "engineering_package"),
    ),
)
def test_all_workspace_agent_keys_create_expected_agent_runs(
    factory: sessionmaker[Session],
    agent_key: str,
    agent_type: str,
) -> None:
    with factory() as db:
        conversation = Conversation(
            user_id="alice",
            project_id="project-agents",
            title="all agents",
        )
        db.add(conversation)
        db.flush()

        node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-agents",
            node_type=agent_type,
            status="queued",
            title=agent_type,
            summary=agent_type,
            agent_key=agent_key,
            input_data={},
        )
        db.commit()

        run = db.get(AgentRun, UUID(str(node.ui_data["agentRunId"])))

    assert run is not None
    assert run.agent_type == agent_type
    assert run.project_id == "project-agents"


def test_workspace_node_status_update_syncs_agent_run(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        conversation = Conversation(
            user_id="alice",
            project_id="project-beta",
            title="power station",
        )
        db.add(conversation)
        db.flush()

        node = workspace_graph_service.create_node(
            db,
            user_id="alice",
            conversation_id=conversation.id,
            project_id="project-beta",
            node_type="cad",
            status="queued",
            title="CAD",
            summary="Generate CAD files",
            agent_key="cad_agent",
            input_data={"prompt": "portable power station"},
        )
        updated = workspace_graph_service.update_node(
            db,
            node_id=node.id,
            user_id="alice",
            status="running",
        )
        assert updated is not None
        db.commit()

        run_id = updated.ui_data["agentRunId"]
        run = db.get(AgentRun, UUID(str(run_id)))

    assert run is not None
    assert run.status == "running"
    assert run.started_at is not None
