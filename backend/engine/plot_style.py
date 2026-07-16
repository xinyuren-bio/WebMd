# ==================================================
# 功能说明：统一 MD 分析出图风格（对齐 md_xhs/Figure/analysis.py 与 protein_lig.py 流水线）
# 使用方法：from plot_style import apply_md_style, plot_line, plot_multiline, save_figure
# 依赖环境：pip install matplotlib
# 生成时间：2026-07-13
# ==================================================

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# 与 Figure/analysis.py 一致的配色
COLOR_PROTEIN = "#f38181"
COLOR_LIGAND = "#81c4f4"
COLOR_COMPLEX = "#95E1A3"
COLOR_RG = "#81c4f4"
COLOR_RMSF = "#95E1A3"
COLOR_SASA = "#3d9e8c"
COLORS_RMSD = [COLOR_PROTEIN, COLOR_LIGAND, COLOR_COMPLEX]

# 二级结构堆叠图配色（与参考 SASA+DSSP 图一致）
SS_COLORS = {
    "Alpha": "#4a90d9",
    "Beta": "#f5a623",
    "3-10": "#7ed321",
    "Turn": "#d0021b",
    "Bend/Coil": "#9013fe",
}

FIG_LINE = (8, 5)
FIG_PANEL = (10, 7)
DPI = 300
RMSD_Y_PAD = 1.5


def apply_md_style() -> None:
    """应用 md_xhs analysis.py 全局 matplotlib 样式。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans", "sans-serif"]
    plt.rcParams["font.size"] = 14
    plt.rcParams["axes.titlesize"] = 16
    plt.rcParams["axes.labelsize"] = 15
    plt.rcParams["xtick.labelsize"] = 13
    plt.rcParams["ytick.labelsize"] = 13
    plt.rcParams["legend.fontsize"] = 13
    plt.rcParams["figure.dpi"] = DPI


def save_figure(fig, path: str) -> None:
    """按 publication 风格保存图片。"""
    fig.savefig(path, bbox_inches="tight", dpi=DPI)


def plot_line(
    xs: Sequence[float],
    ys: Sequence[float],
    path: str,
    xlabel: str,
    ylabel: str,
    title: str,
    color: str = COLOR_LIGAND,
    y_pad: Optional[float] = None,
) -> None:
    """单曲线时序图（RMSD / Rg / RMSF / SASA 等）。"""
    if not xs or not ys:
        return
    apply_md_style()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=FIG_LINE)
    ax.plot(xs, ys, c=color, linewidth=1.2)
    ax.set(xlabel=xlabel, ylabel=ylabel, title=title)
    if y_pad is not None:
        y_lo, y_hi = float(min(ys)), float(max(ys))
        pad = max(0.0, float(y_pad))
        ax.set_ylim(max(0.0, y_lo - pad), y_hi + pad)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    save_figure(fig, path)
    plt.close(fig)


def plot_multiline(
    xs: Sequence[float],
    series: Sequence[Tuple[Sequence[float], str, str]],
    path: str,
    xlabel: str,
    ylabel: str,
    title: str,
    y_pad: Optional[float] = None,
) -> None:
    """多曲线时序图（与 analysis.py 的 RMSD 合图一致）。"""
    if not xs or not series:
        return
    apply_md_style()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=FIG_LINE)
    all_y: List[float] = []
    for idx, (ys, label, color) in enumerate(series):
        if not ys:
            continue
        c = color or COLORS_RMSD[idx % len(COLORS_RMSD)]
        ax.plot(xs, ys, c=c, label=label, linewidth=1.2)
        all_y.extend(float(v) for v in ys)
    if not all_y:
        plt.close(fig)
        return
    if y_pad is not None:
        y_lo, y_hi = min(all_y), max(all_y)
        pad = max(0.0, float(y_pad))
        ax.set_ylim(max(0.0, y_lo - pad), y_hi + pad)
    ax.set(xlabel=xlabel, ylabel=ylabel, title=title)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    save_figure(fig, path)
    plt.close(fig)


def plot_sasa(
    xs: Sequence[float],
    ys: Sequence[float],
    path: str,
) -> None:
    """单独绘制 SASA 时序图。"""
    plot_line(xs, ys, path, "Time (ns)", "SASA (Å²)", "SASA", color=COLOR_SASA)


def plot_secondary_structure(
    ss_data: dict,
    ss_order: Sequence[str],
    path: str,
    xs: Optional[Sequence[float]] = None,
) -> None:
    """单独绘制二级结构堆叠面积图。"""
    apply_md_style()
    import matplotlib.pyplot as plt
    import numpy as np

    n = 0
    for v in ss_data.values():
        if v:
            n = len(v)
            break
    if n <= 0:
        return
    if xs is not None and len(xs) == n:
        x = np.asarray(xs, dtype=float)
        xlabel = "Time (ns)"
    else:
        x = np.arange(n)
        xlabel = "Frame"

    fig, ax = plt.subplots(figsize=FIG_LINE)
    bottom = np.zeros(n)
    has_ss = any(sum(ss_data.get(c, [])) > 0 for c in ss_order)
    if not has_ss:
        ax.text(0.5, 0.5, "DSSP N/A", ha="center", va="center", transform=ax.transAxes)
    else:
        for cat in ss_order:
            y = np.array(ss_data.get(cat, [0.0] * n), dtype=float)
            if len(y) != n:
                continue
            ax.fill_between(x, bottom, bottom + y, label=cat, color=SS_COLORS.get(cat, "#888888"), alpha=0.85)
            bottom = bottom + y
        ax.set_ylabel("Residue Count")
        ax.set_title("Secondary Structure Analysis")
        ax.legend(loc="upper right", ncol=3)
        ax.grid(alpha=0.3)
    ax.set_xlabel(xlabel)
    fig.tight_layout()
    save_figure(fig, path)
    plt.close(fig)


def plot_sasa_ss(
    sasa_y: Optional[Sequence[float]],
    ss_data: dict,
    ss_order: Sequence[str],
    path: str,
) -> None:
    """SASA + 二级结构堆叠面积双面板图（兼容旧接口）。"""
    apply_md_style()
    import matplotlib.pyplot as plt
    import numpy as np

    n = 0
    if sasa_y:
        n = len(sasa_y)
    elif ss_data:
        for v in ss_data.values():
            if v:
                n = len(v)
                break
    if n <= 0:
        return

    x = np.arange(n)
    fig, axes = plt.subplots(2, 1, figsize=FIG_PANEL, sharex=True, gridspec_kw={"height_ratios": [1, 1.2]})
    ax1, ax2 = axes

    if sasa_y:
        ax1.plot(x, sasa_y, c=COLOR_SASA, linewidth=1.2)
        ax1.set_ylabel("SASA (Å²)")
        ax1.set_title("SASA")
        ax1.grid(alpha=0.3)
    else:
        ax1.text(0.5, 0.5, "SASA N/A", ha="center", va="center", transform=ax1.transAxes)

    bottom = np.zeros(n)
    has_ss = any(sum(ss_data.get(c, [])) > 0 for c in ss_order)
    if has_ss:
        for cat in ss_order:
            y = np.array(ss_data.get(cat, [0.0] * n), dtype=float)
            if len(y) != n:
                continue
            ax2.fill_between(x, bottom, bottom + y, label=cat, color=SS_COLORS.get(cat, "#888888"), alpha=0.85)
            bottom = bottom + y
        ax2.set_ylabel("Residue Count")
        ax2.set_title("Secondary Structure Analysis")
        ax2.legend(loc="upper right", ncol=3)
        ax2.grid(alpha=0.3)
    else:
        ax2.text(0.5, 0.5, "DSSP N/A", ha="center", va="center", transform=ax2.transAxes)

    ax2.set_xlabel("Frame")
    fig.tight_layout()
    save_figure(fig, path)
    plt.close(fig)
