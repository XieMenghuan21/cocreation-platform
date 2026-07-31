from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Thread
from typing import Final
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.router import router
from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.models.persistence import Asset, UserSession, WorkspaceState
from app.services.session_service import SessionService

TRUSTED_ORIGIN = "http://localhost:5174"


@pytest.fixture()
def api(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace.db'}",
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


def payload(version: int, **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "selectedProjectId": None,
        "selectedReferenceVersionId": None,
        "selectedReferenceAssetId": None,
        "activeScenario": "design",
        "activeWorkflowStage": "concept",
        "activeStepIndex": 2,
        "viewMode": "3d",
        "sceneMode": "studio",
        "selectedIndustry": "manufacturing",
        "generationPrompt": "create a cabinet",
        "stateData": {"zoom": 1.25},
        "version": version,
    }
    body.update(changes)
    return body


def test_workspace_default_is_not_persisted_and_round_trip_uses_optimistic_lock(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    default = client.get("/api/v1/workspace")
    assert default.status_code == 200
    assert default.json()["version"] == 0
    assert default.json()["stateData"] == {}
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkspaceState)) == 0

    created = client.put(
        "/api/v1/workspace",
        json=payload(0),
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert created.status_code == 200
    assert created.json()["version"] == 1
    assert created.json()["generationPrompt"] == "create a cabinet"

    updated = client.put(
        "/api/v1/workspace",
        json=payload(1, generationPrompt="revised"),
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    stale = client.put(
        "/api/v1/workspace",
        json=payload(1, generationPrompt="stale"),
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["latest"]["version"] == 2
    assert stale.json()["detail"]["latest"]["generationPrompt"] == "revised"


def test_workspace_update_rejects_unexpected_fields(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")

    response = client.put(
        "/api/v1/workspace",
        json=payload(0, unexpectedWorkspace=True),
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 422


def test_workspace_isolated_and_reference_asset_must_be_owned_and_available(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    with factory() as db:
        owned = Asset(
            user_id="alice", kind="image", filename="a.png", extension="png",
            content_type="image/png", size_bytes=0, sha256="0" * 64,
            chunk_size=4, chunk_count=0, status="available", source="upload",
            asset_metadata={},
        )
        other = Asset(
            user_id="bob", kind="image", filename="b.png", extension="png",
            content_type="image/png", size_bytes=0, sha256="0" * 64,
            chunk_size=4, chunk_count=0, status="available", source="upload",
            asset_metadata={},
        )
        unavailable = Asset(
            user_id="alice", kind="image", filename="c.png", extension="png",
            content_type="image/png", size_bytes=0, sha256="0" * 64,
            chunk_size=4, chunk_count=0, status="uploading", source="upload",
            asset_metadata={},
        )
        db.add_all([owned, other, unavailable])
        db.commit()

    login(factory, client, "alice")
    accepted = client.put(
        "/api/v1/workspace",
        json=payload(0, selectedReferenceAssetId=str(owned.id)),
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert accepted.status_code == 200
    for asset_id in (other.id, unavailable.id, uuid4()):
        rejected = client.put(
            "/api/v1/workspace",
            json=payload(1, selectedReferenceAssetId=str(asset_id)),
            headers={"Origin": TRUSTED_ORIGIN},
        )
        assert rejected.status_code == 404

    client.cookies.clear()
    assert client.get("/api/v1/workspace").status_code == 401
    login(factory, client, "bob")
    assert client.get("/api/v1/workspace").json()["version"] == 0
    evil = client.put(
        "/api/v1/workspace",
        json=payload(0),
        headers={"Origin": "https://evil.test"},
    )
    assert evil.status_code == 403


def test_first_workspace_put_is_concurrency_safe(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
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
    with factory() as db:
        token, session = SessionService.create_session(
            db,
            user_id="concurrent-user",
            client_metadata={"username": "concurrent-user"},
        )
        session.last_seen_at = datetime.now(timezone.utc)
        db.commit()

    barrier = Barrier(3)
    results: list[tuple[int, dict[str, object]]] = []
    first_prompt: Final = "first contender"
    second_prompt: Final = "second contender"

    def put_workspace(prompt: str) -> None:
        with TestClient(app, raise_server_exceptions=False) as concurrent_client:
            concurrent_client.cookies.set(settings.SESSION_COOKIE_NAME, token)
            barrier.wait()
            response = concurrent_client.put(
                "/api/v1/workspace",
                json=payload(0, generationPrompt=prompt),
                headers={"Origin": TRUSTED_ORIGIN},
            )
            results.append((response.status_code, response.json()))

    threads = [
        Thread(target=put_workspace, args=(first_prompt,)),
        Thread(target=put_workspace, args=(second_prompt,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()

    assert sorted(status_code for status_code, _ in results) == [200, 409]
    winner = next(body for status_code, body in results if status_code == 200)
    conflict = next(body for status_code, body in results if status_code == 409)
    with factory() as db:
        states = list(
            db.scalars(
                select(WorkspaceState).where(
                    WorkspaceState.user_id == "concurrent-user"
                )
            )
        )
        assert len(states) == 1
        assert states[0].version == 1
        assert states[0].generation_prompt == winner["generationPrompt"]
        assert conflict["detail"]["latest"]["generationPrompt"] == winner["generationPrompt"]
    engine.dispose()


def test_workspace_commit_failure_returns_500_and_persists_nothing(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    with factory() as db:
        session = db.scalar(select(UserSession).where(UserSession.user_id == "alice"))
        assert session is not None
        session.last_seen_at = datetime.now(timezone.utc)
        db.commit()

    class CommitFailSession(Session):
        def commit(self) -> None:
            raise RuntimeError("simulated commit failure")

    failing_factory = sessionmaker(
        bind=factory.kw["bind"],
        class_=CommitFailSession,
        expire_on_commit=False,
    )

    def failing_db() -> Generator[Session, None, None]:
        with failing_factory() as db:
            yield db

    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[get_db] = failing_db
    response = client.put(
        "/api/v1/workspace",
        json=payload(0),
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 500
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkspaceState)) == 0


def test_workspace_rejects_oversize_fields_before_database_write(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    for field, value in (
        ("activeScenario", "x" * 121),
        ("selectedProjectId", "x" * 161),
        ("viewMode", "x" * 65),
    ):
        response = client.put(
            "/api/v1/workspace",
            json=payload(0, **{field: value}),
            headers={"Origin": TRUSTED_ORIGIN},
        )
        assert response.status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkspaceState)) == 0
