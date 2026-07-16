# ==================================================
# 功能说明：用户注册与登录 API
# 使用方法：挂载到 FastAPI /api/auth
# 依赖环境：见 auth_util、user_store、verify_store
# 生成时间：2026-07-13
# ==================================================

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from auth_util import hash_password, verify_password, create_token
from user_store import create_user, get_user_by_email, count_users
from verify_store import generate_code, save_code, seconds_until_resend, verify_and_consume
from email_util import send_verification_code, send_admin_new_user_notify
from config import USERS_DB, SMTP_USER, SMTP_PASSWORD, SITE_BASE_URL
from api.deps import get_current_user

router = APIRouter(prefix="/api/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SendCodeBody(BaseModel):
    email: str = Field(min_length=3, max_length=120)


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=4, max_length=8)


class LoginBody(BaseModel):
    email: str
    password: str


def _normalize_email(em: str) -> str:
    em = em.strip().lower()
    if not _EMAIL_RE.match(em):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return em


@router.post("/send-verification-code")
async def send_code(body: SendCodeBody):
    """发送注册邮箱验证码。"""
    if not SMTP_USER or not SMTP_PASSWORD:
        raise HTTPException(
            status_code=503,
            detail="邮件服务未配置，暂无法注册。请联系管理员。",
        )

    em = _normalize_email(body.email)
    if get_user_by_email(Path(USERS_DB), em):
        raise HTTPException(status_code=400, detail="该邮箱已注册，请直接登录")

    db = Path(USERS_DB)
    wait = seconds_until_resend(db, em)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"请 {wait} 秒后再获取验证码")

    code = generate_code()
    if not send_verification_code(em, code):
        raise HTTPException(status_code=500, detail="验证码发送失败，请稍后重试")

    save_code(db, em, code)
    return {"ok": True, "message": "验证码已发送至您的邮箱"}


@router.post("/register")
async def register(body: RegisterBody):
    """邮箱注册（需验证码）。"""
    em = _normalize_email(body.email)
    if get_user_by_email(Path(USERS_DB), em):
        raise HTTPException(status_code=400, detail="该邮箱已注册")

    if not verify_and_consume(Path(USERS_DB), em, body.code.strip()):
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请重新获取")

    try:
        u = create_user(Path(USERS_DB), em, hash_password(body.password))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    total = count_users(Path(USERS_DB))
    send_admin_new_user_notify(
        user_email=u["email"],
        user_id=u["user_id"],
        total_users=total,
        created_at=u["created_at"],
        site_base=SITE_BASE_URL.rstrip("/"),
    )

    token = create_token(u["user_id"], u["email"])
    return {
        "token": token,
        "user": {"user_id": u["user_id"], "email": u["email"]},
    }


@router.post("/login")
async def login(body: LoginBody):
    """邮箱密码登录。"""
    u = get_user_by_email(Path(USERS_DB), body.email)
    if not u or not verify_password(body.password, u["password_hash"]):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_token(u["user_id"], u["email"])
    return {
        "token": token,
        "user": {"user_id": u["user_id"], "email": u["email"]},
    }


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """当前登录用户。"""
    return {"user_id": user["user_id"], "email": user["email"]}
