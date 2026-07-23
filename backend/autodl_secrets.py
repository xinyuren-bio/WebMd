# ==================================================
# 功能说明：AutoDL SSH 密码单独落盘（不写入 task_meta.json）
# 使用方法：save_autodl_password / load_autodl_password / purge_autodl_password
# 依赖环境：Python 标准库
# 生成时间：2026-07-23
# ==================================================

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from config import BASE_DIR

logger = logging.getLogger(__name__)

# 与 task_meta 分离，避免公开元数据夹带明文密码；文件权限尽量 0600
_SECRETS_PATH = Path(BASE_DIR) / "data" / "autodl_ssh_secrets.json"
_lock = threading.Lock()


def _load_all() -> dict[str, str]:
    """读取全部任务密码表。"""
    if not _SECRETS_PATH.is_file():
        return {}
    try:
        raw = json.loads(_SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 AutoDL 密码库失败: %s", e)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        tid = str(k or "").strip()
        pwd = str(v or "")
        if tid and pwd:
            out[tid] = pwd
    return out


def _save_all(data: dict[str, str]) -> None:
    """原子写入密码库并收紧权限。"""
    _SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SECRETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _SECRETS_PATH)
    try:
        os.chmod(_SECRETS_PATH, 0o600)
    except OSError:
        pass


def save_autodl_password(task_id: str, password: str) -> None:
    """保存某任务的 AutoDL SSH 密码（重启后可恢复）。"""
    tid = (task_id or "").strip()
    pwd = password or ""
    if not tid or not pwd:
        return
    with _lock:
        data = _load_all()
        data[tid] = pwd
        _save_all(data)


def load_autodl_password(task_id: str) -> str:
    """读取某任务密码；不存在则空串。"""
    tid = (task_id or "").strip()
    if not tid:
        return ""
    with _lock:
        return _load_all().get(tid, "")


def purge_autodl_password(task_id: str) -> None:
    """任务删除时清除对应密码。"""
    tid = (task_id or "").strip()
    if not tid:
        return
    with _lock:
        data = _load_all()
        if tid in data:
            del data[tid]
            _save_all(data)


def restore_passwords_into_tasks(task_map: dict) -> int:
    """启动时把密码灌回内存中的 Task 对象，返回恢复条数。"""
    with _lock:
        data = _load_all()
    n = 0
    for tid, pwd in data.items():
        t = task_map.get(tid)
        if t is None or not pwd:
            continue
        t.autodl_ssh_password = pwd
        n += 1
    if n:
        logger.info("已从密码库恢复 %d 个任务的 AutoDL SSH 密码", n)
    return n
