"""API 依赖注入。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.identity import AuthIdentityError, auth_user_id
from app.db.session import get_db
from app.services.session_service import (
    InvalidSessionError,
    SessionService,
    SessionTouchResult,
)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object] | None:
    """从 HttpOnly Cookie 解析当前用户，未携带 Cookie 时返回 None。"""
    plain_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not plain_token:
        return None

    try:
        now = datetime.now(timezone.utc)
        user_session = SessionService.resolve_session(
            db,
            plain_token,
            now,
        )
        touch_result = SessionService.touch_session(db, plain_token, now)
        if touch_result is SessionTouchResult.TOUCHED:
            db.commit()
        elif touch_result is SessionTouchResult.THROTTLED:
            db.rollback()
        else:
            raise InvalidSessionError("会话已失效")
    except InvalidSessionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或已过期的会话",
        ) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="无法更新登录会话",
        ) from exc

    metadata = user_session.client_metadata
    username_value = metadata.get("username", user_session.user_id)
    display_name_value = metadata.get("displayName", username_value)
    user: dict[str, object] = {
        "sub": user_session.user_id,
        "username": username_value if isinstance(username_value, str) else user_session.user_id,
        "displayName": (
            display_name_value
            if isinstance(display_name_value, str)
            else user_session.user_id
        ),
    }
    request.state.audit_user = user
    return user


def require_auth(
    request: Request,
    user: dict[str, object] | None = Depends(get_current_user),
) -> dict[str, object]:
    """要求请求携带有效的数据库会话 Cookie。"""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要认证",
        )
    try:
        auth_user_id(user)
    except AuthIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证身份缺少稳定 sub",
        ) from exc
    request.state.audit_user = user
    return user
