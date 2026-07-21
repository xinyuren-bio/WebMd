# ==================================================
# 功能说明：定期清理过期任务目录（默认超过 7 天删除）
# 使用方法：启动时调用 cleanup_expired_tasks / start_cleanup_scheduler
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path

from models import META_FILENAME, TaskStatus, tasks

logger = logging.getLogger(__name__)

# 仍在前处理流水线中的状态：即使超时也不删，避免打断运行中任务
_PREP_ACTIVE = frozenset(
    {
        TaskStatus.PENDING.value,
        TaskStatus.PROCESSING_PROTEIN.value,
        TaskStatus.PROCESSING_LIGAND.value,
        TaskStatus.SOLVATING.value,
        TaskStatus.CONVERTING_GMX.value,
        TaskStatus.GENERATING_MDP.value,
        TaskStatus.PACKAGING.value,
    }
)

# MD 仍在排队/运行：保留目录便于回传结果
_MD_ACTIVE = frozenset({"queued", "running"})


def _task_age_seconds(d: Path, meta: dict | None) -> float:
    """计算任务年龄（秒）：优先 created_at，否则用目录 mtime。"""
    now = time.time()
    if meta and meta.get("created_at"):
        try:
            return max(0.0, now - float(meta["created_at"]))
        except (TypeError, ValueError):
            pass
    try:
        return max(0.0, now - d.stat().st_mtime)
    except OSError:
        return 0.0


def _read_meta(d: Path) -> dict | None:
    """读取 task_meta.json，失败返回 None。"""
    p = d / META_FILENAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _should_keep(meta: dict | None) -> bool:
    """判断是否因仍在处理而保留。"""
    if not meta:
        return False
    st = str(meta.get("status") or "")
    if st in _PREP_ACTIVE:
        return True
    md = str(meta.get("md_status") or "none")
    if md in _MD_ACTIVE:
        return True
    return False


def cleanup_expired_tasks(
    tasks_dir: str | Path,
    retention_days: int = 7,
    *,
    dry_run: bool = False,
) -> list[str]:
    """删除超过保留天数的任务目录，并从内存 tasks 移除。

    设计思路：按 created_at（或缺省目录 mtime）判定年龄；
    前处理进行中或 MD 排队/运行中的任务跳过，避免误删。
    返回已删除（或 dry_run 将删）的 task_id 列表。
    """
    root = Path(tasks_dir)
    if not root.is_dir():
        return []
    days = max(1, int(retention_days))
    limit_sec = days * 86400.0
    removed: list[str] = []

    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        tid = d.name
        meta = _read_meta(d)
        if _should_keep(meta):
            logger.info("跳过清理（仍在处理）: %s", tid)
            continue
        age = _task_age_seconds(d, meta)
        if age < limit_sec:
            continue
        if dry_run:
            logger.info("[dry-run] 将删除过期任务 %s（约 %.1f 天）", tid, age / 86400.0)
            removed.append(tid)
            continue
        try:
            shutil.rmtree(d)
            tasks.pop(tid, None)
            removed.append(tid)
            logger.info("已删除过期任务目录 %s（约 %.1f 天）", tid, age / 86400.0)
        except OSError as e:
            logger.warning("删除任务目录失败 %s: %s", tid, e)

    if removed and not dry_run:
        logger.info("任务清理完成：删除 %d 个，保留阈值=%d 天", len(removed), days)
    return removed


def start_cleanup_scheduler(
    tasks_dir: str | Path,
    retention_days: int = 7,
    interval_sec: int = 86400,
) -> None:
    """后台线程：启动时立即清理一次，之后按间隔重复。"""

    def _loop() -> None:
        while True:
            try:
                cleanup_expired_tasks(tasks_dir, retention_days=retention_days)
            except Exception:
                logger.exception("任务目录定期清理异常")
            time.sleep(max(3600, int(interval_sec)))

    th = threading.Thread(target=_loop, name="task-cleanup", daemon=True)
    th.start()
    logger.info(
        "已启动任务清理线程：保留 %d 天，间隔 %d 秒",
        retention_days,
        max(3600, int(interval_sec)),
    )
