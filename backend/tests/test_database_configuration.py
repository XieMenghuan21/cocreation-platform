from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_production_rejects_sqlite_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./cocreation.db")
    monkeypatch.setenv("JWT_SECRET_KEY", "production-test-secret-that-is-not-a-default")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings()


def test_test_environment_keeps_sqlite_and_default_asset_chunk_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")

    configured_settings = Settings()

    assert configured_settings.DATABASE_URL == "sqlite+pysqlite:///:memory:"
    assert configured_settings.ASSET_CHUNK_SIZE_BYTES == 4 * 1024 * 1024
    assert configured_settings.ASSET_UPLOAD_OVERHEAD_MAX_BYTES == 1024 * 1024
    assert configured_settings.ASSET_METADATA_MAX_BYTES == 64 * 1024


def test_production_rejects_database_scheme_with_postgresql_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresqlite://user:password@localhost/database")
    monkeypatch.setenv("JWT_SECRET_KEY", "production-test-secret-that-is-not-a-default")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings()


def test_prod_alias_rejects_non_postgresql_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./cocreation.db")
    monkeypatch.setenv("JWT_SECRET_KEY", "production-test-secret-that-is-not-a-default")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings()


def test_postgresql_url_uses_psycopg_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost/database")
    monkeypatch.setenv("JWT_SECRET_KEY", "production-test-secret-that-is-not-a-default")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    configured_settings = Settings()

    assert configured_settings.DATABASE_URL == (
        "postgresql+psycopg://user:password@localhost/database"
    )


def test_constructor_production_environment_rejects_default_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+psycopg://user:password@localhost/database",
            JWT_SECRET_KEY="change-me-to-a-strong-random-secret-at-least-32-chars",
            SESSION_COOKIE_SECURE=True,
        )


def production_settings_kwargs() -> dict[str, object]:
    return {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+psycopg://user:password@localhost/database",
        "JWT_SECRET_KEY": "production-test-secret-that-is-not-a-default",
    }


def test_production_requires_secure_session_cookie() -> None:
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        Settings(**production_settings_kwargs(), SESSION_COOKIE_SECURE=False)


def test_samesite_none_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError, match="SameSite"):
        Settings(
            ENVIRONMENT="test",
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            SESSION_COOKIE_SAMESITE="none",
            SESSION_COOKIE_SECURE=False,
        )


def test_production_rejects_enabled_local_test_users() -> None:
    with pytest.raises(ValidationError, match="ENABLE_LOCAL_TEST_USERS"):
        Settings(
            **production_settings_kwargs(),
            SESSION_COOKIE_SECURE=True,
            ENABLE_LOCAL_TEST_USERS=True,
        )


def test_development_can_explicitly_enable_local_test_users() -> None:
    configured = Settings(
        ENVIRONMENT="development",
        DATABASE_URL="sqlite+pysqlite:///:memory:",
        ENABLE_LOCAL_TEST_USERS=True,
    )
    assert configured.ENABLE_LOCAL_TEST_USERS is True


def test_credentialed_cors_rejects_wildcard_origin() -> None:
    with pytest.raises(ValidationError, match="ALLOWED_ORIGINS"):
        Settings(
            ENVIRONMENT="test",
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            ALLOWED_ORIGINS_STR="*",
        )


def test_sso_state_samesite_none_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError, match="SSO state SameSite"):
        Settings(
            ENVIRONMENT="test",
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            SSO_STATE_COOKIE_SAMESITE="none",
            SESSION_COOKIE_SECURE=False,
        )


def test_sso_limits_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            ENVIRONMENT="test",
            DATABASE_URL="sqlite+pysqlite:///:memory:",
            SSO_STATE_RATE_LIMIT_MAX=0,
        )
