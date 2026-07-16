# ==================================================
# 功能说明：用户注册与登录数据存储（SQLite）
# 使用方法：由 auth 模块调用
# 依赖环境：Python 标准库 sqlite3
# 生成时间：2026-07-13
# ==================================================

import logging
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def init_db(p: Path) -> None:
    """初始化用户表。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        c.commit()


def create_user(p: Path, email: str, password_hash: str) -> dict:
    """注册新用户，返回用户信息。"""
    uid = uuid.uuid4().hex[:16]
    now = time.time()
    with sqlite3.connect(p) as c:
        try:
            c.execute(
                "INSERT INTO users (user_id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (uid, email.lower().strip(), password_hash, now),
            )
            c.commit()
        except sqlite3.IntegrityError:
            raise ValueError("该邮箱已注册")
    return {"user_id": uid, "email": email.lower().strip(), "created_at": now}


def get_user_by_email(p: Path, email: str) -> dict | None:
    """按邮箱查询用户。"""
    with sqlite3.connect(p) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT user_id, email, password_hash, created_at FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def get_user_by_id(p: Path, user_id: str) -> dict | None:
    """按用户 ID 查询。"""
    with sqlite3.connect(p) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT user_id, email, created_at FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def count_users(p: Path) -> int:
    """统计注册用户总数。"""
    with sqlite3.connect(p) as c:
        row = c.execute("SELECT COUNT(*) FROM users").fetchone()
    return int(row[0]) if row else 0


def list_recent_users(p: Path, limit: int = 30) -> list[dict]:
    """按注册时间倒序列出最近用户。"""
    n = max(1, min(int(limit), 200))
    with sqlite3.connect(p) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT user_id, email, created_at FROM users ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [dict(r) for r in rows]
