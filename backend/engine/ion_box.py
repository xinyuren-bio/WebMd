# ==================================================
# 功能说明：根据 Amber inpcrd 实际盒矢量计算体积与一价盐离子对数
# 使用方法：由 system_builder 两阶段溶剂化调用；也可单独单测
# 依赖环境：Python 标准库
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 阿伏伽德罗常数 (mol^-1)
N_A = 6.02214076e23
# 1 Å³ = 1e-27 L
_A3_TO_L = 1e-27
# 两阶段盒体积相对偏差上限（加水离子不应改变盒边）
_BOX_VOL_REL_TOL = 1e-4


@dataclass
class BoxInfo:
    """正交或一般晶胞盒信息。"""

    lx: float
    ly: float
    lz: float
    alpha: float = 90.0
    beta: float = 90.0
    gamma: float = 90.0

    @property
    def volume_A3(self) -> float:
        """盒体积 (Å³)；正交为乘积，非正交用行列式公式。"""
        return box_volume_A3(self.lx, self.ly, self.lz, self.alpha, self.beta, self.gamma)

    @property
    def is_orthogonal(self) -> bool:
        """是否可视为正交盒。"""
        return (
            abs(self.alpha - 90.0) < 1e-3
            and abs(self.beta - 90.0) < 1e-3
            and abs(self.gamma - 90.0) < 1e-3
        )


@dataclass
class SaltPlan:
    """目标盐浓度对应的加盐方案（不含中和反离子）。"""

    salt_type: str
    cation: str
    target_conc_M: float
    volume_A3: float
    n_pair: int


@dataclass
class SaltReport:
    """最终体系盐离子报告（区分额外盐对与中和反离子）。"""

    salt_type: str
    cation: str
    target_conc_M: float
    volume_stage1_A3: float
    volume_final_A3: float
    n_pair: float
    n_cation_total: int
    n_anion_total: int
    n_neutralize_cation: int
    n_neutralize_anion: int
    actual_pair_conc_M: float


def box_volume_A3(
    lx: float,
    ly: float,
    lz: float,
    alpha: float = 90.0,
    beta: float = 90.0,
    gamma: float = 90.0,
) -> float:
    """由晶胞边长与夹角计算体积 (Å³)。

    设计思路：正交盒 V=Lx·Ly·Lz；一般晶胞使用标准行列式公式，
    避免非正交时直接相乘导致体积错误。
    """
    if min(lx, ly, lz) <= 0:
        return 0.0
    if (
        abs(alpha - 90.0) < 1e-6
        and abs(beta - 90.0) < 1e-6
        and abs(gamma - 90.0) < 1e-6
    ):
        return lx * ly * lz
    a = math.radians(alpha)
    b = math.radians(beta)
    g = math.radians(gamma)
    # V = abc * sqrt(1 - cos²α - cos²β - cos²γ + 2 cosα cosβ cosγ)
    ca, cb, cg = math.cos(a), math.cos(b), math.cos(g)
    tri = 1.0 - ca * ca - cb * cb - cg * cg + 2.0 * ca * cb * cg
    if tri <= 0:
        raise ValueError(f"非法晶胞角导致体积无定义: α={alpha}, β={beta}, γ={gamma}")
    return lx * ly * lz * math.sqrt(tri)


def ion_pairs_from_conc(c: float, vol_A3: float) -> int:
    """由摩尔浓度与体积 (Å³) 计算一价盐离子对数。

    N_pair = round(C × V_A3 × 1e-27 × N_A)
    """
    if c <= 0 or vol_A3 <= 0:
        return 0
    n = c * (vol_A3 * _A3_TO_L) * N_A
    return max(0, int(round(n)))


def actual_pair_concentration_M(n_pair: int, vol_A3: float) -> float:
    """由最终盒体积与盐对数反算实际额外盐对浓度 (M)。"""
    if n_pair <= 0 or vol_A3 <= 0:
        return 0.0
    vol_L = vol_A3 * _A3_TO_L
    return float(n_pair) / (vol_L * N_A)


def read_amber_inpcrd_box(fp: str | Path) -> BoxInfo:
    """从 Amber ASCII inpcrd/rst7 读取盒矢量。

    坐标按 6F12.7 排列；全部原子之后为盒边长，可选夹角（度）。
    """
    p = Path(fp)
    raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(raw) < 3:
        raise ValueError(f"inpcrd 过短: {p}")
    # 第 2 行：原子数（可能附带时间）
    try:
        natom = int(raw[1].split()[0])
    except (IndexError, ValueError) as e:
        raise ValueError(f"无法解析 inpcrd 原子数: {p}") from e

    # 坐标：每行最多 6 个浮点数，共 3*natom 个
    vals: list[float] = []
    for line in raw[2:]:
        parts = line.split()
        if not parts:
            continue
        try:
            vals.extend(float(x) for x in parts)
        except ValueError:
            break
    need = 3 * natom
    if len(vals) < need:
        raise ValueError(f"inpcrd 坐标不足: 需要 {need}，得到 {len(vals)}")
    box_vals = vals[need:]
    if len(box_vals) < 3:
        raise ValueError(f"inpcrd 缺少盒信息: {p}")
    lx, ly, lz = box_vals[0], box_vals[1], box_vals[2]
    alpha = box_vals[3] if len(box_vals) >= 6 else 90.0
    beta = box_vals[4] if len(box_vals) >= 6 else 90.0
    gamma = box_vals[5] if len(box_vals) >= 6 else 90.0
    return BoxInfo(lx=lx, ly=ly, lz=lz, alpha=alpha, beta=beta, gamma=gamma)


def assert_box_volume_consistent(v1: float, v2: float, tol: float = _BOX_VOL_REL_TOL) -> None:
    """校验两阶段盒体积一致；偏差过大则失败。"""
    if v1 <= 0 or v2 <= 0:
        raise RuntimeError(f"盒体积非法: stage1={v1}, final={v2}")
    rel = abs(v2 - v1) / v1
    if rel > tol:
        raise RuntimeError(
            f"两阶段盒体积不一致：stage1={v1:.3f} Å³, final={v2:.3f} Å³, "
            f"相对偏差={rel:.3e} > {tol:.1e}。禁止静默使用旧体积。"
        )


def plan_salt_pairs(
    salt_type: str,
    cation: str,
    target_conc_M: float,
    volume_A3: float,
) -> SaltPlan:
    """根据实际溶剂化盒体积规划额外盐对数（不含中和）。"""
    n = ion_pairs_from_conc(target_conc_M, volume_A3)
    return SaltPlan(
        salt_type=salt_type,
        cation=cation,
        target_conc_M=target_conc_M,
        volume_A3=volume_A3,
        n_pair=n,
    )


def build_salt_report(
    *,
    salt_type: str,
    cation: str,
    target_conc_M: float,
    volume_stage1_A3: float,
    volume_final_A3: float,
    n_pair: int,
    n_cation_total: int,
    n_anion_total: int,
) -> SaltReport:
    """汇总盐报告：额外盐对 vs 中和反离子 vs 实际盐对浓度。"""
    # 额外盐对各贡献 n_pair 个阴阳离子；超出部分视为中和反离子
    n_neu_cat = max(0, n_cation_total - n_pair)
    n_neu_ani = max(0, n_anion_total - n_pair)
    c_act = actual_pair_concentration_M(n_pair, volume_final_A3)
    return SaltReport(
        salt_type=salt_type,
        cation=cation,
        target_conc_M=target_conc_M,
        volume_stage1_A3=volume_stage1_A3,
        volume_final_A3=volume_final_A3,
        n_pair=float(n_pair),
        n_cation_total=n_cation_total,
        n_anion_total=n_anion_total,
        n_neutralize_cation=n_neu_cat,
        n_neutralize_anion=n_neu_ani,
        actual_pair_conc_M=c_act,
    )


def format_salt_report(r: SaltReport) -> str:
    """生成中文盐离子报告文本。"""
    lines = [
        "WebMD 溶剂化盐离子报告",
        "====================",
        "",
        f"盐种类: {r.salt_type.upper()}（阳离子 {r.cation}，阴离子 Cl-）",
        f"目标额外盐浓度: {r.target_conc_M:.6g} M（不含中和反离子）",
        f"第一阶段盒体积: {r.volume_stage1_A3:.3f} Å³",
        f"最终盒体积: {r.volume_final_A3:.3f} Å³",
        f"额外盐对数 N_pair: {int(r.n_pair)}",
        f"中和用阳离子数: {r.n_neutralize_cation}",
        f"中和用阴离子数: {r.n_neutralize_anion}",
        f"最终阳离子总数: {r.n_cation_total}",
        f"最终阴离子总数: {r.n_anion_total}",
        f"按最终盒体积计算的实际额外盐对浓度: {r.actual_pair_conc_M:.6g} M",
        "",
        "说明：目标/实际浓度均指「中和之后额外加入的一价盐对浓度」，",
        "不是体系中全部阴阳离子折合的总浓度。",
    ]
    return "\n".join(lines) + "\n"


def log_salt_report(r: SaltReport) -> None:
    """将盐报告写入日志。"""
    logger.info(
        "盐离子: 目标额外浓度=%.4g M, N_pair=%d, 中和阳离子=%d, 中和阴离子=%d, "
        "最终%s=%d, Cl-=%d, 实际额外盐对浓度=%.4g M, 盒体积=%.0f Å³",
        r.target_conc_M,
        int(r.n_pair),
        r.n_neutralize_cation,
        r.n_neutralize_anion,
        r.cation,
        r.n_cation_total,
        r.n_anion_total,
        r.actual_pair_conc_M,
        r.volume_final_A3,
    )
