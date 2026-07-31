"""数据库会话的创建、解析与撤销。"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.persistence import UserSession


class InvalidSessionError(LookupError):
    """会话不存在或已失效。"""


class SessionTouchResult(str, Enum):
    TOUCHED = "touched"
    THROTTLED = "throttled"
    INVALID = "invalid"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SessionService:
    """无事务提交副作用的数据库会话服务。"""

    @staticmethod
    def _token_hash(plain_token: str) -> str:
        return hashlib.sha256(plain_token.encode("utf-8")).hexdigest()

    @classmethod
    def create_session(
        cls,
        db: Session,
        user_id: str,
        client_metadata: Mapping[str, object],
    ) -> tuple[str, UserSession]:
        plain_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        user_session = UserSession(
            user_id=user_id,
            token_hash=cls._token_hash(plain_token),
            created_at=now,
            expires_at=now + timedelta(minutes=settings.SESSION_TTL_MINUTES),
            client_metadata=dict(client_metadata),
        )
        db.add(user_session)
        return plain_token, user_session

    @classmethod
    def resolve_session(
        cls,
        db: Session,
        plain_token: str,
        now: datetime,
    ) -> UserSession:
        if not plain_token:
            raise InvalidSessionError("会话令牌缺失")

        user_session = db.scalar(
            select(UserSession).where(
                UserSession.token_hash == cls._token_hash(plain_token)
            )
        )
        normalized_now = _as_utc(now)
        if (
            user_session is None
            or user_session.revoked_at is not None
            or _as_utc(user_session.expires_at) <= normalized_now
        ):
            raise InvalidSessionError("会话无效或已过期")

        return user_session

    @classmethod
    def touch_session(
        cls,
        db: Session,
        plain_token: str,
        now: datetime,
    ) -> SessionTouchResult:
        normalized_now = _as_utc(now)
        touch_before = normalized_now - timedelta(
            seconds=settings.SESSION_LAST_SEEN_TOUCH_SECONDS
        )
        result = db.execute(
            update(UserSession)
            .where(
                UserSession.token_hash == cls._token_hash(plain_token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > normalized_now,
                or_(
                    UserSession.last_seen_at.is_(None),
                    UserSession.last_seen_at < touch_before,
                ),
            )
            .values(last_seen_at=normalized_now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            return SessionTouchResult.TOUCHED
        still_valid = db.scalar(
            select(UserSession.id)
            .where(
                UserSession.token_hash == cls._token_hash(plain_token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > normalized_now,
            )
            .limit(1)
        )
        if still_valid is not None:
            return SessionTouchResult.THROTTLED
        return SessionTouchResult.INVALID

    @classmethod
    def revoke_session(
        cls,
        db: Session,
        plain_token: str,
        now: datetime | None = None,
    ) -> bool:
        if not plain_token:
            return False
        result = db.execute(
            update(UserSession)
            .where(
                UserSession.token_hash == cls._token_hash(plain_token),
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=_as_utc(now or datetime.now(timezone.utc)))
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    @staticmethod
    def cleanup_expired(db: Session, now: datetime | None = None) -> int:
        result = db.execute(
            delete(UserSession).where(
                UserSession.expires_at <= _as_utc(now or datetime.now(timezone.utc))
            )
        )
        return int(result.rowcount or 0)
