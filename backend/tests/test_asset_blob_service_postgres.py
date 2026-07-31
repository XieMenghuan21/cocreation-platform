from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.persistence import Asset, WorkspaceState
from app.services.asset_blob_service import AssetBlobService


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


@pytest.mark.postgres_integration
def test_postgres_successful_store_is_removed_by_outer_rollback(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    service = AssetBlobService(chunk_size=4, session_factory=factory)
    user_id = f"pg-rollback-{uuid4()}"

    with factory() as session:
        asset = service.store_bytes(
            db=session,
            user_id=user_id,
            filename="rollback.bin",
            content_type="application/octet-stream",
            kind="binary",
            source="test",
            content=b"abcdefghij",
        )
        asset_id = asset.id
        session.rollback()

    with factory() as verification:
        assert verification.get(Asset, asset_id) is None


@pytest.mark.postgres_integration
def test_postgres_failed_stream_preserves_prior_outer_write_on_commit(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    service = AssetBlobService(chunk_size=4, session_factory=factory)
    user_id = f"pg-savepoint-{uuid4()}"

    def broken_stream() -> Iterator[bytes]:
        yield b"abcd"
        raise RuntimeError("source stream failed")

    try:
        with factory() as session:
            workspace = WorkspaceState(user_id=user_id, active_scenario="prior-write")
            session.add(workspace)
            session.flush()

            with pytest.raises(RuntimeError, match="source stream failed"):
                service.store_stream(
                    db=session,
                    user_id=user_id,
                    filename="failed.bin",
                    content_type="application/octet-stream",
                    kind="binary",
                    source="test",
                    stream=broken_stream(),
                )
            session.commit()
            workspace_id = workspace.id

        with factory() as verification:
            persisted = verification.get(WorkspaceState, workspace_id)
            assert persisted is not None
            assert persisted.active_scenario == "prior-write"
            assert (
                verification.scalar(
                    select(Asset).where(
                        Asset.user_id == user_id,
                        Asset.filename == "failed.bin",
                    )
                )
                is None
            )
    finally:
        with factory.begin() as cleanup:
            cleanup.execute(delete(Asset).where(Asset.user_id == user_id))
            cleanup.execute(
                delete(WorkspaceState).where(WorkspaceState.user_id == user_id)
            )
