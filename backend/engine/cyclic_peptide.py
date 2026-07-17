# ==================================================
# 功能说明：标准氨基酸环肽/线形肽的 PDB 校验、清理与 tleap 准备
# 使用方法：pipeline 调用 prepare_cyclic_peptide / prepare_linear_peptide
# 依赖环境：Python 标准库；组氨酸命名复用 protein._fix_histidine_protonation
# 生成时间：2026-07-17（线形肽 N 端 H→H1）
# ==================================================

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .pdb_sanitize import assert_peptide_amber_names, rename_std_aa_atoms_in_pdb
from .protein import _fix_histidine_protonation

logger = logging.getLogger(__name__)


def _prepare_source_with_amber_names(src: Path, work: Path) -> Path:
    """复制源 PDB 并尝试将标准氨基酸原子名修复为 Amber 风格。"""
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / f"_peptide_rename_{src.name}"
    shutil.copy2(src, tmp)
    info = rename_std_aa_atoms_in_pdb(tmp)
    if info.get("residues_renamed"):
        logger.info(
            "肽链原子名已自动修复 %d 个残基（来源: %s）",
            info["residues_renamed"],
            src.name,
        )
    return tmp

# 标准氨基酸（含 Amber 组氨酸命名）；第一期不支持杂原子侧链修饰
_STD_AA = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL", "HID", "HIE", "HIP",
})

# 设计残基号起点（仅用于 tleap 成环引用）；体系建成后 gro 会重编号，
# 分析/索引须用 peptide_resid_map 按序列映射到实际残基号
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

    # UFF/对接 PDB 常破坏原子名，先按 CONECT/几何重命名
    src = _prepare_source_with_amber_names(src, work)
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
            # 列 17–19 残基名，20 插入码空格，21 链号，22–25 残基号
            ln = ln[:17] + f"{rn:<3s} C{new_num:4d}" + ln[26:]
            if ln.startswith("HETATM"):
                ln = "ATOM  " + ln[6:]
            out_lines.append(ln.rstrip("\n") + "\n")

    out_lines.append("END\n")
    clean_path = work / "cyclic_peptide_clean.pdb"
    clean_path.write_text("".join(out_lines), encoding="utf-8")

    # 组氨酸 HID/HIE/HIP（就地改文件）
    _fix_histidine_protonation(str(clean_path))
    assert_peptide_amber_names(clean_path)

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


def prepare_linear_peptide(
    pdb_path: str,
    work_dir: str,
    confirmed_sequence: str | None = None,
) -> dict:
    """校验并清理线形肽 PDB，保留 N/C 末端，返回元数据。

    与环肽区别：不去掉 OXT/N 端多余质子；tleap 用 loadpdb 自动识别末端。
    仅支持标准氨基酸；残基号重排为 9001 起以便分析选组。

    若 PDB 为对接非标准格式：无确认序列时抛 NeedPeptideSequence；
    有确认序列则严格核实并重建后再继续。
    """
    from .peptide_seq_rebuild import (
        NeedPeptideSequence,
        hint_n_residues_from_pdb,
        is_nonstandard_peptide_pdb,
        rebuild_peptide_pdb_from_sequence,
    )

    src = Path(pdb_path)
    work = Path(work_dir)
    if not src.is_file():
        raise FileNotFoundError(f"线形肽 PDB 不存在: {pdb_path}")

    seq = (confirmed_sequence or "").strip() or None
    if is_nonstandard_peptide_pdb(src):
        if not seq:
            hint = hint_n_residues_from_pdb(src)
            raise NeedPeptideSequence(
                "检测到非标准肽 PDB（残基名/原子名不完整，常见于对接导出）。"
                "请输入该肽的单字母氨基酸序列；系统将严格核实组成与三维匹配后再继续。"
                + (f"结构中氮原子约 {hint} 个，可作长度参考。" if hint else ""),
                hint_n_res=hint,
            )
        rebuilt = work / "linear_peptide_from_seq.pdb"
        logger.info("非标准肽 PDB：使用用户序列严格重建 → %s", seq)
        rebuild_peptide_pdb_from_sequence(src, seq, rebuilt)
        src = rebuilt
    else:
        # UFF/对接 PDB 常破坏原子名，先按 CONECT/几何重命名
        src = _prepare_source_with_amber_names(src, work)

    residues = _parse_residues(src)
    if len(residues) < 2:
        raise ValueError("线形肽至少需要 2 个氨基酸残基")

    bad = sorted({r["resname"] for r in residues if r["resname"] not in _STD_AA})
    if bad:
        raise ValueError(
            "线形肽含非标准残基（当前仅支持标准氨基酸）: " + ", ".join(bad)
        )

    n_res = len(residues)
    resid_start = _CYC_RESID_START
    resid_end = resid_start + n_res - 1
    out_lines: list[str] = []

    for i, res in enumerate(residues):
        new_num = resid_start + i
        rn = res["resname"]
        for ln in res["lines"]:
            # 列 17–19 残基名，20 插入码空格，21 链号，22–25 残基号
            ln = ln[:17] + f"{rn:<3s} C{new_num:4d}" + ln[26:]
            if ln.startswith("HETATM"):
                ln = "ATOM  " + ln[6:]
            out_lines.append(ln.rstrip("\n") + "\n")

    out_lines.append("END\n")
    clean_path = work / "linear_peptide_clean.pdb"
    clean_path.write_text("".join(out_lines), encoding="utf-8")

    _fix_histidine_protonation(str(clean_path))
    # N 端 H/H2/H3 → H1/H2/H3（与蛋白 _fix_terminal_atoms_for_tleap 一致）
    from .system_builder import _fix_terminal_atoms_for_tleap

    pep_lines = clean_path.read_text(encoding="utf-8", errors="replace").splitlines(
        keepends=True
    )
    clean_path.write_text(
        "".join(_fix_terminal_atoms_for_tleap(pep_lines)),
        encoding="utf-8",
    )
    assert_peptide_amber_names(clean_path)

    residues2 = _parse_residues(clean_path)
    seq_names = [r["resname"] for r in residues2]
    if any(r not in _STD_AA for r in seq_names):
        raise ValueError("线形肽组氨酸处理后仍存在非标准残基名")

    meta = {
        "type": "linear_peptide",
        "source": src.name,
        "clean_pdb": str(clean_path),
        "n_residues": n_res,
        "resid_start": resid_start,
        "resid_end": resid_end,
        "chain": "C",
        "sequence": seq_names,
    }
    meta_path = work / META_NAME
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "线形肽已准备: %d 残基, 残基号 %d–%d, 序列=%s",
        n_res,
        resid_start,
        resid_end,
        " ".join(seq_names),
    )
    return meta
