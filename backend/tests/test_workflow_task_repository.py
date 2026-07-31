from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread
from time import sleep

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.db.session import Base
from app.models.persistence import WorkflowTask, WorkflowTaskEvent
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.services.workflow_task_repository import WorkflowTaskRepository


def _request() -> IndustrialDesignWorkflowRequest:
    return IndustrialDesignWorkflowRequest.model_validate(
        {
            "inputType": "text",
            "text": "设计伺服底座",
            "projectName": "伺服底座",
            "context": {"nested": {"revision": 2}},
        }
    )


def _snapshot(task_id: str = "task-1", status: str = "pending") -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "taskId": task_id,
        "status": status,
        "progress": 5,
        "currentStep": "等待处理",
        "sourceMode": "text",
        "projectId": "project-1",
        "versionId": "version-1",
        "designSpec": {"width": 100},
        "outputs": {},
        "diagnostics": [],
        "createdAt": now,
        "updatedAt": now,
    }


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_create_persists_request_snapshot_and_first_event_across_repository_rebuild(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        created = repository.create("user-a", _request(), _snapshot())
        session.commit()

    with session_factory() as session:
        rebuilt = WorkflowTaskRepository(session)
        loaded = rebuilt.get("task-1", "user-a")
        events = session.scalars(
            select(WorkflowTaskEvent)
            .where(WorkflowTaskEvent.task_id == "task-1")
            .order_by(WorkflowTaskEvent.sequence)
        ).all()

    assert created["taskId"] == "task-1"
    assert loaded is not None
    assert loaded["designSpec"] == {"width": 100}
    assert loaded["inputPayload"] == _request().model_dump(by_alias=True)
    assert [(event.sequence, event.event_type) for event in events] == [(1, "created")]


def test_get_is_isolated_by_user(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository.create("user-a", _request(), _snapshot())
        session.commit()
        assert repository.get("task-1", "user-b") is None


def test_update_and_event_are_atomic_on_rollback(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository.create("user-a", _request(), _snapshot())
        session.commit()
        repository.update_and_append_event(
            "task-1",
            "user-a",
            status="running",
            progress=30,
            current_step="生成中",
            outputs={"model": "new"},
            event_type="progress",
            message="生成中",
        )
        session.rollback()

    with session_factory() as session:
        task = session.get(WorkflowTask, "task-1")
        events = session.scalars(
            select(WorkflowTaskEvent).where(WorkflowTaskEvent.task_id == "task-1")
        ).all()
        assert task is not None
        assert task.status == "pending"
        assert task.outputs == {}
        assert len(events) == 1


def test_events_receive_monotonic_sequences(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository.create("user-a", _request(), _snapshot())
        repository.update_and_append_event(
            "task-1", "user-a", progress=20, event_type="progress", message="20"
        )
        repository.update_and_append_event(
            "task-1", "user-a", progress=40, event_type="progress", message="40"
        )
        session.commit()
        events = session.scalars(
            select(WorkflowTaskEvent)
            .where(WorkflowTaskEvent.task_id == "task-1")
            .order_by(WorkflowTaskEvent.sequence)
        ).all()
        assert [event.sequence for event in events] == [1, 2, 3]


def test_lease_has_one_winner_and_non_owner_cannot_renew_or_release(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository._database_now = lambda: now  # type: ignore[method-assign]
        repository.create("user-a", _request(), _snapshot())
        session.commit()

    with session_factory() as first_session, session_factory() as second_session:
        first = WorkflowTaskRepository(first_session)
        second = WorkflowTaskRepository(second_session)
        assert first.acquire_lease("task-1", "worker-a", now, 30) is not None
        first_session.commit()
        assert second.acquire_lease("task-1", "worker-b", now, 30) is None
        assert second.renew_lease("task-1", "worker-b", now, 30) is False
        assert second.release_lease("task-1", "worker-b") is False
        second_session.rollback()
        assert first.renew_lease("task-1", "worker-a", now, 30) is True
        assert first.release_lease("task-1", "worker-a") is True


def test_recover_expired_is_idempotent_and_marks_unrecoverable_failed(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository._database_now = lambda: now  # type: ignore[method-assign]
        repository.create("user-a", _request(), _snapshot("recoverable"))
        repository.create("user-a", _request(), _snapshot("fatal"))
        repository.acquire_lease("recoverable", "dead-worker", now - timedelta(minutes=2), 10)
        repository.acquire_lease("fatal", "dead-worker", now - timedelta(minutes=2), 10)
        fatal = session.get(WorkflowTask, "fatal")
        assert fatal is not None
        fatal.recoverable = False
        session.commit()

    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository._database_now = lambda: now + timedelta(seconds=20)  # type: ignore[method-assign]
        recovered = repository.recover_expired(now)
        session.commit()
        repeated = repository.recover_expired(now)
        session.commit()
        recoverable = session.get(WorkflowTask, "recoverable")
        fatal = session.get(WorkflowTask, "fatal")
        recovered_events = session.scalars(
            select(WorkflowTaskEvent).where(
                WorkflowTaskEvent.task_id == "recoverable",
                WorkflowTaskEvent.event_type == "recovered",
            )
        ).all()

    assert [task["taskId"] for task in recovered] == ["recoverable"]
    assert [task["taskId"] for task in repeated] == ["recoverable"]
    assert recoverable is not None and recoverable.status == "pending"
    assert recoverable.attempt == 1
    assert fatal is not None and fatal.status == "failed"
    assert fatal.error_code == "WORKFLOW_LEASE_EXPIRED"
    assert len(recovered_events) == 1


def test_terminal_tasks_do_not_recover_and_history_flag_is_persisted(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        repository.create("user-a", _request(), _snapshot(status="completed"))
        repository.mark_history_persisted("task-1", "user-a")
        session.commit()

    with session_factory() as session:
        repository = WorkflowTaskRepository(session)
        assert repository.recover_expired(datetime.now(timezone.utc)) == []
        loaded = repository.get("task-1", "user-a")
        assert loaded is not None
        assert loaded["historyPersisted"] is True


def test_sqlite_threads_append_unique_event_sequences(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'event-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        WorkflowTaskRepository(session).create("user-a", _request(), _snapshot())
    barrier = Barrier(3)
    failures: list[BaseException] = []
    result_lock = Lock()

    def update(progress: int) -> None:
        try:
            with factory.begin() as session:
                barrier.wait()
                WorkflowTaskRepository(session).update_and_append_event(
                    "task-1",
                    "user-a",
                    progress=progress,
                    message=str(progress),
                )
                sleep(0.1)
        except BaseException as exc:
            with result_lock:
                failures.append(exc)

    threads = [Thread(target=update, args=(20,)), Thread(target=update, args=(40,))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert failures == []
    with factory() as session:
        sequences = session.scalars(
            select(WorkflowTaskEvent.sequence)
            .where(WorkflowTaskEvent.task_id == "task-1")
            .order_by(WorkflowTaskEvent.sequence)
        ).all()
        assert sequences == [1, 2, 3]
    engine.dispose()


def test_sqlite_concurrent_recovery_appends_one_recovered_event(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery-concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        WorkflowTaskRepository(session).create("user-a", _request(), _snapshot())
        task = session.get(WorkflowTask, "task-1")
        assert task is not None
        task.status = "running"
        task.lease_owner = "dead"
        task.lease_expires_at = now - timedelta(seconds=1)
    barrier = Barrier(3)

    def recover() -> None:
        with factory.begin() as session:
            repository = WorkflowTaskRepository(session)
            repository._database_now = lambda: now  # type: ignore[method-assign]
            barrier.wait()
            repository.recover_expired(now + timedelta(hours=8))
            sleep(0.1)

    threads = [Thread(target=recover), Thread(target=recover)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    with factory() as session:
        events = session.scalars(
            select(WorkflowTaskEvent).where(
                WorkflowTaskEvent.task_id == "task-1",
                WorkflowTaskEvent.event_type == "recovered",
            )
        ).all()
        assert len(events) == 1
    engine.dispose()
