from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import Message, Receive, Scope, Send

import app.api.v1.assets as assets_module
from app.api.v1.router import router
from app.db.session import Base, get_db
from app.models.persistence import Asset, AssetBlobChunk
from app.core.middleware import RequestBodyLimitMiddleware
from app.core.middleware import setup_middleware
from app.config.settings import settings
from app.services.session_service import SessionService


async def run_limited_request(
    chunks: list[bytes],
    *,
    content_length: int | None,
    limit: int,
    path: str = "/api/v1/assets/upload",
    json_limit: int | None = None,
    forgecad_limit: int | None = None,
) -> tuple[list[Message], bool]:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    app_called = False
    sent: list[Message] = []

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal app_called
        app_called = True
        while (await receive()).get("more_body", False):
            pass
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
        "state": {},
    }
    middleware = RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=limit,
        json_max_body_bytes=json_limit,
        forgecad_import_max_body_bytes=forgecad_limit,
    )
    await middleware(scope, receive, send)
    return sent, app_called


@pytest.mark.asyncio
async def test_chunked_body_without_content_length_is_rejected_while_receiving() -> None:
    sent, app_called = await run_limited_request(
        [b"1234", b"5678", b"9"],
        content_length=None,
        limit=8,
    )
    assert app_called is True
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_false_small_content_length_cannot_bypass_actual_body_limit() -> None:
    sent, app_called = await run_limited_request(
        [b"12345", b"67890"],
        content_length=1,
        limit=8,
    )
    assert app_called is True
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_downstream() -> None:
    sent, app_called = await run_limited_request(
        [b"unused"],
        content_length=9,
        limit=8,
    )
    assert app_called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_forgecad_multipart_uses_its_upload_limit_not_json_limit() -> None:
    sent, app_called = await run_limited_request(
        [b"123456"],
        content_length=6,
        limit=100,
        json_limit=2,
        forgecad_limit=8,
        path="/api/v1/forgecad/import",
    )
    assert app_called is True
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_forgecad_multipart_over_its_upload_limit_is_413() -> None:
    sent, app_called = await run_limited_request(
        [b"123456789"],
        content_length=9,
        limit=100,
        json_limit=2,
        forgecad_limit=8,
        path="/api/v1/forgecad/import",
    )
    assert app_called is False
    assert sent[0]["status"] == 413


def test_body_limit_413_keeps_cors_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_MAX_SIZE", 4)
    monkeypatch.setattr(settings, "ASSET_UPLOAD_OVERHEAD_MAX_BYTES", 4)
    app = FastAPI()
    setup_middleware(app)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assets/upload",
            content=b"123456789",
            headers={
                "Content-Type": "application/octet-stream",
                "Origin": "http://localhost:5174",
            },
        )
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert response.json() == {"detail": "请求体超过大小限制"}


def multipart_body(
    *,
    metadata: str,
    file_content: bytes,
) -> tuple[bytes, bytes]:
    request = httpx.Request(
        "POST",
        "http://test/api/v1/assets/upload",
        data={"kind": "binary", "metadata": metadata},
        files={"file": ("sample.bin", file_content, "application/octet-stream")},
    )
    content_type = request.headers["Content-Type"].encode("latin-1")
    return request.read(), content_type


async def invoke_upload(
    app: FastAPI,
    body: bytes,
    content_type: bytes,
    *,
    cookie: str | None,
    chunk_size: int,
    content_length: int | None = None,
    receive_error_after: int | None = None,
) -> tuple[list[Message], int]:
    chunks = [
        body[offset : offset + chunk_size]
        for offset in range(0, len(body), chunk_size)
    ]
    consumed = 0
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal consumed
        await asyncio.sleep(0.001)
        if receive_error_after is not None and consumed >= receive_error_after:
            raise RuntimeError("simulated receive failure")
        if not chunks:
            return {"type": "http.disconnect"}
        chunk = chunks.pop(0)
        consumed += len(chunk)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": bool(chunks),
        }

    async def send(message: Message) -> None:
        sent.append(message)

    headers = [
        (b"content-type", content_type),
        (b"origin", b"http://localhost:5174"),
    ]
    if cookie is not None:
        headers.append(
            (
                b"cookie",
                f"{settings.SESSION_COOKIE_NAME}={cookie}".encode("latin-1"),
            )
        )
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/assets/upload",
        "raw_path": b"/api/v1/assets/upload",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
        "state": {},
    }
    await app(scope, receive, send)
    return sent, consumed


@pytest.fixture()
def upload_asgi_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[FastAPI, sessionmaker[Session], str], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'body-limit.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "ASSET_METADATA_MAX_BYTES", 8)
    app = FastAPI()
    setup_middleware(app)
    app.include_router(router, prefix="/api/v1")

    def override_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with factory() as db:
        token, user_session = SessionService.create_session(
            db,
            user_id="body-user",
            client_metadata={"username": "body-user"},
        )
        user_session.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    try:
        yield app, factory, token
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_unauthenticated_upload_does_not_consume_multipart_body(
    upload_asgi_app: tuple[FastAPI, sessionmaker[Session], str],
) -> None:
    app, factory, _ = upload_asgi_app
    body, content_type = multipart_body(
        metadata="x" * 100,
        file_content=b"z" * 100_000,
    )
    sent, consumed = await invoke_upload(
        app,
        body,
        content_type,
        cookie=None,
        chunk_size=16,
    )
    assert sent[0]["status"] == 401
    assert consumed == 0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0


@pytest.mark.asyncio
async def test_authenticated_metadata_limit_stops_stream_early(
    upload_asgi_app: tuple[FastAPI, sessionmaker[Session], str],
) -> None:
    app, factory, token = upload_asgi_app
    metadata = "m" * 100
    body, content_type = multipart_body(
        metadata=metadata,
        file_content=b"z" * 100_000,
    )
    sent, consumed = await invoke_upload(
        app,
        body,
        content_type,
        cookie=token,
        chunk_size=4,
    )
    metadata_start = body.index(metadata.encode())
    assert sent[0]["status"] == 413
    assert consumed <= metadata_start + settings.ASSET_METADATA_MAX_BYTES + 4
    assert consumed < len(body)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0


@pytest.mark.parametrize("content_length", [None, 1])
@pytest.mark.asyncio
async def test_authenticated_actual_body_limit_is_413_and_closes_parser_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_length: int | None,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'actual-body-limit.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "UPLOAD_MAX_SIZE", 256)
    monkeypatch.setattr(settings, "ASSET_UPLOAD_OVERHEAD_MAX_BYTES", 256)
    parser_instances: list[assets_module.LimitedMultiPartParser] = []
    real_parser = assets_module.LimitedMultiPartParser

    class TrackingParser(real_parser):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            parser_instances.append(self)

    monkeypatch.setattr(assets_module, "LimitedMultiPartParser", TrackingParser)
    app = FastAPI()
    setup_middleware(app)
    app.include_router(router, prefix="/api/v1")

    def override_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with factory() as db:
        token, user_session = SessionService.create_session(
            db,
            user_id="limited-user",
            client_metadata={"username": "limited-user"},
        )
        user_session.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    body, content_type = multipart_body(metadata="{}", file_content=b"x" * 10_000)
    sent, consumed = await invoke_upload(
        app,
        body,
        content_type,
        cookie=token,
        chunk_size=32,
        content_length=content_length,
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert sent[0]["status"] == 413
    assert response_body == '{"detail":"请求体超过大小限制"}'.encode()
    assert consumed <= 512 + 32
    assert len(parser_instances) == 1
    assert parser_instances[0]._files_to_close_on_error
    assert all(file.closed for file in parser_instances[0]._files_to_close_on_error)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0
    engine.dispose()


@pytest.mark.asyncio
async def test_generic_receive_failure_is_500_and_closes_parser_files(
    upload_asgi_app: tuple[FastAPI, sessionmaker[Session], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, factory, token = upload_asgi_app
    parser_instances: list[assets_module.LimitedMultiPartParser] = []
    real_parser = assets_module.LimitedMultiPartParser

    class TrackingParser(real_parser):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            parser_instances.append(self)

    monkeypatch.setattr(assets_module, "LimitedMultiPartParser", TrackingParser)
    body, content_type = multipart_body(metadata="{}", file_content=b"x" * 10_000)
    sent, _ = await invoke_upload(
        app,
        body,
        content_type,
        cookie=token,
        chunk_size=32,
        receive_error_after=512,
    )
    assert sent[0]["status"] == 500
    assert len(parser_instances) == 1
    assert parser_instances[0]._files_to_close_on_error
    assert all(file.closed for file in parser_instances[0]._files_to_close_on_error)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.scalar(select(func.count()).select_from(AssetBlobChunk)) == 0
