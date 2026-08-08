"""Workspace Graph 后端测试：Turn → Project/Requirement → 方向 → 渲染 主链。"""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.router import router
from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.models.conversation import Conversation
from app.models.workspace_node import WorkspaceNode
from app.services.session_service import SessionService

TRUSTED_ORIGIN = "http://localhost:5174"


@pytest.fixture()
def api(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace_graph.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    app = FastAPI()
    setup_middleware(app)
    app.include_router(router, prefix="/api/v1")

    def override_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, factory
    engine.dispose()


def login(factory: sessionmaker[Session], client: TestClient, user_id: str) -> None:
    with factory() as db:
        token, _ = SessionService.create_session(
            db,
            user_id=user_id,
            client_metadata={"username": user_id},
        )
        db.commit()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)


INTENT = {
    "projectName": "测试项目",
    "industry": "装备制造",
    "requirementText": "做一个适合露营使用的便携咖啡机",
    "needsMaterials": True,
}


async def fake_analyze(text: str) -> dict[str, object]:
    return dict(INTENT)


async def fake_generate_directions(
    *,
    requirement: str,
    project_name: str,
    industry: str | None = None,
) -> list[dict[str, str]]:
    return [
        {"key": "A", "name": "极简便携", "summary": "紧凑折叠", "styleKeywords": ["轻量"], "cmf": "深灰铝合金", "imagePrompt": "便携咖啡机设计稿"},
        {"key": "B", "name": "专业机能", "summary": "户外硬朗", "styleKeywords": ["耐候"], "cmf": "黑色聚合物", "imagePrompt": "户外咖啡机设计稿"},
        {"key": "C", "name": "复古黄铜", "summary": "露营美学", "styleKeywords": ["复古"], "cmf": "黄铜+皮革", "imagePrompt": "复古咖啡机设计稿"},
    ]


def test_first_turn_creates_project_and_requirement(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import intent_service as intent_module
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")

    response = client.post(
        "/api/v1/conversations/turns",
        json={"text": "做一个适合露营使用的便携咖啡机"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    conversation_id = UUID(data["conversationId"])

    with factory() as db:
        nodes = list(
            db.scalars(
                select(WorkspaceNode).where(
                    WorkspaceNode.conversation_id == conversation_id
                )
            )
        )
        assert len(nodes) >= 2
        project = next(n for n in nodes if n.node_type == "project")
        requirement = next(n for n in nodes if n.node_type == "requirement")
        assert project.status == "completed"
        assert requirement.status == "waiting_user"
        conversation = db.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.project_id == project.project_id
        assert conversation.project_id is not None


def test_second_turn_does_not_create_second_project(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")

    first = client.post(
        "/api/v1/conversations/turns",
        json={"text": "做一个便携咖啡机"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    conversation_id = UUID(first.json()["data"]["conversationId"])

    second = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"text": "补充：需要耐热材质"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert second.status_code == 200

    with factory() as db:
        project_nodes = list(
            db.scalars(
                select(WorkspaceNode).where(
                    WorkspaceNode.conversation_id == conversation_id,
                    WorkspaceNode.node_type == "project",
                )
            )
        )
        assert len(project_nodes) == 1


def test_confirm_requirement_creates_design_directions(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.agents.design_agent import design_agent
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)
    monkeypatch.setattr(design_agent, "generate_directions", fake_generate_directions)

    client, factory = api
    login(factory, client, "alice")

    first = client.post(
        "/api/v1/conversations/turns",
        json={"text": "做一个便携咖啡机"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    conversation_id = UUID(first.json()["data"]["conversationId"])

    with factory() as db:
        requirement = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.conversation_id == conversation_id,
                WorkspaceNode.node_type == "requirement",
            )
        )
        assert requirement is not None
        requirement_id = str(requirement.id)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"action": {"nodeId": requirement_id, "type": "confirm"}},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 200, response.text

    with factory() as db:
        directions = list(
            db.scalars(
                select(WorkspaceNode).where(
                    WorkspaceNode.conversation_id == conversation_id,
                    WorkspaceNode.node_type == "design_direction",
                )
            )
        )
        assert len(directions) >= 3


def test_select_direction_supersedes_other_directions(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.agents.design_agent import design_agent
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)
    monkeypatch.setattr(design_agent, "generate_directions", fake_generate_directions)

    client, factory = api
    login(factory, client, "alice")

    first = client.post(
        "/api/v1/conversations/turns",
        json={"text": "做一个便携咖啡机"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    conversation_id = UUID(first.json()["data"]["conversationId"])

    with factory() as db:
        requirement = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.conversation_id == conversation_id,
                WorkspaceNode.node_type == "requirement",
            )
        )
        requirement_id = str(requirement.id)

    confirm = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"action": {"nodeId": requirement_id, "type": "confirm"}},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert confirm.status_code == 200

    with factory() as db:
        directions = list(
            db.scalars(
                select(WorkspaceNode).where(
                    WorkspaceNode.conversation_id == conversation_id,
                    WorkspaceNode.node_type == "design_direction",
                )
            )
        )
        direction_b = next(d for d in directions if d.input_data.get("key") == "B")

    picked = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"action": {"nodeId": str(direction_b.id), "type": "confirm"}},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert picked.status_code == 200, picked.text

    with factory() as db:
        directions = list(
            db.scalars(
                select(WorkspaceNode).where(
                    WorkspaceNode.conversation_id == conversation_id,
                    WorkspaceNode.node_type == "design_direction",
                )
            )
        )
        by_key = {d.input_data.get("key"): d for d in directions}
        assert by_key["B"].status == "completed"
        assert by_key["A"].status == "superseded"
        assert by_key["C"].status == "superseded"
        render = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.conversation_id == conversation_id,
                WorkspaceNode.node_type == "render",
            )
        )
        assert render is not None
        assert render.status in {"queued", "running"}


def test_workspace_snapshot_is_user_scoped(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")
    created = client.post(
        "/api/v1/conversations/turns",
        json={"text": "做一个便携咖啡机"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    conversation_id = UUID(created.json()["data"]["conversationId"])

    login(factory, client, "bob")
    snapshot = client.get(
        f"/api/v1/conversations/{conversation_id}/workspace",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert snapshot.status_code == 404


def test_conversation_patch_binds_project(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")

    created = client.post(
        "/api/v1/conversations",
        json={"title": "绑定测试"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    conversation_id = created.json()["data"]["id"]

    patched = client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"projectId": "demo-project-1"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["projectId"] == "demo-project-1"
