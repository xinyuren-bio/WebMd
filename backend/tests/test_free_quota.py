# ==================================================
# 功能说明：10 ns 免费额度与 100 ns 定价单元测试
# 使用方法：在 backend 目录 python -m unittest tests.test_free_quota
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import unittest

from models import Task, TaskStatus
from payment_util import (
    FREE_10NS_QUOTA,
    can_use_free_10ns,
    free_10ns_remaining,
    price_for_sim_ns,
)


class TestFreeQuota(unittest.TestCase):
    """验证免费额度计数与定价。"""

    def test_price_100ns(self) -> None:
        """100 ns 默认价为 147.70。"""
        self.assertAlmostEqual(price_for_sim_ns(100.0), 147.70, places=2)

    def test_free_then_paid_10ns(self) -> None:
        """有额度时 10 ns 为 0，用尽后恢复标价。"""
        bag: dict = {}
        t = Task(task_id="t1", user_id="u1", status=TaskStatus.COMPLETED)
        t.params["simulation_time_ns"] = 10.0
        bag["t1"] = t
        self.assertTrue(can_use_free_10ns(t, bag))
        self.assertEqual(free_10ns_remaining("u1", bag), FREE_10NS_QUOTA)
        self.assertEqual(price_for_sim_ns(10.0, "u1", bag), 0.0)

        # 消耗全部额度
        for i in range(FREE_10NS_QUOTA):
            x = Task(
                task_id=f"used{i}",
                user_id="u1",
                status=TaskStatus.COMPLETED,
                payment_status="paid",
                payment_amount=0.0,
                paid=True,
            )
            x.params["simulation_time_ns"] = 10.0
            bag[f"used{i}"] = x
        self.assertEqual(free_10ns_remaining("u1", bag), 0)
        self.assertFalse(can_use_free_10ns(t, bag))
        self.assertGreater(price_for_sim_ns(10.0, "u1", bag), 0.0)


if __name__ == "__main__":
    unittest.main()
