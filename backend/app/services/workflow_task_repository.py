"""工业设计工作流任务的事务仓库。"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import cast

from sqlalchemy import Select, func, literal, or_, select, text, update
from sqlalchemy.orm import Session

from app.models.persistence import WorkflowTask, WorkflowTaskEvent
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest

TERMINAL_STATUSES = frozenset({"completed", "failed"})
# SQLite 仅支持单进程测试/开发；跨进程并发由生产 PostgreSQL 行锁保障。
_SQLITE_TASK_LOCKS = tuple(RLock() for _ in range(256))
_SQLITE_RECOVERY_LOCK = RLock()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value:
        return None
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _mapping(value: object) -> dict[str, object]:
    return deepcopy(cast(dict[str, object], value)) if isinstance(value, dict) else {}


def _diagnostics(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        deepcopy(cast(dict[str, object], item))
        for item in value
        if isinstance(item, dict)
    ]


class WorkflowTaskRepository:
    """在调用方事务内读写任务；本类从不 commit。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _database_now(self) -> datetime:
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            value = self._session.scalar(select(func.clock_timestamp()))
        elif bind.dialect.name == "sqlite":
            raw_value = self._session.scalar(
                select(func.strftime("%Y-%m-%d %H:%M:%f", "now"))
            )
            if not isinstance(raw_value, str):
                raise RuntimeError("SQLite 未返回有效当前时间")
            value = datetime.fromisoformat(raw_value).replace(tzinfo=timezone.utc)
        else:
            value = self._session.scalar(select(func.current_timestamp()))
        if not isinstance(value, datetime):
            raise RuntimeError("数据库未返回有效当前时间")
        return _utc(value)

    def _task_lock(self, task_id: str) -> object:
        if self._session.get_bind().dialect.name != "sqlite":
            return nullcontext()
        return _SQLITE_TASK_LOCKS[hash(task_id) % len(_SQLITE_TASK_LOCKS)]

    def begin_write_transaction(self) -> None:
        """SQLite 在读取写入目标前获取数据库写锁，并由调用方 commit/rollback 释放。"""
        if self._session.get_bind().dialect.name != "sqlite":
            return
        current = self._session.get_transaction()
        stored_key = self._session.info.get("workflow_immediate_transaction")
        if (
            stored_key is not None
            and current is not None
            and stored_key == id(current)
        ):
            return
        connection = self._session.connection()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        self._session.info["workflow_immediate_transaction"] = id(
            self._session.get_transaction()
        )

    def create(
        self,
        user_id: str,
        request: IndustrialDesignWorkflowRequest,
        snapshot: dict[str, object],
    ) -> dict[str, object]:
        self.begin_write_transaction()
        task_id = str(snapshot["taskId"])
        status = str(snapshot.get("status") or "pending")
        progress = int(snapshot.get("progress") or 0)
        current_step = str(snapshot.get("currentStep") or "")
        now = _parse_datetime(snapshot.get("createdAt")) or datetime.now(timezone.utc)
        task = WorkflowTask(
            id=task_id,
            user_id=user_id,
            project_id=_optional_string(snapshot.get("projectId")),
            version_id=_optional_string(snapshot.get("versionId")),
            status=status,
            progress=progress,
            current_step=current_step,
            input_payload=deepcopy(request.model_dump(by_alias=True)),
            design_spec=_mapping(snapshot.get("designSpec")),
            outputs=_mapping(snapshot.get("outputs")),
            diagnostics=_diagnostics(snapshot.get("diagnostics")),
            error_code=_optional_string(snapshot.get("errorCode")),
            error_message=_optional_string(snapshot.get("errorMessage")),
            recoverable=bool(snapshot.get("recoverable", True)),
            created_at=now,
            updated_at=_parse_datetime(snapshot.get("updatedAt")) or now,
            completed_at=now if status in TERMINAL_STATUSES else None,
        )
        self._session.add(task)
        self._session.flush()
        self._append_event(
            task,
            sequence=1,
            event_type="created",
            message=current_step or "工作流任务已创建",
            event_data={"snapshot": deepcopy(snapshot)},
        )
        self._session.flush()
        return self._snapshot(task)

    def get(self, task_id: str, user_id: str) -> dict[str, object] | None:
        task = self._session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.user_id == user_id,
            )
        )
        return self._snapshot(task) if task is not None else None

    def get_internal(self, task_id: str) -> dict[str, object] | None:
        task = self._session.get(WorkflowTask, task_id)
        return self._snapshot(task) if task is not None else None

    def update_and_append_event(
        self,
        task_id: str,
        user_id: str | None = None,
        *,
        status: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        design_spec: dict[str, object] | None = None,
        outputs: dict[str, object] | None = None,
        diagnostics: list[dict[str, object]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        recoverable: bool | None = None,
        lease_owner: str | None = None,
        event_type: str = "updated",
        message: str = "",
        event_data: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        self.begin_write_transaction()
        with self._task_lock(task_id):
            return self._update_and_append_event_locked(
                task_id,
                user_id,
                status=status,
                progress=progress,
                current_step=current_step,
                design_spec=design_spec,
                outputs=outputs,
                diagnostics=diagnostics,
                error_code=error_code,
                error_message=error_message,
                recoverable=recoverable,
                lease_owner=lease_owner,
                event_type=event_type,
                message=message,
                event_data=event_data,
            )

    def _update_and_append_event_locked(
        self,
        task_id: str,
        user_id: str | None = None,
        *,
        status: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        design_spec: dict[str, object] | None = None,
        outputs: dict[str, object] | None = None,
        diagnostics: list[dict[str, object]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        recoverable: bool | None = None,
        lease_owner: str | None = None,
        event_type: str = "updated",
        message: str = "",
        event_data: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        conditions = [WorkflowTask.id == task_id]
        if user_id is not None:
            conditions.append(WorkflowTask.user_id == user_id)
        if lease_owner is not None:
            conditions.append(WorkflowTask.lease_owner == lease_owner)
            if self._session.get_bind().dialect.name == "postgresql":
                conditions.append(
                    WorkflowTask.lease_expires_at > func.clock_timestamp()
                )
            else:
                conditions.append(
                    WorkflowTask.lease_expires_at > self._database_now()
                )
        task = self._session.scalar(
            select(WorkflowTask).where(*conditions).with_for_update()
        )
        if task is None:
            return None
        now = self._database_now()
        if (
            lease_owner is not None
            and (
                task.lease_expires_at is None
                or _utc(task.lease_expires_at) <= now
            )
        ):
            return None
        if status is not None:
            task.status = status
        if progress is not None:
            task.progress = progress
        if current_step is not None:
            task.current_step = current_step
        if design_spec is not None:
            task.design_spec = deepcopy(design_spec)
        if outputs is not None:
            task.outputs = deepcopy(outputs)
        if diagnostics is not None:
            task.diagnostics = deepcopy(diagnostics)
        if error_code is not None:
            task.error_code = error_code
        if error_message is not None:
            task.error_message = error_message
        if recoverable is not None:
            task.recoverable = recoverable
        task.updated_at = now
        if status in TERMINAL_STATUSES:
            task.completed_at = now
        next_sequence = int(
            self._session.scalar(
                select(func.coalesce(func.max(WorkflowTaskEvent.sequence), 0)).where(
                    WorkflowTaskEvent.task_id == task_id
                )
            )
            or 0
        ) + 1
        self._append_event(
            task,
            sequence=next_sequence,
            event_type=event_type,
            message=message or task.current_step,
            event_data=event_data or {},
        )
        self._session.flush()
        return self._snapshot(task)

    def acquire_lease(
        self,
        task_id: str,
        owner_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> dict[str, object] | None:
        del now
        self.begin_write_transaction()
        is_postgres = self._session.get_bind().dialect.name == "postgresql"
        if is_postgres:
            db_now: object = func.clock_timestamp()
            expiry: object = (
                func.clock_timestamp()
                + literal(lease_seconds) * text("INTERVAL '1 second'")
            )
        else:
            sqlite_now = self._database_now()
            db_now = sqlite_now
            expiry = sqlite_now + timedelta(seconds=lease_seconds)
        result = self._session.execute(
            update(WorkflowTask)
            .where(
                WorkflowTask.id == task_id,
                WorkflowTask.status.in_(("pending", "running")),
                WorkflowTask.recoverable.is_(True),
                or_(
                    WorkflowTask.lease_owner.is_(None),
                    WorkflowTask.lease_expires_at.is_(None),
                    WorkflowTask.lease_expires_at <= db_now,
                ),
            )
            .values(
                status="running",
                lease_owner=owner_id,
                lease_expires_at=expiry,
                started_at=func.coalesce(WorkflowTask.started_at, db_now),
                updated_at=db_now,
            )
        )
        if result.rowcount != 1:
            return None
        self._session.flush()
        task = self._session.get(WorkflowTask, task_id)
        return self._snapshot(task) if task is not None else None

    def renew_lease(
        self,
        task_id: str,
        owner_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> bool:
        del now
        self.begin_write_transaction()
        is_postgres = self._session.get_bind().dialect.name == "postgresql"
        if is_postgres:
            db_now: object = func.clock_timestamp()
            expiry: object = (
                func.clock_timestamp()
                + literal(lease_seconds) * text("INTERVAL '1 second'")
            )
        else:
            sqlite_now = self._database_now()
            db_now = sqlite_now
            expiry = sqlite_now + timedelta(seconds=lease_seconds)
        result = self._session.execute(
            update(WorkflowTask)
            .where(
                WorkflowTask.id == task_id,
                WorkflowTask.lease_owner == owner_id,
                WorkflowTask.status.not_in(TERMINAL_STATUSES),
                WorkflowTask.lease_expires_at > db_now,
            )
            .values(
                lease_expires_at=expiry,
                updated_at=db_now,
            )
        )
        return result.rowcount == 1

    def release_lease(self, task_id: str, owner_id: str) -> bool:
        self.begin_write_transaction()
        result = self._session.execute(
            update(WorkflowTask)
            .where(
                WorkflowTask.id == task_id,
                WorkflowTask.lease_owner == owner_id,
            )
            .values(lease_owner=None, lease_expires_at=None)
        )
        return result.rowcount == 1

    def list_recoverable(self) -> list[dict[str, object]]:
        tasks = self._session.scalars(
            select(WorkflowTask)
            .where(
                WorkflowTask.status == "pending",
                WorkflowTask.recoverable.is_(True),
            )
            .order_by(WorkflowTask.created_at, WorkflowTask.id)
        ).all()
        return [self._snapshot(task) for task in tasks]

    def recover_expired(self, now: datetime) -> list[dict[str, object]]:
        del now
        self.begin_write_transaction()
        with _SQLITE_RECOVERY_LOCK if self._session.get_bind().dialect.name == "sqlite" else nullcontext():
            return self._recover_expired_locked()

    def _recover_expired_locked(self) -> list[dict[str, object]]:
        is_postgres = self._session.get_bind().dialect.name == "postgresql"
        comparison_now: object = (
            func.clock_timestamp() if is_postgres else self._database_now()
        )
        expired = self._session.scalars(
            select(WorkflowTask)
            .where(
                WorkflowTask.status == "running",
                WorkflowTask.lease_expires_at.is_not(None),
                WorkflowTask.lease_expires_at <= comparison_now,
            )
            .with_for_update(skip_locked=is_postgres)
            .order_by(WorkflowTask.created_at, WorkflowTask.id)
        ).all()
        now = self._database_now()
        for task in expired:
            task.lease_owner = None
            task.lease_expires_at = None
            if task.recoverable:
                task.status = "pending"
                task.attempt += 1
                event_type = "recovered"
                message = "工作流租约已过期，任务已重新排队"
            else:
                task.status = "failed"
                task.error_code = "WORKFLOW_LEASE_EXPIRED"
                task.error_message = "工作流执行租约已过期且任务不可恢复"
                task.completed_at = now
                event_type = "failed"
                message = task.error_message
            task.updated_at = now
            sequence = int(
                self._session.scalar(
                    select(func.coalesce(func.max(WorkflowTaskEvent.sequence), 0)).where(
                        WorkflowTaskEvent.task_id == task.id
                    )
                )
                or 0
            ) + 1
            self._append_event(
                task,
                sequence=sequence,
                event_type=event_type,
                message=message,
                event_data={"reason": "lease_expired", "attempt": task.attempt},
            )
        self._session.flush()
        return self.list_recoverable()

    def mark_history_persisted(self, task_id: str, user_id: str) -> bool:
        self.begin_write_transaction()
        result = self._session.execute(
            update(WorkflowTask)
            .where(
                WorkflowTask.id == task_id,
                WorkflowTask.user_id == user_id,
                WorkflowTask.status.in_(TERMINAL_STATUSES),
                WorkflowTask.history_persisted.is_(False),
            )
            .values(history_persisted=True, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount == 1

    def list_unpersisted_terminal(self) -> list[dict[str, object]]:
        tasks = self._session.scalars(
            select(WorkflowTask)
            .where(
                WorkflowTask.status.in_(TERMINAL_STATUSES),
                WorkflowTask.history_persisted.is_(False),
            )
            .order_by(WorkflowTask.completed_at, WorkflowTask.id)
        ).all()
        return [self._snapshot(task) for task in tasks]

    def get_terminal_for_history(
        self,
        task_id: str,
    ) -> dict[str, object] | None:
        self.begin_write_transaction()
        task = self._session.scalar(
            select(WorkflowTask)
            .where(
                WorkflowTask.id == task_id,
                WorkflowTask.status.in_(TERMINAL_STATUSES),
                WorkflowTask.history_persisted.is_(False),
            )
            .with_for_update()
        )
        return self._snapshot(task) if task is not None else None

    def _append_event(
        self,
        task: WorkflowTask,
        *,
        sequence: int,
        event_type: str,
        message: str,
        event_data: dict[str, object],
    ) -> None:
        self._session.add(
            WorkflowTaskEvent(
                task_id=task.id,
                sequence=sequence,
                event_type=event_type,
                status=task.status,
                progress=task.progress,
                message=message,
                event_data=deepcopy(event_data),
            )
        )

    @staticmethod
    def _snapshot(task: WorkflowTask) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "taskId": task.id,
            "userId": task.user_id,
            "status": task.status,
            "progress": task.progress,
            "currentStep": task.current_step,
            "sourceMode": str(task.input_payload.get("inputType") or ""),
            "projectId": task.project_id,
            "versionId": task.version_id,
            "inputPayload": deepcopy(task.input_payload),
            "designSpec": deepcopy(task.design_spec),
            "outputs": deepcopy(task.outputs),
            "diagnostics": deepcopy(task.diagnostics),
            "errorCode": task.error_code,
            "errorMessage": task.error_message,
            "attempt": task.attempt,
            "recoverable": task.recoverable,
            "historyPersisted": task.history_persisted,
            "leaseOwner": task.lease_owner,
            "leaseExpiresAt": (
                _utc(task.lease_expires_at).isoformat()
                if task.lease_expires_at is not None
                else None
            ),
            "createdAt": _utc(task.created_at).isoformat(),
            "updatedAt": _utc(task.updated_at).isoformat(),
        }
        return snapshot


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
