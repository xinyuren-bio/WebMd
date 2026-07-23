# ==================================================
# 功能说明：网站访问统计（PV/UV）与 MD 完成永久累计分档
# 使用方法：由 api/routes 调用 record_visit / get_analytics_stats /
#           record_md_completion / get_md_completion_stats /
#           sync_md_completion_from_tasks
# 依赖环境：Python 标准库
# 生成时间：2026-07-14
# ==================================================

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_TZ = timezone(timedelta(hours=8))


def _today() -> str:
    """北京时间日期字符串 YYYY-MM-DD。"""
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _visitor_hash(ip: str, ua: str, d: str) -> str:
    """按日 + IP + UA 生成访客标识（不存原始 IP）。"""
    raw = f"{d}|{ip}|{ua}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _empty_md_lifetime() -> dict:
    """MD 完成永久累计的空结构（任务删除后仍保留）。"""
    return {
        "md_completed_total": 0,
        "md_by_ns": {"10": 0, "100": 0, "200": 0, "other": 0},
        "md_per_user": {},
        # 已计入的任务 ID，避免回调/补发重复累加
        "recorded_task_ids": [],
    }


def _empty_data() -> dict:
    return {
        "total_pv": 0,
        "daily": {},
        "pages": {},
        "first_visit_at": None,
        "last_visit_at": None,
        "md_lifetime": _empty_md_lifetime(),
    }


def _ns_bucket(ns: float) -> str:
    """将模拟时长归入 10/100/200/other 分档。"""
    if abs(ns - 10.0) < 1e-6:
        return "10"
    if abs(ns - 100.0) < 1e-6:
        return "100"
    if abs(ns - 200.0) < 1e-6:
        return "200"
    return "other"


def _normalize_md_lifetime(raw: Any) -> dict:
    """补齐 md_lifetime 缺失字段。"""
    base = _empty_md_lifetime()
    if not isinstance(raw, dict):
        return base
    base["md_completed_total"] = int(raw.get("md_completed_total", 0) or 0)
    by_ns = raw.get("md_by_ns") or {}
    for k in ("10", "100", "200", "other"):
        base["md_by_ns"][k] = int(by_ns.get(k, 0) or 0)
    per_user = raw.get("md_per_user") or {}
    if isinstance(per_user, dict):
        base["md_per_user"] = {
            str(u): int(n or 0) for u, n in per_user.items() if str(u).strip()
        }
    ids = raw.get("recorded_task_ids") or []
    if isinstance(ids, list):
        base["recorded_task_ids"] = [str(x) for x in ids if str(x).strip()]
    return base


def _load(p: Path) -> dict:
    if not p.exists():
        return _empty_data()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for k, v in _empty_data().items():
            if k == "md_lifetime":
                continue
            data.setdefault(k, v if not isinstance(v, dict) else {})
        data["md_lifetime"] = _normalize_md_lifetime(data.get("md_lifetime"))
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取访问统计失败，将重建: %s", e)
        return _empty_data()


def _save(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_visit(p: Path, path: str, ip: str, ua: str) -> None:
    """记录一次页面访问。"""
    path = (path or "/").strip()[:200] or "/"
    ip = ip or "unknown"
    ua = (ua or "")[:300]
    day = _today()
    now = time.time()
    vh = _visitor_hash(ip, ua, day)

    with _lock:
        data = _load(p)
        data["total_pv"] = int(data.get("total_pv", 0)) + 1
        data["last_visit_at"] = now
        if not data.get("first_visit_at"):
            data["first_visit_at"] = now

        daily = data.setdefault("daily", {})
        day_row = daily.setdefault(day, {"pv": 0, "uv": []})
        day_row["pv"] = int(day_row.get("pv", 0)) + 1
        uv_list = day_row.setdefault("uv", [])
        if vh not in uv_list:
            uv_list.append(vh)

        pages = data.setdefault("pages", {})
        pages[path] = int(pages.get(path, 0)) + 1

        # 只保留近 90 天明细
        cutoff = (datetime.now(_TZ) - timedelta(days=90)).strftime("%Y-%m-%d")
        for old_day in list(daily.keys()):
            if old_day < cutoff:
                del daily[old_day]

        _save(p, data)


def get_analytics_stats(p: Path) -> dict:
    """汇总访问统计数据供管理页展示。"""
    with _lock:
        data = _load(p)

    daily = data.get("daily", {})
    today = _today()
    today_row = daily.get(today, {"pv": 0, "uv": []})

    last7 = []
    for i in range(6, -1, -1):
        d = (datetime.now(_TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
        row = daily.get(d, {"pv": 0, "uv": []})
        last7.append({
            "date": d,
            "pv": int(row.get("pv", 0)),
            "uv": len(row.get("uv", [])),
        })

    pages = data.get("pages", {})
    top_pages = sorted(pages.items(), key=lambda x: x[1], reverse=True)[:15]

    total_uv = sum(len(row.get("uv", [])) for row in daily.values())

    return {
        "total_pv": int(data.get("total_pv", 0)),
        "total_uv": total_uv,
        "today_pv": int(today_row.get("pv", 0)),
        "today_uv": len(today_row.get("uv", [])),
        "last7": last7,
        "top_pages": [{"path": k, "pv": v} for k, v in top_pages],
        "first_visit_at": data.get("first_visit_at"),
        "last_visit_at": data.get("last_visit_at"),
    }


def record_md_completion(
    p: Path,
    *,
    task_id: str,
    user_id: str = "",
    simulation_time_ns: float | int | None = None,
) -> bool:
    """将一次成功的 MD 完成写入永久累计（按 task_id 幂等）。

    设计思路：任务目录可能被定期清理，统计不能依赖现存任务；
    用 recorded_task_ids 去重，保证回调重试/补发邮件不会重复加 1。
    返回 True 表示本次新计入，False 表示此前已记录。
    """
    tid = (task_id or "").strip()
    if not tid:
        return False
    try:
        ns = float(simulation_time_ns or 0)
    except (TypeError, ValueError):
        ns = 0.0
    uid = (user_id or "").strip()
    bucket = _ns_bucket(ns)

    with _lock:
        data = _load(p)
        life = _normalize_md_lifetime(data.get("md_lifetime"))
        recorded = set(life.get("recorded_task_ids") or [])
        if tid in recorded:
            data["md_lifetime"] = life
            _save(p, data)
            return False

        recorded.add(tid)
        life["recorded_task_ids"] = sorted(recorded)
        life["md_completed_total"] = int(life.get("md_completed_total", 0)) + 1
        by_ns = life.setdefault("md_by_ns", _empty_md_lifetime()["md_by_ns"])
        by_ns[bucket] = int(by_ns.get(bucket, 0) or 0) + 1
        if uid:
            per_user = life.setdefault("md_per_user", {})
            per_user[uid] = int(per_user.get(uid, 0) or 0) + 1
        data["md_lifetime"] = life
        _save(p, data)
        logger.info(
            "MD 完成已永久计入：task=%s ns=%s user=%s total=%s",
            tid,
            bucket,
            uid or "—",
            life["md_completed_total"],
        )
        return True


def get_md_completion_stats(p: Path) -> dict:
    """读取永久累计的 MD 完成统计（供管理页展示）。"""
    with _lock:
        data = _load(p)
        life = _normalize_md_lifetime(data.get("md_lifetime"))

    per_user = dict(life.get("md_per_user") or {})
    top_users = sorted(
        ({"user_id": u, "md_completed": n} for u, n in per_user.items()),
        key=lambda x: (-x["md_completed"], x["user_id"]),
    )[:50]
    return {
        "md_completed_total": int(life.get("md_completed_total", 0) or 0),
        "md_by_ns": dict(life.get("md_by_ns") or _empty_md_lifetime()["md_by_ns"]),
        "md_per_user": per_user,
        "md_top_users": top_users,
    }


def sync_md_completion_from_tasks(p: Path, task_list: Iterable[Any]) -> int:
    """启动时把现存已完成 MD 补写入永久累计，返回新计入条数。"""
    n_new = 0
    for t in task_list:
        if getattr(t, "md_status", None) != "completed":
            continue
        params = getattr(t, "params", None) or {}
        try:
            ns = float(params.get("simulation_time_ns") or 0)
        except (TypeError, ValueError):
            ns = 0.0
        if record_md_completion(
            p,
            task_id=str(getattr(t, "task_id", "") or ""),
            user_id=str(getattr(t, "user_id", "") or ""),
            simulation_time_ns=ns,
        ):
            n_new += 1
    return n_new


def collect_md_completion_stats(task_list: Iterable[Any]) -> dict:
    """兼容旧接口：仅扫描当前任务内存（不持久，任务删除后会变少）。

    新逻辑请用 get_md_completion_stats / record_md_completion。
    """
    by_ns = {"10": 0, "100": 0, "200": 0, "other": 0}
    per_user: dict[str, int] = {}
    total = 0

    for t in task_list:
        if getattr(t, "md_status", None) != "completed":
            continue
        total += 1
        uid = (getattr(t, "user_id", None) or "").strip()
        if uid:
            per_user[uid] = per_user.get(uid, 0) + 1

        params = getattr(t, "params", None) or {}
        try:
            ns = float(params.get("simulation_time_ns") or 0)
        except (TypeError, ValueError):
            ns = 0.0
        by_ns[_ns_bucket(ns)] += 1

    top_users = sorted(
        ({"user_id": u, "md_completed": n} for u, n in per_user.items()),
        key=lambda x: (-x["md_completed"], x["user_id"]),
    )[:50]

    return {
        "md_completed_total": total,
        "md_by_ns": by_ns,
        "md_per_user": per_user,
        "md_top_users": top_users,
    }
