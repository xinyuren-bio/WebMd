# ==================================================
# 功能说明：付费下载辅助（唯一金额计算、管理员密钥校验）
# 使用方法：由 api/routes 调用
# 依赖环境：Python 标准库
# 生成时间：2026-07-05
# ==================================================

import os


def calc_payment_amount(task_id: str, base: float) -> float:
    """按任务 ID 生成唯一支付金额（基准价 + 0.01~0.99），便于核对到账。"""
    try:
        n = int(task_id[-6:], 16)
    except ValueError:
        n = sum(ord(c) for c in task_id)
    cents = (n % 99) + 1
    return round(base + cents / 100.0, 2)


def get_admin_payment_key() -> str:
    """管理员核实密钥，可通过环境变量 WEBMD_ADMIN_KEY 覆盖。"""
    return os.environ.get("WEBMD_ADMIN_KEY", "webmd-admin-2026")


def verify_admin_key(k: str) -> bool:
    """校验管理员密钥。"""
    expected = get_admin_payment_key()
    return bool(k) and k == expected
