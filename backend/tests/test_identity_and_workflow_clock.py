from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.identity import AuthIdentityError, auth_user_id
from app.db.session import Base
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.services.workflow_task_repository import WorkflowTaskRepository


def test_auth_user_id_uses_only_stable_non_empty_sub() -> None:
    assert auth_user_id({"sub": "stable-1", "username": "old-name"}) == "stable-1"
    assert auth_user_id({"sub": "stable-1", "username": "new-name"}) == "stable-1"
    with pytest.raises(AuthIdentityError):
        auth_user_id({"username": "display-only"})
    with pytest.raises(AuthIdentityError):
        auth_user_id({"sub": "  ", "username": "display-only"})


def test_repository_lease_uses_database_clock_not_caller_clock(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = IndustrialDesignWorkflowRequest.model_validate(
        {"inputType": "text", "text": "clock"}
    )
    db_now = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        repository = WorkflowTaskRepository(session)
        repository.create(
            "owner",
            request,
            {
                "taskId": "clock-task",
                "status": "pending",
                "progress": 0,
                "currentStep": "pending",
            },
        )
        monkeypatch.setattr(repository, "_database_now", lambda: db_now)
        leased = repository.acquire_lease(
            "clock-task",
            "worker-a",
            db_now + timedelta(hours=8),
            60,
        )
        session.commit()

    assert leased is not None
    assert leased["leaseExpiresAt"] == (db_now + timedelta(seconds=60)).isoformat()
    engine.dispose()


def test_recovery_uses_database_clock_despite_opposite_application_skew(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = IndustrialDesignWorkflowRequest.model_validate(
        {"inputType": "text", "text": "recover clock"}
    )
    db_now = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        repository = WorkflowTaskRepository(session)
        repository.create(
            "owner",
            request,
            {
                "taskId": "recover-clock",
                "status": "pending",
                "progress": 0,
                "currentStep": "pending",
            },
        )
        monkeypatch.setattr(repository, "_database_now", lambda: db_now)
        repository.acquire_lease(
            "recover-clock",
            "worker-a",
            db_now - timedelta(hours=8),
            60,
        )
        session.commit()
    with Session(engine, expire_on_commit=False) as session:
        repository = WorkflowTaskRepository(session)
        monkeypatch.setattr(
            repository,
            "_database_now",
            lambda: db_now + timedelta(seconds=61),
        )
        recovered = repository.recover_expired(db_now - timedelta(hours=8))
        session.commit()

    assert [task["taskId"] for task in recovered] == ["recover-clock"]
    engine.dispose()


def test_postgres_lease_sql_uses_inline_database_clock_expressions() -> None:
    acquire_source = inspect.getsource(WorkflowTaskRepository.acquire_lease)
    renew_source = inspect.getsource(WorkflowTaskRepository.renew_lease)
    recover_source = inspect.getsource(
        WorkflowTaskRepository._recover_expired_locked
    )
    assert "func.clock_timestamp()" in acquire_source
    assert "INTERVAL '1 second'" in acquire_source
    assert "func.clock_timestamp()" in renew_source
    assert "INTERVAL '1 second'" in renew_source
    assert "func.clock_timestamp()" in recover_source
    assert "skip_locked=is_postgres" in recover_source
