# ==================================================
# 功能说明：绘制蛋白-配体/肽残基氢键时间图（对齐 md_xhs Figure/hbond_residue_timeline）
# 使用方法：由 advanced_analyze 调用 run_hbond_residue_timeline，或命令行运行本脚本
# 依赖环境：pip install MDAnalysis numpy matplotlib
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

try:
    import MDAnalysis as mda
except ImportError as e:
    print("请先安装 MDAnalysis: pip install MDAnalysis", file=sys.stderr)
    raise e

try:
    import matplotlib.pyplot as plt
except ImportError as e:
    print("请先安装 matplotlib: pip install matplotlib", file=sys.stderr)
    raise e

try:
    from MDAnalysis.analysis.hydrogenbonds.hbond_analysis import HydrogenBondAnalysis
except Exception as e:
    print("当前 MDAnalysis 版本不支持 HydrogenBondAnalysis。请升级: pip install -U MDAnalysis", file=sys.stderr)
    raise e


def _parse_args():
    p = argparse.ArgumentParser(description="绘制蛋白残基-时间氢键时间图")
    p.add_argument("--topology", required=True, help="拓扑文件路径，如 complex.pdb 或 md.tpr")
    p.add_argument("--trajectory", required=True, help="轨迹文件路径，如 fit.xtc")
    p.add_argument("--group1", required=True, help="第一个组；可写 LIG（自动按resname）或 protein（直接选择）")
    p.add_argument("--group2", required=True, help="第二个组；可写 UNK（自动按resname）或完整选择语句")
    p.add_argument("--output-dir", required=True, help="PNG 输出目录（也可作 CSV 默认目录）")
    p.add_argument(
        "--csv-dir",
        default="",
        help="CSV 输出目录；不填则与 --output-dir 相同。推荐与图片目录分开",
    )
    p.add_argument("--prefix", default="hbond_residue_timeline", help="输出文件前缀")
    p.add_argument("--end-ns", type=float, default=None, help="仅分析到该时间(ns)，不填则分析全轨迹")
    p.add_argument("--d-a-cutoff", type=float, default=3.0, help="供体-受体距离阈值(Å)")
    p.add_argument("--angle-cutoff", type=float, default=150.0, help="D-H-A 角阈值(度)")
    p.add_argument(
        "--protein-side",
        choices=("auto", "group1", "group2"),
        default="auto",
        help="指定哪个组作为蛋白残基层（auto 会自动选择包含 protein 原子的组）",
    )
    p.add_argument(
        "--mode",
        choices=("binary", "count"),
        default="binary",
        help="binary: 该帧该残基是否有氢键；count: 该帧该残基氢键个数",
    )
    p.add_argument(
        "--min-frequency",
        type=float,
        default=0.05,
        help="仅显示氢键出现频率高于该阈值的残基，默认0.05表示5%%",
    )
    return p.parse_args()


def _build_selection(text: str) -> str:
    s = (text or "").strip()
    if not s:
        raise ValueError("选择字符串不能为空")

    s_low = s.lower()
    raw_prefixes = (
        "protein",
        "backbone",
        "all",
        "nucleic",
        "water",
        "solvent",
        "resname ",
        "segid ",
        "chainid ",
        "resid ",
        "resnum ",
        "name ",
        "type ",
        "index ",
        "bynum ",
        "around ",
        "not ",
        "same ",
        "global ",
        "prop ",
        "point ",
        "group ",
    )
    has_expr_token = any(ch in s for ch in (" ", "(", ")", "'", "\"", ":", ">", "<", "=", "*"))
    if has_expr_token or s_low.startswith(raw_prefixes):
        return s
    return f"resname {s}"


def _slug(text: str) -> str:
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "group"


def _end_frame_for_ns(u: "mda.Universe", end_ns: float | None) -> int:
    n_frames_full = len(u.trajectory)
    if end_ns is None or end_ns <= 0:
        return n_frames_full

    u.trajectory[0]
    t0 = float(u.trajectory.time)
    if n_frames_full >= 2:
        u.trajectory[1]
        dt = float(u.trajectory.time) - t0
        u.trajectory[0]
    else:
        dt = 100.0
    end_frame = min(n_frames_full, int(end_ns * 1000.0 / dt) + 1)
    return max(end_frame, 1)


def _run_directional_hbond(
    u: "mda.Universe",
    donor_sel: str,
    acceptor_sel: str,
    start: int,
    stop: int,
    d_a_cutoff: float,
    angle_cutoff: float,
) -> np.ndarray:
    hydrogens_sel = f"({donor_sel}) and (name H* or name [0-9]H*)"
    h_atoms = u.select_atoms(hydrogens_sel)
    if len(h_atoms) == 0:
        return np.empty((0, 6), dtype=np.float64)

    h = HydrogenBondAnalysis(
        universe=u,
        donors_sel=donor_sel,
        hydrogens_sel=hydrogens_sel,
        acceptors_sel=acceptor_sel,
        d_a_cutoff=d_a_cutoff,
        d_h_a_angle_cutoff=angle_cutoff,
        update_selections=True,
    )
    h.run(start=start, stop=stop, step=1, verbose=False)
    return h.results.hbonds


def _choose_protein_side(args, g1, g2):
    if args.protein_side == "group1":
        return 1
    if args.protein_side == "group2":
        return 2

    g1_has_protein = len(g1.select_atoms("protein")) > 0
    g2_has_protein = len(g2.select_atoms("protein")) > 0
    if g1_has_protein and not g2_has_protein:
        return 1
    if g2_has_protein and not g1_has_protein:
        return 2
    if g1_has_protein and g2_has_protein:
        print("提示：group1/group2 都包含 protein，自动使用 group1 作为残基层。")
        return 1
    raise ValueError("自动模式下未检测到 protein，请用 --protein-side 手动指定 group1 或 group2。")


def _atom_chain_tag(atom) -> str:
    chain = ""
    try:
        chain = str(atom.chainID).strip()
    except Exception:
        chain = ""
    if not chain:
        try:
            chain = str(atom.segid).strip()
        except Exception:
            chain = ""
    return chain


def main():
    args = _parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    csv_dir = (args.csv_dir or "").strip() or args.output_dir
    os.makedirs(csv_dir, exist_ok=True)

    print("加载轨迹中...")
    u = mda.Universe(args.topology, args.trajectory)

    sel1 = _build_selection(args.group1)
    sel2 = _build_selection(args.group2)
    g1 = u.select_atoms(sel1)
    g2 = u.select_atoms(sel2)
    if len(g1) == 0:
        raise ValueError(f"未选中任何原子: {sel1}")
    if len(g2) == 0:
        raise ValueError(f"未选中任何原子: {sel2}")

    protein_side = _choose_protein_side(args, g1, g2)
    print(f"group1: {args.group1} -> {sel1} ({len(g1)} atoms)")
    print(f"group2: {args.group2} -> {sel2} ({len(g2)} atoms)")
    print(f"蛋白残基层来源: group{protein_side}")

    end_frame = _end_frame_for_ns(u, args.end_ns)
    if args.end_ns is not None and args.end_ns > 0:
        print(f"仅分析前 {args.end_ns} ns：帧 0–{end_frame - 1}（共 {end_frame} 帧）")

    print("计算方向 1: group1 -> group2 ...")
    hb12 = _run_directional_hbond(
        u=u,
        donor_sel=sel1,
        acceptor_sel=sel2,
        start=0,
        stop=end_frame,
        d_a_cutoff=args.d_a_cutoff,
        angle_cutoff=args.angle_cutoff,
    )
    print("计算方向 2: group2 -> group1 ...")
    hb21 = _run_directional_hbond(
        u=u,
        donor_sel=sel2,
        acceptor_sel=sel1,
        start=0,
        stop=end_frame,
        d_a_cutoff=args.d_a_cutoff,
        angle_cutoff=args.angle_cutoff,
    )

    # 按蛋白侧提取“哪个残基在该帧参与了氢键”
    # hbonds 列含义: frame, donor_idx, hydrogen_idx, acceptor_idx, distance, angle
    all_events = []
    if len(hb12) > 0:
        all_events.append(("12", hb12))
    if len(hb21) > 0:
        all_events.append(("21", hb21))

    protein_idx_set = set(u.select_atoms("protein").indices.tolist())
    residue_set = set()
    events = []

    for direction, arr in all_events:
        for row in arr:
            frame = int(row[0])
            donor_idx = int(row[1])
            acceptor_idx = int(row[3])
            donor_atom = u.atoms[donor_idx]
            acceptor_atom = u.atoms[acceptor_idx]

            if protein_side == 1:
                if direction == "12":
                    protein_atom = donor_atom
                else:
                    protein_atom = acceptor_atom
            else:
                if direction == "12":
                    protein_atom = acceptor_atom
                else:
                    protein_atom = donor_atom

            if int(protein_atom.index) not in protein_idx_set:
                continue

            key = (_atom_chain_tag(protein_atom), int(protein_atom.resid), str(protein_atom.resname))
            residue_set.add(key)
            events.append((frame, key))

    if not residue_set:
        raise ValueError("未检测到蛋白残基参与的氢键。请检查组选择、cutoff 参数或轨迹。")

    residues_sorted = sorted(residue_set, key=lambda x: (x[0], x[1], x[2]))
    resid_to_row = {k: i for i, k in enumerate(residues_sorted)}

    matrix = np.zeros((len(residues_sorted), end_frame), dtype=np.float64)
    for frame, key in events:
        r = resid_to_row[key]
        if 0 <= frame < end_frame:
            if args.mode == "binary":
                matrix[r, frame] = 1.0
            else:
                matrix[r, frame] += 1.0

    u.trajectory[0]
    t0 = float(u.trajectory.time)
    if len(u.trajectory) >= 2:
        u.trajectory[1]
        dt = float(u.trajectory.time) - t0
        u.trajectory[0]
    else:
        dt = 100.0
    times_ps = t0 + np.arange(end_frame, dtype=np.float64) * dt
    times_ns = times_ps / 1000.0

    slug1 = _slug(args.group1)
    slug2 = _slug(args.group2)
    csv_path = os.path.join(csv_dir, f"{args.prefix}_{slug1}_vs_{slug2}.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("frame,time_ps,time_ns,chain,resid,resname,value\n")
        for (chain, resid, resname), r in resid_to_row.items():
            for frame in range(end_frame):
                v = matrix[r, frame]
                if v > 0:
                    f.write(
                        f"{frame},{times_ps[frame]:.6f},{times_ns[frame]:.6f},"
                        f"{chain},{resid},{resname},{v:.0f}\n"
                    )
    print(f"已写入: {csv_path}")

    # 参考 contact_heatmap 风格：按出现频率过滤，避免图过挤
    if args.mode == "binary":
        residue_freq = (matrix > 0).mean(axis=1)
    else:
        residue_freq = (matrix > 0).mean(axis=1)
    active_idx = np.where(residue_freq > float(args.min_frequency))[0]
    if active_idx.size == 0:
        raise ValueError(
            f"没有残基满足出现频率 > {args.min_frequency:.3f}，可尝试减小 --min-frequency。"
        )
    matrix_plot = matrix[active_idx, :]
    chain_set = {c for c, _rid, _rn in residues_sorted if c}
    show_chain = len(chain_set) > 1
    labels_all = [
        f"{resname}{resid}{chain}" if show_chain and chain else f"{resname}{resid}"
        for chain, resid, resname in residues_sorted
    ]
    labels = [labels_all[i] for i in active_idx]

    fig_h = max(4.0, min(18.0, 0.24 * len(labels) + 2.5))
    fig, ax = plt.subplots(figsize=(12, fig_h), dpi=180)

    if args.mode == "binary":
        from matplotlib.colors import ListedColormap

        cmap = ListedColormap(["#ffffff", "#81c4f4"])
        ax.imshow(
            matrix_plot,
            aspect="auto",
            interpolation="nearest",
            cmap=cmap,
            vmin=0.0,
            vmax=1.0,
            origin="upper",
        )
    else:
        im = ax.imshow(
            matrix_plot,
            aspect="auto",
            interpolation="nearest",
            cmap="Blues",
            origin="upper",
        )
        cbar = fig.colorbar(im, ax=ax, pad=0.01)
        cbar.set_label("H-bond count per frame")

    xtick_step = max(1, end_frame // 10)
    xticks = np.arange(0, end_frame, xtick_step, dtype=np.int64)
    if xticks.size == 0 or xticks[-1] != end_frame - 1:
        xticks = np.append(xticks, end_frame - 1)
    ax.set_xticks(xticks)
    ax.set_xticklabels([str(int(np.rint(times_ns[x]))) for x in xticks])
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_xlabel("Time (ns)")
    ax.set_ylabel("Residue")
    ax.set_title(f"Protein-Ligand H-bond Map (Cutoff = {args.d_a_cutoff} $\\AA$)")
    fig.tight_layout()

    png_path = os.path.join(args.output_dir, f"{args.prefix}_{slug1}_vs_{slug2}.png")
    fig.savefig(png_path)
    plt.close(fig)
    print(f"已写入: {png_path}")




def run_hbond_residue_timeline(
    topology: str,
    trajectory: str,
    group1: str,
    group2: str,
    output_dir: str,
    prefix: str = "hbond_residue_timeline",
    d_a_cutoff: float = 3.0,
    angle_cutoff: float = 150.0,
    min_frequency: float = 0.05,
    protein_side: str = "auto",
    mode: str = "binary",
    end_ns=None,
    csv_dir: str | None = None,
):
    """计算并绘制蛋白残基-配体氢键时间图，返回 (png路径, csv路径)。

    png 写入 output_dir；csv 写入 csv_dir（默认与 output_dir 相同）。
    """
    import sys as _sys
    from pathlib import Path as _P

    old_argv = _sys.argv
    csv_out = (csv_dir or output_dir)
    try:
        _sys.argv = [
            "hbond_residue_timeline.py",
            "--topology", topology,
            "--trajectory", trajectory,
            "--group1", group1,
            "--group2", group2,
            "--output-dir", output_dir,
            "--csv-dir", csv_out,
            "--prefix", prefix,
            "--d-a-cutoff", str(d_a_cutoff),
            "--angle-cutoff", str(angle_cutoff),
            "--min-frequency", str(min_frequency),
            "--protein-side", protein_side,
            "--mode", mode,
        ]
        if end_ns is not None:
            _sys.argv.extend(["--end-ns", str(end_ns)])
        main()
    finally:
        _sys.argv = old_argv
    pngs = sorted(_P(output_dir).glob(f"{prefix}_*.png"))
    csvs = sorted(_P(csv_out).glob(f"{prefix}_*.csv"))
    return (str(pngs[-1]) if pngs else None, str(csvs[-1]) if csvs else None)


if __name__ == "__main__":
    main()
