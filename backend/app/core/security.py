"""
安全相关功能 - JWT token 生成和验证、密码加密
"""
import threading
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt
from passlib.context import CryptContext

from app.config.settings import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token 黑名单（生产环境应迁移到 Redis）
_revoked_tokens: dict[str, datetime] = {}
_revoked_lock = threading.Lock()


def revoke_token(jti: str, exp: datetime) -> None:
    with _revoked_lock:
        _revoked_tokens[jti] = exp


def is_token_revoked(jti: str) -> bool:
    with _revoked_lock:
        return jti in _revoked_tokens


def cleanup_revoked_tokens() -> int:
    now = datetime.utcnow()
    to_remove = []
    with _revoked_lock:
        for jti, exp in _revoked_tokens.items():
            if exp < now:
                to_remove.append(jti)
        for jti in to_remove:
            del _revoked_tokens[jti]
    return len(to_remove)


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "jti": _uuid.uuid4().hex})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            return None
        return payload
    except jwt.JWTError:
        return None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
