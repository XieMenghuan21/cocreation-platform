from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.router import router
from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.services.session_service import SessionService
from tests.test_workspace_api import login  # noqa: F401  (reuse auth helper)


@pytest.fixture()
def api(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    from sqlalchemy import create_engine

    engine = create_engine(
        f"sqlite:///{tmp_path / 'workbench.db'}",
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


def _auth_cookie(client: TestClient, factory: sessionmaker[Session]) -> None:
    with factory() as db:
        token, _ = SessionService.create_session(
            db,
            user_id="alice",
            client_metadata={"username": "alice"},
        )
        db.commit()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)


def test_prompt_writer_skill_returns_optimized_result(
    api: tuple[TestClient, sessionmaker[Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, factory = api
    _auth_cookie(client, factory)

    async def fake_optimize(**_: Any) -> dict[str, object]:
        return {
            "originalPrompt": "a chair",
            "optimizedPrompt": "modern wooden chair, soft studio light",
            "finalPrompt": "modern wooden chair, soft studio light",
            "enabled": True,
            "aiOptimized": True,
            "references": [{"category": "lighting", "prompt": "soft light"}],
        }

    monkeypatch.setattr(
        "app.api.v1.aggregation_workbench.image_prompt_optimizer_service.optimize",
        fake_optimize,
    )

    response = client.post(
        "/api/v1/platform-tools/aggregation-workbench/skill/prompt-writer",
        json={"prompt": "a chair", "model": "test-model"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["skill"] == "prompt-writer"
    assert body["originalPrompt"] == "a chair"
    assert body["optimizedPrompt"] == "modern wooden chair, soft studio light"
    assert body["aiOptimized"] is True
    assert len(body["references"]) == 1


def test_prompt_writer_skill_requires_auth(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = api
    response = client.post(
        "/api/v1/platform-tools/aggregation-workbench/skill/prompt-writer",
        json={"prompt": "a chair"},
    )
    assert response.status_code == 401


def test_prompt_writer_skill_rejects_empty_prompt(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    _auth_cookie(client, factory)
    response = client.post(
        "/api/v1/platform-tools/aggregation-workbench/skill/prompt-writer",
        json={"prompt": ""},
    )
    assert response.status_code == 422
