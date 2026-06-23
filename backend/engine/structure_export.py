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


def export_complex_pdb(gro_path: str, out_path: str) -> str:
    """从 gro 导出仅含蛋白与配体的 PDB 文件，返回输出路径。"""
    gro = Path(gro_path)
    out = Path(out_path)
    if not gro.exists():
        raise FileNotFoundError(f"未找到 gro 文件: {gro}")

    kept = [a for a in _parse_gro_atoms(gro) if not _is_solvent_or_ion(a["resname"])]
    if not kept:
        raise ValueError("gro 中未找到蛋白或配体原子（可能全部被识别为溶剂/离子）")

    lines = ["REMARK   蛋白-配体复合物（已去除水分子与离子）", "REMARK   来源: " + gro.name]
    for i, a in enumerate(kept, start=1):
        rec = "HETATM" if a["resname"].upper() in {"UNL", "MOL", "LIG", "UNK"} else "ATOM  "
        elem = _guess_element(a["atomname"])
        lines.append(
            f"{rec}{i:5d} {_pdb_atom_name(a['atomname'])} "
            f"{a['resname'][:3]:>3s} A{a['resnr']:4d}    "
            f"{a['x']:8.3f}{a['y']:8.3f}{a['z']:8.3f}  1.00  0.00          {elem:>2s}"
        )
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
        return export_complex_pdb(str(gro), str(pdb))
    except (OSError, ValueError) as e:
        logger.warning("生成 complex.pdb 失败: %s", e)
        return None
