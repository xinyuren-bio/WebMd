# ==================================================
# 功能说明：过期任务目录清理单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_task_cleanup
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
import tempfile

from models import META_FILENAME, tasks
from task_cleanup import cleanup_expired_tasks


class TestTaskCleanup(unittest.TestCase):
    """验证按天数删除与进行中任务保护。"""

    def tearDown(self) -> None:
        tasks.clear()

    def _write_task(self, root: Path, tid: str, *, created_at: float, status: str, md: str = "none") -> Path:
        d = root / tid
        d.mkdir(parents=True, exist_ok=True)
        (d / "marker.txt").write_text("x", encoding="utf-8")
        meta = {
            "task_id": tid,
            "status": status,
            "created_at": created_at,
            "md_status": md,
            "work_dir": str(d),
        }
        (d / META_FILENAME).write_text(json.dumps(meta), encoding="utf-8")
        return d

    def test_delete_old_completed(self) -> None:
        """超过保留期的已完成任务应删除。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = time.time() - 8 * 86400
            self._write_task(root, "oldtask00001", created_at=old, status="completed")
            self._write_task(root, "newtask00001", created_at=time.time(), status="completed")
            removed = cleanup_expired_tasks(root, retention_days=7)
            self.assertEqual(removed, ["oldtask00001"])
            self.assertFalse((root / "oldtask00001").exists())
            self.assertTrue((root / "newtask00001").exists())

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
                root, "mdrunning001", created_at=old, status="completed", md="running",
            )
            removed = cleanup_expired_tasks(root, retention_days=7)
            self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
