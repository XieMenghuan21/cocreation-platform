from __future__ import annotations

import os
from collections.abc import Iterator
from threading import Barrier, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.cocreation_history import (
    CocreationAssetLibraryEntry,
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
    CocreationVersionAssetEntry,
)
from app.models.persistence import Asset, WorkspaceState
from app.services.cocreation_history_service import CocreationHistoryService
from app.services.workspace_service import WorkspaceService


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


def seed_publishable_version(
    factory: sessionmaker[Session],
    user_id: str,
    project_id: str,
    version_id: str,
) -> None:
    with factory.begin() as db:
        asset = Asset(
            user_id=user_id,
            kind="cad",
            filename="published.step",
            extension="step",
            content_type="application/step",
            size_bytes=1,
            sha256="0" * 64,
            chunk_size=1,
            chunk_count=1,
            status="available",
            source="generated",
            asset_metadata={},
        )
        project = CocreationProjectHistory(
            user_id=user_id,
            project_id=project_id,
            project_name="并发发布",
            project_data={},
        )
        db.add_all([asset, project])
        db.flush()
        version = CocreationProjectVersionHistory(
                user_id=user_id,
                project_history_id=project.id,
                version_id=version_id,
                label="V1",
                status="completed",
                note="ready",
                project_id=project_id,
            )
        db.add(version)
        db.flush()
        db.add(
            CocreationVersionAssetEntry(
                user_id=user_id,
                version_history_id=version.id,
                asset_id=asset.id,
                role="output",
                kind="cad",
            )
        )


@pytest.mark.postgres_integration
def test_postgres_publish_and_reference_are_concurrency_safe(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    user_id = f"pg-history-{uuid4()}"
    project_id = f"project-{uuid4()}"
    seed_publishable_version(factory, user_id, project_id, "version-a")
    with factory.begin() as db:
        project = db.scalar(
            select(CocreationProjectHistory).where(
                CocreationProjectHistory.user_id == user_id
            )
        )
        assert project is not None
        db.add(
            CocreationProjectVersionHistory(
                user_id=user_id,
                project_history_id=project.id,
                version_id="version-b",
                label="V2",
                status="completed",
                note="ready",
                project_id=project_id,
            )
        )

    barrier = Barrier(5)
    lock = Lock()
    failures: list[BaseException] = []

    def publish() -> None:
        try:
            with factory() as db:
                barrier.wait()
                CocreationHistoryService().publish_version(
                    db,
                    auth_user={"sub": user_id},
                    project_id=project_id,
                    version_id="version-a",
                )
        except BaseException as exc:
            with lock:
                failures.append(exc)

    def reference(version_id: str) -> None:
        try:
            with factory() as db:
                barrier.wait()
                WorkspaceService().set_reference_version(db, user_id, version_id)
                db.commit()
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [
        Thread(target=publish),
        Thread(target=publish),
        Thread(target=reference, args=("version-a",)),
        Thread(target=reference, args=("version-b",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    try:
        assert failures == []
        with factory() as db:
            assert db.scalar(
                select(func.count(CocreationAssetLibraryEntry.id)).where(
                    CocreationAssetLibraryEntry.user_id == user_id
                )
            ) == 1
            workspace = db.scalar(
                select(WorkspaceState).where(WorkspaceState.user_id == user_id)
            )
            assert workspace is not None
            assert workspace.version == 2
            assert workspace.selected_reference_version_id in {
                "version-a",
                "version-b",
            }
    finally:
        with factory.begin() as cleanup:
            cleanup.execute(
                delete(WorkspaceState).where(WorkspaceState.user_id == user_id)
            )
            cleanup.execute(
                delete(CocreationProjectHistory).where(
                    CocreationProjectHistory.user_id == user_id
                )
            )
            cleanup.execute(delete(Asset).where(Asset.user_id == user_id))
