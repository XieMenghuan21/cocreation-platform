from __future__ import annotations

from collections.abc import Generator
import inspect
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock, RLock, Thread
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import require_auth
from app.api.v1.assets import router as assets_router
from app.api.v1.cocreation_history import router as cocreation_history_router
from app.api.v1.workspace import router as workspace_router
from app.db.session import Base, get_db
from app.models.cocreation_history import (
    CocreationAssetLibraryEntry,
    CocreationProjectHistory,
    CocreationProjectVersionHistory,
)
from app.models.persistence import Asset, WorkspaceState
from app.schemas.cocreation_history import (
    ProjectRecordPayload,
    VersionSnapshotPayload,
)
from app.services.cocreation_history_service import CocreationHistoryService
import app.services.cocreation_history_service as history_service_module
from app.api.v1.cocreation_history import (
    delete_history_version,
    list_history_projects,
    upsert_project_with_version,
)


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "history-test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    testing_session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)

    test_app = FastAPI()
    test_app.state.testing_session_factory = testing_session_local
    test_app.include_router(cocreation_history_router, prefix="/api/v1")
    test_app.include_router(assets_router, prefix="/api/v1")
    test_app.include_router(workspace_router, prefix="/api/v1")

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    def override_require_auth(request: Request) -> dict[str, str]:
        username = request.headers.get("x-test-user", "").strip() or "anonymous"
        subject = request.headers.get("x-test-sub", "").strip() or username
        return {
            "sub": subject,
            "username": username,
            "displayName": username,
        }

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[require_auth] = override_require_auth

    with TestClient(test_app) as test_client:
        yield test_client

    test_app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def auth_headers(username: str) -> dict[str, str]:
    return {"x-test-user": username}


def stable_auth_headers(subject: str, username: str) -> dict[str, str]:
    return {"x-test-sub": subject, "x-test-user": username}


def sample_payload(project_id: str, version_id: str, image_url: str) -> dict[str, object]:
    return {
        "project": {
            "id": project_id,
            "name": "测试项目",
            "industry": "装备制造",
            "description": "项目说明",
            "inputMode": "prompt",
            "createdAt": "2026-07-20T10:00:00Z",
            "updatedAt": "2026-07-20T10:05:00Z",
            "lastTaskId": "task-1",
            "lastStatus": "已完成",
            "lastResultText": "生成完成",
            "lastImageUrl": image_url,
            "versionCount": 1,
        },
        "version": {
            "id": version_id,
            "label": "V1.0",
            "status": "已完成",
            "note": "测试版本",
            "projectId": project_id,
            "projectName": "测试项目",
            "versionNumber": 1,
            "isFinalized": False,
            "sourceProjectId": project_id,
            "prompt": "生成一个工业设计方案",
            "resultText": "生成完成",
            "previewImageUrl": image_url,
            "generatedImageUrls": [image_url],
            "changeType": "方案生成",
            "sourceObject": "测试项目",
            "taskId": "task-1",
            "scriptPath": "版本：v1",
            "workDir": "测试项目",
            "outputPath": image_url,
            "downloadUrl": image_url,
            "executionSummary": "执行完成",
            "createdAt": "2026-07-20T10:05:00Z",
            "cliExecuted": True,
            "exportFormat": "png",
            "generatedAssets": [],
            "diagnostics": [],
        },
    }


def upload_asset(
    client: TestClient,
    *,
    username: str,
    filename: str = "output.step",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/assets/upload",
        headers=auth_headers(username),
        data={"kind": "cad", "source": "generated"},
        files={"file": (filename, b"database-only-asset", "application/step")},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def open_testing_session(client: TestClient) -> Session:
    factory = cast(sessionmaker[Session], client.app.state.testing_session_factory)
    return factory()


def test_transactional_upsert_flushes_without_committing(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'history-transaction.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    payload = sample_payload("transaction-project", "V1", "/preview.png")
    project = ProjectRecordPayload.model_validate(payload["project"])
    version = VersionSnapshotPayload.model_validate(payload["version"])
    service = CocreationHistoryService()

    with factory() as session:
        service.upsert_project_with_version_in_transaction(
            session,
            auth_user={"sub": "alice"},
            project_payload=project,
            version_payload=version,
        )
        assert session.scalar(select(func.count(CocreationProjectHistory.id))) == 1
        session.rollback()

    with factory() as session:
        assert session.scalar(select(func.count(CocreationProjectHistory.id))) == 0
    engine.dispose()


def test_history_db_routes_are_sync_for_fastapi_threadpool() -> None:
    assert not inspect.iscoroutinefunction(upsert_project_with_version)
    assert not inspect.iscoroutinefunction(list_history_projects)
    assert not inspect.iscoroutinefunction(delete_history_version)


def test_history_routes_use_typed_response_models() -> None:
    for route in cocreation_history_router.routes:
        response_model = getattr(route, "response_model", None)
        assert response_model is not dict


def test_version_asset_relationship_table_is_registered() -> None:
    assert "cocreation_version_asset_entries" in Base.metadata.tables


def test_sqlite_concurrent_first_upsert_same_project_version_is_stable(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'history-concurrency.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    raw = sample_payload("same-project", "same-version", "/same.png")
    project = ProjectRecordPayload.model_validate(raw["project"])
    version = VersionSnapshotPayload.model_validate(raw["version"])
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
                    auth_user={"sub": "stable-owner", "username": username},
                    project_payload=project,
                    version_payload=version,
                )
        except BaseException as exc:
            with failure_lock:
                failures.append(exc)

    threads = [Thread(target=write, args=("old-name",)), Thread(target=write, args=("new-name",))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert failures == []
    with factory() as session:
        assert session.scalar(select(func.count(CocreationProjectHistory.id))) == 1
        assert session.scalar(select(func.count(CocreationProjectVersionHistory.id))) == 1
    engine.dispose()


def test_sqlite_delete_last_version_racing_upsert_is_linearized(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'history-delete-upsert.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    first_raw = sample_payload("race-project", "v1", "/v1.png")
    second_raw = sample_payload("race-project", "v2", "/v2.png")
    project = ProjectRecordPayload.model_validate(first_raw["project"])
    first_version = VersionSnapshotPayload.model_validate(first_raw["version"])
    second_version = VersionSnapshotPayload.model_validate(second_raw["version"])
    service = CocreationHistoryService()
    with factory() as session:
        service.upsert_project_with_version(
            session,
            auth_user={"sub": "race-owner"},
            project_payload=project,
            version_payload=first_version,
        )
    barrier = Barrier(3)
    failures: list[BaseException] = []
    failure_lock = Lock()

    def delete_first() -> None:
        try:
            with factory() as session:
                barrier.wait()
                service.delete_version(
                    session,
                    auth_user={"sub": "race-owner"},
                    project_id="race-project",
                    version_id="v1",
                )
        except BaseException as exc:
            with failure_lock:
                failures.append(exc)

    def upsert_second() -> None:
        try:
            with factory() as session:
                barrier.wait()
                service.upsert_project_with_version(
                    session,
                    auth_user={"sub": "race-owner"},
                    project_payload=project,
                    version_payload=second_version,
                )
        except BaseException as exc:
            with failure_lock:
                failures.append(exc)

    threads = [Thread(target=delete_first), Thread(target=upsert_second)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert failures == []
    with factory() as session:
        projects = session.scalars(select(CocreationProjectHistory)).all()
        versions = session.scalars(select(CocreationProjectVersionHistory)).all()
        assert len(projects) == 1
        assert [version.version_id for version in versions] == ["v2"]
    engine.dispose()


def test_sqlite_history_uses_one_global_write_lock() -> None:
    assert isinstance(history_service_module._SQLITE_HISTORY_WRITE_LOCK, type(RLock()))


def test_upsert_and_list_history_are_scoped_by_user(client: TestClient) -> None:
    payload = sample_payload("project-a", "V1.0", "/api/v1/industrial-design/assets/demo-a.png")

    save_response = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )

    assert save_response.status_code == 200

    alice_response = client.get(
        "/api/v1/cocreation-history/projects",
        headers=auth_headers("alice"),
    )
    bob_response = client.get(
        "/api/v1/cocreation-history/projects",
        headers=auth_headers("bob"),
    )

    assert alice_response.status_code == 200
    assert bob_response.status_code == 200
    assert len(alice_response.json()["data"]["snapshots"]) == 1
    assert alice_response.json()["data"]["snapshots"][0]["projectId"] == "project-a"
    assert bob_response.json()["data"]["snapshots"] == []


def test_list_history_projects_supports_bounded_pagination(client: TestClient) -> None:
    for index in range(3):
        response = client.post(
            "/api/v1/cocreation-history/projects/upsert-with-version",
            json=sample_payload(f"page-project-{index}", "V1.0", f"/page-{index}.png"),
            headers=auth_headers("alice"),
        )
        assert response.status_code == 200

    first = client.get(
        "/api/v1/cocreation-history/projects?limit=1&offset=0",
        headers=auth_headers("alice"),
    )
    second = client.get(
        "/api/v1/cocreation-history/projects?limit=1&offset=1",
        headers=auth_headers("alice"),
    )
    invalid = client.get(
        "/api/v1/cocreation-history/projects?limit=201&offset=0",
        headers=auth_headers("alice"),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["total"] == 3
    assert len(first.json()["data"]["projects"]) == 1
    assert len(first.json()["data"]["snapshots"]) == 1
    assert first.json()["data"]["projects"] != second.json()["data"]["projects"]
    assert invalid.status_code == 422


def test_username_rename_keeps_history_under_stable_sub(client: TestClient) -> None:
    payload = sample_payload("stable-project", "V1", "/stable.png")
    saved = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=stable_auth_headers("subject-1", "old-name"),
    )
    listed = client.get(
        "/api/v1/cocreation-history/projects",
        headers=stable_auth_headers("subject-1", "new-name"),
    )
    wrong_subject = client.get(
        "/api/v1/cocreation-history/projects",
        headers=stable_auth_headers("subject-2", "old-name"),
    )

    assert saved.status_code == 200
    assert len(listed.json()["data"]["snapshots"]) == 1
    assert wrong_subject.json()["data"]["snapshots"] == []


def test_delete_version_removes_snapshot_from_history(client: TestClient) -> None:
    payload = sample_payload("project-a", "V1.0", "/api/v1/industrial-design/assets/demo-a.png")
    client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )

    delete_response = client.delete(
        "/api/v1/cocreation-history/projects/project-a/versions/V1.0",
        headers=auth_headers("alice"),
    )
    list_response = client.get(
        "/api/v1/cocreation-history/projects",
        headers=auth_headers("alice"),
    )

    assert delete_response.status_code == 200
    assert list_response.json()["data"]["snapshots"] == []


def test_publish_version_creates_queryable_database_library_association(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("publish-project", "publish-v1", "/legacy-preview.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    version["generatedAssets"] = [
        {
            "assetId": asset["id"],
            "kind": "cad",
        }
    ]
    saved = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )
    assert saved.status_code == 200

    published = client.post(
        "/api/v1/cocreation-history/projects/publish-project/versions/publish-v1/publish",
        headers=auth_headers("alice"),
    )

    assert published.status_code == 200
    library = client.get(
        "/api/v1/assets?library=true",
        headers=auth_headers("alice"),
    )
    assert library.status_code == 200
    assert {
        item["sourceVersionId"] for item in library.json()["items"]
    } == {"publish-v1"}


def test_workspace_reference_endpoint_persists_owned_version_id(
    client: TestClient,
) -> None:
    payload = sample_payload("reference-project", "reference-v1", "/legacy.png")
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200

    saved = client.put(
        "/api/v1/workspace/reference",
        headers=auth_headers("alice"),
        json={"versionId": "reference-v1"},
    )

    assert saved.status_code == 200
    restored = client.get(
        "/api/v1/workspace",
        headers=auth_headers("alice"),
    )
    assert restored.status_code == 200
    assert restored.json()["selectedReferenceVersionId"] == "reference-v1"


def test_local_import_route_is_removed(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/cocreation-history/import-local",
        json={"projects": [], "snapshots": []},
        headers=auth_headers("alice"),
    )

    assert response.status_code == 404


def test_history_urls_are_derived_only_from_owned_asset_ids(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice", filename="derived.step")
    payload = sample_payload("derived-project", "derived-v1", "/forbidden/local/path.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    version["generatedAssets"] = [
        {
            "assetId": asset["id"],
            "kind": "cad",
        }
    ]

    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200
    snapshot = client.get(
        "/api/v1/cocreation-history/projects",
        headers=auth_headers("alice"),
    ).json()["data"]["snapshots"][0]

    expected_url = f"/api/v1/assets/{asset['id']}/download"
    assert snapshot["previewImageUrl"] == expected_url
    assert snapshot["downloadUrl"] == expected_url
    assert snapshot["generatedAssets"] == [
        {"assetId": asset["id"], "kind": "cad", "downloadUrl": expected_url}
    ]
    assert "/forbidden/" not in str(snapshot)
    with open_testing_session(client) as db:
        stored = db.scalar(
            select(CocreationProjectVersionHistory).where(
                CocreationProjectVersionHistory.version_id == "derived-v1"
            )
        )
        assert stored is not None
        assert stored.preview_image_url is None
        assert stored.download_url is None
        project = db.scalar(
            select(CocreationProjectHistory).where(
                CocreationProjectHistory.project_id == "derived-project"
            )
        )
        assert project is not None
        assert getattr(project, "last_image_asset_id", None) == UUID(str(asset["id"]))
        stored.generated_assets = []
        stored.snapshot_data = {
            **stored.snapshot_data,
            "scriptPath": "/forbidden/migrated-script.jscad",
            "outputPath": "/forbidden/migrated-output.step",
            "workDir": "/forbidden/migrated-work-dir",
        }
        db.commit()
    migrated_snapshot = client.get(
        "/api/v1/cocreation-history/projects",
        headers=auth_headers("alice"),
    ).json()["data"]["snapshots"][0]
    assert "scriptPath" not in migrated_snapshot
    assert "outputPath" not in migrated_snapshot
    assert "workDir" not in migrated_snapshot
    assert migrated_snapshot["generatedAssets"] == [
        {"assetId": asset["id"], "kind": "cad", "downloadUrl": expected_url}
    ]


def test_publish_rolls_back_when_asset_becomes_unavailable(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("rollback-project", "rollback-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200
    with open_testing_session(client) as db:
        stored_asset = db.get(Asset, UUID(str(asset["id"])))
        assert stored_asset is not None
        stored_asset.status = "failed"
        db.commit()

    response = client.post(
        "/api/v1/cocreation-history/projects/rollback-project/versions/rollback-v1/publish",
        headers=auth_headers("alice"),
    )

    assert response.status_code == 409
    with open_testing_session(client) as db:
        stored_version = db.scalar(
            select(CocreationProjectVersionHistory).where(
                CocreationProjectVersionHistory.version_id == "rollback-v1"
            )
        )
        assert stored_version is not None
        assert stored_version.is_finalized is False
        assert db.scalar(select(func.count(CocreationAssetLibraryEntry.id))) == 0


@pytest.mark.parametrize(
    ("asset_state", "asset_id"),
    [
        ("unknown", "00000000-0000-0000-0000-000000000001"),
        ("cross-owner", None),
        ("failed", None),
    ],
)
def test_generated_asset_rejects_unknown_cross_owner_and_unavailable_ids(
    client: TestClient,
    asset_state: str,
    asset_id: str | None,
) -> None:
    if asset_id is None:
        owner = "bob" if asset_state == "cross-owner" else "alice"
        uploaded = upload_asset(client, username=owner)
        asset_id = str(uploaded["id"])
        if asset_state == "failed":
            with open_testing_session(client) as db:
                stored = db.get(Asset, UUID(asset_id))
                assert stored is not None
                stored.status = "failed"
                db.commit()
    payload = sample_payload(
        f"invalid-{asset_state}",
        f"invalid-{asset_state}-v1",
        "/legacy.png",
    )
    version = payload["version"]
    assert isinstance(version, dict)
    version["generatedAssets"] = [{"assetId": asset_id}]

    response = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )

    assert response.status_code == 400


def test_generated_asset_schema_rejects_url_and_path_fields(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    for forbidden_key in ("downloadUrl", "path", "outputPath"):
        payload = sample_payload(
            f"strict-{forbidden_key}",
            f"strict-{forbidden_key}-v1",
            "/legacy.png",
        )
        version = payload["version"]
        assert isinstance(version, dict)
        version["generatedAssets"] = [
            {"assetId": asset["id"], forbidden_key: "/forbidden/value"}
        ]
        response = client.post(
            "/api/v1/cocreation-history/projects/upsert-with-version",
            json=payload,
            headers=auth_headers("alice"),
        )
        assert response.status_code == 422


def test_generated_asset_schema_rejects_duplicate_asset_role(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("duplicate-generated", "duplicate-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["generatedAssets"] = [
        {"assetId": asset["id"], "kind": "cad"},
        {"assetId": asset["id"], "kind": "cad"},
    ]

    response = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )

    assert response.status_code in {400, 422}
    assert response.status_code != 500


@pytest.mark.parametrize(
    ("target", "unexpected_key"),
    [
        ("wrapper", "unexpectedWrapper"),
        ("project", "unexpectedProject"),
        ("version", "unexpectedVersion"),
    ],
)
def test_history_upsert_rejects_unexpected_fields(
    client: TestClient,
    target: str,
    unexpected_key: str,
) -> None:
    payload = sample_payload("strict-extra", "strict-extra-v1", "/legacy.png")
    if target == "wrapper":
        payload[unexpected_key] = True
    else:
        nested = payload[target]
        assert isinstance(nested, dict)
        nested[unexpected_key] = True

    response = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )

    assert response.status_code == 422


def test_legacy_import_route_is_removed_even_with_extra_fields(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/cocreation-history/import-local",
        json={"projects": [], "snapshots": [], "unexpected": True},
        headers=auth_headers("alice"),
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("status_value", "is_finalized"),
    [("published", False), ("已发布", False), ("已完成", True)],
)
def test_upsert_rejects_forged_publication_state(
    client: TestClient,
    status_value: str,
    is_finalized: bool,
) -> None:
    payload = sample_payload("forged-project", "forged-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["status"] = status_value
    version["isFinalized"] = is_finalized

    response = client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    )

    assert response.status_code == 422


def test_publish_and_reference_do_not_cross_user_boundaries(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("private-project", "private-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200

    assert client.post(
        "/api/v1/cocreation-history/projects/private-project/versions/private-v1/publish",
        headers=auth_headers("bob"),
    ).status_code == 404
    assert client.put(
        "/api/v1/workspace/reference",
        json={"versionId": "private-v1"},
        headers=auth_headers("bob"),
    ).status_code == 404
    assert client.get(
        "/api/v1/assets?library=true",
        headers=auth_headers("bob"),
    ).json()["items"] == []


def test_library_query_requires_current_user_library_relationship(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("scoped-library", "scoped-library-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200
    assert client.post(
        "/api/v1/cocreation-history/projects/scoped-library/versions/scoped-library-v1/publish",
        headers=auth_headers("alice"),
    ).status_code == 200
    with open_testing_session(client) as db:
        entry = db.scalar(select(CocreationAssetLibraryEntry))
        assert entry is not None
        entry.user_id = "bob"
        db.commit()

    assert client.get(
        "/api/v1/assets?library=true",
        headers=auth_headers("alice"),
    ).json()["items"] == []


def test_delete_version_clears_workspace_reference(
    client: TestClient,
) -> None:
    payload = sample_payload("clear-reference", "clear-reference-v1", "/legacy.png")
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200
    assert client.put(
        "/api/v1/workspace/reference",
        json={"versionId": "clear-reference-v1"},
        headers=auth_headers("alice"),
    ).status_code == 200

    assert client.delete(
        "/api/v1/cocreation-history/projects/clear-reference/versions/clear-reference-v1",
        headers=auth_headers("alice"),
    ).status_code == 200
    restored = client.get(
        "/api/v1/workspace",
        headers=auth_headers("alice"),
    ).json()
    assert restored["selectedReferenceVersionId"] is None


def test_reference_requires_project_when_version_id_is_ambiguous(
    client: TestClient,
) -> None:
    for project_id in ("duplicate-project-a", "duplicate-project-b"):
        assert client.post(
            "/api/v1/cocreation-history/projects/upsert-with-version",
            json=sample_payload(project_id, "duplicate-v1", "/legacy.png"),
            headers=auth_headers("alice"),
        ).status_code == 200

    ambiguous = client.put(
        "/api/v1/workspace/reference",
        json={"versionId": "duplicate-v1"},
        headers=auth_headers("alice"),
    )
    resolved = client.put(
        "/api/v1/workspace/reference",
        json={"projectId": "duplicate-project-b", "versionId": "duplicate-v1"},
        headers=auth_headers("alice"),
    )

    assert ambiguous.status_code == 409
    assert resolved.status_code == 200
    assert resolved.json()["selectedProjectId"] == "duplicate-project-b"
    assert resolved.json()["selectedReferenceVersionId"] == "duplicate-v1"
    with open_testing_session(client) as db:
        state = db.scalar(select(WorkspaceState).where(WorkspaceState.user_id == "alice"))
        version = db.scalar(
            select(CocreationProjectVersionHistory)
            .join(CocreationProjectHistory)
            .where(
                CocreationProjectHistory.project_id == "duplicate-project-b",
                CocreationProjectVersionHistory.version_id == "duplicate-v1",
            )
        )
        assert state is not None
        assert version is not None
        assert state.selected_reference_version_history_id == version.id


def test_delete_last_version_clears_workspace_project_and_reference(
    client: TestClient,
) -> None:
    payload = sample_payload("delete-workspace-project", "delete-workspace-v1", "/legacy.png")
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200
    assert client.put(
        "/api/v1/workspace/reference",
        json={
            "projectId": "delete-workspace-project",
            "versionId": "delete-workspace-v1",
        },
        headers=auth_headers("alice"),
    ).status_code == 200

    assert client.delete(
        "/api/v1/cocreation-history/projects/delete-workspace-project/versions/delete-workspace-v1",
        headers=auth_headers("alice"),
    ).status_code == 200

    workspace = client.get(
        "/api/v1/workspace",
        headers=auth_headers("alice"),
    ).json()
    assert workspace["selectedProjectId"] is None
    assert workspace["selectedReferenceVersionId"] is None


def test_refresh_project_summary_eager_loads_version_asset_entries(
    client: TestClient,
) -> None:
    for index in range(5):
        payload = sample_payload("query-count", f"query-count-v{index}", "/legacy.png")
        version = payload["version"]
        assert isinstance(version, dict)
        if index == 0:
            asset = upload_asset(
                client,
                username="alice",
                filename="summary-0.step",
            )
            version["outputAssetId"] = asset["id"]
        assert client.post(
            "/api/v1/cocreation-history/projects/upsert-with-version",
            json=payload,
            headers=auth_headers("alice"),
        ).status_code == 200

    with open_testing_session(client) as db:
        project = db.scalar(
            select(CocreationProjectHistory).where(
                CocreationProjectHistory.project_id == "query-count"
            )
        )
        assert project is not None
        db.expire_all()
        db.refresh(project)
        selects = 0

        def count_selects(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            nonlocal selects
            if statement.lstrip().upper().startswith("SELECT"):
                selects += 1

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            CocreationHistoryService()._refresh_project_summary(db, project)
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

    assert selects <= 2


def test_publish_is_idempotent_and_delete_removes_library_relationship(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("lifecycle-project", "lifecycle-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200

    endpoint = (
        "/api/v1/cocreation-history/projects/"
        "lifecycle-project/versions/lifecycle-v1/publish"
    )
    assert client.post(endpoint, headers=auth_headers("alice")).status_code == 200
    assert client.post(endpoint, headers=auth_headers("alice")).status_code == 200
    with open_testing_session(client) as db:
        assert db.scalar(select(func.count(CocreationAssetLibraryEntry.id))) == 1

    deleted = client.delete(
        "/api/v1/cocreation-history/projects/lifecycle-project/versions/lifecycle-v1",
        headers=auth_headers("alice"),
    )
    assert deleted.status_code == 200
    assert client.get(
        "/api/v1/assets?library=true",
        headers=auth_headers("alice"),
    ).json()["items"] == []


def test_publish_rejects_asset_already_published_from_another_version(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    for version_id in ("shared-v1", "shared-v2"):
        payload = sample_payload("shared-project", version_id, "/legacy.png")
        version = payload["version"]
        assert isinstance(version, dict)
        version["outputAssetId"] = asset["id"]
        assert client.post(
            "/api/v1/cocreation-history/projects/upsert-with-version",
            json=payload,
            headers=auth_headers("alice"),
        ).status_code == 200

    first_endpoint = (
        "/api/v1/cocreation-history/projects/"
        "shared-project/versions/shared-v1/publish"
    )
    second_endpoint = (
        "/api/v1/cocreation-history/projects/"
        "shared-project/versions/shared-v2/publish"
    )
    assert client.post(first_endpoint, headers=auth_headers("alice")).status_code == 200

    rejected = client.post(second_endpoint, headers=auth_headers("alice"))

    assert rejected.status_code == 409
    assert "其他版本" in rejected.json()["detail"]
    assert client.post(first_endpoint, headers=auth_headers("alice")).status_code == 200


def test_delete_asset_removes_version_and_library_relationships(
    client: TestClient,
) -> None:
    asset = upload_asset(client, username="alice")
    payload = sample_payload("asset-delete", "asset-delete-v1", "/legacy.png")
    version = payload["version"]
    assert isinstance(version, dict)
    version["outputAssetId"] = asset["id"]
    version["generatedAssets"] = [{"assetId": asset["id"], "kind": "cad"}]
    assert client.post(
        "/api/v1/cocreation-history/projects/upsert-with-version",
        json=payload,
        headers=auth_headers("alice"),
    ).status_code == 200
    assert client.post(
        "/api/v1/cocreation-history/projects/asset-delete/versions/asset-delete-v1/publish",
        headers=auth_headers("alice"),
    ).status_code == 200

    deleted = client.delete(
        f"/api/v1/assets/{asset['id']}",
        headers=auth_headers("alice"),
    )

    assert deleted.status_code == 204
    snapshot = client.get(
        "/api/v1/cocreation-history/projects",
        headers=auth_headers("alice"),
    ).json()["data"]["snapshots"][0]
    assert snapshot["outputAssetId"] is None
    assert snapshot["downloadUrl"] is None
    assert snapshot["generatedAssets"] == []
    assert client.get(
        "/api/v1/assets?library=true",
        headers=auth_headers("alice"),
    ).json()["items"] == []


def test_concurrent_reference_updates_remain_owner_scoped_and_consistent(
    client: TestClient,
) -> None:
    for version_id in ("reference-a", "reference-b"):
        assert client.post(
            "/api/v1/cocreation-history/projects/upsert-with-version",
            json=sample_payload("reference-race", version_id, "/legacy.png"),
            headers=auth_headers("alice"),
        ).status_code == 200

    barrier = Barrier(3)
    statuses: list[int] = []
    lock = Lock()

    def save_reference(version_id: str) -> None:
        barrier.wait()
        response = client.put(
            "/api/v1/workspace/reference",
            json={"versionId": version_id},
            headers=auth_headers("alice"),
        )
        with lock:
            statuses.append(response.status_code)

    threads = [
        Thread(target=save_reference, args=("reference-a",)),
        Thread(target=save_reference, args=("reference-b",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert statuses == [200, 200]
    restored = client.get(
        "/api/v1/workspace",
        headers=auth_headers("alice"),
    ).json()
    assert restored["selectedReferenceVersionId"] in {"reference-a", "reference-b"}
    assert restored["version"] == 2
