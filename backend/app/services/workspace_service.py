"""用户工作区状态服务。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cocreation_history import (
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
)
from app.models.persistence import Asset, WorkspaceState
from app.schemas.workspace import WorkspaceUpdate


class WorkspaceConflict(Exception):
    def __init__(self, latest: WorkspaceState) -> None:
        super().__init__("workspace version conflict")
        self.latest = latest


class WorkspaceReferenceError(Exception):
    """工作区引用不存在、不可用或不属于当前用户。"""


class WorkspaceReferenceConflict(Exception):
    """外部版本标识在当前用户范围内不唯一。"""


class WorkspaceService:
    @staticmethod
    def get_or_default(db: Session, user_id: str) -> WorkspaceState:
        state = db.scalar(select(WorkspaceState).where(WorkspaceState.user_id == user_id))
        if state is not None:
            return state
        return WorkspaceState(
            user_id=user_id,
            active_scenario="",
            active_workflow_stage="",
            active_step_index=0,
            view_mode="",
            scene_mode="",
            selected_industry="",
            generation_prompt="",
            state_data={},
            version=0,
        )

    def update(
        self,
        db: Session,
        user_id: str,
        payload: WorkspaceUpdate,
    ) -> WorkspaceState:
        referenced_version = self._validate_references(db, user_id, payload)
        values = payload.model_dump(exclude={"version"})
        values["selected_reference_version_history_id"] = (
            referenced_version.id if referenced_version is not None else None
        )
        now = datetime.now(timezone.utc)
        current = db.scalar(
            select(WorkspaceState).where(WorkspaceState.user_id == user_id)
        )
        if current is None:
            if payload.version != 0:
                raise WorkspaceConflict(self.get_or_default(db, user_id))
            created = WorkspaceState(
                user_id=user_id,
                **values,
                version=1,
                created_at=now,
                updated_at=now,
            )
            try:
                self._ensure_database_transaction(db)
                with db.begin_nested():
                    db.add(created)
                    db.flush()
            except IntegrityError:
                db.expire_all()
                latest = db.scalar(
                    select(WorkspaceState).where(WorkspaceState.user_id == user_id)
                )
                if latest is None:
                    raise
                raise WorkspaceConflict(latest) from None
            return created

        statement = (
            update(WorkspaceState)
            .where(
                WorkspaceState.user_id == user_id,
                WorkspaceState.version == payload.version,
            )
            .values(**values, version=payload.version + 1, updated_at=now)
        )
        result = db.execute(statement)
        if result.rowcount != 1:
            db.expire_all()
            latest = db.scalar(
                select(WorkspaceState).where(WorkspaceState.user_id == user_id)
            )
            if latest is None:
                latest = self.get_or_default(db, user_id)
            raise WorkspaceConflict(latest)
        db.flush()
        updated_state = db.scalar(
            select(WorkspaceState).where(WorkspaceState.user_id == user_id)
        )
        if updated_state is None:
            raise RuntimeError("workspace disappeared after update")
        db.refresh(updated_state)
        return updated_state

    def set_reference_version(
        self,
        db: Session,
        user_id: str,
        version_id: str,
        project_id: str | None = None,
    ) -> WorkspaceState:
        statement = (
            select(CocreationProjectVersionHistory)
            .join(CocreationProjectHistory)
            .where(
                CocreationProjectVersionHistory.user_id == user_id,
                CocreationProjectVersionHistory.version_id == version_id,
            )
        )
        if project_id is not None:
            statement = statement.where(
                CocreationProjectHistory.user_id == user_id,
                CocreationProjectHistory.project_id == project_id,
            )
        matches = list(db.scalars(statement.limit(2)))
        if not matches:
            raise WorkspaceReferenceError("selected version is unavailable")
        if len(matches) > 1:
            raise WorkspaceReferenceConflict("versionId 对当前用户不唯一，请提供 projectId")
        owned_version = matches[0]
        owned_project = db.get(
            CocreationProjectHistory,
            owned_version.project_history_id,
        )
        if owned_project is None:
            raise WorkspaceReferenceError("selected project is unavailable")

        now = datetime.now(timezone.utc)
        result = db.execute(
            update(WorkspaceState)
            .where(WorkspaceState.user_id == user_id)
            .values(
                selected_project_id=owned_project.project_id,
                selected_reference_version_id=version_id,
                selected_reference_version_history_id=owned_version.id,
                version=WorkspaceState.version + 1,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            self._ensure_database_transaction(db)
            try:
                with db.begin_nested():
                    db.add(
                        WorkspaceState(
                            user_id=user_id,
                            selected_project_id=owned_project.project_id,
                            selected_reference_version_id=version_id,
                            selected_reference_version_history_id=owned_version.id,
                            version=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    db.flush()
            except IntegrityError:
                retry = db.execute(
                    update(WorkspaceState)
                    .where(WorkspaceState.user_id == user_id)
                    .values(
                        selected_project_id=owned_project.project_id,
                        selected_reference_version_id=version_id,
                        selected_reference_version_history_id=owned_version.id,
                        version=WorkspaceState.version + 1,
                        updated_at=now,
                    )
                )
                if retry.rowcount != 1:
                    raise
        state = db.scalar(
            select(WorkspaceState).where(WorkspaceState.user_id == user_id)
        )
        if state is None:
            raise RuntimeError("workspace disappeared after reference update")
        db.refresh(state)
        return state

    @staticmethod
    def _validate_references(
        db: Session,
        user_id: str,
        payload: WorkspaceUpdate,
    ) -> CocreationProjectVersionHistory | None:
        project: CocreationProjectHistory | None = None
        if payload.selected_project_id is not None:
            project = db.scalar(
                select(CocreationProjectHistory).where(
                    CocreationProjectHistory.project_id == payload.selected_project_id,
                    CocreationProjectHistory.user_id == user_id,
                )
            )
            if project is None:
                raise WorkspaceReferenceError("selected project is unavailable")

        referenced_version: CocreationProjectVersionHistory | None = None
        if payload.selected_reference_version_id is not None:
            version_query = select(CocreationProjectVersionHistory).where(
                CocreationProjectVersionHistory.version_id
                == payload.selected_reference_version_id,
                CocreationProjectVersionHistory.user_id == user_id,
            )
            if project is not None:
                version_query = version_query.where(
                    CocreationProjectVersionHistory.project_history_id == project.id
                )
            matches = list(db.scalars(version_query.limit(2)))
            if not matches:
                raise WorkspaceReferenceError("selected version is unavailable")
            if len(matches) > 1:
                raise WorkspaceReferenceConflict(
                    "selected version is ambiguous without selected project"
                )
            referenced_version = matches[0]

        if payload.selected_reference_asset_id is not None:
            asset = db.get(Asset, payload.selected_reference_asset_id)
            if (
                asset is None
                or asset.user_id != user_id
                or asset.status != "available"
            ):
                raise WorkspaceReferenceError("selected asset is unavailable")
        return referenced_version

    @staticmethod
    def _ensure_database_transaction(db: Session) -> None:
        """避免 SQLite 旧事务模式将首次插入的 SAVEPOINT 提前提交。"""
        connection = db.connection()
        driver_connection = connection.connection.driver_connection
        if (
            isinstance(driver_connection, sqlite3.Connection)
            and not driver_connection.in_transaction
        ):
            connection.exec_driver_sql("BEGIN")


workspace_service = WorkspaceService()
