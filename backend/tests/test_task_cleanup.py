# ==================================================
# 功能说明：任务目录清理策略单元测试（10/100/200 ns 永久保留）
# 使用方法：在 backend 目录 python -m unittest tests.test_task_cleanup
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from models import META_FILENAME, tasks
from task_cleanup import cleanup_expired_tasks


class TestTaskCleanup(unittest.TestCase):
    """验证 10/100/200 ns MD 永久保留与其余过期删除。"""

    def tearDown(self) -> None:
        tasks.clear()

    def _write_task(
        self,
        root: Path,
        tid: str,
        *,
        created_at: float,
        status: str,
        md: str = "none",
        ns: float | None = None,
    ) -> Path:
        d = root / tid
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker.txt").write_text("x", encoding="utf-8")
        meta = {
            "task_id": tid,
            "status": status,
            "created_at": created_at,
            "md_status": md,
            "work_dir": str(d),
            "params": {},
        }
        if ns is not None:
            meta["params"]["simulation_time_ns"] = ns
        (d / META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
        return d

    def test_delete_old_failed_or_unpaid(self) -> None:
        """超过保留期的前处理失败 / 仅前处理完成应删除。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = time.time() - 8 * 86400
            self._write_task(root, "oldfailed001", created_at=old, status="failed", ns=100)
            self._write_task(
                root, "oldprep00001", created_at=old, status="completed", md="none", ns=100,
            )
            self._write_task(
                root, "newfailed001", created_at=time.time(), status="failed", ns=100,
            )
            removed = cleanup_expired_tasks(root, retention_days=7)
            self.assertEqual(sorted(removed), ["oldfailed001", "oldprep00001"])
            self.assertFalse((root / "oldfailed001").exists())
            self.assertTrue((root / "newfailed001").exists())

    def test_keep_10_100_200_ns_md_forever(self) -> None:
        """10/100/200 ns MD 已完成即使很旧也保留。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = time.time() - 365 * 86400
            self._write_task(
                root, "md10keep0001", created_at=old, status="completed", md="completed", ns=10,
            )
            self._write_task(
                root, "md100keep001", created_at=old, status="completed", md="completed", ns=100,
            )
            self._write_task(
                root, "md200keep001", created_at=old, status="completed", md="completed", ns=200,
            )
            self._write_task(
                root, "md50delete01", created_at=old, status="completed", md="completed", ns=50,
            )
            removed = cleanup_expired_tasks(root, retention_days=7)
            self.assertEqual(removed, ["md50delete01"])
            self.assertTrue((root / "md10keep0001").exists())
            self.assertTrue((root / "md100keep001").exists())
            self.assertTrue((root / "md200keep001").exists())

    def test_keep_active_prep_even_if_old(self) -> None:
        """仍在溶剂化的任务即使很旧也不删。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = time.time() - 30 * 86400
            self._write_task(root, "solvating001", created_at=old, status="solvating")
            removed = cleanup_expired_tasks(root, retention_days=7)
            self.assertEqual(removed, [])
            self.assertTrue((root / "solvating001").exists())

    def test_keep_running_md(self) -> None:
        """MD 运行中即使前处理已完成且过期也保留。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = time.time() - 10 * 86400
            self._write_task(
                root, "mdrunning001", created_at=old, status="completed", md="running", ns=100,
            )
            removed = cleanup_expired_tasks(root, retention_days=7)
            self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
