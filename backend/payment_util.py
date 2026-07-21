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


# ==================================================
# 方案A：按体系大小（原子数）分档定价
# 说明：价格只由体系大小决定，与模拟时长无关；10 ns 免费额度仍优先。
#       原子数未知或关闭开关时，回退到上面的纯时长定价，保持向后兼容。
# ==================================================

# 是否启用按体系大小定价（默认开启；设为 0 回退纯时长定价）
SIZE_PRICING_ENABLED = os.environ.get("WEBMD_SIZE_PRICING", "1").strip().lower() in ("1", "true", "yes")

# >最大档（30 万原子）时展示的客服微信码，引导面议
SUPPORT_QR = os.environ.get("WEBMD_SUPPORT_QR", "/assets/images/wechat_support.png")

# 体系分档：(原子数上限, 档位名, 价格, 支付宝码, 微信码)
# 从小到大匹配第一个「原子数 <= 上限」的档位；超过最大上限则为 XXL 面议
SIZE_TIERS: list[tuple[int, str, float, str, str]] = [
    (40000,  "S",  float(os.environ.get("WEBMD_PRICE_S",  "77")),  "/assets/images/pay_77.jpg",  "/assets/images/wechat_pay_77.png"),
    (90000,  "M",  float(os.environ.get("WEBMD_PRICE_M",  "111")), "/assets/images/pay_111.jpg", "/assets/images/wechat_pay_111.png"),
    (150000, "L",  float(os.environ.get("WEBMD_PRICE_L",  "148")), "/assets/images/pay_148.jpg", "/assets/images/wechat_pay_148.png"),
    (300000, "XL", float(os.environ.get("WEBMD_PRICE_XL", "222")), "/assets/images/pay_222.jpg", "/assets/images/wechat_pay_222.png"),
]


def size_tier_for(atom_count: int) -> dict:
    """按原子数返回体系分档信息。

    设计思路：从小到大匹配第一个「原子数 <= 上限」的档位；超过最大上限
    （>30 万原子）返回面议档（price=None），前端引导添加客服微信。
    原子数未知（<=0）时返回空 dict，调用方据此回退纯时长定价。
    """
    n = int(atom_count or 0)
    if n <= 0:
        return {}
    for upper, name, price, ali, wx in SIZE_TIERS:
        if n <= upper:
            return {
                "tier": name,
                "price": price,
                "qr_url": ali,
                "wechat_qr_url": wx,
                "negotiable": False,
            }
    # 超过最大档：面议
    return {
        "tier": "XXL",
        "price": None,
        "qr_url": SUPPORT_QR,
        "wechat_qr_url": SUPPORT_QR,
        "negotiable": True,
    }


def price_for(
    sim_ns: float,
    atom_count: int = 0,
    user_id: str = "",
    all_tasks: dict | None = None,
) -> float | None:
    """按方案A返回应付金额。

    返回值：float 金额；0.0 表示免费；None 表示面议（>30 万原子）。
    优先级：10 ns 免费额度 > 体系大小定价 > 纯时长定价（回退）。
    """
    ns = float(sim_ns)
    # 10 ns 免费额度优先（无论体系大小）
    if abs(ns - 10.0) < 1e-6 and user_id and all_tasks is not None:
        if free_10ns_remaining(user_id, all_tasks) > 0:
            return 0.0
    if SIZE_PRICING_ENABLED:
        info = size_tier_for(atom_count)
        if info:
            return info["price"]  # 可能为 None（面议）
    return _tier(ns)[0]


def qr_urls_for(sim_ns: float, atom_count: int = 0) -> tuple[str, str]:
    """返回 (支付宝码, 微信码)；启用大小定价且原子数已知时按体系档返回。"""
    if SIZE_PRICING_ENABLED:
        info = size_tier_for(atom_count)
        if info:
            return info["qr_url"], info["wechat_qr_url"]
    _, ali, wx = _tier(sim_ns)
    return ali, wx


def size_pricing_table() -> dict:
    """返回体系分档定价表，供前端展示。"""
    tiers = []
    prev = 0
    for upper, name, price, ali, wx in SIZE_TIERS:
        tiers.append({
            "tier": name,
            "atom_min": prev,
            "atom_max": upper,
            "amount": price,
            "qr_url": ali,
            "wechat_qr_url": wx,
        })
        prev = upper
    # 面议档
    tiers.append({
        "tier": "XXL",
        "atom_min": prev,
        "atom_max": None,
        "amount": None,
        "negotiable": True,
        "qr_url": SUPPORT_QR,
        "wechat_qr_url": SUPPORT_QR,
    })
    return {"enabled": SIZE_PRICING_ENABLED, "support_qr_url": SUPPORT_QR, "tiers": tiers}


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
