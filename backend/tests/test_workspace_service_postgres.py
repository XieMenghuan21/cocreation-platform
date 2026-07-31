from __future__ import annotations

import os
from collections.abc import Iterator
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.persistence import WorkspaceState
from app.schemas.workspace import WorkspaceUpdate
from app.services.workspace_service import WorkspaceConflict, WorkspaceService


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("TEST_POSTGRES_DATABASE_URL is not configured")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL test database is unreachable: {exc}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def workspace_payload(version: int, prompt: str) -> WorkspaceUpdate:
    return WorkspaceUpdate(version=version, generationPrompt=prompt)


def run_concurrent_updates(
    factory: sessionmaker[Session],
    user_id: str,
    version: int,
) -> list[tuple[str, str]]:
    barrier = Barrier(3)
    lock = Lock()
    results: list[tuple[str, str]] = []

    def update_workspace(prompt: str) -> None:
        with factory() as db:
            barrier.wait()
            try:
                WorkspaceService().update(
                    db,
                    user_id,
                    workspace_payload(version, prompt),
                )
                db.commit()
                outcome = ("success", prompt)
            except WorkspaceConflict:
                db.rollback()
                outcome = ("conflict", prompt)
        with lock:
            results.append(outcome)

    threads = [
        Thread(target=update_workspace, args=("contender-a",)),
        Thread(target=update_workspace, args=("contender-b",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    return results


@pytest.mark.postgres_integration
def test_postgres_workspace_concurrent_first_and_existing_updates_have_one_winner(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    user_id = f"pg-workspace-{uuid4()}"
    try:
        first_results = run_concurrent_updates(factory, user_id, 0)
        assert sorted(result for result, _ in first_results) == ["conflict", "success"]
        with factory() as db:
            state = db.query(WorkspaceState).filter_by(user_id=user_id).one()
            assert state.version == 1
            first_winner = next(
                prompt for result, prompt in first_results if result == "success"
            )
            assert state.generation_prompt == first_winner

        update_results = run_concurrent_updates(factory, user_id, 1)
        assert sorted(result for result, _ in update_results) == ["conflict", "success"]
        with factory() as db:
            state = db.query(WorkspaceState).filter_by(user_id=user_id).one()
            assert state.version == 2
            update_winner = next(
                prompt for result, prompt in update_results if result == "success"
            )
            assert state.generation_prompt == update_winner
    finally:
        with factory.begin() as cleanup:
            cleanup.execute(
                delete(WorkspaceState).where(WorkspaceState.user_id == user_id)
            )


def test_workspace_schema_rejects_database_length_overflows_before_write() -> None:
    with pytest.raises(ValidationError):
        WorkspaceUpdate(version=0, activeScenario="x" * 121)
    with pytest.raises(ValidationError):
        WorkspaceUpdate(version=0, selectedProjectId="x" * 161)
