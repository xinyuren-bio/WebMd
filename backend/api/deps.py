# ==================================================
# 功能说明：API 依赖注入（登录用户校验）
# 使用方法：FastAPI Depends
# 依赖环境：同 auth_util
# 生成时间：2026-07-13
# ==================================================

from pathlib import Path

from fastapi import Header, HTTPException

from auth_util import decode_token
from user_store import get_user_by_id
from config import USERS_DB


def _parse_bearer(h: str) -> str:
    if not h or not h.startswith("Bearer "):
        return ""
    return h[7:].strip()


async def get_current_user(authorization: str = Header(default="")) -> dict:
    """必须登录。"""
    token = _parse_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    u = get_user_by_id(Path(USERS_DB), payload["sub"])
    if not u:
        raise HTTPException(status_code=401, detail="用户不存在")
    return u


async def get_optional_user(authorization: str = Header(default="")) -> dict | None:
    """可选登录。"""
    token = _parse_bearer(authorization)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        return None
    return get_user_by_id(Path(USERS_DB), payload["sub"])
