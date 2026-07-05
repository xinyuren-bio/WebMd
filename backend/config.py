import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
}

WATER_MODEL = "tip3p"
PROTEIN_FF = "amber14sb"

# 付费下载：将收款码图片放到 frontend/assets/images/wechat-pay.png
PAYMENT_AMOUNT = 30.0
PAYMENT_QR_URL = "/assets/images/wechat-pay.png"
PAYMENT_CURRENCY = "CNY"
