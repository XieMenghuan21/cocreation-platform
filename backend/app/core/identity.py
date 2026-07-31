"""稳定用户身份解析。"""
from __future__ import annotations


class AuthIdentityError(ValueError):
    """认证上下文缺少稳定 subject。"""


def auth_user_id(auth_user: dict[str, object]) -> str:
    """只接受认证系统签发的非空 sub，展示名不得作为持久化主键。"""
    value = auth_user.get("sub")
    if not isinstance(value, str) or not value.strip():
        raise AuthIdentityError("认证用户缺少稳定 sub")
    return value.strip()
