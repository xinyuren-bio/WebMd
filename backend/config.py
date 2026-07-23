# ==================================================
# 功能说明：WebMD 全局配置与环境变量加载
# 使用方法：由 main / routes / engine 导入；生产请配置 backend/.env
# 依赖环境：Python 标准库
# 生成时间：2026-07-21
# ==================================================

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 已知不安全默认值：生产环境禁止使用
_DEFAULT_JWT_SECRET = "webmd-change-jwt-secret-in-production"
_DEFAULT_MD_CALLBACK_SECRET = "webmd-md-callback-change-me"
_DEFAULT_ADMIN_KEY = "webmd-admin-2026"


def _load_env_file() -> None:
    """从 backend/.env 加载环境变量（不覆盖已存在的变量）。"""
    p = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(p):
        return
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_env_file()

TASKS_DIR = os.path.join(BASE_DIR, "tasks")
os.makedirs(TASKS_DIR, exist_ok=True)

DEFAULT_PARAMS = {
    "temperature": 310.0,         # K（生理温度）
    "pressure": 1.0,              # bar
    "timestep": 0.002,            # ps (2 fs)
    "simulation_time_ns": 100.0,  # ns
    "report_interval_ps": 100.0,  # 轨迹输出间隔 (ps)
    "constraints": "HBonds",      # GROMACS 约束: h-bonds / all-bonds / h-angles
    "nonbonded_cutoff": 1.0,      # nm
    "tau_t": 0.1,                 # 温控耦合时间 (ps)
    "tau_p": 5.0,                 # 压控耦合时间 (ps)；NPT 用 C-rescale，生产用 PR
    "nvt_time_ps": 500.0,         # NVT 平衡时长 (ps)
    "npt_time_ps": 1000.0,        # NPT 平衡时长 (ps)
    "box_padding": 10.0,          # 溶剂盒子边距 (Å)
    "ion_conc": 0.15,             # 中和后额外一价盐对目标浓度 (mol/L)
    "salt_type": "nacl",          # 背景盐种类：nacl / kcl
    "ligand_add_hydrogens": True, # 配体 antechamber 前自动补氢
}

# 允许的 MD 模拟时长（ns），与定价档位一致
ALLOWED_SIM_NS = (10.0, 100.0, 200.0)

# 每用户同时占用的前处理名额上限（含待付款；付费并进入模拟后释放）
MAX_ACTIVE_PREP_TASKS = int(os.environ.get("WEBMD_MAX_ACTIVE_PREP_TASKS", "10"))

# 蛋白标准氨基酸残基数上限（超限需联系管理员；避免小内存机 tleap OOM）
MAX_PROTEIN_RESIDUES = int(os.environ.get("WEBMD_MAX_PROTEIN_RESIDUES", "1000"))

# 不受氨基酸上限限制的用户邮箱（逗号分隔，小写比对）
_PROTEIN_AA_EXEMPT_RAW = os.environ.get(
    "WEBMD_PROTEIN_AA_LIMIT_EXEMPT_EMAILS",
    "lry541818@163.com",
)
PROTEIN_AA_LIMIT_EXEMPT_EMAILS = frozenset(
    x.strip().lower()
    for x in _PROTEIN_AA_EXEMPT_RAW.split(",")
    if x.strip()
)


def is_protein_aa_limit_exempt(email: str) -> bool:
    """判断该邮箱是否豁免蛋白氨基酸数上限。"""
    return (email or "").strip().lower() in PROTEIN_AA_LIMIT_EXEMPT_EMAILS

# 允许的背景盐种类（中和阳离子与背景盐阳离子一致）
ALLOWED_SALT_TYPES = ("nacl", "kcl")

WATER_MODEL = "tip3p"
PROTEIN_FF = "amber14sb"

# 付费下载 / MD 模拟（默认开启；金额以 payment_util 按时长为准）
PAYMENT_ENABLED = os.environ.get("WEBMD_PAYMENT_ENABLED", "1").strip().lower() not in ("0", "false", "no")
PAYMENT_AMOUNT = float(os.environ.get("WEBMD_MD_PRICE", "147.70"))
MD_MAX_NS = float(os.environ.get("WEBMD_MD_MAX_NS", "200"))
PAYMENT_QR_URL = "/assets/images/pay.jpg"
WECHAT_QR_URL = os.environ.get("WEBMD_WECHAT_QR_URL", "/assets/images/wechat_pay.png")
PAYMENT_CURRENCY = "CNY"

# 自愿打赏（付费开启时默认关闭打赏区）
TIP_ENABLED = os.environ.get("WEBMD_TIP_ENABLED", "0").strip().lower() in ("1", "true", "yes")
TIP_QR_URL = PAYMENT_QR_URL

# 用户与认证
USERS_DB = os.path.join(BASE_DIR, "data", "users.db")
JWT_SECRET = os.environ.get("WEBMD_JWT_SECRET", _DEFAULT_JWT_SECRET)
JWT_EXPIRE_DAYS = int(os.environ.get("WEBMD_JWT_EXPIRE_DAYS", "14"))
SITE_BASE_URL = os.environ.get("WEBMD_SITE_URL", "http://localhost:8000")
WEBMD_ENV = os.environ.get("WEBMD_ENV", "development").strip().lower()

# 邮件通知（管理员收信地址勿提交 Git，用环境变量覆盖）
ADMIN_NOTIFY_EMAIL = os.environ.get("WEBMD_ADMIN_NOTIFY_EMAIL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.163.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# 注册邮箱验证码
VERIFY_CODE_EXPIRE_SEC = int(os.environ.get("WEBMD_VERIFY_EXPIRE_SEC", "600"))
VERIFY_CODE_COOLDOWN_SEC = int(os.environ.get("WEBMD_VERIFY_COOLDOWN_SEC", "60"))

# 访问统计 JSON 存储路径
ANALYTICS_FILE = os.path.join(BASE_DIR, "data", "analytics.json")

# AutoDL 远程 MD（SSH 阶段 1，需配置环境变量）
AUTODL_SSH_HOST = os.environ.get("AUTODL_SSH_HOST", "").strip()
AUTODL_SSH_PORT = int(os.environ.get("AUTODL_SSH_PORT", "22"))
AUTODL_SSH_USER = os.environ.get("AUTODL_SSH_USER", "root").strip()
AUTODL_SSH_PASSWORD = os.environ.get("AUTODL_SSH_PASSWORD", "")
AUTODL_REMOTE_DIR = os.environ.get("AUTODL_REMOTE_DIR", "/root/webmd_jobs").strip()
AUTODL_MAX_CONCURRENT = int(os.environ.get("AUTODL_MAX_CONCURRENT", "2"))
AUTODL_MARKET_URL = os.environ.get("AUTODL_MARKET_URL", "https://www.autodl.com/market/list")
MD_CALLBACK_SECRET = os.environ.get(
    "WEBMD_MD_CALLBACK_SECRET", _DEFAULT_MD_CALLBACK_SECRET
)
# 邮件单附件大小上限（字节），超过则仅发下载链接
MAX_EMAIL_ATTACH_BYTES = int(os.environ.get("WEBMD_MAX_EMAIL_ATTACH_MB", "20")) * 1024 * 1024

# 启动副作用开关（1/true 跳过）
SKIP_AUTODL_DISPATCH = os.environ.get("WEBMD_SKIP_AUTODL_DISPATCH", "0").strip().lower() in (
    "1", "true", "yes",
)
SKIP_AMBER_REPAIR = os.environ.get("WEBMD_SKIP_AMBER_REPAIR", "0").strip().lower() in (
    "1", "true", "yes",
)
SKIP_TASK_CLEANUP = os.environ.get("WEBMD_SKIP_TASK_CLEANUP", "0").strip().lower() in (
    "1", "true", "yes",
)

# 任务目录保留天数（超过则自动删除）；清理间隔秒数（默认每天）
TASK_RETENTION_DAYS = int(os.environ.get("WEBMD_TASK_RETENTION_DAYS", "7"))
TASK_CLEANUP_INTERVAL_SEC = int(os.environ.get("WEBMD_TASK_CLEANUP_INTERVAL_SEC", "86400"))


def is_production() -> bool:
    """判断是否按生产环境规则运行。"""
    if WEBMD_ENV in ("prod", "production"):
        return True
    # 未显式标注但站点 URL 已指向公网域名/生产 IP 时，也视为生产
    u = (SITE_BASE_URL or "").lower()
    if "localhost" in u or "127.0.0.1" in u:
        return False
    return any(x in u for x in ("webmd.tech", "39.106.154.145", "https://"))


def cors_allow_origins() -> list[str]:
    """生产环境收紧 CORS；开发仍允许 *。"""
    if not is_production():
        return ["*"]
    base = (SITE_BASE_URL or "").rstrip("/")
    origins = {base}
    if base.startswith("https://"):
        origins.add("http://" + base[len("https://"):])
    elif base.startswith("http://"):
        origins.add("https://" + base[len("http://"):])
    # 临时公网 IP（ICP 备案前）与历史/域名访问入口
    origins.update(
        {
            "http://39.106.154.145",
            "http://39.106.154.145:8000",
            "http://webmd.tech",
            "https://webmd.tech",
            "http://www.webmd.tech",
            "https://www.webmd.tech",
            "http://webmd.tech:8000",
            "http://www.webmd.tech:8000",
            "http://8.219.168.5:8000",
        }
    )
    return sorted(o for o in origins if o)


def assert_production_secrets() -> None:
    """生产环境禁止默认密钥，避免误用开发占位值上线。"""
    if not is_production():
        return
    admin_key = os.environ.get("WEBMD_ADMIN_KEY", _DEFAULT_ADMIN_KEY)
    bad: list[str] = []
    if not JWT_SECRET or JWT_SECRET == _DEFAULT_JWT_SECRET:
        bad.append("WEBMD_JWT_SECRET")
    if not MD_CALLBACK_SECRET or MD_CALLBACK_SECRET == _DEFAULT_MD_CALLBACK_SECRET:
        bad.append("WEBMD_MD_CALLBACK_SECRET")
    if not admin_key or admin_key == _DEFAULT_ADMIN_KEY:
        bad.append("WEBMD_ADMIN_KEY")
    if bad:
        msg = (
            "生产环境检测到未替换的默认密钥: "
            + ", ".join(bad)
            + "。请在 backend/.env 中设置强随机值后重启。"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(msg)
