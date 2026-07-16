# ==================================================
# 功能说明：标准氨基酸头尾环肽的 PDB 校验、清理与 tleap 序列准备
# 使用方法：由 pipeline 调用 prepare_cyclic_peptide(pdb_path, work_dir)
# 依赖环境：Python 标准库；组氨酸命名复用 protein._fix_histidine_protonation
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import json
import logging
from pathlib import Path

from .protein import _fix_histidine_protonation

logger = logging.getLogger(__name__)

# 标准氨基酸（含 Amber 组氨酸命名）；第一期不支持杂原子侧链修饰
_STD_AA = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL", "HID", "HIE", "HIP",
})

# 环肽残基号起点：避免与蛋白残基号冲突，便于 make_ndx 按 ri 选组
_CYC_RESID_START = 9001

# 元数据文件名（分析阶段读取）
META_NAME = "webmd_cyclic_peptide.json"


def _parse_residues(p: Path) -> list[dict]:
    """按出现顺序解析 PDB 残基，返回 [{chain, resnum, resname, lines}]。"""
    order: list[tuple[str, str, str]] = []
    atoms: dict[tuple[str, str, str], list[str]] = {}
    with p.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not (ln.startswith("ATOM") or ln.startswith("HETATM")):
                continue
            if len(ln) < 26:
                continue
            rn = ln[17:20].strip().upper()
            ri = ln[22:26].strip()
            ch = ln[21] if len(ln) > 21 else " "
            key = (ch, ri, rn)
            if key not in atoms:
                atoms[key] = []
                order.append(key)
            atoms[key].append(ln)
    out = []
    for ch, ri, rn in order:
        out.append({
            "chain": ch,
            "resnum": ri,
            "resname": rn,
            "lines": atoms[(ch, ri, rn)],
        })
    return out


def _strip_terminal_atoms(lines: list[str], *, is_n_term: bool, is_c_term: bool) -> list[str]:
    """去掉头尾带电末端多余原子，使两端按链中残基处理以便成环。"""
    keep = []
    for ln in lines:
        an = ln[12:16].strip().upper()
        if is_c_term and an in {"OXT", "O2", "OT2"}:
            continue
        if is_n_term and an in {"H2", "H3", "HT2", "HT3"}:
            continue
        if is_n_term and an in {"H1", "HT1"}:
            # N 端多余质子名改为链中 HN/H
            ln = ln[:12] + " H  " + ln[16:]
        keep.append(ln)
    return keep


def prepare_cyclic_peptide(pdb_path: str, work_dir: str) -> dict:
    """校验并清理环肽 PDB，返回元数据（含清理后路径与 leap 序列）。

    要求：仅标准氨基酸；用户保证头尾几何已可成键。
    处理：去 OXT/N 端多余 H、链号设为 C、残基号重排为 9001 起、组氨酸命名。
    """
    src = Path(pdb_path)
    work = Path(work_dir)
    if not src.is_file():
        raise FileNotFoundError(f"环肽 PDB 不存在: {pdb_path}")

    residues = _parse_residues(src)
    if len(residues) < 3:
        raise ValueError("环肽至少需要 3 个氨基酸残基")

    bad = sorted({r["resname"] for r in residues if r["resname"] not in _STD_AA})
    if bad:
        raise ValueError(
            "环肽含非标准残基（当前仅支持标准氨基酸头尾成环）: "
            + ", ".join(bad)
        )

    n_res = len(residues)
    resid_start = _CYC_RESID_START
    resid_end = resid_start + n_res - 1
    seq_names: list[str] = []
    out_lines: list[str] = []

    for i, res in enumerate(residues):
        is_n = i == 0
        is_c = i == n_res - 1
        cleaned = _strip_terminal_atoms(res["lines"], is_n_term=is_n, is_c_term=is_c)
        if not cleaned:
            raise ValueError(f"环肽残基 {res['resname']}{res['resnum']} 清理后无原子")
        new_num = resid_start + i
        rn = res["resname"]
        # HIS 先保留，稍后统一走组氨酸质子化修正
        seq_names.append(rn if rn != "HIS" else "HIE")
        for ln in cleaned:
            # 强制链 C、新残基号
            ln = ln[:17] + f"{rn:<3s}" + "C" + f"{new_num:4d}" + ln[26:]
            if ln.startswith("HETATM"):
                ln = "ATOM  " + ln[6:]
            out_lines.append(ln.rstrip("\n") + "\n")

    out_lines.append("END\n")
    clean_path = work / "cyclic_peptide_clean.pdb"
    clean_path.write_text("".join(out_lines), encoding="utf-8")

    # 组氨酸 HID/HIE/HIP（就地改文件）
    _fix_histidine_protonation(str(clean_path))

    # 按清理后文件重读序列（HIS 可能已变为 HID/HIE/HIP）
    residues2 = _parse_residues(clean_path)
    seq_names = [r["resname"] for r in residues2]
    if any(r not in _STD_AA for r in seq_names):
        raise ValueError("环肽组氨酸处理后仍存在非标准残基名")

    # leap 序列：全部使用链中残基名，避免 N*/C* 末端模板
    leap_seq = "{ " + " ".join(seq_names) + " }"

    meta = {
        "type": "cyclic_peptide",
        "source": src.name,
        "clean_pdb": str(clean_path),
        "n_residues": n_res,
        "resid_start": resid_start,
        "resid_end": resid_end,
        "chain": "C",
        "sequence": seq_names,
        "leap_seq": leap_seq,
    }
    meta_path = work / META_NAME
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "环肽已准备: %d 残基, 残基号 %d–%d, 序列=%s",
        n_res, resid_start, resid_end, " ".join(seq_names),
    )
    return meta
