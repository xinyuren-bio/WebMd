# ==================================================
# 功能说明：网站访问统计（PV/UV）与 MD 完成分档统计
# 使用方法：由 api/routes 调用 record_visit / get_analytics_stats /
#           collect_md_completion_stats
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


def _empty_data() -> dict:
    return {
        "total_pv": 0,
        "daily": {},
        "pages": {},
        "first_visit_at": None,
        "last_visit_at": None,
    }


def _load(p: Path) -> dict:
    if not p.exists():
        return _empty_data()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for k, v in _empty_data().items():
            data.setdefault(k, v if not isinstance(v, dict) else {})
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


def collect_md_completion_stats(task_list: Iterable[Any]) -> dict:
    """统计已完成 MD：按时长分档（10/100/200 ns）及每用户完成数。

    仅计 md_status == completed 的任务；未知时长计入 other。
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

        if abs(ns - 10.0) < 1e-6:
            by_ns["10"] += 1
        elif abs(ns - 100.0) < 1e-6:
            by_ns["100"] += 1
        elif abs(ns - 200.0) < 1e-6:
            by_ns["200"] += 1
        else:
            by_ns["other"] += 1

    # 用户完成数排行（多到少，最多 50）
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
