# ==================================================
# 功能说明：高级轨迹分析（三组 RMSD、Gibbs FEL、SASA+二级结构）
# 使用方法：由 traj_analyze.py 调用 run_advanced(...)，或独立运行
# 依赖环境：GROMACS gmx；numpy、matplotlib
# 生成时间：2026-07-13
# ==================================================

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# 保证 fel_plot 与同目录脚本可导入
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# 标准氨基酸与溶剂/离子残基名（用于识别配体）
_AA20 = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "HID", "HIE", "HIP",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HSD", "HSE", "HSP",
}
_溶剂离子 = {
    "SOL", "WAT", "HOH", "TIP3", "TIP4", "SPC", "NA", "CL", "K", "MG", "CA", "ZN",
    "NA+", "CL-", "K+", "IB+", "RB+", "CS+", "LI+", "BR-", "F-", "IOD", "CA2", "MG2", "ZN2",
}


def _跑(
    gmx: str,
    *a: str,
    inp: str = "",
    wd: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> bool:
    """执行 gmx 子命令。"""
    p = wd or Path.cwd()
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run(
            [gmx, *a],
            input=inp,
            text=True,
            cwd=str(p),
            capture_output=True,
            timeout=3600,
            env=env,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _跑带日志(
    gmx: str,
    *a: str,
    inp: str = "",
    wd: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """执行 gmx 并返回 (成功与否, stderr 末段)。"""
    p = wd or Path.cwd()
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run(
            [gmx, *a],
            input=inp,
            text=True,
            cwd=str(p),
            capture_output=True,
            timeout=3600,
            env=env,
        )
        err = (r.stderr or r.stdout or "")[-800:]
        return r.returncode == 0, err
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def _定位dssp可执行文件() -> Optional[str]:
    """查找 mkdssp/dssp，供旧版 gmx do_dssp 使用。"""
    d = os.environ.get("DSSP", "").strip()
    if d and os.path.isfile(d) and os.access(d, os.X_OK):
        return d
    for name in ("mkdssp", "dssp"):
        p = subprocess.run(["which", name], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    for p in (
        "/usr/bin/mkdssp", "/usr/bin/dssp",
        "/usr/local/bin/mkdssp", "/usr/local/bin/dssp",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _检测dssp模式(gmx: str, wd: Path) -> str:
    """
    检测 GROMACS DSSP 子命令模式：
    - native：GROMACS 2024+ 内置 gmx dssp（-num）
    - legacy：gmx do_dssp + 外部 mkdssp
    - none：不可用
    """
    ok, help_native = _跑带日志(gmx, "dssp", "-h", wd=wd)
    if ok and "-num" in help_native:
        return "native"
    ok, _ = _跑带日志(gmx, "do_dssp", "-h", wd=wd)
    if ok:
        return "legacy"
    return "none"


def _尝试安装dssp(log: Callable[[str], None]) -> Optional[str]:
    """调用 install_dssp.sh 安装外部 DSSP（旧版 GROMACS）。"""
    script = _HERE / "install_dssp.sh"
    if not script.is_file():
        return _定位dssp可执行文件()
    try:
        r = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(_HERE),
        )
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("export DSSP="):
                # 解析 export DSSP='path'
                val = ln.split("=", 1)[1].strip().strip("'\"")
                os.environ["DSSP"] = val
                log(f"[DSSP] 已定位外部程序: {val}")
                return val
        if r.stderr:
            log(f"[DSSP] 安装提示: {r.stderr.strip()[:200]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"[DSSP] 安装脚本失败: {e}")
    return _定位dssp可执行文件()


def _跑dssp分析(
    wd: Path,
    gmx: str,
    xtc: str,
    prot: int,
    dssp_xvg: Path,
    log: Callable[[str], None],
) -> bool:
    """按 GROMACS 版本自动选择 gmx dssp 或 gmx do_dssp。"""
    mode = _检测dssp模式(gmx, wd)
    if mode == "none":
        log("[DSSP] 当前 GROMACS 无 dssp/do_dssp 子命令")
        return False

    if mode == "native":
        log("[DSSP] 使用 GROMACS 内置 gmx dssp（2024+，无需安装 mkdssp）")
        # 多数 MD 拓扑无全氢，用 -hmode dssp -clear
        ok, err = _跑带日志(
            gmx, "dssp", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx",
            "-num", dssp_xvg.name, "-o", "analysis_dssp.dat",
            "-tu", "ns", "-hmode", "dssp", "-clear",
            "-sel", "protein",
            wd=wd,
        )
        if not ok:
            # 退回 ndx 组号选择
            ok, err = _跑带日志(
                gmx, "dssp", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx",
                "-num", dssp_xvg.name, "-o", "analysis_dssp.dat",
                "-tu", "ns", "-hmode", "dssp", "-clear",
                inp=f"{prot}\n", wd=wd,
            )
        if not ok:
            log(f"[DSSP] gmx dssp 失败: {err.strip()[:300]}")
        return ok and dssp_xvg.is_file()

    # legacy: do_dssp + 外部 mkdssp
    dssp_bin = _定位dssp可执行文件() or _尝试安装dssp(log)
    if not dssp_bin:
        log("[DSSP] 未找到 mkdssp：请 apt install dssp 或 conda install -c conda-forge dssp")
        log("[DSSP] 或升级 GROMACS 至 2024+ 使用内置 gmx dssp")
        return False
    env = {"DSSP": dssp_bin}
    log(f"[DSSP] 使用 gmx do_dssp + {dssp_bin}")
    # Ubuntu dssp 包多为 v4，优先 -ver 4
    for ver_flag in ("-ver", "4"), ("",):
        args = ["do_dssp", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx",
                "-o", "analysis_dssp.xpm", "-sc", dssp_xvg.name, "-tu", "ns"]
        if ver_flag[0]:
            args.extend(list(ver_flag))
        ok, err = _跑带日志(gmx, *args, inp=f"{prot}\n", wd=wd, extra_env=env)
        if ok and dssp_xvg.is_file():
            return True
    log(f"[DSSP] gmx do_dssp 失败: {err.strip()[:300]}")
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


def _读xvg列名(p: Path) -> List[str]:
    """从 xvg 的 @ sN legend 行读取列名。"""
    names: Dict[int, str] = {}
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'@ s(\d+) legend "([^"]+)"', ln.strip())
        if m:
            names[int(m.group(1))] = m.group(2)
    if not names:
        return []
    return [names[i] for i in sorted(names)]


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


def _解析ndx组(p: Path) -> Dict[str, int]:
    """解析 ndx 文件，返回 {组名: 组号}（与 gmx 交互编号一致）。"""
    groups: Dict[str, int] = {}
    if not p.is_file():
        return groups
    idx = 0
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            name = s[1:-1].strip()
            groups[name] = idx
            idx += 1
    return groups


def _从gro找配体残基(gro: Path) -> Optional[str]:
    """从 gro 中推断单个配体残基名（兼容旧逻辑）。"""
    all_lig = _从gro找所有配体残基(gro)
    return all_lig[0] if all_lig else None


def _读环肽残基范围(wd: Path) -> Optional[Tuple[int, int]]:
    """读取肽在 gro 中的实际残基号范围（禁止直接用设计号 9001）。

    tleap combine 后残基号会重排；优先按序列映射到 md.gro 中的真实编号。
    """
    try:
        from peptide_resid_map import peptide_ri_range_for_ndx
        mapped = peptide_ri_range_for_ndx(wd)
        if mapped is not None:
            return mapped
    except Exception:
        pass
    # 回退：仅当已写入 resid_gmx_* 时使用；绝不使用裸 resid_start(9001)
    import json
    meta = wd / "webmd_cyclic_peptide.json"
    if not meta.is_file():
        return None
    try:
        d = json.loads(meta.read_text(encoding="utf-8"))
        a, b = int(d.get("resid_gmx_start") or 0), int(d.get("resid_gmx_end") or 0)
        if a > 0 and b >= a:
            return a, b
    except (OSError, TypeError, ValueError):
        return None
    return None


def _从gro找所有配体残基(gro: Path) -> List[str]:
    """从 gro 中推断全部配体残基名（优先 LIG1/LIG2/LIG3）。"""
    if not gro.is_file():
        return []
    het: Dict[str, int] = {}
    for ln in gro.read_text(encoding="utf-8", errors="replace").splitlines()[2:]:
        if len(ln) < 10:
            continue
        rn = ln[5:10].strip().upper()
        if not rn or rn in _AA20 or rn in _溶剂离子:
            continue
        het[rn] = het.get(rn, 0) + 1
    if not het:
        return []
    lig_order = [f"LIG{i}" for i in range(1, 4)]
    found = [r for r in lig_order if r in het]
    if found:
        return found
    for pref in ("UNL", "LIG", "MOL", "UNK", "UN1"):
        if pref in het:
            return [pref]
    return sorted(het.keys(), key=lambda k: het[k], reverse=True)


def _扩展ndx配体复合物(wd: Path, gmx: str, gro: Path) -> None:
    """在 to.ndx 中追加各配体组、Ligand 合并组与 Complex 组。"""
    ndx = wd / "to.ndx"
    groups = _解析ndx组(ndx)
    prot = groups.get("Protein", 1)
    nxt = max(groups.values()) + 1 if groups else 13

    # 环肽：按残基号范围建 Ligand（标准 AA 名无法用 r LIG1 识别）
    cyc_range = _读环肽残基范围(wd)
    if cyc_range is not None:
        a, b = cyc_range
        lines_c: List[str] = []
        if "Ligand" not in groups:
            lines_c.append(f"ri {a}-{b}\nname {nxt} Ligand\n")
            lig_tmp = nxt
            nxt += 1
        else:
            lig_tmp = groups["Ligand"]
        if "Receptor" not in groups:
            lines_c.append(f"{prot} & ! {lig_tmp}\nname {nxt} Receptor\n")
            rec_tmp = nxt
            nxt += 1
        else:
            rec_tmp = groups["Receptor"]
        if "Complex" not in groups:
            lines_c.append(f"{rec_tmp} | {lig_tmp}\nname {nxt} Complex\n")
            nxt += 1
        if lines_c:
            _跑(
                gmx, "make_ndx", "-f", gro.name, "-n", ndx.name, "-o", ndx.name,
                inp="".join(lines_c) + "q\n", wd=wd,
            )
            groups = _解析ndx组(ndx)
        # 追加 Receptor_Backbone = Receptor & Backbone（叠合参考不含肽）
        if "Receptor_Backbone" not in groups:
            rec_id = groups.get("Receptor")
            bb_id = groups.get("Backbone", groups.get("MainChain"))
            if rec_id is not None and bb_id is not None:
                nxt2 = max(groups.values()) + 1 if groups else nxt
                _跑(
                    gmx, "make_ndx", "-f", gro.name, "-n", ndx.name, "-o", ndx.name,
                    inp=f"{rec_id} & {bb_id}\nname {nxt2} Receptor_Backbone\nq\n",
                    wd=wd,
                )
        return

    lig_res_list = _从gro找所有配体残基(gro)
    if not lig_res_list:
        return
    lig_group_ids: List[int] = []
    lines: List[str] = []
    for res in lig_res_list:
        gname = f"Ligand_{res}" if len(lig_res_list) > 1 else "Ligand"
        if gname in groups:
            lig_group_ids.append(groups[gname])
            continue
        lines.append(f"r {res}\nname {nxt} {gname}\n")
        lig_group_ids.append(nxt)
        groups[gname] = nxt
        nxt += 1
    if lines:
        inp = "".join(lines) + "q\n"
        _跑(gmx, "make_ndx", "-f", gro.name, "-n", ndx.name, "-o", ndx.name, inp=inp, wd=wd)
        groups = _解析ndx组(ndx)
        nxt = max(groups.values()) + 1
    # 合并 Ligand 组（多配体）
    if len(lig_res_list) > 1 and "Ligand" not in groups:
        merge = " | ".join(str(i) for i in lig_group_ids)
        inp = f"{merge}\nname {nxt} Ligand\nq\n"
        _跑(gmx, "make_ndx", "-f", gro.name, "-n", ndx.name, "-o", ndx.name, inp=inp, wd=wd)
        groups = _解析ndx组(ndx)
        nxt = max(groups.values()) + 1
    if "Complex" not in groups:
        lig_id = groups.get("Ligand", lig_group_ids[0] if lig_group_ids else None)
        if lig_id is not None:
            inp = f"{prot} | {lig_id}\nname {nxt} Complex\nq\n"
            _跑(gmx, "make_ndx", "-f", gro.name, "-n", ndx.name, "-o", ndx.name, inp=inp, wd=wd)


def _找组号(groups: Dict[str, int], *candidates: str) -> Optional[int]:
    """按候选名查找 ndx 组号。"""
    for c in candidates:
        if c in groups:
            return groups[c]
    upper = {k.upper(): v for k, v in groups.items()}
    for c in candidates:
        if c.upper() in upper:
            return upper[c.upper()]
    return None


def _resolve_groups(wd: Path, gmx: str) -> Dict[str, int]:
    """解析 Backbone / Protein / Ligand / Complex 组号。"""
    gro = wd / "md.gro"
    if not gro.is_file():
        gro = wd / "npt.gro"
    ndx = wd / "to.ndx"
    if gro.is_file() and ndx.is_file():
        _扩展ndx配体复合物(wd, gmx, gro)
    groups = _解析ndx组(ndx)
    out: Dict[str, int] = {}
    bb = _找组号(groups, "Receptor_Backbone", "Backbone", "MainChain")
    # 环肽/线形肽场景优先用 Receptor（蛋白不含肽）
    prot = _找组号(groups, "Receptor", "Protein")
    lig = _找组号(groups, "Ligand", "UNL", "LIG", "MOL", "UNK")
    cpx = _找组号(groups, "Complex", "Protein_Ligand", "Protein-MOL")
    if bb is None:
        bb = 4
    if prot is None:
        prot = 1
    if lig is None:
        lig_res = _从gro找配体残基(gro) if gro.is_file() else None
        if lig_res:
            for k, v in groups.items():
                if lig_res.upper() in k.upper():
                    lig = v
                    break
    if cpx is None and lig is not None and prot is not None:
        cpx = lig  # 退化为配体组，后续 sham 仍可用蛋白
    out["backbone"] = bb
    out["protein"] = prot
    if lig is not None:
        out["ligand"] = lig
    if cpx is not None:
        out["complex"] = cpx
    elif lig is not None and prot is not None:
        out["complex"] = prot
    return out


def _跑rmsd图(
    wd: Path,
    gmx: str,
    xtc: str,
    fit_g: int,
    calc_g: int,
    tag: str,
    label: str,
    color: str,
    csv_dir: Path,
    plot_dir: Path,
    log: Callable[[str], None],
) -> Tuple[List[float], List[float]]:
    """以 backbone 为参考叠合，计算指定组 RMSD 并出单图（单位 Å，md_xhs 风格）。"""
    from plot_style import RMSD_Y_PAD, plot_line

    xvg = wd / f"analysis_rmsd_{tag}.xvg"
    inp = f"{fit_g}\n{calc_g}\n"
    if not _跑(gmx, "rms", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx", "-o", xvg.name, "-tu", "ns", inp=inp, wd=wd):
        log(f"[RMSD-{tag}] 计算失败")
        return [], []
    rows = _读xvg数据(xvg)
    if not rows:
        log(f"[RMSD-{tag}] 无有效数据")
        return [], []
    xs = [r[0] for r in rows]
    ys = [r[1] * 10.0 for r in rows]  # nm → Å
    _xvg转csv(xvg, csv_dir / f"rmsd_{tag}.csv", ["time_ns", "rmsd_angstrom"])
    plot_dir.mkdir(parents=True, exist_ok=True)
    plot_line(xs, ys, str(plot_dir / f"rmsd_{tag}.png"), "Time (ns)", "RMSD (Å)", "RMSD", color=color, y_pad=RMSD_Y_PAD)
    avg = sum(ys) / len(ys)
    log(f"[RMSD-{tag}] 均值: {avg:.2f} Å（参考: 蛋白 Backbone）")
    return xs, ys


def _写合并rmsd(
    series: List[Tuple[List[float], List[float], str, str]],
    csv_dir: Path,
    plot_dir: Path,
    log: Callable[[str], None],
) -> None:
    """输出与 md_xhs analysis.py 一致的 RMSD 合图与 CSV。"""
    from plot_style import RMSD_Y_PAD, plot_multiline

    if not series:
        return
    xs = series[0][0]
    if not xs:
        return
    # 写 md_xhs 格式 CSV
    csv_p = csv_dir / "rmsd.csv"
    csv_p.parent.mkdir(parents=True, exist_ok=True)
    col_map = {"protein": "Protein_RMSD (Å)", "ligand": "Ligand_RMSD (Å)", "complex": "Complex_RMSD (Å)"}
    data: Dict[str, List[float]] = {}
    for _, ys, label, _ in series:
        key = label.lower()
        if key in col_map:
            data[col_map[key]] = ys
    with csv_p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        headers = ["Time (ns)"] + list(data.keys())
        w.writerow(headers)
        for i, t in enumerate(xs):
            row = [t] + [data[h][i] for h in data if i < len(data[h])]
            w.writerow(row)

    plot_data = [(ys, label, color) for _, ys, label, color in series if ys]
    plot_multiline(
        xs,
        plot_data,
        str(plot_dir / "rmsd.png"),
        "Time (ns)",
        "RMSD (Å)",
        "RMSD",
        y_pad=RMSD_Y_PAD,
    )
    log(f"[RMSD] 已生成合图 rmsd.png（md_xhs 风格，{len(plot_data)} 条曲线）")


def _pca合并(f1: Path, f2: Path, out: Path) -> bool:
    """合并两个 xvg 的第二列为 sham 输入（PC1+PC2）。"""
    def _load(p: Path) -> List[List[str]]:
        data = []
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith("@") or s.startswith("&"):
                continue
            data.append(s.split())
        return data

    if not f1.is_file() or not f2.is_file():
        return False
    d1, d2 = _load(f1), _load(f2)
    lines = []
    for i in range(min(len(d1), len(d2))):
        # 按行号对齐（RMSD 与 Rg 帧数一致即可，时间列单位可能不同）
        lines.append(f"{d1[i][0]:>16} {d1[i][1]:>16} {d2[i][1]:>16}")
    if len(lines) < 10:
        return False
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _跑fel(
    wd: Path,
    gmx: str,
    xtc: str,
    groups: Dict[str, int],
    plot_dir: Path,
    log: Callable[[str], None],
) -> bool:
    """RMSD+Rg → sham → Gibbs FEL 2D/3D 图。"""
    bb = groups["backbone"]
    prot = groups["protein"]
    rmsd_xvg = wd / "fel_rmsd.xvg"
    rg_xvg = wd / "fel_gyrate.xvg"
    combined = wd / "fel_pc.xvg"
    gibbs_xpm = wd / "gibbs.xpm"

    if not _跑(
        gmx, "rms", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx",
        "-o", rmsd_xvg.name, "-tu", "ns", inp=f"{bb}\n{prot}\n", wd=wd,
    ):
        log("[FEL] gmx rms 失败")
        return False
    if not _跑(gmx, "gyrate", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx", "-o", rg_xvg.name, inp=f"{prot}\n", wd=wd):
        log("[FEL] gmx gyrate 失败")
        return False
    if not _pca合并(rmsd_xvg, rg_xvg, combined):
        log("[FEL] PC 数据合并失败（帧数过少？）")
        return False
    if not _跑(
        gmx, "sham", "-tsham", "300", "-nlevels", "100",
        "-f", combined.name, "-ls", gibbs_xpm.name,
        "-g", "gibbs.log", "-lsh", "enthalpy.xpm", "-lss", "entropy.xpm",
        wd=wd,
    ):
        log("[FEL] gmx sham 失败（轨迹过短时常发生）")
        return False
    if not gibbs_xpm.is_file():
        log("[FEL] 未生成 gibbs.xpm")
        return False
    try:
        from fel_plot import plot_fel_from_xpm
        p2, p3 = plot_fel_from_xpm(str(gibbs_xpm), str(plot_dir), "gibbs")
        log(f"[FEL] 已生成 Gibbs 自由能景观: {Path(p2).name}, {Path(p3).name}")
        return True
    except Exception as e:
        log(f"[FEL] 绘图失败: {e}")
        return False


def _归一化ss列名(name: str) -> Optional[str]:
    """将 gmx dssp 列名映射到堆叠图类别。"""
    n = name.lower()
    if "alpha" in n or "a-helix" in n or n == "helix" or n == "h":
        return "Alpha"
    if "beta" in n or "b-sheet" in n or "sheet" in n or n == "e":
        return "Beta"
    if "3-10" in n or "3_10" in n or "310" in n or n == "g":
        return "3-10"
    if "turn" in n or n == "t":
        return "Turn"
    if "bend" in n or "coil" in n or "loop" in n or "structure" in n or n in ("s", "c", "~", " "):
        return "Bend/Coil"
    if "bridge" in n or n == "b":
        return "Beta"
    if "pi-helix" in n or n == "i":
        return "Alpha"
    return None


def _跑sasa二级结构(
    wd: Path,
    gmx: str,
    xtc: str,
    groups: Dict[str, int],
    csv_dir: Path,
    plot_dir: Path,
    log: Callable[[str], None],
) -> bool:
    """SASA + DSSP 二级结构计数堆叠图（双面板）。"""
    prot = groups["protein"]
    sasa_xvg = wd / "analysis_sasa.xvg"
    dssp_xvg = wd / "analysis_dssp_sc.xvg"

    sasa_ok = _跑(
        gmx, "sasa", "-s", "md.tpr", "-f", xtc, "-n", "to.ndx",
        "-o", sasa_xvg.name, "-tu", "ns", inp=f"{prot}\n", wd=wd,
    )
    dssp_ok = _跑dssp分析(wd, gmx, xtc, prot, dssp_xvg, log)
    if not sasa_ok and not dssp_ok:
        log("[SASA/DSSP] 计算均失败（可能未安装 dssp 或轨迹过短）")
        return False

    try:
        from plot_style import plot_sasa_ss
    except ImportError:
        log("[SASA/DSSP] 缺少 matplotlib，跳过出图")
        return False

    frames: Optional[List[float]] = None
    sasa_vals: Optional[List[float]] = None
    if sasa_ok and sasa_xvg.is_file():
        rows = _读xvg数据(sasa_xvg)
        if rows:
            frames = [r[0] for r in rows]
            sasa_vals = [r[1] for r in rows]  # nm² → 换算为 Å²
            sasa_vals = [v * 100.0 for v in sasa_vals]
            _xvg转csv(sasa_xvg, csv_dir / "sasa.csv", ["time_ns", "sasa_nm2"])

    ss_cats = ["Alpha", "Beta", "3-10", "Turn", "Bend/Coil"]
    ss_data: Dict[str, List[float]] = {c: [] for c in ss_cats}

    if dssp_ok and dssp_xvg.is_file():
        legends = _读xvg列名(dssp_xvg)
        rows = _读xvg数据(dssp_xvg)
        if rows:
            if frames is None:
                frames = [r[0] for r in rows]
            col_map: Dict[int, str] = {}
            for i, leg in enumerate(legends):
                cat = _归一化ss列名(leg)
                if cat:
                    col_map[i + 1] = cat  # xvg 第 0 列为时间
            if not col_map:
                # 默认列序（常见 gmx dssp -sc 输出）
                default = ["Bend/Coil", "Turn", "Beta", "Bend/Coil", "Alpha", "3-10"]
                for j in range(1, min(len(rows[0]), 7)):
                    col_map[j] = default[j - 1] if j - 1 < len(default) else "Bend/Coil"
            for _ in ss_cats:
                ss_data[_] = [0.0] * len(rows)
            for ri, r in enumerate(rows):
                for ci, val in enumerate(r[1:], start=1):
                    cat = col_map.get(ci)
                    if cat:
                        ss_data[cat][ri] += val
            # 写 CSV
            csv_p = csv_dir / "secondary_structure.csv"
            csv_p.parent.mkdir(parents=True, exist_ok=True)
            with csv_p.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["frame"] + ss_cats)
                for i, fr in enumerate(frames or range(len(rows))):
                    w.writerow([i] + [ss_data[c][i] for c in ss_cats])

    if frames is None:
        return False

    out_png = plot_dir / "sasa_secondary_structure.png"
    plot_dir.mkdir(parents=True, exist_ok=True)
    plot_sasa_ss(sasa_vals, ss_data, ss_cats, str(out_png))
    log(f"[SASA/DSSP] 已生成: {out_png.name}")
    return True


def run_advanced(
    wd: Path,
    gmx: str,
    xtc_name: str,
    csv_dir: Path,
    plot_dir: Path,
    log: Callable[[str], None],
) -> None:
    """执行高级分析流程（三组 RMSD、FEL、SASA+DSSP）。"""
    log("--- 高级分析 ---")
    groups = _resolve_groups(wd, gmx)
    bb = groups.get("backbone", 4)
    prot = groups.get("protein", 1)

    from plot_style import COLOR_COMPLEX, COLOR_LIGAND, COLOR_PROTEIN

    lig_colors = ["#e11d48", "#2563eb", "#ca8a04"]

    # 三组 RMSD（均以蛋白 Backbone 为参考叠合）+ md_xhs 风格合图
    rmsd_series: List[Tuple[List[float], List[float], str, str]] = []
    xs, ys = _跑rmsd图(wd, gmx, xtc_name, bb, prot, "protein", "Protein", COLOR_PROTEIN, csv_dir, plot_dir, log)
    if xs:
        rmsd_series.append((xs, ys, "Protein", COLOR_PROTEIN))

    gro = wd / "md.gro"
    if not gro.is_file():
        gro = wd / "npt.gro"
    lig_res_list = _从gro找所有配体残基(gro) if gro.is_file() else []
    ndx = wd / "to.ndx"
    ndx_groups = _解析ndx组(ndx) if ndx.is_file() else {}

    per_lig_done = False
    if len(lig_res_list) > 1:
        for i, res in enumerate(lig_res_list):
            gname = f"Ligand_{res}"
            gid = _找组号(ndx_groups, gname)
            if gid is None:
                continue
            color = lig_colors[i % len(lig_colors)]
            tag = res.lower()
            xs, ys = _跑rmsd图(wd, gmx, xtc_name, bb, gid, tag, res, color, csv_dir, plot_dir, log)
            if xs:
                rmsd_series.append((xs, ys, res, color))
                per_lig_done = True
        if per_lig_done and (plot_dir / f"rmsd_{lig_res_list[0].lower()}.png").is_file():
            # 兼容旧交付检查：首个配体图复制为 rmsd_ligand.png
            import shutil
            shutil.copy(
                plot_dir / f"rmsd_{lig_res_list[0].lower()}.png",
                plot_dir / "rmsd_ligand.png",
            )

    if not per_lig_done and "ligand" in groups:
        xs, ys = _跑rmsd图(wd, gmx, xtc_name, bb, groups["ligand"], "ligand", "Ligand", COLOR_LIGAND, csv_dir, plot_dir, log)
        if xs:
            rmsd_series.append((xs, ys, "Ligand", COLOR_LIGAND))
    elif not per_lig_done:
        log("[RMSD-ligand] 未找到配体组，跳过")
    if "complex" in groups:
        xs, ys = _跑rmsd图(wd, gmx, xtc_name, bb, groups["complex"], "complex", "Complex", COLOR_COMPLEX, csv_dir, plot_dir, log)
        if xs:
            rmsd_series.append((xs, ys, "Complex", COLOR_COMPLEX))
    else:
        log("[RMSD-complex] 未找到复合物组，跳过")
    _写合并rmsd(rmsd_series, csv_dir, plot_dir, log)

    _跑fel(wd, gmx, xtc_name, groups, plot_dir, log)
    _跑sasa二级结构(wd, gmx, xtc_name, groups, csv_dir, plot_dir, log)
    log("--- 高级分析结束 ---")
