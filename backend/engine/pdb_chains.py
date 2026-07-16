# ==================================================
# 功能说明：解析 PDB 链信息，并按链拆分蛋白/肽复合物
# 使用方法：list_chains(fp) / split_complex(fp, protein_chains, peptide_chain, out_dir)
# 依赖环境：Python 标准库
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

# 用于粗判“肽链”残基是否为标准氨基酸（拆分后仍会走肽准备校验）
_STD_AA = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL", "HID", "HIE", "HIP",
})


def list_chains(fp: str | Path) -> list[dict]:
    """列出 PDB 中各链的残基数与原子数，供前端链选择。"""
    p = Path(fp)
    if not p.is_file():
        raise FileNotFoundError(f"PDB 不存在: {fp}")

    # chain -> ordered residues / atom count
    res_order: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    n_atoms: dict[str, int] = defaultdict(int)

    with p.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not (ln.startswith("ATOM") or ln.startswith("HETATM")):
                continue
            if len(ln) < 26:
                continue
            ch = ln[21] if len(ln) > 21 else " "
            rn = ln[17:20].strip().upper()
            ri = ln[22:26].strip()
            key = (ri, rn)
            n_atoms[ch] += 1
            if key not in seen[ch]:
                seen[ch].add(key)
                res_order[ch].append(rn)

    out: list[dict] = []
    for ch in sorted(res_order.keys(), key=lambda x: (x.strip() == "", x)):
        seq = res_order[ch]
        std_n = sum(1 for r in seq if r in _STD_AA)
        label = ch if ch.strip() else "(空白链号)"
        out.append({
            "chain": ch,
            "label": label,
            "n_residues": len(seq),
            "n_atoms": int(n_atoms[ch]),
            "n_std_aa": std_n,
            "resnames_head": seq[:8],
        })
    return out


def _write_chain_pdb(
    src: Path,
    chains: set[str],
    out: Path,
    *,
    force_atom_record: bool = False,
) -> int:
    """写出指定链的原子行，返回原子数。"""
    n = 0
    lines: list[str] = []
    with src.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not (ln.startswith("ATOM") or ln.startswith("HETATM")):
                continue
            if len(ln) < 22:
                continue
            ch = ln[21]
            if ch not in chains:
                continue
            if force_atom_record and ln.startswith("HETATM"):
                ln = "ATOM  " + ln[6:]
            lines.append(ln if ln.endswith("\n") else ln + "\n")
            n += 1
    if n == 0:
        raise ValueError(f"所选链无原子: {sorted(chains)!r}")
    lines.append("END\n")
    out.write_text("".join(lines), encoding="utf-8")
    return n


def split_complex(
    fp: str | Path,
    protein_chains: list[str],
    peptide_chain: str,
    out_dir: str | Path,
) -> tuple[str, str]:
    """将复合物 PDB 按链拆成蛋白与肽两个文件，返回 (protein_pdb, peptide_pdb)。"""
    src = Path(fp)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    prot_chs = [c if c is not None else " " for c in protein_chains]
    # 前端可能把空白链传成 "" 或 "_"
    norm: list[str] = []
    for c in prot_chs:
        s = str(c)
        if s in ("", "_", "(空白链号)"):
            norm.append(" ")
        else:
            norm.append(s[:1] if len(s) >= 1 else " ")
    pep = str(peptide_chain)
    if pep in ("", "_", "(空白链号)"):
        pep = " "
    else:
        pep = pep[:1]

    if not norm:
        raise ValueError("请至少选择一条蛋白链")
    if pep in norm:
        raise ValueError(f"肽链 {pep!r} 不能与蛋白链重复")

    avail = {x["chain"] for x in list_chains(src)}
    for c in norm + [pep]:
        if c not in avail:
            raise ValueError(f"PDB 中不存在链 {c!r}，可选: {sorted(avail)!r}")

    prot_path = out / "protein.pdb"
    pep_path = out / "peptide_from_complex.pdb"
    n_p = _write_chain_pdb(src, set(norm), prot_path, force_atom_record=True)
    n_e = _write_chain_pdb(src, {pep}, pep_path, force_atom_record=True)
    logger.info(
        "复合物已拆分: 蛋白链=%s (%d 原子) → %s; 肽链=%r (%d 原子) → %s",
        norm,
        n_p,
        prot_path.name,
        pep,
        n_e,
        pep_path.name,
    )
    return str(prot_path), str(pep_path)
