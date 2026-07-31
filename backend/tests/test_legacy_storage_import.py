from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

from app.db.session import Base
from app.migrations.import_legacy_storage import LegacyStorageImporter
from app.models.cocreation_history import (
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
    CocreationVersionAssetEntry,
)
from app.models.persistence import Asset
from app.services.asset_blob_service import AssetBlobService


@dataclass(frozen=True)
class LegacyFixture:
    sqlite_path: Path
    storage_roots: list[Path]
    file_bytes: bytes


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    database_path = tmp_path / "import-target.db"
    test_engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


@pytest.fixture
def legacy_fixture(tmp_path: Path) -> LegacyFixture:
    sqlite_path = tmp_path / "legacy.db"
    storage_root = tmp_path / "legacy-storage"
    storage_root.mkdir()
    asset_path = storage_root / "legacy" / "output.step"
    asset_path.parent.mkdir(parents=True)
    file_bytes = b"solid legacy-step\nendsolid legacy-step\n"
    asset_path.write_bytes(file_bytes)

    with sqlite3.connect(sqlite_path) as connection:
        connection.executescript(
            """
            CREATE TABLE cocreation_project_histories (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                project_name TEXT NOT NULL,
                industry TEXT,
                description TEXT,
                input_mode TEXT,
                created_at TEXT,
                updated_at TEXT,
                last_task_id TEXT,
                last_status TEXT,
                last_result_text TEXT,
                project_data TEXT
            );

            CREATE TABLE cocreation_project_version_histories (
                id INTEGER PRIMARY KEY,
                project_history_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL,
                project_id TEXT,
                project_name TEXT,
                version_number INTEGER,
                is_finalized INTEGER,
                source_project_id TEXT,
                prompt TEXT,
                optimized_prompt TEXT,
                result_text TEXT,
                preview_image_url TEXT,
                change_type TEXT,
                source_object TEXT,
                task_id TEXT,
                script_path TEXT,
                output_path TEXT,
                work_dir TEXT,
                execution_summary TEXT,
                created_at TEXT,
                cli_executed INTEGER,
                export_format TEXT,
                model_objects TEXT,
                parameters TEXT,
                generated_assets TEXT,
                diagnostics TEXT,
                snapshot_data TEXT,
                updated_at TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO cocreation_project_histories (
                id, user_id, project_id, project_name, industry, description,
                input_mode, created_at, updated_at, last_task_id, last_status,
                last_result_text, project_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "alice",
                "legacy-project",
                "Legacy Project",
                "装备制造",
                "legacy description",
                "prompt",
                "2026-07-20T10:00:00+00:00",
                "2026-07-20T10:05:00+00:00",
                "task-legacy",
                "completed",
                "legacy done",
                json.dumps({"id": "legacy-project", "name": "Legacy Project"}),
            ),
        )
        connection.execute(
            """
            INSERT INTO cocreation_project_version_histories (
                id, project_history_id, user_id, version_id, label, status, note,
                project_id, project_name, version_number, is_finalized,
                source_project_id, prompt, optimized_prompt, result_text,
                preview_image_url, change_type, source_object, task_id,
                script_path, output_path, work_dir, execution_summary,
                created_at, cli_executed, export_format, model_objects,
                parameters, generated_assets, diagnostics, snapshot_data, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                1,
                "alice",
                "legacy-v1",
                "Legacy V1",
                "completed",
                "legacy import",
                "legacy-project",
                "Legacy Project",
                1,
                1,
                "legacy-project",
                "prompt",
                "optimized prompt",
                "result",
                None,
                "方案生成",
                "legacy-project",
                "task-legacy",
                None,
                "/legacy/output.step",
                "/legacy/work",
                "summary",
                "2026-07-20T10:05:00+00:00",
                1,
                "step",
                "[]",
                "[]",
                "[]",
                "[]",
                json.dumps({"legacy": True}),
                "2026-07-20T10:05:00+00:00",
            ),
        )
        connection.commit()

    return LegacyFixture(
        sqlite_path=sqlite_path,
        storage_roots=[storage_root],
        file_bytes=file_bytes,
    )


def test_legacy_import_is_idempotent(
    legacy_fixture: LegacyFixture,
    engine: Engine,
) -> None:
    service = AssetBlobService(chunk_size=8)
    with Session(engine) as session:
        importer = LegacyStorageImporter(session, blob_service=service)
        first = importer.run(
            sqlite_path=legacy_fixture.sqlite_path,
            storage_roots=legacy_fixture.storage_roots,
        )
        second = importer.run(
            sqlite_path=legacy_fixture.sqlite_path,
            storage_roots=legacy_fixture.storage_roots,
        )

        assert first.imported_projects == 1
        assert first.imported_versions == 1
        assert first.imported_assets == 1
        assert second.imported_projects == 0
        assert second.imported_versions == 0
        assert second.imported_assets == 0
        assert second.skipped_versions == 1
        assert session.scalar(select(func.count(Asset.id))) == 1
        assert session.scalar(select(func.count(CocreationProjectHistory.id))) == 1
        assert session.scalar(select(func.count(CocreationProjectVersionHistory.id))) == 1
        assert session.scalar(select(func.count(CocreationVersionAssetEntry.id))) == 1


def test_legacy_file_checksum_matches_database(
    legacy_fixture: LegacyFixture,
    engine: Engine,
) -> None:
    with Session(engine) as session:
        result = LegacyStorageImporter(session).run(
            sqlite_path=legacy_fixture.sqlite_path,
            storage_roots=legacy_fixture.storage_roots,
        )
        assert len(result.asset_ids) == 1
        asset = session.get(Asset, UUID(result.asset_ids[0]))
        assert asset is not None
        assert asset.sha256 == sha256(legacy_fixture.file_bytes).hexdigest()
        assert asset.asset_metadata["legacyRelativePath"] == "legacy/output.step"
        assert asset.source == "legacy-import"
