# ==================================================
# 功能说明：解析 GROMACS sham 输出的 XPM，绘制 2D/3D Gibbs 自由能景观图
# 使用方法：python fel_plot.py --xpm gibbs.xpm --output-dir analysis_plots
# 依赖环境：pip install numpy matplotlib
# 生成时间：2026-07-13
# ==================================================

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

import matplotlib.cm as cm
import numpy as np
from matplotlib.colors import Normalize
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

# 保证 plot_style 可导入
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from plot_style import apply_md_style, save_figure

# 与 md_xhs/Figure/fel.py 一致
FEL_CMAP = "coolwarm"
FEL_TITLE = "Gibbs Energy Landscape"


def parse_xpm(p: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, str]]:
    """解析 GROMACS XPM 文件，返回 Z 矩阵、坐标轴与元数据。"""
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.rstrip() for line in f]

    meta: Dict[str, str] = {}
    for line in lines:
        if line.startswith("/* ") and "*/" in line:
            m = re.search(r'/\*\s*(\w+):\s*"([^"]*)"', line)
            if m:
                meta[m.group(1)] = m.group(2)

    dim_line = None
    start_colors = 0
    for i, line in enumerate(lines):
        if re.match(r'^\s*"\d+\s+\d+\s+\d+\s+\d+"', line):
            dim_line = line
            start_colors = i + 1
            break
    if dim_line is None:
        raise ValueError("未找到 XPM 尺寸行")

    parts = re.findall(r"\d+", dim_line)
    nx, ny, ncolors, cpp = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])

    symbol_to_value: Dict[str, float] = {}
    for j in range(start_colors, start_colors + ncolors):
        if j >= len(lines):
            break
        line = lines[j]
        val_match = re.search(r'/\*\s*"([^"]*)"\s*\*/', line)
        sym_match = re.match(r'"(\S+)\s+c\s', line)
        if sym_match and val_match:
            sym = sym_match.group(1)
            try:
                symbol_to_value[sym] = float(val_match.group(1))
            except ValueError:
                symbol_to_value[sym] = 0.0

    x_axis = None
    y_axis = None
    for line in lines:
        if "/* x-axis:" in line:
            nums = re.findall(r"[-\d.eE+]+", line.split(":", 1)[1].split("*/")[0])
            x_axis = np.array([float(x) for x in nums])
        if "/* y-axis:" in line:
            nums = re.findall(r"[-\d.eE+]+", line.split(":", 1)[1].split("*/")[0])
            y_axis = np.array([float(x) for x in nums])

    data_rows = []
    for line in lines:
        s = line.strip().rstrip(",")
        if not s.startswith('"') or "/*" in s or " c #" in s:
            continue
        s = s[1:]
        if s.endswith('"'):
            s = s[:-1]
        if len(s) == nx * cpp:
            data_rows.append(s)

    if len(data_rows) != ny:
        raise ValueError(f"数据行数 {len(data_rows)} 与 ny={ny} 不符")

    z = np.zeros((ny, nx))
    for i, row in enumerate(data_rows):
        for j in range(nx):
            sym = row[j * cpp : (j + 1) * cpp]
            z[i, j] = symbol_to_value.get(sym, np.nan)

    if x_axis is None:
        x_axis = np.arange(nx, dtype=float)
    if y_axis is None:
        y_axis = np.arange(ny, dtype=float)

    if len(x_axis) == nx + 1:
        x_axis = (x_axis[:-1] + x_axis[1:]) / 2.0
    if len(y_axis) == ny + 1:
        y_axis = (y_axis[:-1] + y_axis[1:]) / 2.0

    if len(x_axis) != nx or len(y_axis) != ny:
        x_axis = np.linspace(0, 1, nx)
        y_axis = np.linspace(0, 1, ny)

    y_axis = np.asarray(y_axis)[::-1].copy()
    return z, x_axis, y_axis, meta


def plot_fel_2d(z, x_axis, y_axis, meta, out_path: str) -> None:
    """绘制 2D 等高线 Gibbs 自由能景观图。"""
    apply_md_style()
    import matplotlib.pyplot as plt

    x, y = np.meshgrid(x_axis, y_axis)
    xlabel = meta.get("x-label", "PC1")
    ylabel = meta.get("y-label", "PC2")
    title = meta.get("title", FEL_TITLE)
    clabel = meta.get("legend", "G (kJ/mol)")

    fig, ax = plt.subplots(figsize=(8, 6))
    levels = np.linspace(np.nanmin(z), np.nanmax(z), 25)
    cf = ax.contourf(x, y, z, levels=levels, cmap=FEL_CMAP)
    ax.contour(x, y, z, levels=levels[::2], colors="k", linewidths=0.3, alpha=0.5)
    plt.colorbar(cf, ax=ax, label=clabel)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    fig.tight_layout()
    save_figure(fig, out_path)
    plt.close(fig)


def plot_fel_3d(z, x_axis, y_axis, meta, out_path: str) -> None:
    """绘制 3D 曲面 Gibbs 自由能景观图（底面含 2D 投影）。"""
    apply_md_style()
    import matplotlib.pyplot as plt

    x, y = np.meshgrid(x_axis, y_axis)
    xlabel = meta.get("x-label", "PC1")
    ylabel = meta.get("y-label", "PC2")
    title = meta.get("title", FEL_TITLE)
    clabel = meta.get("legend", "G (kJ/mol)")

    z_min, z_max = np.nanmin(z), np.nanmax(z)
    levels = np.linspace(z_min, z_max, 25)

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.contourf(x, y, z, levels=levels, zdir="z", offset=z_min, cmap=FEL_CMAP, alpha=0.9)
    ax.plot_surface(x, y, z, cmap=FEL_CMAP, edgecolor="none", alpha=0.85)
    ax.set_xlabel(xlabel, labelpad=10)
    ax.set_ylabel(ylabel, labelpad=22)
    ax.set_zlabel(clabel, labelpad=10)
    ax.set_title(title)
    ax.set_zlim(z_min, z_max)

    sm = cm.ScalarMappable(cmap=FEL_CMAP, norm=Normalize(vmin=z_min, vmax=z_max))
    sm.set_array([])
    fig.colorbar(sm, ax=ax, shrink=0.6, aspect=20, label=clabel)
    fig.subplots_adjust(left=0.02, right=0.82)
    fig.tight_layout()
    ax.set_ylabel(ylabel, labelpad=36)
    save_figure(fig, out_path)
    plt.close(fig)


def plot_fel_from_xpm(xpm_path: str, out_dir: str, prefix: str = "gibbs") -> Tuple[str, str]:
    """从 XPM 生成 2D/3D FEL 图，返回输出路径元组。"""
    os.makedirs(out_dir, exist_ok=True)
    z, xa, ya, meta = parse_xpm(xpm_path)
    if "title" not in meta:
        meta["title"] = FEL_TITLE
    out_2d = os.path.join(out_dir, f"{prefix}_fel_2d.png")
    out_3d = os.path.join(out_dir, f"{prefix}_fel_3d.png")
    plot_fel_2d(z, xa, ya, meta, out_2d)
    plot_fel_3d(z, xa, ya, meta, out_3d)
    return out_2d, out_3d


def main() -> int:
    ap = argparse.ArgumentParser(description="解析 GROMACS XPM 并绘制 Gibbs 自由能景观")
    ap.add_argument("--xpm", required=True, help="gibbs.xpm 路径")
    ap.add_argument("--output-dir", required=True, help="图片输出目录")
    ap.add_argument("--prefix", default="gibbs", help="输出文件名前缀")
    a = ap.parse_args()
    if not os.path.isfile(a.xpm):
        print(f"未找到 XPM 文件: {a.xpm}")
        return 1
    p2, p3 = plot_fel_from_xpm(a.xpm, a.output_dir, a.prefix)
    print(f"已保存 2D FEL: {p2}")
    print(f"已保存 3D FEL: {p3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
