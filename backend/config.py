import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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
    "temperature": 298.15,        # K
    "pressure": 1.0,              # bar
    "timestep": 0.002,            # ps (2 fs)
    "simulation_time_ns": 100.0,  # ns
    "report_interval_ps": 100.0,  # 轨迹输出间隔 (ps)
    "constraints": "HBonds",      # GROMACS 约束: h-bonds / all-bonds / h-angles
    "nonbonded_cutoff": 1.0,      # nm
    "tau_t": 0.1,                 # 温控耦合时间 (ps)
    "tau_p": 2.0,                 # 压控耦合时间 (ps)
    "nvt_time_ps": 50.0,          # NVT 平衡时长 (ps)
    "npt_time_ps": 50.0,          # NPT 平衡时长 (ps)
    "box_padding": 10.0,          # 溶剂盒子边距 (Å)
    "ion_conc": 0.15,             # 离子浓度 (mol/L)
    "salt_type": "nacl",          # 背景盐种类：nacl / kcl
    "ligand_add_hydrogens": True, # 配体 antechamber 前自动补氢
}

# 允许的 MD 模拟时长（ns），与定价档位一致
ALLOWED_SIM_NS = (10.0, 100.0, 200.0)

# 允许的背景盐种类（中和阳离子与背景盐阳离子一致）
ALLOWED_SALT_TYPES = ("nacl", "kcl")

WATER_MODEL = "tip3p"
PROTEIN_FF = "amber14sb"

# 付费下载 / MD 模拟（默认开启，¥240）
PAYMENT_ENABLED = os.environ.get("WEBMD_PAYMENT_ENABLED", "1").strip().lower() not in ("0", "false", "no")
PAYMENT_AMOUNT = float(os.environ.get("WEBMD_MD_PRICE", "240"))
MD_MAX_NS = float(os.environ.get("WEBMD_MD_MAX_NS", "200"))
PAYMENT_QR_URL = "/assets/images/pay.jpg"
WECHAT_QR_URL = os.environ.get("WEBMD_WECHAT_QR_URL", "/assets/images/wechat_pay.png")
PAYMENT_CURRENCY = "CNY"

# 自愿打赏（付费开启时默认关闭打赏区）
TIP_ENABLED = os.environ.get("WEBMD_TIP_ENABLED", "0").strip().lower() in ("1", "true", "yes")
TIP_QR_URL = PAYMENT_QR_URL

# 用户与认证
USERS_DB = os.path.join(BASE_DIR, "data", "users.db")
JWT_SECRET = os.environ.get("WEBMD_JWT_SECRET", "webmd-change-jwt-secret-in-production")
JWT_EXPIRE_DAYS = int(os.environ.get("WEBMD_JWT_EXPIRE_DAYS", "14"))
SITE_BASE_URL = os.environ.get("WEBMD_SITE_URL", "http://localhost:8000")

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
MD_CALLBACK_SECRET = os.environ.get("WEBMD_MD_CALLBACK_SECRET", "webmd-md-callback-change-me")
# 邮件单附件大小上限（字节），超过则仅发下载链接
MAX_EMAIL_ATTACH_BYTES = int(os.environ.get("WEBMD_MAX_EMAIL_ATTACH_MB", "20")) * 1024 * 1024
