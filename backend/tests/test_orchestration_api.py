from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.router import router
from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.models.orchestration import AgentRun
from app.services.session_service import SessionService

TRUSTED_ORIGIN = "http://localhost:5174"


@pytest.fixture()
def api(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'orchestration-api.db'}",
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


def test_orchestration_create_read_action_and_retry(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")

    created = client.post(
        "/api/v1/orchestrations",
        json={
            "projectId": "project-alpha",
            "prompt": "design a white three-seat fabric sofa",
            "attachmentAssetIds": [],
        },
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert created.status_code == 201
    workflow_id = created.json()["id"]
    assert created.json()["projectId"] == "project-alpha"
    assert [run["agentType"] for run in created.json()["agentRuns"]] == ["requirement"]

    detail = client.get(f"/api/v1/orchestrations/{workflow_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == workflow_id

    action = client.post(
        f"/api/v1/orchestrations/{workflow_id}/actions",
        json={"type": "confirm_design_direction", "payload": {"directionId": "direction-a"}},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert action.status_code == 200
    assert "render" in [run["agentType"] for run in action.json()["agentRuns"]]
    assert "three_d" in [run["agentType"] for run in action.json()["agentRuns"]]

    with factory() as db:
        render_run = (
            db.query(AgentRun)
            .filter(AgentRun.project_id == "project-alpha", AgentRun.agent_type == "render")
            .one()
        )
        render_run.status = "failed"
        render_run.error_code = "RENDER_REFERENCE_EDIT_REQUIRED"
        render_run.error_message = "reference image is required"
        db.commit()
        render_run_id = str(render_run.id)

    retry = client.post(
        f"/api/v1/orchestrations/{workflow_id}/agent-runs/{render_run_id}/retry",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert retry.status_code == 200
    retry_runs = [
        run
        for run in retry.json()["agentRuns"]
        if run["agentType"] == "render" and run["status"] == "queued"
    ]
    assert retry_runs
    assert retry_runs[-1]["retryCount"] == 1


def test_orchestration_is_user_isolated(api: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _factory = api
    factory = _factory
    login(factory, client, "alice")
    created = client.post(
        "/api/v1/orchestrations",
        json={"projectId": "project-secret", "prompt": "secret product"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    client.cookies.clear()
    login(factory, client, "bob")
    forbidden = client.get(f"/api/v1/orchestrations/{workflow_id}")

    assert forbidden.status_code == 404
