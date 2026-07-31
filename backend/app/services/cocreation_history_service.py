"""共创项目历史持久化服务。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from hashlib import sha256
from threading import RLock
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from app.types.json import JSONValue

from app.models.cocreation_history import (
    CocreationAssetLibraryEntry,
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
    CocreationVersionAssetEntry,
)
from app.models.persistence import Asset, WorkspaceState
from app.core.identity import auth_user_id
from app.schemas.cocreation_history import (
    GeneratedAssetPayload,
    ProjectRecordPayload,
    VersionSnapshotPayload,
)

_SQLITE_HISTORY_WRITE_LOCK = RLock()


class HistoryNotFoundError(Exception):
    """项目或版本不存在。"""


class VersionPublicationError(Exception):
    """版本状态或资产不允许发布。"""


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)


class CocreationHistoryService:
    @contextmanager
    def transaction_lock(
        self,
        db: Session,
        *,
        user_id: str,
        project_id: str,
    ) -> Iterator[None]:
        """串行化同一 owner/project 的首次 upsert。"""
        lock_key = f"cocreation-history:v1:{user_id}:{project_id}"
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            digest = sha256(lock_key.encode("utf-8")).digest()
            advisory_key = int.from_bytes(digest[:8], "big", signed=True)
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": advisory_key},
            )
            lock_context = nullcontext()
        else:
            # SQLite 仅用于 dev/test，统一串行化所有历史写以消除多项目 AB/BA 死锁。
            lock_context = _SQLITE_HISTORY_WRITE_LOCK
        with lock_context:
            yield

    def upsert_project_with_version(
        self,
        db: Session,
        *,
        auth_user: dict[str, object],
        project_payload: ProjectRecordPayload,
        version_payload: VersionSnapshotPayload,
    ) -> dict[str, str]:
        user_id = auth_user_id(auth_user)
        with self.transaction_lock(
            db,
            user_id=user_id,
            project_id=project_payload.id,
        ):
            try:
                result = self.upsert_project_with_version_in_transaction(
                    db,
                    auth_user=auth_user,
                    project_payload=project_payload,
                    version_payload=version_payload,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return result

    def upsert_project_with_version_in_transaction(
        self,
        db: Session,
        *,
        auth_user: dict[str, object],
        project_payload: ProjectRecordPayload,
        version_payload: VersionSnapshotPayload,
    ) -> dict[str, str]:
        """在调用方事务内写入历史，只 flush，不提交事务。"""
        user_id = auth_user_id(auth_user)
        project = self._get_or_create_project(db, user_id=user_id, payload=project_payload)
        self._upsert_version(db, user_id=user_id, project=project, payload=version_payload)
        self._refresh_project_summary(db, project)
        db.flush()
        return {"projectId": project.project_id, "versionId": version_payload.id}

    def list_history(
        self,
        db: Session,
        *,
        auth_user: dict[str, object],
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, JSONValue]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        user_id = auth_user_id(auth_user)
        total = db.scalar(
            select(func.count(CocreationProjectHistory.id)).where(
                CocreationProjectHistory.user_id == user_id
            )
        ) or 0
        projects = db.scalars(
            select(CocreationProjectHistory)
            .options(
                selectinload(CocreationProjectHistory.versions).selectinload(
                    CocreationProjectVersionHistory.asset_entries
                )
            )
            .where(CocreationProjectHistory.user_id == user_id)
            .order_by(
                CocreationProjectHistory.updated_at.desc(),
                CocreationProjectHistory.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()

        snapshots: list[dict[str, JSONValue]] = []
        project_records: list[dict[str, JSONValue]] = []
        for project in projects:
            project_records.append(self._serialize_project(project))
            ordered_versions = sorted(
                project.versions,
                key=lambda item: (item.created_at, item.version_number or 0, item.id),
                reverse=True,
            )
            snapshots.extend(self._serialize_version(version) for version in ordered_versions)

        return {
            "projects": project_records,
            "snapshots": snapshots,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def delete_version(
        self,
        db: Session,
        *,
        auth_user: dict[str, object],
        project_id: str,
        version_id: str,
    ) -> bool:
        user_id = auth_user_id(auth_user)
        with self.transaction_lock(
            db,
            user_id=user_id,
            project_id=project_id,
        ):
            try:
                project = db.scalar(
                    select(CocreationProjectHistory).where(
                        CocreationProjectHistory.user_id == user_id,
                        CocreationProjectHistory.project_id == project_id,
                    )
                )
                if project is None:
                    db.rollback()
                    return False

                version = db.scalar(
                    select(CocreationProjectVersionHistory).where(
                        CocreationProjectVersionHistory.user_id == user_id,
                        CocreationProjectVersionHistory.project_history_id == project.id,
                        CocreationProjectVersionHistory.version_id == version_id,
                    )
                )
                if version is None:
                    db.rollback()
                    return False

                db.execute(
                    update(WorkspaceState)
                    .where(
                        WorkspaceState.user_id == user_id,
                        WorkspaceState.selected_reference_version_history_id
                        == version.id,
                    )
                    .values(
                        selected_reference_version_history_id=None,
                        selected_reference_version_id=None,
                    )
                )
                db.delete(version)
                db.flush()

                remaining = db.scalars(
                    select(CocreationProjectVersionHistory).where(
                        CocreationProjectVersionHistory.project_history_id == project.id
                    )
                ).all()
                if not remaining:
                    db.execute(
                        update(WorkspaceState)
                        .where(
                            WorkspaceState.user_id == user_id,
                            WorkspaceState.selected_project_id == project_id,
                        )
                        .values(selected_project_id=None)
                    )
                    db.delete(project)
                else:
                    self._refresh_project_summary(db, project)

                db.commit()
            except Exception:
                db.rollback()
                raise
        return True

    def publish_version(
        self,
        db: Session,
        *,
        auth_user: dict[str, object],
        project_id: str,
        version_id: str,
    ) -> dict[str, JSONValue]:
        user_id = auth_user_id(auth_user)
        with self.transaction_lock(db, user_id=user_id, project_id=project_id):
            try:
                project = db.scalar(
                    select(CocreationProjectHistory).where(
                        CocreationProjectHistory.user_id == user_id,
                        CocreationProjectHistory.project_id == project_id,
                    )
                )
                if project is None:
                    raise HistoryNotFoundError("项目版本不存在")
                version = db.scalar(
                    select(CocreationProjectVersionHistory).where(
                        CocreationProjectVersionHistory.user_id == user_id,
                        CocreationProjectVersionHistory.project_history_id == project.id,
                        CocreationProjectVersionHistory.version_id == version_id,
                    )
                )
                if version is None:
                    raise HistoryNotFoundError("项目版本不存在")
                if version.status not in {"completed", "已完成", "published", "已发布"}:
                    raise VersionPublicationError("只有已完成版本可以发布")

                asset_ids = self._version_asset_ids(version)
                if not asset_ids:
                    raise VersionPublicationError("版本没有可发布的数据库资产")
                assets = db.scalars(
                    select(Asset).where(
                        Asset.id.in_(asset_ids),
                        Asset.user_id == user_id,
                        Asset.status == "available",
                    )
                ).all()
                if {asset.id for asset in assets} != asset_ids:
                    raise VersionPublicationError("版本资产不存在、不可用或不属于当前用户")
                conflicting_asset_id = db.scalar(
                    select(CocreationAssetLibraryEntry.asset_id).where(
                        CocreationAssetLibraryEntry.asset_id.in_(asset_ids),
                        CocreationAssetLibraryEntry.version_history_id != version.id,
                    )
                )
                if conflicting_asset_id is not None:
                    raise VersionPublicationError("资产已关联其他版本，不能重复发布")

                linked_ids = set(
                    db.scalars(
                        select(CocreationAssetLibraryEntry.asset_id).where(
                            CocreationAssetLibraryEntry.version_history_id == version.id
                        )
                    ).all()
                )
                for asset in assets:
                    if asset.id not in linked_ids:
                        db.add(
                            CocreationAssetLibraryEntry(
                                user_id=user_id,
                                version_history_id=version.id,
                                asset_id=asset.id,
                            )
                        )
                version.is_finalized = True
                version.status = "published"
                db.flush()
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise VersionPublicationError(
                    "资产已关联其他版本，不能重复发布"
                ) from exc
            except Exception:
                db.rollback()
                raise
        return {
            "projectId": project_id,
            "versionId": version_id,
            "published": True,
            "assetCount": len(asset_ids),
        }

    @staticmethod
    def _version_asset_ids(
        version: CocreationProjectVersionHistory,
    ) -> set[UUID]:
        return {entry.asset_id for entry in version.asset_entries}

    def _get_or_create_project(
        self,
        db: Session,
        *,
        user_id: str,
        payload: ProjectRecordPayload,
    ) -> CocreationProjectHistory:
        project = db.scalar(
            select(CocreationProjectHistory).where(
                CocreationProjectHistory.user_id == user_id,
                CocreationProjectHistory.project_id == payload.id,
            )
        )
        if project is None:
            project = CocreationProjectHistory(user_id=user_id, project_id=payload.id, project_name=payload.name)
            db.add(project)

        project.project_name = payload.name
        project.industry = payload.industry
        project.description = payload.description
        project.input_mode = payload.input_mode
        project.created_at = _parse_datetime(payload.created_at)
        project.updated_at = _parse_datetime(payload.updated_at)
        project.last_task_id = payload.last_task_id
        project.last_status = payload.last_status
        project.last_result_text = payload.last_result_text
        project.last_image_url = None
        project_data = payload.model_dump(by_alias=True)
        project_data.pop("lastImageUrl", None)
        project.project_data = project_data
        db.flush()
        return project

    def _upsert_version(
        self,
        db: Session,
        *,
        user_id: str,
        project: CocreationProjectHistory,
        payload: VersionSnapshotPayload,
    ) -> CocreationProjectVersionHistory:
        version = db.scalar(
            select(CocreationProjectVersionHistory).where(
                CocreationProjectVersionHistory.user_id == user_id,
                CocreationProjectVersionHistory.project_history_id == project.id,
                CocreationProjectVersionHistory.version_id == payload.id,
            )
        )
        if version is None:
            version = CocreationProjectVersionHistory(
                user_id=user_id,
                project_history_id=project.id,
                version_id=payload.id,
                label=payload.label,
                status=payload.status,
                note=payload.note,
            )
            db.add(version)
        elif version.is_finalized:
            raise ValueError("已发布版本不能通过普通写入接口修改")

        version.label = payload.label
        version.status = payload.status
        version.note = payload.note
        version.project_id = payload.project_id or project.project_id
        version.project_name = payload.project_name or project.project_name
        version.version_number = payload.version_number
        # 发布状态只能由 publish_version 的资产校验事务推进。
        version.source_project_id = payload.source_project_id or project.project_id
        version.prompt = payload.prompt
        version.optimized_prompt = payload.optimized_prompt
        version.result_text = payload.result_text
        version.preview_image_url = None
        version.generated_image_urls = []
        version.change_type = payload.change_type
        version.source_object = payload.source_object
        version.task_id = payload.task_id
        script_asset_id = self._validate_asset_reference(
            db,
            user_id=user_id,
            asset_id=payload.script_asset_id,
            label="脚本",
        )
        output_asset_id = self._validate_asset_reference(
            db,
            user_id=user_id,
            asset_id=payload.output_asset_id,
            label="输出",
        )
        generated_assets = self._validate_generated_assets(
            db,
            user_id=user_id,
            generated_assets=payload.generated_assets,
        )
        version.download_url = None
        version.execution_summary = payload.execution_summary
        version.created_at = _parse_datetime(payload.created_at)
        version.cli_executed = payload.cli_executed
        version.export_format = payload.export_format
        version.model_objects = payload.model_objects
        version.parameters = payload.parameters
        version.script_asset_id = None
        version.output_asset_id = None
        version.generated_assets = []
        version.diagnostics = payload.diagnostics
        snapshot_data = payload.model_dump(by_alias=True)
        for legacy_key in (
            "previewImageUrl",
            "generatedImageUrls",
            "downloadUrl",
            "scriptPath",
            "outputPath",
            "workDir",
            "isFinalized",
            "generatedAssets",
        ):
            snapshot_data.pop(legacy_key, None)
        version.snapshot_data = snapshot_data
        db.flush()
        self._sync_version_asset_entries(
            db,
            version=version,
            user_id=user_id,
            script_asset_id=script_asset_id,
            output_asset_id=output_asset_id,
            generated_assets=generated_assets,
        )
        return version

    @staticmethod
    def _validate_asset_reference(
        db: Session,
        *,
        user_id: str,
        asset_id: str | None,
        label: str,
    ) -> UUID | None:
        if asset_id is None:
            return None
        try:
            parsed_id = UUID(asset_id)
        except ValueError as exc:
            raise ValueError(f"{label}资产标识格式无效") from exc
        owned = db.scalar(
            select(Asset.id).where(
                Asset.id == parsed_id,
                Asset.user_id == user_id,
                Asset.status == "available",
            )
        )
        if owned is None:
            raise ValueError(f"{label}资产不存在、不可用或不属于当前用户")
        return parsed_id

    def _validate_generated_assets(
        self,
        db: Session,
        *,
        user_id: str,
        generated_assets: list[GeneratedAssetPayload] | list[dict[str, object]],
    ) -> list[tuple[UUID, str]]:
        normalized: list[tuple[UUID, str]] = []
        for generated_asset in generated_assets:
            if isinstance(generated_asset, GeneratedAssetPayload):
                asset_id = generated_asset.asset_id
                kind = generated_asset.kind
            else:
                raw_asset_id = generated_asset.get("assetId")
                if not isinstance(raw_asset_id, str):
                    continue
                asset_id = raw_asset_id
                raw_kind = generated_asset.get("kind") or generated_asset.get(
                    "assetType"
                )
                kind = raw_kind if isinstance(raw_kind, str) else None
            parsed_id = self._validate_asset_reference(
                db,
                user_id=user_id,
                asset_id=asset_id,
                label="生成",
            )
            if parsed_id is None:
                raise ValueError("生成资产必须引用数据库资产标识")
            normalized.append((parsed_id, kind or "generated"))
        return normalized

    @staticmethod
    def _sync_version_asset_entries(
        db: Session,
        *,
        version: CocreationProjectVersionHistory,
        user_id: str,
        script_asset_id: UUID | None,
        output_asset_id: UUID | None,
        generated_assets: list[tuple[UUID, str]],
    ) -> None:
        db.execute(
            delete(CocreationVersionAssetEntry).where(
                CocreationVersionAssetEntry.version_history_id == version.id
            )
        )
        entries: list[CocreationVersionAssetEntry] = []
        for role, asset_id, kind in (
            ("script", script_asset_id, "script"),
            ("output", output_asset_id, "output"),
        ):
            if asset_id is not None:
                entries.append(
                    CocreationVersionAssetEntry(
                        user_id=user_id,
                        version_history_id=version.id,
                        asset_id=asset_id,
                        role=role,
                        kind=kind,
                    )
                )
        entries.extend(
            CocreationVersionAssetEntry(
                user_id=user_id,
                version_history_id=version.id,
                asset_id=asset_id,
                role="generated",
                kind=kind,
            )
            for asset_id, kind in generated_assets
        )
        db.add_all(entries)
        db.flush()

    def _refresh_project_summary(self, db: Session, project: CocreationProjectHistory) -> None:
        versions = db.scalars(
            select(CocreationProjectVersionHistory)
            .options(
                selectinload(CocreationProjectVersionHistory.asset_entries)
            )
            .where(
                CocreationProjectVersionHistory.project_history_id == project.id
            )
        ).all()
        if not versions:
            return

        latest_version = max(versions, key=lambda item: (item.created_at, item.version_number or 0, item.id))
        latest_image_version = next(
            (
                item
                for item in sorted(
                    versions,
                    key=lambda entry: (entry.created_at, entry.version_number or 0, entry.id),
                    reverse=True,
                )
                if self._preview_asset_id(item) is not None
            ),
            latest_version,
        )

        project.project_name = latest_version.project_name or project.project_name
        project.updated_at = latest_version.created_at
        project.last_task_id = latest_version.task_id
        project.last_status = latest_version.status
        project.last_result_text = latest_version.result_text or latest_version.execution_summary or latest_version.note
        project.last_image_url = None
        project.last_image_asset_id = self._preview_asset_id(latest_image_version)
        if not project.description:
            project.description = latest_version.result_text or latest_version.note
        db.flush()

    def _serialize_project(self, project: CocreationProjectHistory) -> dict[str, JSONValue]:
        payload = dict(project.project_data or {})
        payload.update(
            {
                "id": project.project_id,
                "name": project.project_name,
                "industry": project.industry or "全部行业",
                "description": project.description or "",
                "inputMode": project.input_mode or "prompt",
                "createdAt": project.created_at.isoformat(),
                "updatedAt": project.updated_at.isoformat(),
                "lastTaskId": project.last_task_id,
                "lastStatus": project.last_status,
                "lastResultText": project.last_result_text,
                "lastImageUrl": self._asset_download_url(
                    project.last_image_asset_id
                ),
                "versionCount": len(project.versions),
            }
        )
        return payload

    def _serialize_version(self, version: CocreationProjectVersionHistory) -> dict[str, JSONValue]:
        payload = dict(version.snapshot_data or {})
        for legacy_key in (
            "scriptPath",
            "outputPath",
            "workDir",
            "previewImageUrl",
            "generatedImageUrls",
            "downloadUrl",
        ):
            payload.pop(legacy_key, None)
        script_asset_id = self._asset_id_for_role(version, "script")
        output_asset_id = self._asset_id_for_role(version, "output")
        payload.update(
            {
                "id": version.version_id,
                "label": version.label,
                "status": version.status,
                "note": version.note,
                "projectId": version.project_id,
                "projectName": version.project_name,
                "versionNumber": version.version_number,
                "isFinalized": version.is_finalized,
                "sourceProjectId": version.source_project_id,
                "prompt": version.prompt,
                "optimizedPrompt": version.optimized_prompt,
                "resultText": version.result_text,
                "previewImageUrl": self._asset_download_url(output_asset_id),
                "generatedImageUrls": self._generated_asset_urls(version),
                "changeType": version.change_type,
                "sourceObject": version.source_object,
                "taskId": version.task_id,
                "scriptAssetId": (
                    str(script_asset_id)
                    if script_asset_id is not None
                    else None
                ),
                "outputAssetId": (
                    str(output_asset_id)
                    if output_asset_id is not None
                    else None
                ),
                "downloadUrl": self._asset_download_url(output_asset_id),
                "executionSummary": version.execution_summary,
                "createdAt": version.created_at.isoformat(),
                "cliExecuted": version.cli_executed,
                "exportFormat": version.export_format,
                "modelObjects": version.model_objects or [],
                "parameters": version.parameters or [],
                "generatedAssets": self._serialize_generated_assets(version),
                "diagnostics": version.diagnostics or [],
            }
        )
        return payload

    @staticmethod
    def _asset_download_url(asset_id: UUID | None) -> str | None:
        if asset_id is None:
            return None
        return f"/api/v1/assets/{asset_id}/download"

    def _generated_asset_urls(
        self,
        version: CocreationProjectVersionHistory,
    ) -> list[str]:
        return [
            f"/api/v1/assets/{entry.asset_id}/download"
            for entry in sorted(version.asset_entries, key=lambda item: item.id)
            if entry.role in {"output", "generated"}
        ]

    def _serialize_generated_assets(
        self,
        version: CocreationProjectVersionHistory,
    ) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for entry in sorted(version.asset_entries, key=lambda item: item.id):
            if entry.role != "generated":
                continue
            serialized.append(
                {
                    "assetId": str(entry.asset_id),
                    "kind": entry.kind,
                    "downloadUrl": self._asset_download_url(entry.asset_id),
                }
            )
        return serialized

    @staticmethod
    def _asset_id_for_role(
        version: CocreationProjectVersionHistory,
        role: str,
    ) -> UUID | None:
        return next(
            (
                entry.asset_id
                for entry in version.asset_entries
                if entry.role == role
            ),
            None,
        )

    def _preview_asset_id(
        self,
        version: CocreationProjectVersionHistory,
    ) -> UUID | None:
        return self._asset_id_for_role(version, "output") or next(
            (
                entry.asset_id
                for entry in version.asset_entries
                if entry.role == "generated"
            ),
            None,
        )

cocreation_history_service = CocreationHistoryService()
