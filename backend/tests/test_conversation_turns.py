"""Conversation Turn 后端测试：恢复、追加、快照隔离、卡片动作。"""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.router import router
from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.models.workspace_node import WorkspaceNode
from app.services.session_service import SessionService

TRUSTED_ORIGIN = "http://localhost:5174"

INTENT = {
    "projectName": "测试项目",
    "industry": "装备制造",
    "requirementText": "做一个便携咖啡机",
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
        {"key": "A", "name": "方向A", "summary": "极简", "styleKeywords": ["轻量"], "cmf": "深灰铝", "imagePrompt": "方向A设计稿"},
        {"key": "B", "name": "方向B", "summary": "机能", "styleKeywords": ["耐候"], "cmf": "黑色聚合物", "imagePrompt": "方向B设计稿"},
        {"key": "C", "name": "方向C", "summary": "复古", "styleKeywords": ["复古"], "cmf": "黄铜", "imagePrompt": "方向C设计稿"},
    ]


@pytest.fixture()
def api(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'conversation_turns.db'}",
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


def _start_conversation(
    client: TestClient,
    factory: sessionmaker[Session],
    text: str = "做一个便携咖啡机",
) -> str:
    response = client.post(
        "/api/v1/conversations/turns",
        json={"text": text},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["conversationId"])


def test_turn_returns_created_and_updated_nodes(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")
    response = client.post(
        "/api/v1/conversations/turns",
        json={"text": "做一个便携咖啡机"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    data = response.json()["data"]
    assert data["message"]["role"] == "assistant"
    assert data["message"]["text"]
    assert len(data.get("nodesCreated") or []) >= 2


def test_snapshot_restores_workspace_after_reload(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """刷新恢复：GET /workspace 返回完整节点，前端可重建。"""
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")
    conversation_id = _start_conversation(client, factory)

    snapshot = client.get(
        f"/api/v1/conversations/{conversation_id}/workspace",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert snapshot.status_code == 200
    data = snapshot.json()["data"]
    assert data["conversation"]["id"] == conversation_id
    assert data["conversation"]["projectId"]
    types = {n["type"] for n in data["nodes"]}
    assert {"project", "requirement"}.issubset(types)


def test_turn_rejected_for_foreign_conversation(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")
    conversation_id = _start_conversation(client, factory)

    login(factory, client, "bob")
    turn = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"text": "别人的会话不能追加"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert turn.status_code == 404


def test_quote_action_creates_quote_node(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)

    client, factory = api
    login(factory, client, "alice")
    conversation_id = _start_conversation(client, factory)

    with factory() as db:
        project = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.conversation_id == UUID(conversation_id),
                WorkspaceNode.node_type == "project",
            )
        )
        assert project is not None

    turn = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"action": {"nodeId": str(project.id), "type": "generate_quote"}},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert turn.status_code == 200, turn.text

    with factory() as db:
        quotes = list(
            db.scalars(
                select(WorkspaceNode).where(
                    WorkspaceNode.conversation_id == UUID(conversation_id),
                    WorkspaceNode.node_type == "quote",
                )
            )
        )
        assert len(quotes) == 1
        assert quotes[0].status == "completed"
        assert quotes[0].output_data.get("range", {}).get("min")


def test_next_action_node_generated_after_direction_selected(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.agents.design_agent import design_agent
    from app.services.intent_service import intent_service

    monkeypatch.setattr(intent_service, "analyze", fake_analyze)
    monkeypatch.setattr(design_agent, "generate_directions", fake_generate_directions)

    client, factory = api
    login(factory, client, "alice")
    conversation_id = _start_conversation(client, factory)

    with factory() as db:
        requirement = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.conversation_id == UUID(conversation_id),
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
                    WorkspaceNode.conversation_id == UUID(conversation_id),
                    WorkspaceNode.node_type == "design_direction",
                )
            )
        )
        assert len(directions) >= 3

    # 选择方向 B → 创建 render 节点（queued）
    with factory() as db:
        picked = next(d for d in directions if d.input_data.get("key") == "B")
        picked_id = str(picked.id)

    selected = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"action": {"nodeId": picked_id, "type": "confirm"}},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert selected.status_code == 200, selected.text

    with factory() as db:
        render = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.conversation_id == UUID(conversation_id),
                WorkspaceNode.node_type == "render",
            )
        )
        assert render is not None
        assert render.status in {"queued", "running"}
