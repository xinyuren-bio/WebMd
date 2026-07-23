# ==================================================
# 功能说明：按策略清理任务目录（仅永久保留 10/100 ns 已完成 MD）
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

# 仅这些时长的「MD 已完成」任务永久保留；其余过期可删
_KEEP_MD_NS = frozenset({10.0, 100.0})


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


def _sim_ns(meta: dict) -> float:
    """读取模拟时长（ns），无效则 0。"""
    params = meta.get("params") or {}
    try:
        return float(params.get("simulation_time_ns") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_kept_md_completed(meta: dict | None) -> bool:
    """是否为应永久保留的 10/100 ns MD 完成任务。"""
    if not meta:
        return False
    if str(meta.get("md_status") or "none") != "completed":
        return False
    ns = _sim_ns(meta)
    return any(abs(ns - x) < 1e-6 for x in _KEEP_MD_NS)


def _should_keep_active(meta: dict | None) -> bool:
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


def _should_keep(meta: dict | None) -> bool:
    """是否跳过清理：进行中，或 10/100 ns MD 已完成。"""
    return _should_keep_active(meta) or _is_kept_md_completed(meta)


def cleanup_expired_tasks(
    tasks_dir: str | Path,
    retention_days: int = 7,
    *,
    dry_run: bool = False,
) -> list[str]:
    """清理可删任务目录，并从内存 tasks 移除。

    设计思路：
    - 永久保留：md_status=completed 且时长为 10 或 100 ns（交付产物）
    - 临时保留：前处理进行中、MD 排队/运行中
    - 其余（前处理失败、未支付、仅前处理完成、200 ns 完成、MD 失败等）
      超过 retention_days 后删除

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
        if _is_kept_md_completed(meta):
            logger.debug("永久保留（10/100 ns MD 完成）: %s", tid)
            continue
        if _should_keep_active(meta):
            logger.info("跳过清理（仍在处理）: %s", tid)
            continue
        age = _task_age_seconds(d, meta)
        if age < limit_sec:
            continue
        if dry_run:
            logger.info("[dry-run] 将删除任务 %s（约 %.1f 天）", tid, age / 86400.0)
            removed.append(tid)
            continue
        try:
            shutil.rmtree(d)
            tasks.pop(tid, None)
            removed.append(tid)
            logger.info("已删除任务目录 %s（约 %.1f 天）", tid, age / 86400.0)
        except OSError as e:
            logger.warning("删除任务目录失败 %s: %s", tid, e)

    if removed and not dry_run:
        logger.info(
            "任务清理完成：删除 %d 个；永久保留 10/100 ns 已完成 MD；其余保留期=%d 天",
            len(removed),
            days,
        )
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
        "已启动任务清理线程：永久保留 10/100 ns 已完成 MD；其余 %d 天后删，间隔 %d 秒",
        retention_days,
        max(3600, int(interval_sec)),
    )
