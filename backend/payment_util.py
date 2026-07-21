# ==================================================
# 功能说明：付费下载辅助（按时长定价、10 ns 免费额度、管理员密钥）
# 使用方法：由 api/routes 调用
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Task

# 时长档位：10 ns 用尽免费额度后 ¥10；100 ns ¥147.70；200 ns ¥240
PRICE_10NS = float(os.environ.get("WEBMD_PRICE_10NS", "10"))
PRICE_100NS = float(os.environ.get("WEBMD_PRICE_100NS", "147.70"))
PRICE_200NS = float(os.environ.get("WEBMD_PRICE_200NS", "240"))

# 每用户 10 ns 免费额度次数
FREE_10NS_QUOTA = int(os.environ.get("WEBMD_FREE_10NS_QUOTA", "5"))

QR_10_ALIPAY = os.environ.get("WEBMD_QR_10_ALIPAY", "/assets/images/pay_10.jpg")
QR_10_WECHAT = os.environ.get("WEBMD_QR_10_WECHAT", "/assets/images/wechat_pay_10.png")
QR_150_ALIPAY = os.environ.get("WEBMD_QR_150_ALIPAY", "/assets/images/pay_150.jpg")
QR_150_WECHAT = os.environ.get("WEBMD_QR_150_WECHAT", "/assets/images/wechat_pay_150.png")
QR_240_ALIPAY = os.environ.get("WEBMD_QR_240_ALIPAY", "/assets/images/pay.jpg")
QR_240_WECHAT = os.environ.get("WEBMD_QR_240_WECHAT", "/assets/images/wechat_pay.png")

# 时长(ns) → (标价, 支付宝收款码, 微信收款码)；10 ns 标价为用尽免费后的价格
_TIER_BY_NS: dict[float, tuple[float, str, str]] = {
    10.0: (PRICE_10NS, QR_10_ALIPAY, QR_10_WECHAT),
    100.0: (PRICE_100NS, QR_150_ALIPAY, QR_150_WECHAT),
    200.0: (PRICE_200NS, QR_240_ALIPAY, QR_240_WECHAT),
}


def _tier(sim_ns: float) -> tuple[float, str, str]:
    """查找时长档位；未知时长回退到 100 ns 档。"""
    return _TIER_BY_NS.get(float(sim_ns), _TIER_BY_NS[100.0])


def is_free_10ns_task(t: "Task") -> bool:
    """是否为已消耗的 10 ns 免费额度任务。"""
    try:
        ns = float((t.params or {}).get("simulation_time_ns", 0))
    except (TypeError, ValueError):
        return False
    if abs(ns - 10.0) > 1e-6:
        return False
    if t.payment_status != "paid":
        return False
    amt = t.payment_amount
    if amt is None:
        return "免费" in (t.payment_note or "")
    try:
        return float(amt) <= 0.0
    except (TypeError, ValueError):
        return False


def count_used_free_10ns(user_id: str, all_tasks: dict) -> int:
    """统计用户已消耗的 10 ns 免费次数。"""
    if not user_id:
        return 0
    n = 0
    for t in all_tasks.values():
        if t.user_id != user_id:
            continue
        if is_free_10ns_task(t):
            n += 1
    return n


def free_10ns_remaining(user_id: str, all_tasks: dict) -> int:
    """返回用户剩余 10 ns 免费额度。"""
    used = count_used_free_10ns(user_id, all_tasks)
    return max(0, FREE_10NS_QUOTA - used)


def can_use_free_10ns(task: "Task", all_tasks: dict) -> bool:
    """当前任务是否可用免费额度（10 ns 且仍有剩余）。"""
    try:
        ns = float((task.params or {}).get("simulation_time_ns", 0))
    except (TypeError, ValueError):
        return False
    if abs(ns - 10.0) > 1e-6:
        return False
    if task.payment_status == "paid":
        return False
    return free_10ns_remaining(task.user_id, all_tasks) > 0


def price_for_sim_ns(sim_ns: float, user_id: str = "", all_tasks: dict | None = None) -> float:
    """根据模拟时长返回应付金额；10 ns 且有免费额度时为 0。"""
    ns = float(sim_ns)
    if abs(ns - 10.0) < 1e-6 and user_id and all_tasks is not None:
        if free_10ns_remaining(user_id, all_tasks) > 0:
            return 0.0
    return _tier(ns)[0]


def qr_urls_for_sim_ns(sim_ns: float) -> tuple[str, str]:
    """返回 (支付宝收款码 URL, 微信收款码 URL)。"""
    _, ali, wx = _tier(sim_ns)
    return ali, wx


def pricing_table() -> dict[str, dict]:
    """返回前端/配置接口用的时长定价表。"""
    out: dict[str, dict] = {}
    for ns, (amount, ali, wx) in sorted(_TIER_BY_NS.items()):
        key = str(int(ns)) if float(ns).is_integer() else str(ns)
        item = {
            "amount": amount,
            "qr_url": ali,
            "wechat_qr_url": wx,
        }
        if abs(float(ns) - 10.0) < 1e-6:
            item["free_quota"] = FREE_10NS_QUOTA
            item["label"] = f"免费（每人 {FREE_10NS_QUOTA} 次），用尽后 ¥{amount:g}"
        out[key] = item
    return out


def calc_payment_amount(
    task_id: str,
    base: float,
    user_id: str = "",
) -> float:
    """返回应付金额；默认固定为基准价，可通过环境变量开启唯一尾数。"""
    if float(base) <= 0:
        return 0.0
    if os.environ.get("WEBMD_PAYMENT_UNIQUE_CENTS", "0").strip().lower() in ("1", "true", "yes"):
        key = f"{user_id}:{task_id}" if user_id else task_id
        try:
            n = int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)
        except ValueError:
            n = sum(ord(c) for c in key)
        cents = (n % 99) + 1
        return round(base + cents / 100.0, 2)
    return round(float(base), 2)


def get_admin_payment_key() -> str:
    """管理员核实密钥，可通过环境变量 WEBMD_ADMIN_KEY 覆盖。"""
    return os.environ.get("WEBMD_ADMIN_KEY", "webmd-admin-2026")


def verify_admin_key(k: str) -> bool:
    """校验管理员密钥。"""
    expected = get_admin_payment_key()
    return bool(k) and k == expected
