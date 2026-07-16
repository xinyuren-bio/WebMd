# ==================================================
# 功能说明：解析 PDB 链/配体残基，拆分蛋白-肽或蛋白-小分子复合物
# 使用方法：list_chains / list_ligand_residues / split_complex / split_complex_mol2
# 依赖环境：Python 标准库；Open Babel（obabel）用于 PDB→MOL2
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import logging
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from .env_check import source_amber_env

logger = logging.getLogger(__name__)

# 用于粗判“肽链”残基是否为标准氨基酸（拆分后仍会走肽准备校验）
_STD_AA = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL", "HID", "HIE", "HIP",
})

# 拆分小分子时跳过的溶剂/离子残基名
_SKIP_LIGAND_RES = frozenset({
    "HOH", "WAT", "TIP", "TIP3", "SOL", "DOD", "OH2", "H2O",
    "NA", "CL", "K", "MG", "CA", "ZN", "FE", "MN", "CO", "NI", "CU",
    "CD", "HG", "IOD", "BR", "CS", "RB", "SR", "BA", "LI", "F",
    "NA+", "CL-", "K+", "MG2",
})


def norm_chain(s: str) -> str:
    """前端链号 _ / 空白 → PDB 单字符链号。"""
    t = (s or "").strip()
    if t in ("", "_", "(空白链号)"):
        return " "
    return t[:1]


def ligand_residue_key(chain: str, resname: str, resid: str) -> str:
    """配体残基唯一键：链|残基名|残基号（链空白用 _）。"""
    ch = chain if chain.strip() else "_"
    return f"{ch}|{resname.strip().upper()}|{resid.strip()}"


def parse_ligand_residue_key(key: str) -> tuple[str, str, str]:
    """解析 ligand_residue_key → (chain, resname, resid)。"""
    parts = (key or "").split("|")
    if len(parts) != 3:
        raise ValueError(f"配体残基键格式无效: {key!r}，应为 链|残基名|残基号")
    ch = norm_chain(parts[0])
    return ch, parts[1].strip().upper(), parts[2].strip()


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


def list_ligand_residues(fp: str | Path) -> list[dict]:
    """列出 PDB 中可作为小分子的 HETATM 残基（排除水/离子）。"""
    p = Path(fp)
    if not p.is_file():
        raise FileNotFoundError(f"PDB 不存在: {fp}")

    groups: dict[tuple[str, str, str], dict] = {}
    order: list[tuple[str, str, str]] = []

    with p.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not ln.startswith("HETATM") or len(ln) < 26:
                continue
            ch = ln[21] if len(ln) > 21 else " "
            rn = ln[17:20].strip().upper()
            ri = ln[22:26].strip()
            if rn in _SKIP_LIGAND_RES:
                continue
            key = (ch, rn, ri)
            if key not in groups:
                groups[key] = {
                    "chain": ch,
                    "label": ch if ch.strip() else "(空白链号)",
                    "resname": rn,
                    "resid": ri,
                    "key": ligand_residue_key(ch, rn, ri),
                    "n_atoms": 0,
                }
                order.append(key)
            groups[key]["n_atoms"] += 1

    return [groups[k] for k in order]


def pdb_to_mol2(pdb_path: str | Path, mol2_path: str | Path) -> str:
    """将配体 PDB 转为 MOL2（依赖 Open Babel）。"""
    src = Path(pdb_path)
    dst = Path(mol2_path)
    if not src.is_file():
        raise FileNotFoundError(f"配体 PDB 不存在: {src}")
    env = source_amber_env()
    last_err = ""
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        r = subprocess.run(
            [exe, "-ipdb", str(src), "-omol2", "-O", str(dst)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            logger.info("配体 PDB→MOL2: %s → %s", src.name, dst.name)
            return str(dst)
        last_err = (r.stderr or r.stdout or "")[-500:]
    raise RuntimeError(
        "无法将配体 PDB 转为 MOL2，请确认服务器已安装 Open Babel (obabel)。"
        + (f" 详情: {last_err}" if last_err else "")
    )


def split_complex_mol2(
    fp: str | Path,
    protein_chains: list[str],
    ligand_keys: list[str],
    out_dir: str | Path,
) -> tuple[str, list[str]]:
    """复合物按蛋白链 + HETATM 配体残基拆分，配体自动转 MOL2。"""
    src = Path(fp)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    norm = [norm_chain(c) for c in protein_chains]
    if not norm:
        raise ValueError("请至少选择一条蛋白链")
    if not ligand_keys:
        raise ValueError("请至少选择一个配体残基")
    if len(ligand_keys) > 3:
        raise ValueError("最多选择 3 个配体残基")

    lig_specs: list[tuple[str, str, str]] = []
    for k in ligand_keys:
        lig_specs.append(parse_ligand_residue_key(k))

    avail_lig = {x["key"] for x in list_ligand_residues(src)}
    for k in ligand_keys:
        if k not in avail_lig:
            raise ValueError(f"PDB 中不存在配体残基 {k!r}")

    prot_ch_set = set(norm)
    prot_lines: list[str] = []
    n_prot = 0
    lig_lines: dict[tuple[str, str, str], list[str]] = {s: [] for s in lig_specs}

    with src.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not (ln.startswith("ATOM") or ln.startswith("HETATM")):
                continue
            if len(ln) < 26:
                continue
            ch = ln[21] if len(ln) > 21 else " "
            rn = ln[17:20].strip().upper()
            ri = ln[22:26].strip()
            row = ln if ln.endswith("\n") else ln + "\n"

            if ln.startswith("ATOM") and ch in prot_ch_set:
                prot_lines.append(row)
                n_prot += 1
                continue

            if ln.startswith("HETATM"):
                spec = (ch, rn, ri)
                if spec in lig_lines:
                    lig_lines[spec].append(row)

    if n_prot == 0:
        raise ValueError("所选蛋白链无 ATOM 记录，请检查链选择")

    prot_path = out / "protein.pdb"
    prot_lines.append("END\n")
    prot_path.write_text("".join(prot_lines), encoding="utf-8")

    mol2_paths: list[str] = []
    for i, spec in enumerate(lig_specs, 1):
        atoms = lig_lines.get(spec) or []
        if not atoms:
            raise ValueError(f"配体残基 {ligand_keys[i - 1]!r} 无 HETATM 原子")
        lig_pdb = out / f"ligand_from_complex_{i}.pdb"
        lig_mol2 = out / f"ligand_{i}.mol2"
        atoms.append("END\n")
        lig_pdb.write_text("".join(atoms), encoding="utf-8")
        pdb_to_mol2(lig_pdb, lig_mol2)
        mol2_paths.append(str(lig_mol2))
        logger.info(
            "配体残基 %s → %s (%d 原子)",
            ligand_keys[i - 1],
            lig_mol2.name,
            len(atoms) - 1,
        )

    logger.info(
        "复合物已拆分(小分子): 蛋白链=%s (%d 原子); 配体 %d 个",
        norm,
        n_prot,
        len(mol2_paths),
    )
    return str(prot_path), mol2_paths


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
        norm.append(norm_chain(str(c)))
    pep = norm_chain(str(peptide_chain))

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
