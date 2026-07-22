# ==================================================
# 功能说明：从 GROMACS gro 提取蛋白-配体复合物并导出 PDB（不含水与离子）
# 使用方法：由 pipeline 或 API 调用 export_complex_pdb(gro_path, out_path)
# 依赖环境：Python 标准库
# 生成时间：2026-06-23
# ==================================================

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 溶剂与离子残基名（GROMACS / Amber 常见命名）
_SOLVENT_ION_RESNAMES = frozenset({
    "WAT", "SOL", "HOH", "TIP3", "TIP4", "SPC", "OW",
    "NA", "CL", "K", "MG", "CA", "ZN", "BR", "CS", "LI", "RB",
    "NA+", "CL-", "K+", "MG2+", "CA2+", "ZN2+",
})

# 标准蛋白残基名（20 种氨基酸 + Amber 质子化/端基/二硫变体）。
# 用途：区分蛋白与配体——凡不在此集合、又非水/离子的残基一律视为配体，
# 从而把配体拆到独立链号并标记为 HETATM。
_PROTEIN_RESNAMES = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HID", "HIE", "HIP", "ASH", "GLH", "LYN", "CYM", "CYX",
    "HSD", "HSE", "HSP", "ACE", "NME", "NMA",
})


def _is_protein_res(r: str) -> bool:
    """判断残基是否为标准蛋白残基。"""
    return r.strip().upper() in _PROTEIN_RESNAMES


def _is_solvent_or_ion(r: str) -> bool:
    """判断残基是否为水分子或离子。"""
    s = r.strip().upper()
    if s in _SOLVENT_ION_RESNAMES:
        return True
    # Cl- / Na+ 等带电荷命名
    if s.startswith(("NA", "CL", "K", "MG", "CA", "ZN")) and len(s) <= 4:
        return s.replace("+", "").replace("-", "").replace("2", "") in {
            "NA", "CL", "K", "MG", "CA", "ZN",
        }
    return False


def _pdb_atom_name(n: str) -> str:
    """将 gro 原子名格式化为 PDB 4 字符列。"""
    s = n.strip()
    if len(s) <= 3:
        return f" {s:<3s}"
    return s[:4]


def _guess_element(n: str) -> str:
    """由原子名粗略推断元素符号。"""
    s = n.strip()
    if not s:
        return "  "
    if s[0].isdigit():
        s = s.lstrip("0123456789")
    if not s:
        return "  "
    if len(s) >= 2 and s[1].islower():
        return s[:2].capitalize()
    return s[0].upper()


def _parse_gro_atoms(p: Path) -> list[dict]:
    """解析 gro 文件中的原子记录（坐标单位 nm）。"""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 3:
        raise ValueError(f"gro 文件格式异常: {p}")

    try:
        n_atoms = int(lines[1].strip())
    except ValueError as e:
        raise ValueError(f"无法读取 gro 原子数: {p}") from e

    atoms = []
    for line in lines[2:2 + n_atoms]:
        if len(line) < 44:
            continue
        resnr = int(line[0:5])
        resname = line[5:10].strip()
        atomname = line[10:15].strip()
        atomnr = int(line[15:20])
        x, y, z = float(line[20:28]), float(line[28:36]), float(line[36:44])
        atoms.append({
            "resnr": resnr,
            "resname": resname,
            "atomname": atomname,
            "atomnr": atomnr,
            "x": x * 10.0,  # nm → Å
            "y": y * 10.0,
            "z": z * 10.0,
        })
    return atoms


def export_complex_pdb(
    gro_path: str,
    out_path: str,
    work_dir: str | None = None,
) -> str:
    """从 gro 导出仅含蛋白与配体的 PDB 文件，返回输出路径。

    work_dir 若提供，则读取 webmd_ligand_spec.json：
    - 肽配体按残基号范围分到 B 链（便于 PyMOL color by chain）
    - 小分子按残基名分到 B/C… 链并标 HETATM
    """
    gro = Path(gro_path)
    out = Path(out_path)
    if not gro.exists():
        raise FileNotFoundError(f"未找到 gro 文件: {gro}")

    kept = [a for a in _parse_gro_atoms(gro) if not _is_solvent_or_ion(a["resname"])]
    if not kept:
        raise ValueError("gro 中未找到蛋白或配体原子（可能全部被识别为溶剂/离子）")

    # 读取统一配体定义（可选）
    pep_range: tuple[int, int] | None = None
    sm_resnames: set[str] = set()
    wd = Path(work_dir) if work_dir else gro.parent
    try:
        from .ligand_spec import (
            load_ligand_spec,
            peptide_resid_range_from_spec,
            small_molecule_resnames_from_spec,
        )
        spec = load_ligand_spec(wd)
        pep_range = peptide_resid_range_from_spec(spec)
        sm_resnames = small_molecule_resnames_from_spec(spec)
    except Exception as e:
        logger.debug("读取 ligand_spec 失败，回退残基名启发式: %s", e)

    lines = ["REMARK   蛋白-配体复合物（已去除水分子与离子）", "REMARK   来源: " + gro.name]
    # 链号分配：蛋白 → A；肽/小分子配体 → B、C…（同一配体分子共链）
    _LIG_CHAIN_LETTERS = "BCDEFGHIJKLMNOPQRSTUVWXYZ"
    # 小分子：按残基号映射链；肽：整段共用 B 链
    lig_chain_map: dict[int, str] = {}
    pep_chain = "B"

    def _is_ligand_atom(a: dict) -> bool:
        """按 spec 或启发式判断是否为配体原子。"""
        if pep_range is not None:
            a0, b0 = pep_range
            if a0 <= a["resnr"] <= b0:
                return True
        rn = a["resname"].strip().upper()
        if sm_resnames and rn in sm_resnames:
            return True
        # 回退：非标准蛋白残基视为小分子配体
        if pep_range is None and not sm_resnames and not _is_protein_res(a["resname"]):
            return True
        return False

    def _chain_for(a: dict) -> tuple[str, str]:
        """返回 (记录类型, 链号)。"""
        if _is_ligand_atom(a):
            if pep_range is not None and pep_range[0] <= a["resnr"] <= pep_range[1]:
                # 肽配体：保留 ATOM（标准氨基酸），独立 B 链即可被 PyMOL 识别
                return "ATOM  ", pep_chain
            # 小分子：HETATM + 按残基号分链
            rn = a["resnr"]
            if rn not in lig_chain_map:
                idx = len(lig_chain_map)
                lig_chain_map[rn] = (
                    _LIG_CHAIN_LETTERS[idx] if idx < len(_LIG_CHAIN_LETTERS) else "Z"
                )
            return "HETATM", lig_chain_map[rn]
        return "ATOM  ", "A"

    serial = 0
    prev_chain: str | None = None
    for a in kept:
        rec, chain = _chain_for(a)
        # 链号切换处插入 TER，收束上一条链
        if prev_chain is not None and chain != prev_chain:
            serial += 1
            lines.append(f"TER   {serial:5d}")
        serial += 1
        elem = _guess_element(a["atomname"])
        lines.append(
            f"{rec}{serial:5d} {_pdb_atom_name(a['atomname'])} "
            f"{a['resname'][:3]:>3s} {chain}{a['resnr']:4d}    "
            f"{a['x']:8.3f}{a['y']:8.3f}{a['z']:8.3f}  1.00  0.00          {elem:>2s}"
        )
        prev_chain = chain
    # 末尾补 TER 收束最后一条链
    if prev_chain is not None:
        serial += 1
        lines.append(f"TER   {serial:5d}")
    lines.append("END")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        "复合物 PDB 已导出: %s（保留 %d 原子）",
        out, len(kept),
    )
    return str(out)


def ensure_complex_pdb(w: str, gro_name: str = "system.gro", pdb_name: str = "complex.pdb") -> str | None:
    """确保任务目录中存在 complex.pdb；若缺失则从 gro 生成。"""
    work = Path(w)
    pdb = work / pdb_name
    if pdb.exists():
        return str(pdb)

    gro = work / gro_name
    if not gro.exists():
        return None

    try:
        return export_complex_pdb(str(gro), str(pdb), work_dir=str(work))
    except (OSError, ValueError) as e:
        logger.warning("生成 complex.pdb 失败: %s", e)
        return None
