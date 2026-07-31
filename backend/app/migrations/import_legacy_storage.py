"""将旧版 SQLite 历史与磁盘文件幂等导入当前数据库。"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.cocreation_history import (
    CocreationAssetLibraryEntry,
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
    CocreationVersionAssetEntry,
)
from app.models.persistence import Asset
from app.services.asset_blob_service import AssetBlobService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return _utc_now()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _utc_now()


def _parse_json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    parsed = json.loads(value)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _parse_json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if not isinstance(value, str) or not value.strip():
        return []
    parsed = json.loads(value)
    return list(parsed) if isinstance(parsed, list) else []


def _string(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _safe_relative_legacy_path(path_text: str) -> PurePosixPath | None:
    if "://" in path_text or path_text.startswith("/api/"):
        return None
    relative_parts: list[str] = []
    for part in PurePosixPath(path_text).parts:
        if part in {"", "/"}:
            continue
        if part == "..":
            return None
        relative_parts.append(part)
    if not relative_parts:
        return None
    return PurePosixPath(*relative_parts)


def _guess_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


@dataclass(slots=True)
class LegacyImportResult:
    imported_projects: int = 0
    skipped_projects: int = 0
    imported_versions: int = 0
    skipped_versions: int = 0
    imported_assets: int = 0
    skipped_assets: int = 0
    missing_files: int = 0
    checksum_failures: int = 0
    asset_ids: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    report_generated_at: str = field(default_factory=lambda: _utc_now().isoformat())

    def to_report(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class LegacyAssetSpec:
    role: str
    kind: str
    legacy_path: str


class LegacyStorageImporter:
    """幂等导入旧版 SQLite 历史与磁盘资产。"""

    def __init__(
        self,
        db: Session,
        *,
        blob_service: AssetBlobService | None = None,
    ) -> None:
        self.db = db
        self.blob_service = blob_service or AssetBlobService(chunk_size=4 * 1024 * 1024)

    def run(
        self,
        *,
        sqlite_path: str | Path,
        storage_roots: list[str | Path],
        report_path: str | Path | None = None,
        dry_run: bool = False,
    ) -> LegacyImportResult:
        sqlite_file = Path(sqlite_path).expanduser().resolve()
        roots = [Path(root).expanduser().resolve() for root in storage_roots]
        result = LegacyImportResult()
        if not sqlite_file.is_file():
            raise FileNotFoundError(f"legacy sqlite not found: {sqlite_file}")
        if not roots:
            raise ValueError("at least one storage root is required")

        existing_legacy_keys = self._load_existing_legacy_keys()
        with sqlite3.connect(sqlite_file) as connection:
            connection.row_factory = sqlite3.Row
            projects = self._read_projects(connection)
            versions = self._read_versions(connection)

        try:
            for project_row in projects:
                self._import_project(
                    project_row=project_row,
                    versions=versions,
                    storage_roots=roots,
                    existing_legacy_keys=existing_legacy_keys,
                    result=result,
                    dry_run=dry_run,
                )
            if dry_run:
                self.db.rollback()
            else:
                self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        if report_path is not None:
            Path(report_path).write_text(
                json.dumps(result.to_report(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return result

    def _import_project(
        self,
        *,
        project_row: sqlite3.Row,
        versions: list[sqlite3.Row],
        storage_roots: list[Path],
        existing_legacy_keys: dict[str, UUID],
        result: LegacyImportResult,
        dry_run: bool,
    ) -> None:
        user_id = _string(project_row["user_id"])
        project_id = _string(project_row["project_id"])
        project_name = _string(project_row["project_name"])
        if user_id is None or project_id is None or project_name is None:
            return

        project = self.db.scalar(
            select(CocreationProjectHistory).where(
                CocreationProjectHistory.user_id == user_id,
                CocreationProjectHistory.project_id == project_id,
            )
        )
        if project is None:
            project = CocreationProjectHistory(
                user_id=user_id,
                project_id=project_id,
                project_name=project_name,
            )
            self.db.add(project)
            self.db.flush()
            result.imported_projects += 1
        else:
            result.skipped_projects += 1

        project.project_name = project_name
        project.industry = _string(project_row["industry"])
        project.description = _string(project_row["description"])
        project.input_mode = _string(project_row["input_mode"])
        project.created_at = _parse_datetime(project_row["created_at"])
        project.updated_at = _parse_datetime(project_row["updated_at"])
        project.last_task_id = _string(project_row["last_task_id"])
        project.last_status = _string(project_row["last_status"])
        project.last_result_text = _string(project_row["last_result_text"])
        project.project_data = _parse_json_object(project_row["project_data"])
        self.db.flush()

        project_versions = [
            version_row
            for version_row in versions
            if version_row["project_history_id"] == project_row["id"]
        ]
        for version_row in project_versions:
            self._import_version(
                project=project,
                version_row=version_row,
                storage_roots=storage_roots,
                existing_legacy_keys=existing_legacy_keys,
                result=result,
                dry_run=dry_run,
            )

    def _import_version(
        self,
        *,
        project: CocreationProjectHistory,
        version_row: sqlite3.Row,
        storage_roots: list[Path],
        existing_legacy_keys: dict[str, UUID],
        result: LegacyImportResult,
        dry_run: bool,
    ) -> None:
        user_id = _string(version_row["user_id"]) or project.user_id
        version_id = _string(version_row["version_id"])
        label = _string(version_row["label"]) or "Legacy"
        status = _string(version_row["status"]) or "completed"
        note = _string(version_row["note"]) or "legacy import"
        if version_id is None:
            return

        existing_version = self.db.scalar(
            select(CocreationProjectVersionHistory).where(
                CocreationProjectVersionHistory.user_id == user_id,
                CocreationProjectVersionHistory.project_history_id == project.id,
                CocreationProjectVersionHistory.version_id == version_id,
            )
        )
        if existing_version is not None:
            result.skipped_versions += 1
            return

        version = CocreationProjectVersionHistory(
            project_history_id=project.id,
            user_id=user_id,
            version_id=version_id,
            label=label,
            status=status,
            note=note,
            project_id=_string(version_row["project_id"]) or project.project_id,
            project_name=_string(version_row["project_name"]) or project.project_name,
            version_number=version_row["version_number"],
            is_finalized=_bool(version_row["is_finalized"]),
            source_project_id=_string(version_row["source_project_id"]) or project.project_id,
            prompt=_string(version_row["prompt"]),
            optimized_prompt=_string(version_row["optimized_prompt"]),
            result_text=_string(version_row["result_text"]),
            change_type=_string(version_row["change_type"]),
            source_object=_string(version_row["source_object"]),
            task_id=_string(version_row["task_id"]),
            execution_summary=_string(version_row["execution_summary"]),
            created_at=_parse_datetime(version_row["created_at"]),
            cli_executed=_bool(version_row["cli_executed"]) if version_row["cli_executed"] is not None else None,
            export_format=_string(version_row["export_format"]),
            model_objects=self._read_json_list(version_row, "model_objects"),
            parameters=self._read_json_list(version_row, "parameters"),
            diagnostics=self._read_json_list(version_row, "diagnostics"),
            snapshot_data=self._read_json_object(version_row, "snapshot_data"),
            updated_at=_parse_datetime(version_row["updated_at"]),
        )
        self.db.add(version)
        self.db.flush()

        generated_asset_refs: list[dict[str, object]] = []
        asset_entries: list[CocreationVersionAssetEntry] = []
        output_asset_id: UUID | None = None
        script_asset_id: UUID | None = None

        for spec in self._collect_asset_specs(version_row):
            asset = self._import_asset(
                user_id=user_id,
                project_id=project.project_id,
                version_id=version.version_id,
                spec=spec,
                storage_roots=storage_roots,
                existing_legacy_keys=existing_legacy_keys,
                result=result,
                dry_run=dry_run,
            )
            if asset is None:
                continue
            asset_entries.append(
                CocreationVersionAssetEntry(
                    user_id=user_id,
                    version_history_id=version.id,
                    asset_id=asset.id,
                    role=spec.role,
                    kind=spec.kind,
                )
            )
            if spec.role == "script":
                script_asset_id = asset.id
            elif spec.role == "output":
                output_asset_id = asset.id
            else:
                generated_asset_refs.append(
                    {
                        "assetId": str(asset.id),
                        "kind": spec.kind,
                        "source": "legacy-import",
                    }
                )

        if asset_entries:
            self.db.add_all(asset_entries)
        version.script_asset_id = script_asset_id
        version.output_asset_id = output_asset_id
        version.generated_assets = generated_asset_refs

        if version.is_finalized:
            for entry in asset_entries:
                existing_library = self.db.scalar(
                    select(CocreationAssetLibraryEntry).where(
                        CocreationAssetLibraryEntry.asset_id == entry.asset_id,
                    )
                )
                if existing_library is None:
                    self.db.add(
                        CocreationAssetLibraryEntry(
                            user_id=user_id,
                            version_history_id=version.id,
                            asset_id=entry.asset_id,
                        )
                    )

        project.last_status = version.status
        project.last_task_id = version.task_id
        project.last_result_text = version.result_text or version.execution_summary or version.note
        if output_asset_id is not None:
            project.last_image_asset_id = output_asset_id
        project.updated_at = max(project.updated_at, version.created_at)
        self.db.flush()
        result.imported_versions += 1

    def _import_asset(
        self,
        *,
        user_id: str,
        project_id: str,
        version_id: str,
        spec: LegacyAssetSpec,
        storage_roots: list[Path],
        existing_legacy_keys: dict[str, UUID],
        result: LegacyImportResult,
        dry_run: bool,
    ) -> Asset | None:
        relative_path = _safe_relative_legacy_path(spec.legacy_path)
        if relative_path is None:
            return None
        legacy_source_key = (
            f"{user_id}:{project_id}:{version_id}:{spec.role}:{relative_path.as_posix()}"
        )
        existing_asset_id = existing_legacy_keys.get(legacy_source_key)
        if existing_asset_id is not None:
            existing_asset = self.db.get(Asset, existing_asset_id)
            if existing_asset is not None:
                result.skipped_assets += 1
                return existing_asset

        resolved_file = self._resolve_file(relative_path, storage_roots)
        if resolved_file is None:
            result.missing_files += 1
            result.missing_paths.append(spec.legacy_path)
            return None

        content = resolved_file.read_bytes()
        checksum = sha256(content).hexdigest()
        metadata = {
            "legacySourceKey": legacy_source_key,
            "legacyRelativePath": relative_path.as_posix(),
            "legacyImportedAt": _utc_now().isoformat(),
            "legacyChecksum": checksum,
        }
        asset = self.blob_service.store_bytes(
            db=self.db,
            user_id=user_id,
            filename=resolved_file.name,
            content_type=_guess_content_type(resolved_file.name),
            kind=spec.kind,
            source="legacy-import",
            content=content,
            project_id=project_id,
            version_id=version_id,
            metadata=metadata,
        )
        self.db.flush()
        existing_legacy_keys[legacy_source_key] = asset.id
        result.imported_assets += 1
        result.asset_ids.append(str(asset.id))
        if asset.sha256 != checksum:
            result.checksum_failures += 1
        if dry_run:
            self.db.flush()
        return asset

    @staticmethod
    def _read_projects(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT
                    id, user_id, project_id, project_name, industry, description,
                    input_mode, created_at, updated_at, last_task_id, last_status,
                    last_result_text, project_data
                FROM cocreation_project_histories
                ORDER BY id ASC
                """
            )
        )

    @staticmethod
    def _read_versions(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT
                    project_history_id, user_id, version_id, label, status, note,
                    project_id, project_name, version_number, is_finalized,
                    source_project_id, prompt, optimized_prompt, result_text,
                    preview_image_url, change_type, source_object, task_id,
                    script_path, output_path, work_dir, execution_summary,
                    created_at, cli_executed, export_format, model_objects,
                    parameters, generated_assets, diagnostics, snapshot_data, updated_at
                FROM cocreation_project_version_histories
                ORDER BY project_history_id ASC, id ASC
                """
            )
        )

    @staticmethod
    def _collect_asset_specs(version_row: sqlite3.Row) -> list[LegacyAssetSpec]:
        specs: list[LegacyAssetSpec] = []
        script_path = _string(version_row["script_path"])
        if script_path is not None:
            specs.append(LegacyAssetSpec(role="script", kind="script", legacy_path=script_path))
        output_path = _string(version_row["output_path"])
        if output_path is not None:
            specs.append(LegacyAssetSpec(role="output", kind="output", legacy_path=output_path))
        preview_image_path = _string(version_row["preview_image_url"])
        if preview_image_path is not None:
            specs.append(LegacyAssetSpec(role="generated", kind="preview", legacy_path=preview_image_path))

        for item in _parse_json_list(version_row["generated_assets"]):
            if isinstance(item, dict):
                raw_path = _string(item.get("path")) or _string(item.get("url")) or _string(item.get("downloadUrl"))
                raw_kind = _string(item.get("kind")) or _string(item.get("assetType")) or "generated"
            elif isinstance(item, str):
                raw_path = _string(item)
                raw_kind = "generated"
            else:
                raw_path = None
                raw_kind = "generated"
            if raw_path is not None:
                specs.append(LegacyAssetSpec(role="generated", kind=raw_kind, legacy_path=raw_path))
        deduplicated: list[LegacyAssetSpec] = []
        seen = set()
        for spec in specs:
            key = (spec.role, spec.kind, spec.legacy_path)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(spec)
        return deduplicated

    @staticmethod
    def _resolve_file(relative_path: PurePosixPath, storage_roots: list[Path]) -> Path | None:
        for root in storage_roots:
            candidate = (root / Path(*relative_path.parts)).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return candidate
        return None

    @staticmethod
    def _read_json_list(row: sqlite3.Row, key: str) -> list[dict[str, object]]:
        values = _parse_json_list(row[key])
        return [value for value in values if isinstance(value, dict)]

    @staticmethod
    def _read_json_object(row: sqlite3.Row, key: str) -> dict[str, object]:
        return _parse_json_object(row[key])

    def _load_existing_legacy_keys(self) -> dict[str, UUID]:
        assets = self.db.scalars(
            select(Asset).where(Asset.source == "legacy-import")
        ).all()
        keys: dict[str, UUID] = {}
        for asset in assets:
            legacy_key = asset.asset_metadata.get("legacySourceKey")
            if isinstance(legacy_key, str):
                keys[legacy_key] = asset.id
        return keys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import legacy cocreation SQLite storage into the current database")
    parser.add_argument("--sqlite-path", required=True, help="旧版 SQLite 数据库路径")
    parser.add_argument(
        "--storage-root",
        action="append",
        dest="storage_roots",
        required=True,
        help="旧版文件存储根目录，可重复传入",
    )
    parser.add_argument("--report", help="导入报告 JSON 输出路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验与统计，不提交导入结果")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    with SessionLocal() as db:
        importer = LegacyStorageImporter(db)
        result = importer.run(
            sqlite_path=args.sqlite_path,
            storage_roots=args.storage_roots,
            report_path=args.report,
            dry_run=args.dry_run,
        )
    print(json.dumps(result.to_report(), ensure_ascii=False, indent=2))
    return 0 if result.checksum_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
