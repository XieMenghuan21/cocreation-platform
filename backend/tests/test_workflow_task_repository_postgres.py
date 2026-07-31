from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier, Lock, Thread
from time import sleep
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.persistence import WorkflowTask, WorkflowTaskEvent
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.schemas.cocreation_history import ProjectRecordPayload, VersionSnapshotPayload
from app.models.cocreation_history import (
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
)
from app.services.cocreation_history_service import CocreationHistoryService
from app.services.industrial_design_workflow_service import IndustrialDesignWorkflowService
from app.services.workflow_task_repository import WorkflowTaskRepository


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


def _request() -> IndustrialDesignWorkflowRequest:
    return IndustrialDesignWorkflowRequest.model_validate(
        {"inputType": "text", "text": "postgres concurrency"}
    )


def _snapshot(task_id: str) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "taskId": task_id,
        "status": "pending",
        "progress": 0,
        "currentStep": "pending",
        "outputs": {},
        "diagnostics": [],
        "designSpec": {},
        "createdAt": now,
        "updatedAt": now,
    }


@pytest.mark.postgres_integration
def test_postgres_concurrent_events_have_unique_monotonic_sequences(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    task_id = f"pg-events-{uuid4()}"
    with factory.begin() as session:
        WorkflowTaskRepository(session).create("pg-user", _request(), _snapshot(task_id))
    barrier = Barrier(3)
    failures: list[BaseException] = []
    lock = Lock()

    def update_task(progress: int) -> None:
        try:
            with factory.begin() as session:
                barrier.wait()
                WorkflowTaskRepository(session).update_and_append_event(
                    task_id,
                    "pg-user",
                    progress=progress,
                    event_type="progress",
                    message=str(progress),
                )
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [Thread(target=update_task, args=(20,)), Thread(target=update_task, args=(40,))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    try:
        assert failures == []
        with factory() as session:
            sequences = session.scalars(
                select(WorkflowTaskEvent.sequence)
                .where(WorkflowTaskEvent.task_id == task_id)
                .order_by(WorkflowTaskEvent.sequence)
            ).all()
            assert sequences == [1, 2, 3]
    finally:
        with factory.begin() as session:
            session.execute(delete(WorkflowTask).where(WorkflowTask.id == task_id))


@pytest.mark.postgres_integration
def test_postgres_concurrent_lease_acquisition_has_one_winner(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    task_id = f"pg-lease-{uuid4()}"
    with factory.begin() as session:
        WorkflowTaskRepository(session).create("pg-user", _request(), _snapshot(task_id))
    barrier = Barrier(3)
    outcomes: list[bool] = []
    lock = Lock()

    def acquire(owner: str) -> None:
        with factory.begin() as session:
            barrier.wait()
            won = (
                WorkflowTaskRepository(session).acquire_lease(
                    task_id,
                    owner,
                    datetime.now(timezone.utc),
                    60,
                )
                is not None
            )
        with lock:
            outcomes.append(won)

    threads = [Thread(target=acquire, args=("worker-a",)), Thread(target=acquire, args=("worker-b",))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    try:
        assert sorted(outcomes) == [False, True]
    finally:
        with factory.begin() as session:
            session.execute(delete(WorkflowTask).where(WorkflowTask.id == task_id))


@pytest.mark.postgres_integration
def test_postgres_acquire_clock_is_evaluated_after_row_lock_wait(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    task_id = f"pg-clock-wait-{uuid4()}"
    with factory.begin() as session:
        WorkflowTaskRepository(session).create("pg-user", _request(), _snapshot(task_id))
    outcome: list[dict[str, object] | None] = []
    with factory() as lock_session:
        lock_session.execute(
            select(WorkflowTask)
            .where(WorkflowTask.id == task_id)
            .with_for_update()
        )

        def acquire_after_wait() -> None:
            with factory.begin() as session:
                outcome.append(
                    WorkflowTaskRepository(session).acquire_lease(
                        task_id,
                        "waiter",
                        datetime(2000, 1, 1, tzinfo=timezone.utc),
                        1,
                    )
                )

        thread = Thread(target=acquire_after_wait)
        thread.start()
        sleep(1.2)
        lock_session.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
    try:
        assert outcome and outcome[0] is not None
        with factory() as session:
            db_now = session.scalar(select(func.clock_timestamp()))
            task = session.get(WorkflowTask, task_id)
            assert task is not None and db_now is not None
            assert task.lease_expires_at is not None
            assert task.lease_expires_at > db_now
    finally:
        with factory.begin() as session:
            session.execute(delete(WorkflowTask).where(WorkflowTask.id == task_id))


@pytest.mark.postgres_integration
def test_postgres_renew_clock_is_evaluated_after_row_lock_wait(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    task_id = f"pg-renew-wait-{uuid4()}"
    with factory.begin() as session:
        repository = WorkflowTaskRepository(session)
        repository.create("pg-user", _request(), _snapshot(task_id))
        assert repository.acquire_lease(
            task_id,
            "owner",
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            5,
        ) is not None
    outcome: list[bool] = []
    with factory() as lock_session:
        lock_session.execute(
            select(WorkflowTask)
            .where(WorkflowTask.id == task_id)
            .with_for_update()
        )

        def renew_after_wait() -> None:
            with factory.begin() as session:
                outcome.append(
                    WorkflowTaskRepository(session).renew_lease(
                        task_id,
                        "owner",
                        datetime(2000, 1, 1, tzinfo=timezone.utc),
                        1,
                    )
                )

        thread = Thread(target=renew_after_wait)
        thread.start()
        sleep(1.2)
        lock_session.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
    try:
        assert outcome == [True]
        with factory() as session:
            db_now = session.scalar(select(func.clock_timestamp()))
            task = session.get(WorkflowTask, task_id)
            assert task is not None and db_now is not None
            assert task.lease_expires_at is not None
            assert task.lease_expires_at > db_now
    finally:
        with factory.begin() as session:
            session.execute(delete(WorkflowTask).where(WorkflowTask.id == task_id))


@pytest.mark.postgres_integration
def test_postgres_concurrent_history_compensation_writes_once(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    task_id = f"pg-history-{uuid4()}"
    snapshot = _snapshot(task_id)
    snapshot["status"] = "completed"
    snapshot["progress"] = 100
    with factory.begin() as session:
        WorkflowTaskRepository(session).create("pg-user", _request(), snapshot)

    class CountingHistory:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = Lock()

        @contextmanager
        def transaction_lock(
            self,
            db: Session,
            *,
            user_id: str,
            project_id: str,
        ) -> Iterator[None]:
            del db, user_id, project_id
            yield

        def upsert_project_with_version_in_transaction(
            self,
            db: Session,
            *,
            auth_user: dict[str, object],
            project_payload: ProjectRecordPayload,
            version_payload: VersionSnapshotPayload,
        ) -> dict[str, str]:
            del db, auth_user, project_payload
            with self.lock:
                self.calls += 1
            sleep(0.1)
            return {"projectId": "", "versionId": version_payload.id}

    @contextmanager
    def db_context() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    history = CountingHistory()
    service = IndustrialDesignWorkflowService(
        history_service=history,
        db_context_factory=db_context,
    )
    barrier = Barrier(3)
    threads = [
        Thread(
            target=lambda: (
                barrier.wait(),
                service._persist_terminal_history_sync(task_id, _request(), None),
            )
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    try:
        assert history.calls == 1
    finally:
        with factory.begin() as session:
            session.execute(delete(WorkflowTask).where(WorkflowTask.id == task_id))


@pytest.mark.postgres_integration
def test_postgres_real_history_concurrent_first_upsert_is_unique(
    postgres_engine: Engine,
) -> None:
    factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    user_id = f"pg-history-owner-{uuid4()}"
    project_id = f"pg-project-{uuid4()}"
    project = ProjectRecordPayload(
        id=project_id,
        name="并发项目",
        createdAt="2026-07-24T00:00:00Z",
        updatedAt="2026-07-24T00:00:00Z",
    )
    version = VersionSnapshotPayload(
        id="v1",
        label="V1",
        status="completed",
        note="",
        projectId=project_id,
    )
    service = CocreationHistoryService()
    barrier = Barrier(3)
    failures: list[BaseException] = []
    failure_lock = Lock()

    def write(username: str) -> None:
        try:
            with factory() as session:
                barrier.wait()
                service.upsert_project_with_version(
                    session,
                    auth_user={"sub": user_id, "username": username},
                    project_payload=project,
                    version_payload=version,
                )
        except BaseException as exc:
            with failure_lock:
                failures.append(exc)

    threads = [Thread(target=write, args=("old",)), Thread(target=write, args=("new",))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    try:
        assert failures == []
        with factory() as session:
            projects = session.scalar(
                select(func.count(CocreationProjectHistory.id)).where(
                    CocreationProjectHistory.user_id == user_id,
                    CocreationProjectHistory.project_id == project_id,
                )
            )
            versions = session.scalar(
                select(func.count(CocreationProjectVersionHistory.id)).where(
                    CocreationProjectVersionHistory.user_id == user_id,
                )
            )
            assert projects == 1
            assert versions == 1
    finally:
        with factory.begin() as session:
            session.execute(
                delete(CocreationProjectVersionHistory).where(
                    CocreationProjectVersionHistory.user_id == user_id
                )
            )
            session.execute(
                delete(CocreationProjectHistory).where(
                    CocreationProjectHistory.user_id == user_id
                )
            )
