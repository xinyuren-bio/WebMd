# ==================================================
# 功能说明：MD 轨迹自动分析（RMSD / Rg / RMSF / 氢键），输出 CSV 与图片
# 使用方法：python traj_analyze.py --workdir 任务目录 --out analysis_summary.txt
# 依赖环境：GROMACS gmx；可选 numpy、matplotlib
# 生成时间：2026-07-13
# ==================================================

import argparse
import csv
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# 保证同目录下的 advanced_analyze / fel_plot 可被导入
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def _跑(gmx: str, *a: str, inp: str = "", wd: Optional[Path] = None) -> bool:
    """执行 gmx 子命令。"""
    p = wd or Path.cwd()
    try:
        r = subprocess.run(
            [gmx, *a],
            input=inp,
            text=True,
            cwd=str(p),
            capture_output=True,
            timeout=3600,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _读xvg数据(p: Path) -> List[Tuple[float, ...]]:
    """解析 xvg 为数值行列表。"""
    rows: List[Tuple[float, ...]] = []
    if not p.is_file():
        return rows
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("@"):
            continue
        parts = s.split()
        try:
            rows.append(tuple(float(x) for x in parts))
        except ValueError:
            pass
    return rows


def _读xvg末值(p: Path) -> Optional[float]:
    """读取 xvg 最后一列末值。"""
    rows = _读xvg数据(p)
    return rows[-1][-1] if rows else None


def _读xvg列均值(p: Path, col: int = 1) -> Optional[float]:
    """读取 xvg 指定列均值。"""
    rows = _读xvg数据(p)
    if not rows or len(rows[0]) <= col:
        return None
    xs = [r[col] for r in rows]
    return sum(xs) / len(xs) if xs else None


def _读xvg最大(p: Path, col: int = 1) -> Tuple[Optional[float], Optional[int]]:
    """读取 xvg 列最大值及行序号。"""
    rows = _读xvg数据(p)
    if not rows or len(rows[0]) <= col:
        return None, None
    best, idx = None, None
    for i, r in enumerate(rows):
        x = r[col]
        if best is None or x > best:
            best, idx = x, i
    return best, idx


def _xvg转csv(xvg: Path, csv_p: Path, headers: Optional[List[str]] = None) -> None:
    """将 xvg 转为 csv 文件。"""
    rows = _读xvg数据(xvg)
    if not rows:
        return
    csv_p.parent.mkdir(parents=True, exist_ok=True)
    ncol = len(rows[0])
    if not headers:
        headers = ["x"] + [f"y{i}" for i in range(1, ncol)]
    with csv_p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers[:ncol])
        for r in rows:
            w.writerow(r)


def _出图(
    xvg: Path,
    png: Path,
    xl: str,
    yl: str,
    title: str,
    color: str = "#81c4f4",
    scale: float = 1.0,
) -> None:
    """输出曲线图到 analysis_plots/（md_xhs 风格）。"""
    try:
        from plot_style import plot_line
    except ImportError:
        return
    rows = _读xvg数据(xvg)
    if not rows:
        return
    xs = [r[0] for r in rows]
    ys = [r[1] * scale for r in rows]
    png.parent.mkdir(parents=True, exist_ok=True)
    plot_line(xs, ys, str(png), xl, yl, title, color=color)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    wd = Path(a.workdir)
    out_p = Path(a.out)
    gmx = os.environ.get("GMX", "gmx")
    lines: List[str] = []
    csv_dir = wd / "analysis_csv"
    plot_dir = wd / "analysis_plots"

    def _记(s: str) -> None:
        lines.append(s)
        print(s)

    tpr = wd / "md.tpr"
    xtc = None
    # 分析必须用与 md.tpr 原子数一致的全体系轨迹（优先 fit_system）
    for cand in ("fit_system.xtc", "fit.xtc", "md.xtc"):
        p = wd / cand
        if p.is_file() and p.stat().st_size > 0:
            xtc = p
            break

    if not tpr.is_file():
        _记("错误：未找到 md.tpr，跳过分析")
        out_p.write_text("\n".join(lines), encoding="utf-8")
        return 1

    _记("=== WebMD 轨迹自动分析 ===")

    if xtc is None:
        _记("提示：无有效轨迹 xtc（短测试 run 常见），跳过 RMSD/Rg/RMSF 等轨迹分析")
        edr = wd / "md.edr"
        if edr.is_file():
            en_xvg = wd / "analysis_energy.xvg"
            if _跑(gmx, "energy", "-f", "md.edr", "-o", str(en_xvg.name), inp="10\n0\n", wd=wd):
                pot = _读xvg末值(en_xvg)
                _xvg转csv(en_xvg, csv_dir / "energy.csv", ["time_ps", "potential_kj_mol"])
                if pot is not None:
                    _记(f"[能量] 最终势能: {pot:.1f} kJ/mol")
        _记("=== 分析完成（无轨迹模式）===")
        out_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0

    _记(f"分析轨迹: {xtc.name}")

    # RMSD
    rmsd_xvg = wd / "analysis_rmsd.xvg"
    if _跑(gmx, "rms", "-s", "md.tpr", "-f", xtc.name, "-o", str(rmsd_xvg.name), "-tu", "ns", inp="1\n1\n", wd=wd):
        fin = _读xvg末值(rmsd_xvg)
        avg = _读xvg列均值(rmsd_xvg)
        _xvg转csv(rmsd_xvg, csv_dir / "rmsd_backbone.csv", ["time_ns", "rmsd_nm"])
        _出图(rmsd_xvg, plot_dir / "rmsd_backbone.png", "Time (ns)", "RMSD (Å)", "RMSD", color="#f38181", scale=10.0)
        if avg:
            _记(f"[RMSD] 骨架均方根偏差均值: {avg:.3f} nm")
        if fin is not None:
            _记(f"[RMSD] 最终值: {fin:.3f} nm")
    else:
        _记("[RMSD] 计算失败")

    # Rg
    rg_xvg = wd / "analysis_rg.xvg"
    if _跑(gmx, "gyrate", "-s", "md.tpr", "-f", xtc.name, "-o", str(rg_xvg.name), inp="1\n", wd=wd):
        avg = _读xvg列均值(rg_xvg, col=1)
        _xvg转csv(rg_xvg, csv_dir / "rg.csv", ["time_ns", "rg_nm"])
        _出图(rg_xvg, plot_dir / "rg.png", "Time (ns)", "Rg (Å)", "Radius of Gyration", color="#81c4f4", scale=10.0)
        if avg:
            _记(f"[Rg] 回转半径均值: {avg:.3f} nm")
    else:
        _记("[Rg] 计算失败")

    # RMSF
    rmsf_xvg = wd / "analysis_rmsf.xvg"
    if _跑(gmx, "rmsf", "-s", "md.tpr", "-f", xtc.name, "-o", str(rmsf_xvg.name), "-res", inp="1\n", wd=wd):
        peak, res_i = _读xvg最大(rmsf_xvg)
        _xvg转csv(rmsf_xvg, csv_dir / "rmsf.csv", ["residue", "rmsf_nm"])
        _出图(rmsf_xvg, plot_dir / "rmsf.png", "Residue Index", "RMSF (Å)", "RMSF (Cα)", color="#95E1A3", scale=10.0)
        if peak is not None:
            _记(f"[RMSF] 最大波动: {peak:.3f} nm" + (f"（残基序号约 {res_i}）" if res_i is not None else ""))
    else:
        _记("[RMSF] 计算失败")

    # 氢键
    hb_xvg = wd / "analysis_hbond.xvg"
    for sel in ("1\n13\n", "1\n1\n"):
        if _跑(gmx, "hbond", "-s", "md.tpr", "-f", xtc.name, "-num", str(hb_xvg.name), inp=sel, wd=wd):
            avg = _读xvg列均值(hb_xvg)
            _xvg转csv(hb_xvg, csv_dir / "hbond.csv", ["time_ps", "hbond_count"])
            _出图(hb_xvg, plot_dir / "hbond.png", "Time (ps)", "H-bonds", "H-bonds", color="#81c4f4")
            if avg is not None:
                _记(f"[氢键] 平均氢键数: {avg:.2f}")
            break
    else:
        _记("[氢键] 计算失败（可能需手动指定 index 组）")

    # 能量
    edr = wd / "md.edr"
    if edr.is_file():
        en_xvg = wd / "analysis_energy.xvg"
        if _跑(gmx, "energy", "-f", "md.edr", "-o", str(en_xvg.name), inp="10\n0\n", wd=wd):
            pot = _读xvg末值(en_xvg)
            _xvg转csv(en_xvg, csv_dir / "energy.csv", ["time_ps", "potential_kj_mol"])
            _出图(en_xvg, plot_dir / "energy.png", "Time (ps)", "Potential (kJ/mol)", "Potential Energy", color="#81c4f4")
            if pot is not None:
                _记(f"[能量] 最终势能: {pot:.1f} kJ/mol")

    # 高级分析：三组 RMSD、Gibbs FEL、SASA+二级结构
    try:
        from advanced_analyze import run_advanced
        run_advanced(wd, gmx, xtc.name, csv_dir, plot_dir, _记)
    except Exception as e:
        _记(f"[高级分析] 跳过或失败: {e}")

    n_csv = len(list(csv_dir.glob("*.csv"))) if csv_dir.is_dir() else 0
    n_png = len(list(plot_dir.glob("*.png"))) if plot_dir.is_dir() else 0
    _记(f"[输出] CSV {n_csv} 个 → analysis_csv/；图片 {n_png} 个 → analysis_plots/")
    _记("=== 分析完成 ===")

    out_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
