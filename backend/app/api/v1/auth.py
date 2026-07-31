"""认证与 SSO 单点登录接口。"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_auth
from app.config.settings import settings
from app.core.security import get_password_hash, verify_password
from app.db.session import get_db
from app.models.persistence import SsoAuthorizationState
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    """独立登录请求。"""

    username: str = Field(..., max_length=120)
    password: str = Field(..., max_length=256)
    auth_source: Literal["platform", "local"] = "platform"


class TokenExchangeRequest(BaseModel):
    """主平台 token 交换请求。"""

    platform_token: str = Field(..., description="主平台的 JWT token")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text_field(
    payload: Mapping[str, object],
    key: str,
    fallback: str = "",
) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else fallback


def _stable_identity(payload: Mapping[str, object]) -> str | None:
    for key in ("sub", "username"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _commit_session(
    db: Session,
    user_id: str,
    username: str,
    display_name: str,
    *,
    source: str,
) -> str:
    plain_token, _ = SessionService.create_session(
        db,
        user_id=user_id,
        client_metadata={
            "username": username,
            "displayName": display_name,
            "source": source,
        },
    )
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("创建登录会话时数据库提交失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="无法创建登录会话",
        ) from exc
    return plain_token


def _set_session_cookie(response: Response, plain_token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=plain_token,
        max_age=settings.SESSION_TTL_MINUTES * 60,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clear_sso_state_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.SSO_STATE_COOKIE_NAME, path="/")


def _sso_error(detail: str, status_code: int = 400) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content={"detail": detail})
    _clear_sso_state_cookie(response)
    return response


def _sso_advisory_lock_key(domain: str, value: str) -> int:
    digest = hashlib.sha256(f"{domain}\0{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _ordered_sso_advisory_lock_keys(
    request_ip_hash: str,
    binding_hash: str,
) -> tuple[int, int]:
    return tuple(
        sorted(
            (
                _sso_advisory_lock_key("sso-ip", request_ip_hash),
                _sso_advisory_lock_key("sso-binding", binding_hash),
            )
        )
    )


def _acquire_sso_advisory_locks(
    db: Session,
    request_ip_hash: str,
    binding_hash: str,
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for key in _ordered_sso_advisory_lock_keys(request_ip_hash, binding_hash):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": key},
        )


def _create_sso_state_record(
    db: Session,
    state: str,
    browser_binding: str,
    request_ip_hash: str,
    now: datetime,
) -> str | None:
    binding_hash = _sha256(browser_binding)
    _acquire_sso_advisory_locks(db, request_ip_hash, binding_hash)
    retention_before = now - timedelta(minutes=settings.SSO_STATE_RETENTION_MINUTES)
    db.execute(
        delete(SsoAuthorizationState).where(
            or_(
                SsoAuthorizationState.expires_at <= now,
                SsoAuthorizationState.consumed_at <= retention_before,
            )
        )
    )
    window_start = now - timedelta(
        seconds=settings.SSO_STATE_RATE_LIMIT_WINDOW_SECONDS
    )
    ip_count = db.scalar(
        select(func.count())
        .select_from(SsoAuthorizationState)
        .where(
            SsoAuthorizationState.request_ip_hash == request_ip_hash,
            SsoAuthorizationState.created_at >= window_start,
        )
    )
    binding_count = db.scalar(
        select(func.count())
        .select_from(SsoAuthorizationState)
        .where(
            SsoAuthorizationState.browser_binding_hash == binding_hash,
            SsoAuthorizationState.consumed_at.is_(None),
            SsoAuthorizationState.expires_at > now,
        )
    )
    if int(ip_count or 0) >= settings.SSO_STATE_RATE_LIMIT_MAX:
        db.commit()
        return "请求过于频繁"
    if int(binding_count or 0) >= settings.SSO_STATE_MAX_ACTIVE_PER_BINDING:
        db.commit()
        return "活动授权请求过多"
    db.add(
        SsoAuthorizationState(
            state_hash=_sha256(state),
            browser_binding_hash=binding_hash,
            request_ip_hash=request_ip_hash,
            redirect_uri=settings.SSO_REDIRECT_URI,
            created_at=now,
            expires_at=now + timedelta(minutes=settings.SSO_STATE_TTL_MINUTES),
        )
    )
    db.commit()
    return None


def _consume_sso_state_record(
    db: Session,
    state_hash: str,
    binding_hash: str,
    now: datetime,
) -> bool:
    stored_state = db.scalar(
        select(SsoAuthorizationState).where(
            SsoAuthorizationState.state_hash == state_hash
        )
    )
    if (
        stored_state is None
        or stored_state.consumed_at is not None
        or _as_utc(stored_state.expires_at) <= now
        or stored_state.redirect_uri != settings.SSO_REDIRECT_URI
        or not hmac.compare_digest(stored_state.browser_binding_hash, binding_hash)
    ):
        db.rollback()
        return False
    result = db.execute(
        update(SsoAuthorizationState)
        .where(
            SsoAuthorizationState.id == stored_state.id,
            SsoAuthorizationState.consumed_at.is_(None),
            SsoAuthorizationState.expires_at > now,
            SsoAuthorizationState.redirect_uri == settings.SSO_REDIRECT_URI,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        db.rollback()
        return False
    db.commit()
    return True


def _revoke_session_committed(db: Session, plain_token: str) -> None:
    SessionService.revoke_session(db, plain_token)
    db.commit()


@router.post("/login", response_model=dict, summary="独立账号密码登录")
async def independent_login(
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """通过主平台验证账号，并建立本地数据库会话。"""
    main_platform_url = settings.MAIN_PLATFORM_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            upstream_response = await client.post(
                f"{main_platform_url}/api/v1/auth/login/password",
                json={
                    "identifier": request.username,
                    "password": request.password,
                },
            )
            if upstream_response.status_code != 200:
                logger.warning(
                    "主平台登录拒绝请求 status=%s",
                    upstream_response.status_code,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="用户名或密码错误",
                )
            try:
                upstream_payload = _mapping(upstream_response.json())
            except (TypeError, ValueError) as exc:
                logger.warning("主平台登录响应不是有效 JSON")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="身份验证服务响应无效",
                ) from exc
            remote_user = _mapping(upstream_payload.get("data"))
    except httpx.HTTPError as exc:
        logger.warning("主平台登录连接失败 type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="身份验证服务暂不可用",
        ) from exc

    user_id = _stable_identity(remote_user)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="身份验证服务响应无效",
        )
    username = _text_field(remote_user, "username", user_id)
    display_name = _text_field(remote_user, "displayName", username)
    plain_token = await run_in_threadpool(
        _commit_session,
        db,
        user_id,
        username,
        display_name,
        source="main-platform-password",
    )
    _set_session_cookie(response, plain_token)
    return {
        "code": 200,
        "message": "登录成功",
        "data": {
            "user": {
                "username": username,
                "displayName": display_name,
            }
        },
    }


_LOCAL_TEST_USERS: dict[str, dict[str, str]] = {
    "admin": {
        "password_hash": get_password_hash("admin123"),
        "displayName": "管理员",
    },
    "demo": {
        "password_hash": get_password_hash("demo123"),
        "displayName": "演示用户",
    },
    "test": {
        "password_hash": get_password_hash("test123"),
        "displayName": "测试用户",
    },
}


@router.post("/login/password", summary="独立登录（兼容格式）")
async def login_password(
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """按显式认证来源验证，绝不在上游失败后降级。"""
    main_platform_url = settings.MAIN_PLATFORM_URL.rstrip("/")
    if request.auth_source == "local":
        if (
            not settings.ENABLE_LOCAL_TEST_USERS
            or settings.ENVIRONMENT.lower() not in {"development", "test"}
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="本地测试账号未启用",
            )
        local_user = _LOCAL_TEST_USERS.get(request.username)
        if (
            local_user is None
            or not verify_password(request.password, local_user["password_hash"])
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )
        remote_user = {
            "username": request.username,
            "displayName": local_user["displayName"],
        }
    else:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                upstream_response = await client.post(
                    f"{main_platform_url}/api/v1/auth/login/password",
                    json={
                        "identifier": request.username,
                        "password": request.password,
                    },
                )
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.warning("主平台登录连接失败 type=%s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="身份验证服务暂不可用",
            ) from exc

        if upstream_response.status_code != 200:
            logger.warning(
                "主平台登录拒绝请求 status=%s",
                upstream_response.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )
        try:
            upstream_payload = _mapping(upstream_response.json())
        except (TypeError, ValueError) as exc:
            logger.warning("主平台登录响应不是有效 JSON")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="身份验证服务响应无效",
            ) from exc
        remote_user = _mapping(upstream_payload.get("data"))
        if not remote_user:
            logger.warning("主平台登录响应缺少用户数据")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="身份验证服务响应无效",
            )

    user_id = (
        request.username
        if request.auth_source == "local"
        else _stable_identity(remote_user)
    )
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="身份验证服务响应无效",
        )
    username = _text_field(remote_user, "username", user_id)
    display_name = _text_field(remote_user, "displayName", username)
    plain_token = await run_in_threadpool(
        _commit_session,
        db,
        user_id,
        username,
        display_name,
        source="password",
    )
    _set_session_cookie(response, plain_token)
    return {
        "username": username,
        "display_name": display_name,
    }


@router.post("/exchange", summary="主平台 token 交换为本地会话")
async def exchange_platform_token(
    request: TokenExchangeRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """验证一次性主平台 token，并仅通过 Cookie 建立本地会话。"""
    main_platform_url = settings.MAIN_PLATFORM_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            upstream_response = await client.get(
                f"{main_platform_url}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {request.platform_token}"},
            )
            if upstream_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="主平台 token 验证失败",
                )
            upstream_payload = _mapping(upstream_response.json())
            remote_user = _mapping(upstream_payload.get("data"))
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="无法连接主平台",
        ) from exc

    user_id = _stable_identity(remote_user)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="主平台用户信息缺少 username",
        )
    username = _text_field(remote_user, "username", user_id)
    display_name = _text_field(
        remote_user,
        "name",
        _text_field(remote_user, "displayName", username),
    )
    plain_token = await run_in_threadpool(
        _commit_session,
        db,
        user_id,
        username,
        display_name,
        source="platform-exchange",
    )
    _set_session_cookie(response, plain_token)
    return {
        "username": username,
        "display_name": display_name,
    }


@router.post("/sso/start", summary="开始 SSO 授权")
async def sso_start(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """创建一次性 SSO state，并用独立 HttpOnly Cookie 绑定浏览器。"""
    if not settings.SSO_CLIENT_ID or not settings.SSO_CLIENT_SECRET:
        return _sso_error("SSO 未配置", status.HTTP_500_INTERNAL_SERVER_ERROR)

    state = secrets.token_urlsafe(32)
    browser_binding = (
        request.cookies.get(settings.SSO_STATE_COOKIE_NAME)
        or secrets.token_urlsafe(32)
    )
    client_host = request.client.host if request.client is not None else ""
    now = datetime.now(timezone.utc)
    try:
        limit_error = await run_in_threadpool(
            _create_sso_state_record,
            db,
            state,
            browser_binding,
            _sha256(client_host),
            now,
        )
    except Exception as exc:
        await run_in_threadpool(db.rollback)
        logger.exception("创建 SSO state 时数据库提交失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="无法开始 SSO 登录",
        ) from exc
    if limit_error is not None:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": limit_error},
        )

    authorization_url = (
        f"{settings.MAIN_PLATFORM_URL.rstrip('/')}/api/v1/sso-provider/authorize?"
        + urlencode(
            {
                "client_id": settings.SSO_CLIENT_ID,
                "redirect_uri": settings.SSO_REDIRECT_URI,
                "response_type": "code",
                "state": state,
            }
        )
    )
    response = JSONResponse(
        content={
            "authorization_url": authorization_url,
            "state": state,
        }
    )
    response.set_cookie(
        key=settings.SSO_STATE_COOKIE_NAME,
        value=browser_binding,
        max_age=settings.SSO_STATE_TTL_MINUTES * 60,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SSO_STATE_COOKIE_SAMESITE,
        path="/",
    )
    return response


@router.get("/sso/callback", summary="SSO 授权码回调")
async def sso_callback(
    request: Request,
    code: str,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    """用授权码获取用户信息，建立 Cookie 会话并重定向至纯前端 URL。"""
    if not settings.SSO_CLIENT_ID or not settings.SSO_CLIENT_SECRET:
        return _sso_error("SSO 未配置", status.HTTP_500_INTERNAL_SERVER_ERROR)
    browser_binding = request.cookies.get(settings.SSO_STATE_COOKIE_NAME)
    if not state or not browser_binding:
        return _sso_error("SSO state 无效")

    state_hash = _sha256(state)
    binding_hash = _sha256(browser_binding)
    now = datetime.now(timezone.utc)
    try:
        consumed = await run_in_threadpool(
            _consume_sso_state_record,
            db,
            state_hash,
            binding_hash,
            now,
        )
    except Exception as exc:
        await run_in_threadpool(db.rollback)
        logger.exception("消费 SSO state 时数据库提交失败")
        return _sso_error("SSO state 无效")
    if not consumed:
        return _sso_error("SSO state 无效")

    main_platform_url = settings.MAIN_PLATFORM_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post(
                f"{main_platform_url}/api/v1/sso-provider/token",
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.SSO_CLIENT_ID,
                    "client_secret": settings.SSO_CLIENT_SECRET,
                    "redirect_uri": settings.SSO_REDIRECT_URI,
                },
            )
            token_response.raise_for_status()
            token_payload = _mapping(token_response.json())
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.error("SSO token 换取失败 type=%s", type(exc).__name__)
        return _sso_error("SSO 登录失败", status.HTTP_502_BAD_GATEWAY)

    access_token = _text_field(token_payload, "access_token")
    if not access_token:
        return _sso_error("SSO 登录失败", status.HTTP_502_BAD_GATEWAY)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            userinfo_response = await client.get(
                f"{main_platform_url}/api/v1/sso-provider/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_response.raise_for_status()
            userinfo = _mapping(userinfo_response.json())
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.error("SSO userinfo 获取失败 type=%s", type(exc).__name__)
        return _sso_error("SSO 登录失败", status.HTTP_502_BAD_GATEWAY)

    user_id = _stable_identity(userinfo)
    if user_id is None:
        return _sso_error("SSO 登录失败", status.HTTP_502_BAD_GATEWAY)
    username = _text_field(userinfo, "username", user_id)
    display_name = _text_field(
        userinfo,
        "displayName",
        _text_field(userinfo, "name", username),
    )
    try:
        plain_token = await run_in_threadpool(
            _commit_session,
            db,
            user_id,
            username,
            display_name,
            source="sso",
        )
    except HTTPException:
        return _sso_error("SSO 登录失败", status.HTTP_500_INTERNAL_SERVER_ERROR)
    redirect = RedirectResponse(url=settings.FRONTEND_URL)
    _clear_sso_state_cookie(redirect)
    _set_session_cookie(redirect, plain_token)
    return redirect


@router.post("/logout", summary="退出当前会话")
async def logout(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """撤销当前 Cookie 对应会话，并清理浏览器 Cookie。"""
    plain_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if plain_token:
        try:
            await run_in_threadpool(_revoke_session_committed, db, plain_token)
        except Exception as exc:
            await run_in_threadpool(db.rollback)
            logger.exception("撤销登录会话时数据库提交失败")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="无法退出登录会话",
            ) from exc

    response = JSONResponse(
        content={
            "code": 200,
            "message": "退出成功",
        }
    )
    response.delete_cookie(key=settings.SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/sso/config", response_model=dict, summary="获取 SSO 配置")
async def get_sso_config() -> dict[str, object]:
    """返回 SSO 配置信息，供前端判断是否启用 SSO 登录。"""
    return {
        "code": 200,
        "message": "success",
        "data": {
            "ssoEnabled": bool(settings.SSO_CLIENT_ID),
            "mainPlatformUrl": settings.MAIN_PLATFORM_URL,
            "clientId": settings.SSO_CLIENT_ID or "",
        },
    }


@router.get("/me", response_model=dict, summary="获取当前用户信息")
async def get_current_user_info(
    user: dict[str, object] = Depends(require_auth),
) -> dict[str, object]:
    """通过数据库会话 Cookie 获取当前用户信息。"""
    return {
        "code": 200,
        "message": "success",
        "data": {
            "username": user.get("username", user.get("sub", "")),
            "displayName": user.get("displayName", ""),
        },
    }
