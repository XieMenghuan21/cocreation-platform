from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.assets as assets_module
from app.api.v1.router import router
from app.config.settings import settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.models.persistence import Asset, AssetBlobChunk, UserSession
from app.services.asset_blob_service import AssetBlobService, AssetIntegrityError
from app.services.session_service import SessionService

TRUSTED_ORIGIN = "http://localhost:5174"


@pytest.fixture()
def api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'assets-api.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    app = FastAPI()
    setup_middleware(app)
    app.include_router(router, prefix="/api/v1")

    def override_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        assets_module,
        "asset_service",
        AssetBlobService(chunk_size=4, session_factory=factory),
    )
    monkeypatch.setattr(settings, "UPLOAD_MAX_SIZE", 10)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, factory
    engine.dispose()


def login(factory: sessionmaker[Session], client: TestClient, user_id: str) -> None:
    with factory() as db:
        token, _ = SessionService.create_session(
            db,
            user_id=user_id,
            client_metadata={"username": user_id},
        )
        db.commit()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)


def upload(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "sample.bin",
    kind: str = "binary",
    source: str = "upload",
    metadata: str = '{"library":true}',
):
    return client.post(
        "/api/v1/assets/upload",
        data={"kind": kind, "source": source, "metadata": metadata},
        files={"file": (filename, content, "application/octet-stream")},
        headers={"Origin": TRUSTED_ORIGIN},
    )


def test_upload_download_detail_list_and_delete(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    response = upload(client, b"abcdefghij", filename='bad\r\nX-Evil: yes.bin')
    assert response.status_code == 200
    body = response.json()
    assert body["sizeBytes"] == 10
    assert body["chunkCount"] == 3
    assert body["sha256"] == sha256(b"abcdefghij").hexdigest()
    asset_id = UUID(body["id"])
    with factory() as db:
        chunks = list(
            db.scalars(
                select(AssetBlobChunk)
                .where(AssetBlobChunk.asset_id == asset_id)
                .order_by(AssetBlobChunk.chunk_index)
            )
        )
        assert [chunk.content for chunk in chunks] == [b"abcd", b"efgh", b"ij"]

    detail = client.get(f"/api/v1/assets/{asset_id}")
    assert detail.status_code == 200
    listing = client.get("/api/v1/assets?limit=1&offset=0&kind=binary&library=true")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0
    download = client.get(f"/api/v1/assets/{asset_id}/download")
    assert download.content == b"abcdefghij"
    assert download.headers["content-length"] == "10"
    disposition = download.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition

    deleted = client.delete(
        f"/api/v1/assets/{asset_id}",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert deleted.status_code == 204
    with factory() as db:
        assert db.get(Asset, asset_id) is None
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0


def test_asset_list_orders_and_paginates_then_filters_exactly(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    first = upload(
        client,
        b"one",
        filename="first.bin",
        kind="binary",
        source="upload",
        metadata='{"library":false}',
    ).json()
    second = upload(
        client,
        b"two",
        filename="second.png",
        kind="image",
        source="generated",
        metadata='{"library":false}',
    ).json()
    third = upload(
        client,
        b"three",
        filename="third.png",
        kind="image",
        source="library",
        metadata='{"library":true}',
    ).json()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with factory() as db:
        for index, body in enumerate((first, second, third)):
            asset = db.get(Asset, UUID(body["id"]))
            assert asset is not None
            asset.created_at = base + timedelta(seconds=index)
        second_asset = db.get(Asset, UUID(second["id"]))
        assert second_asset is not None
        second_asset.status = "archived"
        db.commit()

    page_zero = client.get("/api/v1/assets?limit=1&offset=0").json()
    page_one = client.get("/api/v1/assets?limit=1&offset=1").json()
    assert page_zero["total"] == 3
    assert page_one["total"] == 3
    assert page_zero["items"][0]["id"] == third["id"]
    assert page_one["items"][0]["id"] == second["id"]
    assert page_zero["items"][0]["id"] != page_one["items"][0]["id"]

    archived = client.get("/api/v1/assets?status=archived").json()
    assert archived["total"] == 1
    assert [item["id"] for item in archived["items"]] == [second["id"]]
    generated = client.get("/api/v1/assets?source=generated").json()
    assert generated["total"] == 1
    assert [item["id"] for item in generated["items"]] == [second["id"]]
    image_library = client.get("/api/v1/assets?kind=image&library=true").json()
    assert image_library["total"] == 0
    assert image_library["items"] == []


def test_empty_limit_invalid_metadata_auth_origin_and_isolation(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    assert client.get("/api/v1/assets").status_code == 401
    login(factory, client, "alice")
    empty = upload(client, b"")
    assert empty.status_code == 200, empty.text
    assert empty.json()["chunkCount"] == 0
    invalid = upload(client, b"x", metadata="[]")
    assert invalid.status_code == 422
    too_large = upload(client, b"01234567890")
    assert too_large.status_code == 413
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 1
    non_library = client.get("/api/v1/assets?library=false")
    assert non_library.status_code == 200
    assert non_library.json()["total"] == 1

    invalid_reference = client.post(
        "/api/v1/assets/upload",
        data={"kind": "binary", "projectId": "missing-project"},
        files={"file": ("x.bin", b"x", "application/octet-stream")},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert invalid_reference.status_code == 404
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 1

    asset_id = empty.json()["id"]
    client.cookies.clear()
    login(factory, client, "bob")
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404
    assert client.get(f"/api/v1/assets/{asset_id}/download").status_code == 404
    assert client.delete(
        f"/api/v1/assets/{asset_id}",
        headers={"Origin": TRUSTED_ORIGIN},
    ).status_code == 404
    evil = upload(client, b"x")
    assert evil.status_code == 200
    blocked = client.delete(
        f"/api/v1/assets/{evil.json()['id']}",
        headers={"Origin": "https://evil.test"},
    )
    assert blocked.status_code == 403


def test_upload_rejects_oversize_metadata_and_unsafe_file_headers_without_residue(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory = api
    login(factory, client, "alice")
    monkeypatch.setattr(settings, "ASSET_METADATA_MAX_BYTES", 8)
    oversized_metadata = upload(client, b"x", metadata='{"long":"value"}')
    assert oversized_metadata.status_code == 413

    cases = [
        ("a" * 256, "application/octet-stream"),
        ("file." + "x" * 33, "application/octet-stream"),
        ("valid.bin", "text/plain\r\nX-Injected: true"),
        ("valid.bin", "x" * 161),
    ]
    for filename, content_type in cases:
        response = client.post(
            "/api/v1/assets/upload",
            data={"kind": "binary"},
            files={"file": (filename, b"x", content_type)},
            headers={"Origin": TRUSTED_ORIGIN},
        )
        assert response.status_code == 422, (filename, content_type, response.text)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0


def test_upload_rejects_missing_duplicate_and_wrong_multipart_fields(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    malformed_parts = [
        [("kind", (None, "binary"))],
        [
            ("kind", (None, "binary")),
            ("kind", (None, "image")),
            ("file", ("x.bin", b"x", "application/octet-stream")),
        ],
        [
            ("kind", ("kind.txt", b"binary", "text/plain")),
            ("file", ("x.bin", b"x", "application/octet-stream")),
        ],
        [
            ("kind", (None, "binary")),
            ("file", ("x.bin", b"x", "application/octet-stream")),
            ("file", ("y.bin", b"y", "application/octet-stream")),
        ],
        [
            ("kind", (None, "binary")),
            ("unknown", (None, "value")),
            ("file", ("x.bin", b"x", "application/octet-stream")),
        ],
    ]
    for parts in malformed_parts:
        response = client.post(
            "/api/v1/assets/upload",
            files=parts,
            headers={"Origin": TRUSTED_ORIGIN},
        )
        assert response.status_code == 422, response.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0


def test_corrupt_download_yields_no_content(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    asset_id = UUID(upload(client, b"abcdefghij").json()["id"])
    with factory() as db:
        chunk = db.scalar(
            select(AssetBlobChunk).where(
                AssetBlobChunk.asset_id == asset_id,
                AssetBlobChunk.chunk_index == 0,
            )
        )
        assert chunk is not None
        chunk.content = b"xxxx"
        db.commit()

    # 服务在响应创建前完整校验，损坏内容不得产生 200 或响应字节。
    response = client.get(f"/api/v1/assets/{asset_id}/download")
    assert response.status_code == 500
    assert b"xxxx" not in response.content


def test_download_maps_deleted_unavailable_and_database_errors(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory = api
    login(factory, client, "alice")
    deleted_id = UUID(upload(client, b"deleted").json()["id"])
    unavailable_id = UUID(upload(client, b"pending").json()["id"])
    database_error_id = UUID(upload(client, b"database").json()["id"])
    with factory() as db:
        deleted = db.get(Asset, deleted_id)
        unavailable = db.get(Asset, unavailable_id)
        assert deleted is not None and unavailable is not None
        db.delete(deleted)
        unavailable.status = "uploading"
        db.commit()

    assert client.get(f"/api/v1/assets/{deleted_id}/download").status_code == 404
    assert client.get(f"/api/v1/assets/{unavailable_id}/download").status_code == 409

    def fail_prepare(asset_id: UUID, user_id: str):
        del asset_id, user_id
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(assets_module.asset_service, "prepare_content", fail_prepare)
    response = client.get(f"/api/v1/assets/{database_error_id}/download")
    assert response.status_code == 500
    assert b"database" not in response.content


def test_download_closes_prepared_snapshot_after_response(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory = api
    login(factory, client, "alice")
    asset_id = UUID(upload(client, b"abcdefghij").json()["id"])
    real_prepare = assets_module.asset_service.prepare_content
    prepared_handles = []

    def track_prepare(requested_asset_id: UUID, user_id: str):
        prepared = real_prepare(requested_asset_id, user_id)
        prepared_handles.append(prepared)
        return prepared

    monkeypatch.setattr(assets_module.asset_service, "prepare_content", track_prepare)
    with client.stream("GET", f"/api/v1/assets/{asset_id}/download") as response:
        assert response.status_code == 200
        iterator = response.iter_bytes()
        assert next(iterator)
    assert len(prepared_handles) == 1
    assert prepared_handles[0].closed is True


def test_upload_commit_failure_returns_500_and_leaves_no_asset(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    login(factory, client, "alice")
    with factory() as db:
        session = db.scalar(select(UserSession).where(UserSession.user_id == "alice"))
        assert session is not None
        session.last_seen_at = datetime.now(timezone.utc)
        db.commit()

    class CommitFailSession(Session):
        def commit(self) -> None:
            raise RuntimeError("simulated commit failure")

    failing_factory = sessionmaker(
        bind=factory.kw["bind"],
        class_=CommitFailSession,
        expire_on_commit=False,
    )

    def failing_db() -> Generator[Session, None, None]:
        with failing_factory() as db:
            yield db

    app = client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[get_db] = failing_db
    response = upload(client, b"abc")
    assert response.status_code == 500
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0
