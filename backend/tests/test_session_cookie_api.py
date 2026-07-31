from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import os
from pathlib import Path
import secrets
from threading import Barrier, Lock, Thread

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.auth as auth_module
from app.api.deps import get_current_user
from app.api.deps import require_auth
from app.api.v1.auth import router as auth_router
from app.config.settings import normalize_database_url, settings
from app.core.middleware import setup_middleware
from app.db.session import Base, get_db
from app.models.persistence import SsoAuthorizationState, UserSession
from app.services.session_service import SessionService


class OfflineAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> OfflineAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def post(self, url: str, *, json: dict[str, object]) -> httpx.Response:
        del url, json
        raise httpx.ConnectError("offline")


class FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = "response"

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "upstream error",
                request=httpx.Request("GET", "https://upstream.test"),
                response=httpx.Response(self.status_code),
            )


class InvalidJsonResponse(FakeResponse):
    def __init__(self, status_code: int) -> None:
        super().__init__({}, status_code)
        self.text = "<private malformed html>"

    def json(self) -> dict[str, object]:
        raise ValueError("invalid JSON containing private upstream body")


class StatusAsyncClient:
    status_code = 401
    payload: object = {"detail": "sensitive upstream detail"}

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> StatusAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def post(self, url: str, *, json: dict[str, object]) -> FakeResponse:
        del url, json
        if not isinstance(self.payload, dict):
            return InvalidJsonResponse(self.status_code)
        return FakeResponse(self.payload, self.status_code)


class ExchangeAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> ExchangeAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        assert url.endswith("/api/v1/auth/me")
        assert headers == {"Authorization": "Bearer platform-token"}
        return FakeResponse(
            {
                "code": 200,
                "data": {
                    "username": "platform-user",
                    "name": "平台用户",
                },
            }
        )


class PasswordSuccessAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> PasswordSuccessAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def post(self, url: str, *, json: dict[str, object]) -> FakeResponse:
        assert url.endswith("/api/v1/auth/login/password")
        assert json == {"identifier": "remote-admin", "password": "secret"}
        return FakeResponse(
            {
                "code": 200,
                "data": {
                    "username": "remote-admin",
                    "displayName": "远程管理员",
                },
            }
        )


class SsoAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> SsoAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def post(self, url: str, *, json: dict[str, object]) -> FakeResponse:
        assert url.endswith("/api/v1/sso-provider/token")
        assert json["code"] == "authorization-code"
        return FakeResponse({"access_token": "upstream-access-token"})

    async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        assert url.endswith("/api/v1/sso-provider/userinfo")
        assert headers == {"Authorization": "Bearer upstream-access-token"}
        return FakeResponse(
            {
                "username": "sso-user",
                "displayName": "单点用户",
            }
        )


class SsoMalformedAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> SsoMalformedAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def post(self, url: str, *, json: dict[str, object]) -> InvalidJsonResponse:
        del url, json
        return InvalidJsonResponse(200)


class MissingIdentityAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> MissingIdentityAsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def post(self, url: str, *, json: dict[str, object]) -> FakeResponse:
        if url.endswith("/sso-provider/token"):
            return FakeResponse({"access_token": "upstream-access-token"})
        return FakeResponse({"data": {"displayName": "无标识用户"}})

    async def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        del url, headers
        return FakeResponse({"username": "", "sub": "", "displayName": "无标识用户"})


@dataclass
class AuthHarness:
    app: FastAPI
    client: TestClient
    session_factory: sessionmaker[Session]


@pytest.fixture()
def database(tmp_path: Path) -> Generator[tuple[Engine, sessionmaker[Session]], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-cookie.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield engine, session_factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def auth_harness(
    database: tuple[Engine, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[AuthHarness, None, None]:
    _, session_factory = database
    app = FastAPI()
    setup_middleware(app)
    app.include_router(auth_router, prefix="/api/v1")

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", OfflineAsyncClient)
    monkeypatch.setattr(settings, "ENABLE_LOCAL_TEST_USERS", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield AuthHarness(app=app, client=client, session_factory=session_factory)
    app.dependency_overrides.clear()


def login_admin(client: TestClient) -> httpx.Response:
    return client.post(
        "/api/v1/auth/login/password",
        json={
            "username": "admin",
            "password": "admin123",
            "auth_source": "local",
        },
        headers={"Origin": "http://localhost:5174"},
    )


def test_session_service_stores_only_sha256_and_resolves_active_session(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _, session_factory = database
    now = datetime.now(timezone.utc)

    with session_factory() as db:
        plain_token, created = SessionService.create_session(
            db,
            user_id="admin",
            client_metadata={"displayName": "管理员"},
        )
        db.commit()

        assert created.token_hash != plain_token
        assert len(created.token_hash) == 64
        assert all(character in "0123456789abcdef" for character in created.token_hash)
        assert SessionService.resolve_session(db, plain_token, now) is created
        assert db.scalar(select(UserSession).where(UserSession.token_hash == plain_token)) is None


def test_session_service_cleanup_deletes_only_expired_without_committing(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _, session_factory = database
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        _, expired = SessionService.create_session(db, "expired", {})
        _, active = SessionService.create_session(db, "active", {})
        expired.expires_at = now - timedelta(seconds=1)
        active.expires_at = now + timedelta(hours=1)
        db.commit()
        expired_id = expired.id

        assert SessionService.cleanup_expired(db, now) == 1
        assert db.get(UserSession, expired_id) is None
        assert db.get(UserSession, active.id) is not None

        db.rollback()

    with session_factory() as verification_db:
        assert verification_db.get(UserSession, expired_id) is not None


def test_password_login_sets_http_only_cookie_without_returning_token(
    auth_harness: AuthHarness,
) -> None:
    response = login_admin(auth_harness.client)

    assert response.status_code == 200
    payload = response.json()
    assert "token" not in payload
    assert "access_token" not in payload
    assert "token" not in payload.get("data", {})
    set_cookie = response.headers["set-cookie"]
    assert f"{settings.SESSION_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert f"Max-Age={settings.SESSION_TTL_MINUTES * 60}" in set_cookie

    plain_token = auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert plain_token
    with auth_harness.session_factory() as db:
        persisted = db.scalar(select(UserSession))
        assert persisted is not None
        assert persisted.token_hash != plain_token
        assert len(persisted.token_hash) == 64


def test_main_platform_login_sets_cookie_without_returning_token(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", PasswordSuccessAsyncClient)

    response = auth_harness.client.post(
        "/api/v1/auth/login",
        json={"username": "remote-admin", "password": "secret"},
        headers={"Origin": "http://localhost:5174"},
    )

    assert response.status_code == 200
    assert "token" not in response.json()
    assert "access_token" not in response.json()
    assert auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert response.json()["data"]["user"]["displayName"] == "远程管理员"


def test_me_uses_cookie_and_returns_client_metadata_display_name(
    auth_harness: AuthHarness,
) -> None:
    assert login_admin(auth_harness.client).status_code == 200

    response = auth_harness.client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "username": "admin",
        "displayName": "管理员",
    }


def test_missing_random_and_bearer_only_credentials_are_rejected(
    auth_harness: AuthHarness,
) -> None:
    missing = auth_harness.client.get("/api/v1/auth/me")
    auth_harness.client.cookies.set(
        settings.SESSION_COOKIE_NAME,
        "random-session-token",
    )
    random_cookie = auth_harness.client.get("/api/v1/auth/me")
    auth_harness.client.cookies.delete(settings.SESSION_COOKIE_NAME)
    bearer_only = auth_harness.client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer legacy-jwt"},
    )

    assert missing.status_code == 401
    assert random_cookie.status_code == 401
    assert bearer_only.status_code == 401
    assert "WWW-Authenticate" not in bearer_only.headers


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_expired_and_revoked_sessions_are_rejected(
    auth_harness: AuthHarness,
    state: str,
) -> None:
    assert login_admin(auth_harness.client).status_code == 200
    plain_token = auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert plain_token

    with auth_harness.session_factory() as db:
        values = (
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
            if state == "expired"
            else {"revoked_at": datetime.now(timezone.utc)}
        )
        db.execute(update(UserSession).values(**values))
        db.commit()

    response = auth_harness.client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_logout_revokes_session_clears_cookie_and_invalidates_old_cookie(
    auth_harness: AuthHarness,
) -> None:
    assert login_admin(auth_harness.client).status_code == 200
    old_cookie = auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert old_cookie

    response = auth_harness.client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:5174"},
    )

    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME) is None
    with auth_harness.session_factory() as db:
        persisted = db.scalar(select(UserSession))
        assert persisted is not None
        assert persisted.revoked_at is not None

    auth_harness.client.cookies.set(settings.SESSION_COOKIE_NAME, old_cookie)
    assert auth_harness.client.get("/api/v1/auth/me").status_code == 401


def test_two_sessions_can_be_revoked_independently(auth_harness: AuthHarness) -> None:
    first = TestClient(auth_harness.app, raise_server_exceptions=False)
    second = TestClient(auth_harness.app, raise_server_exceptions=False)
    try:
        assert login_admin(first).status_code == 200
        assert login_admin(second).status_code == 200
        first_cookie = first.cookies.get(settings.SESSION_COOKIE_NAME)
        second_cookie = second.cookies.get(settings.SESSION_COOKIE_NAME)
        assert first_cookie and second_cookie and first_cookie != second_cookie

        assert first.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost:5174"},
        ).status_code == 200
        first.cookies.set(settings.SESSION_COOKIE_NAME, first_cookie)
        assert first.get("/api/v1/auth/me").status_code == 401
        assert second.get("/api/v1/auth/me").status_code == 200
    finally:
        first.close()
        second.close()


def test_exchange_sets_cookie_without_returning_token(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", ExchangeAsyncClient)

    response = auth_harness.client.post(
        "/api/v1/auth/exchange",
        json={"platform_token": "platform-token"},
        headers={"Origin": "http://localhost:5174"},
    )

    assert response.status_code == 200
    assert "access_token" not in response.json()
    assert "token" not in response.json()
    assert auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert response.json()["display_name"] == "平台用户"


def test_sso_callback_redirect_has_no_token_and_sets_cookie(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", SsoAsyncClient)
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://frontend.test/app")

    start_response = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    state = start_response.json()["state"]
    response = auth_harness.client.get(
        f"/api/v1/auth/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code in {302, 303, 307}
    assert response.headers["location"] == "https://frontend.test/app"
    assert "token" not in response.headers["location"].lower()
    assert auth_harness.client.cookies.get(settings.SESSION_COOKIE_NAME)


def test_sso_state_is_hashed_bound_and_single_use(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", SsoAsyncClient)
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    start = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    assert start.status_code == 200
    state = start.json()["state"]
    binding = auth_harness.client.cookies.get(settings.SSO_STATE_COOKIE_NAME)
    assert binding
    with auth_harness.session_factory() as db:
        stored = db.scalar(select(SsoAuthorizationState))
        assert stored is not None
        assert stored.state_hash == hashlib.sha256(state.encode()).hexdigest()
        assert stored.state_hash != state
        assert stored.browser_binding_hash != binding

    success = auth_harness.client.get(
        f"/api/v1/auth/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    assert success.status_code in {302, 303, 307}
    auth_harness.client.cookies.set(settings.SSO_STATE_COOKIE_NAME, binding)
    replay = auth_harness.client.get(
        f"/api/v1/auth/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    assert replay.status_code == 400


def test_sso_callback_rejects_missing_or_mismatched_binding(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    missing = auth_harness.client.get(
        "/api/v1/auth/sso/callback?code=authorization-code&state=missing",
        follow_redirects=False,
    )
    assert missing.status_code == 400
    start = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    state = start.json()["state"]
    auth_harness.client.cookies.set(settings.SSO_STATE_COOKIE_NAME, "wrong-binding")
    mismatch = auth_harness.client.get(
        f"/api/v1/auth/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    assert mismatch.status_code == 400
    assert "Max-Age=0" in mismatch.headers["set-cookie"]


def test_sso_malformed_upstream_response_is_sanitized_and_clears_binding(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    start = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    state = start.json()["state"]
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", SsoMalformedAsyncClient)
    response = auth_harness.client.get(
        f"/api/v1/auth/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "SSO 登录失败"
    assert "private" not in response.text
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_remote_password_user_without_stable_identity_is_rejected(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", MissingIdentityAsyncClient)
    response = auth_harness.client.post(
        "/api/v1/auth/login/password",
        json={"username": "claimed-name", "password": "secret"},
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 502
    assert "set-cookie" not in response.headers
    with auth_harness.session_factory() as db:
        assert db.scalar(select(UserSession)) is None


def test_sso_userinfo_without_stable_identity_is_rejected(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    start = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    state = start.json()["state"]
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", MissingIdentityAsyncClient)
    response = auth_harness.client.get(
        f"/api/v1/auth/sso/callback?code=authorization-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 502
    assert "set-cookie" in response.headers
    with auth_harness.session_factory() as db:
        assert db.scalar(select(UserSession)) is None


def test_sso_start_rate_limits_same_client_ip_in_database(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "SSO_STATE_RATE_LIMIT_MAX", 2)
    responses = [
        auth_harness.client.post(
            "/api/v1/auth/sso/start",
            headers={"Origin": "http://localhost:5174", "X-Forwarded-For": f"evil-{index}"},
        )
        for index in range(3)
    ]
    assert [response.status_code for response in responses] == [200, 200, 429]
    with auth_harness.session_factory() as db:
        assert len(db.scalars(select(SsoAuthorizationState)).all()) == 2


class PostgreSqlDialect:
    name = "postgresql"


class PostgreSqlBind:
    dialect = PostgreSqlDialect()


class AdvisoryLockProbe:
    def __init__(self) -> None:
        self.parameters: list[dict[str, int]] = []

    def get_bind(self) -> PostgreSqlBind:
        return PostgreSqlBind()

    def execute(
        self,
        statement: object,
        parameters: dict[str, int],
    ) -> RowCountResult:
        assert "pg_advisory_xact_lock" in str(statement)
        self.parameters.append(parameters)
        return RowCountResult()


def test_postgres_sso_advisory_lock_keys_are_stable_distinct_and_sorted() -> None:
    request_ip_hash = "a" * 64
    binding_hash = "b" * 64
    first = auth_module._ordered_sso_advisory_lock_keys(
        request_ip_hash,
        binding_hash,
    )
    second = auth_module._ordered_sso_advisory_lock_keys(
        request_ip_hash,
        binding_hash,
    )
    assert first == second
    assert first == tuple(sorted(first))
    assert len(first) == 2
    assert first[0] != first[1]
    assert all(-(2**63) <= key < 2**63 for key in first)

    probe = AdvisoryLockProbe()
    auth_module._acquire_sso_advisory_locks(
        probe,
        request_ip_hash,
        binding_hash,
    )
    assert [item["key"] for item in probe.parameters] == list(first)
    source = inspect.getsource(auth_module._create_sso_state_record)
    assert source.index("_acquire_sso_advisory_locks") < source.index("delete(")


def test_sso_start_limits_active_states_for_same_binding(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "SSO_STATE_MAX_ACTIVE_PER_BINDING", 1)
    first = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    binding = auth_harness.client.cookies.get(settings.SSO_STATE_COOKIE_NAME)
    second = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    assert first.status_code == 200
    assert binding
    assert second.status_code == 429
    assert auth_harness.client.cookies.get(settings.SSO_STATE_COOKIE_NAME) == binding


def test_sso_state_cookie_stays_lax_when_session_cookie_is_strict(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "SESSION_COOKIE_SAMESITE", "strict")
    monkeypatch.setattr(settings, "SSO_STATE_COOKIE_SAMESITE", "lax")
    response = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 200
    assert "SameSite=lax" in response.headers["set-cookie"]


def test_sso_start_cleans_expired_and_old_consumed_rows(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SSO_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "SSO_CLIENT_SECRET", "client-secret")
    now = datetime.now(timezone.utc)
    with auth_harness.session_factory() as db:
        db.add_all(
            [
                SsoAuthorizationState(
                    state_hash="a" * 64,
                    browser_binding_hash="b" * 64,
                    request_ip_hash="c" * 64,
                    redirect_uri=settings.SSO_REDIRECT_URI,
                    created_at=now - timedelta(days=2),
                    expires_at=now - timedelta(days=1),
                ),
                SsoAuthorizationState(
                    state_hash="d" * 64,
                    browser_binding_hash="e" * 64,
                    request_ip_hash="f" * 64,
                    redirect_uri=settings.SSO_REDIRECT_URI,
                    created_at=now - timedelta(days=2),
                    expires_at=now + timedelta(days=1),
                    consumed_at=now - timedelta(days=1),
                ),
            ]
        )
        db.commit()
    response = auth_harness.client.post(
        "/api/v1/auth/sso/start",
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 200
    with auth_harness.session_factory() as db:
        assert len(db.scalars(select(SsoAuthorizationState)).all()) == 1


def test_cookie_uses_secure_and_samesite_settings(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(settings, "SESSION_COOKIE_SAMESITE", "strict")

    response = login_admin(auth_harness.client)

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_upstream_failure_never_falls_back_to_local_admin(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    monkeypatch.setattr(settings, "ENABLE_LOCAL_TEST_USERS", True)
    StatusAsyncClient.status_code = status_code
    StatusAsyncClient.payload = {"detail": "sensitive upstream detail"}
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", StatusAsyncClient)

    response = auth_harness.client.post(
        "/api/v1/auth/login/password",
        json={"username": "admin", "password": "admin123"},
        headers={"Origin": "http://localhost:5174"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"
    assert "set-cookie" not in response.headers
    assert "sensitive" not in response.text


def test_malformed_upstream_json_is_sanitized(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    StatusAsyncClient.status_code = 200
    StatusAsyncClient.payload = "<invalid>"
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", StatusAsyncClient)
    response = auth_harness.client.post(
        "/api/v1/auth/login/password",
        json={"username": "remote", "password": "secret"},
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "身份验证服务响应无效"
    assert "private" not in response.text
    assert "set-cookie" not in response.headers


def test_production_offline_local_admin_cannot_login(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENABLE_LOCAL_TEST_USERS", False)
    response = login_admin(auth_harness.client)
    assert response.status_code in {401, 403}
    assert "set-cookie" not in response.headers


def test_last_seen_is_committed_before_route_execution(
    auth_harness: AuthHarness,
) -> None:
    assert login_admin(auth_harness.client).status_code == 200
    with auth_harness.session_factory() as db:
        persisted = db.scalar(select(UserSession))
        assert persisted is not None
        session_id = persisted.id
        assert persisted.last_seen_at is None

    assert auth_harness.client.get("/api/v1/auth/me").status_code == 200
    with auth_harness.session_factory() as db:
        reloaded = db.get(UserSession, session_id)
        assert reloaded is not None
        assert reloaded.last_seen_at is not None


def test_last_seen_second_request_within_window_does_not_change_timestamp(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert login_admin(auth_harness.client).status_code == 200
    original_commit = Session.commit
    commit_count = 0

    def counting_commit(db: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit(db)

    monkeypatch.setattr(Session, "commit", counting_commit)
    assert auth_harness.client.get("/api/v1/auth/me").status_code == 200
    with auth_harness.session_factory() as db:
        first_touch = db.scalar(select(UserSession.last_seen_at))
    assert first_touch is not None
    assert auth_harness.client.get("/api/v1/auth/me").status_code == 200
    with auth_harness.session_factory() as db:
        second_touch = db.scalar(select(UserSession.last_seen_at))
    assert second_touch == first_touch
    assert commit_count == 1


@pytest.mark.parametrize("invalidated_by", ["revoke", "expire"])
def test_session_invalidated_between_resolve_and_touch_is_rejected(
    auth_harness: AuthHarness,
    monkeypatch: pytest.MonkeyPatch,
    invalidated_by: str,
) -> None:
    assert login_admin(auth_harness.client).status_code == 200
    original_resolve = SessionService.resolve_session

    def invalidate_after_resolve(
        cls: type[SessionService],
        db: Session,
        plain_token: str,
        now: datetime,
    ) -> UserSession:
        resolved = original_resolve(db, plain_token, now)
        values: dict[str, object] = (
            {"revoked_at": now}
            if invalidated_by == "revoke"
            else {"expires_at": now - timedelta(seconds=1)}
        )
        db.execute(
            update(UserSession)
            .where(UserSession.id == resolved.id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return resolved

    monkeypatch.setattr(
        SessionService,
        "resolve_session",
        classmethod(invalidate_after_resolve),
    )
    response = auth_harness.client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_database_auth_dependencies_are_sync_for_threadpool_execution() -> None:
    assert inspect.iscoroutinefunction(get_current_user) is False
    assert inspect.iscoroutinefunction(require_auth) is False
    auth_source = inspect.getsource(auth_module)
    assert "await run_in_threadpool" in auth_source


def test_revoke_session_is_idempotent_atomic_update(
    database: tuple[Engine, sessionmaker[Session]],
) -> None:
    _, session_factory = database
    with session_factory() as db:
        token, _ = SessionService.create_session(db, "atomic-user", {})
        db.commit()
        assert SessionService.revoke_session(db, token) is True
        db.commit()
        assert SessionService.revoke_session(db, token) is False


def test_csrf_origin_and_preflight_enforcement(auth_harness: AuthHarness) -> None:
    missing = auth_harness.client.post(
        "/api/v1/auth/login/password",
        json={"username": "admin", "password": "admin123", "auth_source": "local"},
    )
    evil = auth_harness.client.post(
        "/api/v1/auth/login/password",
        json={"username": "admin", "password": "admin123", "auth_source": "local"},
        headers={"Origin": "https://evil.test"},
    )
    trusted_preflight = auth_harness.client.options(
        "/api/v1/auth/login/password",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
        },
    )
    evil_preflight = auth_harness.client.options(
        "/api/v1/auth/login/password",
        headers={
            "Origin": "https://evil.test",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert missing.status_code == 403
    assert evil.status_code == 403
    assert trusted_preflight.status_code == 200
    assert trusted_preflight.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert evil_preflight.status_code == 400


class FailingCommitDatabase:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.rollback_called = False

    def add(self, value: object) -> None:
        self.added.append(value)

    def commit(self) -> None:
        raise RuntimeError("database commit failed")

    def rollback(self) -> None:
        self.rollback_called = True


class RowCountResult:
    rowcount = 1


class FailingLogoutDatabase(FailingCommitDatabase):
    def execute(self, statement: object) -> RowCountResult:
        del statement
        return RowCountResult()


def test_database_commit_failure_does_not_set_cookie(
    auth_harness: AuthHarness,
) -> None:
    failing_db = FailingCommitDatabase()

    def override_failing_db() -> Generator[FailingCommitDatabase, None, None]:
        yield failing_db

    auth_harness.app.dependency_overrides[get_db] = override_failing_db
    response = login_admin(auth_harness.client)

    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert failing_db.rollback_called


def test_logout_commit_failure_does_not_clear_cookie(
    auth_harness: AuthHarness,
) -> None:
    failing_db = FailingLogoutDatabase()

    def override_failing_db() -> Generator[FailingLogoutDatabase, None, None]:
        yield failing_db

    auth_harness.client.cookies.set(settings.SESSION_COOKIE_NAME, "existing-token")
    auth_harness.app.dependency_overrides[get_db] = override_failing_db
    response = auth_harness.client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert failing_db.rollback_called


@pytest.mark.postgres_integration
def test_postgres_concurrent_revoke_has_single_winner() -> None:
    database_url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_DATABASE_URL is not configured")
    engine = create_engine(normalize_database_url(database_url))
    UserSession.__table__.create(bind=engine, checkfirst=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        token, created = SessionService.create_session(db, "concurrent-revoke", {})
        db.commit()
        session_id = created.id

    barrier = Barrier(2)
    lock = Lock()
    outcomes: list[bool] = []

    def revoke_once() -> None:
        with session_factory() as db:
            barrier.wait()
            outcome = SessionService.revoke_session(db, token)
            db.commit()
        with lock:
            outcomes.append(outcome)

    threads = [Thread(target=revoke_once), Thread(target=revoke_once)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [False, True]
    with session_factory() as db:
        persisted = db.get(UserSession, session_id)
        assert persisted is not None
        db.delete(persisted)
        db.commit()
    engine.dispose()


@pytest.mark.postgres_integration
def test_postgres_concurrent_sso_start_burst_respects_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_DATABASE_URL is not configured")
    monkeypatch.setattr(settings, "SSO_STATE_RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(settings, "SSO_STATE_MAX_ACTIVE_PER_BINDING", 2)
    engine = create_engine(normalize_database_url(database_url))
    SsoAuthorizationState.__table__.create(bind=engine, checkfirst=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    request_ip_hash = hashlib.sha256(b"burst-ip").hexdigest()
    binding = "shared-browser-binding"
    binding_hash = hashlib.sha256(binding.encode()).hexdigest()
    with session_factory() as db:
        db.query(SsoAuthorizationState).filter(
            (SsoAuthorizationState.request_ip_hash == request_ip_hash)
            | (SsoAuthorizationState.browser_binding_hash == binding_hash)
        ).delete(synchronize_session=False)
        db.commit()

    barrier = Barrier(5)
    lock = Lock()
    outcomes: list[str | None] = []

    def create_once(index: int) -> None:
        with session_factory() as db:
            barrier.wait()
            outcome = auth_module._create_sso_state_record(
                db,
                f"state-{index}-{secrets.token_urlsafe(8)}",
                binding,
                request_ip_hash,
                datetime.now(timezone.utc),
            )
        with lock:
            outcomes.append(outcome)

    threads = [Thread(target=create_once, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(outcome is None for outcome in outcomes) <= 2
    assert sum(outcome is not None for outcome in outcomes) >= 3
    with session_factory() as db:
        active_count = len(
            db.scalars(
                select(SsoAuthorizationState).where(
                    SsoAuthorizationState.request_ip_hash == request_ip_hash,
                    SsoAuthorizationState.consumed_at.is_(None),
                    SsoAuthorizationState.expires_at > datetime.now(timezone.utc),
                )
            ).all()
        )
        assert active_count <= 2
        db.query(SsoAuthorizationState).filter(
            SsoAuthorizationState.request_ip_hash == request_ip_hash
        ).delete(synchronize_session=False)
        db.commit()
    engine.dispose()


def test_get_current_user_sets_audit_user(auth_harness: AuthHarness) -> None:
    assert login_admin(auth_harness.client).status_code == 200

    @auth_harness.app.get("/audit-user")
    async def audit_user_probe(
        request: Request,
        request_user: dict[str, object] | None = Depends(get_current_user),
    ) -> dict[str, object]:
        assert request_user is not None
        return request.state.audit_user

    response = auth_harness.client.get("/audit-user")
    assert response.status_code == 200
    assert response.json()["displayName"] == "管理员"
