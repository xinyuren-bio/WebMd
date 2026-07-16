# ==================================================
# 功能说明：注册邮箱验证码存储与校验
# 使用方法：由 api/auth 调用
# 依赖环境：Python 标准库 sqlite3
# 生成时间：2026-07-13
# ==================================================

import random
import sqlite3
import time
from pathlib import Path

from config import VERIFY_CODE_COOLDOWN_SEC, VERIFY_CODE_EXPIRE_SEC


def _init_codes_table(p: Path) -> None:
    with sqlite3.connect(p) as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS email_codes (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        c.commit()


def generate_code() -> str:
    """生成 6 位数字验证码。"""
    return f"{random.randint(0, 999999):06d}"


def save_code(p: Path, email: str, code: str) -> None:
    """保存验证码。"""
    _init_codes_table(p)
    em = email.lower().strip()
    with sqlite3.connect(p) as c:
        c.execute(
            "INSERT OR REPLACE INTO email_codes (email, code, created_at) VALUES (?, ?, ?)",
            (em, code, time.time()),
        )
        c.commit()


def seconds_until_resend(p: Path, email: str) -> int:
    """距离可再次发送还需等待的秒数，0 表示可以发送。"""
    _init_codes_table(p)
    em = email.lower().strip()
    with sqlite3.connect(p) as c:
        row = c.execute(
            "SELECT created_at FROM email_codes WHERE email = ?",
            (em,),
        ).fetchone()
    if not row:
        return 0
    elapsed = time.time() - float(row[0])
    wait = int(VERIFY_CODE_COOLDOWN_SEC - elapsed)
    return wait if wait > 0 else 0


def verify_and_consume(p: Path, email: str, code: str) -> bool:
    """校验验证码是否正确且未过期，成功后删除。"""
    _init_codes_table(p)
    em = email.lower().strip()
    c_in = (code or "").strip()
    if not c_in:
        return False
    with sqlite3.connect(p) as c:
        row = c.execute(
            "SELECT code, created_at FROM email_codes WHERE email = ?",
            (em,),
        ).fetchone()
        if not row:
            return False
        saved, created = row[0], float(row[1])
        if time.time() - created > VERIFY_CODE_EXPIRE_SEC:
            c.execute("DELETE FROM email_codes WHERE email = ?", (em,))
            c.commit()
            return False
        if saved != c_in:
            return False
        c.execute("DELETE FROM email_codes WHERE email = ?", (em,))
        c.commit()
        return True
