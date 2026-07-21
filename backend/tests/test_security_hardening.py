# ==================================================
# 功能说明：生产密钥检查与 AutoDL 密码不落盘的单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_security_hardening
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models import Task, TaskStatus


class TestSecurityHardening(unittest.TestCase):
    """安全加固相关行为。"""

    def test_task_meta_omits_ssh_password(self) -> None:
        """save 写出的 task_meta.json 不得包含 SSH 密码。"""
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            t = Task(
                task_id="abc123def456",
                status=TaskStatus.COMPLETED,
                work_dir=str(work),
                autodl_ssh_host="example.com",
                autodl_ssh_password="super-secret",
            )
            t.save()
            data = json.loads((work / "task_meta.json").read_text(encoding="utf-8"))
            self.assertNotIn("autodl_ssh_password", data)
            self.assertEqual(data.get("autodl_ssh_host"), "example.com")

    def test_load_ignores_legacy_password(self) -> None:
        """即使旧 meta 含明文密码，加载后内存也不恢复。"""
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            meta = work / "task_meta.json"
            meta.write_text(
                json.dumps(
                    {
                        "task_id": "abc123def456",
                        "status": "completed",
                        "work_dir": str(work),
                        "autodl_ssh_password": "legacy-secret",
                        "autodl_ssh_host": "h.example",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            t = Task.load(meta)
            self.assertIsNotNone(t)
            assert t is not None
            self.assertEqual(t.autodl_ssh_password, "")
            self.assertEqual(t.autodl_ssh_host, "h.example")

    def test_production_rejects_default_secrets(self) -> None:
        """生产环境使用默认密钥时应退出。"""
        import config as cfg

        with mock.patch.dict(
            os.environ,
            {
                "WEBMD_ENV": "production",
                "WEBMD_JWT_SECRET": "webmd-change-jwt-secret-in-production",
                "WEBMD_MD_CALLBACK_SECRET": "ok-callback-secret-value",
                "WEBMD_ADMIN_KEY": "ok-admin-key",
            },
            clear=False,
        ):
            # 重新读取模块级常量较麻烦；直接测函数对当前进程 env 的判断
            with mock.patch.object(cfg, "WEBMD_ENV", "production"), mock.patch.object(
                cfg, "JWT_SECRET", "webmd-change-jwt-secret-in-production"
            ), mock.patch.object(
                cfg, "MD_CALLBACK_SECRET", "ok-callback-secret-value"
            ):
                with self.assertRaises(SystemExit):
                    cfg.assert_production_secrets()


if __name__ == "__main__":
    unittest.main()
