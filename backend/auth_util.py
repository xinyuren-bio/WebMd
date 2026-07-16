# ==================================================
# 功能说明：JWT 签发/校验与密码哈希
# 使用方法：由 api/auth 调用
# 依赖环境：pip install pyjwt bcrypt
# 生成时间：2026-07-13
# ==================================================

import time

import bcrypt
import jwt

from config import JWT_SECRET, JWT_EXPIRE_DAYS


def hash_password(pw: str) -> str:
    """密码哈希。"""
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, h: str) -> bool:
    """校验密码。"""
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), h.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, email: str) -> str:
    """签发 JWT。"""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + JWT_EXPIRE_DAYS * 86400,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """解析 JWT，失败返回 None。"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
