# ==================================================
# 功能说明：付费下载辅助（按模拟时长定价、管理员密钥校验）
# 使用方法：由 api/routes 调用
# 依赖环境：Python 标准库
# 生成时间：2026-07-14
# ==================================================

import hashlib
import os


# 时长档位：10 ns → ¥10，100 ns → ¥66，200 ns → ¥240
PRICE_10NS = float(os.environ.get("WEBMD_PRICE_10NS", "10"))
PRICE_100NS = float(os.environ.get("WEBMD_PRICE_100NS", "66"))
PRICE_200NS = float(os.environ.get("WEBMD_PRICE_200NS", "240"))
QR_10_ALIPAY = os.environ.get("WEBMD_QR_10_ALIPAY", "/assets/images/pay_10.jpg")
QR_10_WECHAT = os.environ.get("WEBMD_QR_10_WECHAT", "/assets/images/wechat_pay_10.png")
QR_150_ALIPAY = os.environ.get("WEBMD_QR_150_ALIPAY", "/assets/images/pay_66.jpg")
QR_150_WECHAT = os.environ.get("WEBMD_QR_150_WECHAT", "/assets/images/wechat_pay_66.png")
QR_240_ALIPAY = os.environ.get("WEBMD_QR_240_ALIPAY", "/assets/images/pay.jpg")
QR_240_WECHAT = os.environ.get("WEBMD_QR_240_WECHAT", "/assets/images/wechat_pay.png")

# 时长(ns) → (金额, 支付宝收款码, 微信收款码)
_TIER_BY_NS: dict[float, tuple[float, str, str]] = {
    10.0: (PRICE_10NS, QR_10_ALIPAY, QR_10_WECHAT),
    100.0: (PRICE_100NS, QR_150_ALIPAY, QR_150_WECHAT),
    200.0: (PRICE_200NS, QR_240_ALIPAY, QR_240_WECHAT),
}


def _tier(sim_ns: float) -> tuple[float, str, str]:
    """查找时长档位；未知时长回退到 100 ns 档。"""
    return _TIER_BY_NS.get(float(sim_ns), _TIER_BY_NS[100.0])


def price_for_sim_ns(sim_ns: float) -> float:
    """根据模拟时长（ns）返回应付金额。"""
    return _tier(sim_ns)[0]


def qr_urls_for_sim_ns(sim_ns: float) -> tuple[str, str]:
    """返回 (支付宝收款码 URL, 微信收款码 URL)。"""
    _, ali, wx = _tier(sim_ns)
    return ali, wx


def pricing_table() -> dict[str, dict]:
    """返回前端/配置接口用的时长定价表。"""
    out: dict[str, dict] = {}
    for ns, (amount, ali, wx) in sorted(_TIER_BY_NS.items()):
        key = str(int(ns)) if float(ns).is_integer() else str(ns)
        out[key] = {
            "amount": amount,
            "qr_url": ali,
            "wechat_qr_url": wx,
        }
    return out


def calc_payment_amount(
    task_id: str,
    base: float,
    user_id: str = "",
) -> float:
    """返回应付金额；默认固定为基准价，可通过环境变量开启唯一尾数。"""
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
