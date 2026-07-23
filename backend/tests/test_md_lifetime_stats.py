# ==================================================
# 功能说明：MD 完成永久累计统计的单元测试
# 使用方法：pytest backend/tests/test_md_lifetime_stats.py
# 依赖环境：pip install pytest
# 生成时间：2026-07-23
# ==================================================

from __future__ import annotations

from types import SimpleNamespace

from analytics_util import (
    get_md_completion_stats,
    record_md_completion,
    sync_md_completion_from_tasks,
)


def test_record_md_completion_is_idempotent(tmp_path) -> None:
    """同一 task_id 只累计一次。"""
    p = tmp_path / "analytics.json"
    assert record_md_completion(p, task_id="t1", user_id="u1", simulation_time_ns=100)
    assert not record_md_completion(p, task_id="t1", user_id="u1", simulation_time_ns=100)
    st = get_md_completion_stats(p)
    assert st["md_completed_total"] == 1
    assert st["md_by_ns"]["100"] == 1
    assert st["md_per_user"]["u1"] == 1


def test_sync_from_tasks_backfills(tmp_path) -> None:
    """启动补录会把现存 completed 任务写入永久统计。"""
    p = tmp_path / "analytics.json"
    tasks = [
        SimpleNamespace(
            task_id="a",
            user_id="u",
            md_status="completed",
            params={"simulation_time_ns": 10},
        ),
        SimpleNamespace(
            task_id="b",
            user_id="u",
            md_status="running",
            params={"simulation_time_ns": 10},
        ),
    ]
    n = sync_md_completion_from_tasks(p, tasks)
    assert n == 1
    assert sync_md_completion_from_tasks(p, tasks) == 0
    st = get_md_completion_stats(p)
    assert st["md_completed_total"] == 1
    assert st["md_by_ns"]["10"] == 1
