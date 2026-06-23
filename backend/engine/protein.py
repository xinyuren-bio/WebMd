# ==================================================
# 功能说明：使用 PDBFixer 修复蛋白结构，并修正组氨酸质子化命名
# 使用方法：由 pipeline 调用 prepare_protein(pdb_path, work_dir)
# 依赖环境：pip install pdbfixer openmm
# 生成时间：2026-06-23
# ==================================================

import logging
from collections import defaultdict
from pathlib import Path

from pdbfixer import PDBFixer
from openmm.app import PDBFile

logger = logging.getLogger(__name__)


def _fix_histidine_protonation(pdb_path: str) -> None:
    """根据侧链质子分布，将 HIS 重命名为 HID/HIE/HIP，并删除不匹配的原子。"""
    with open(pdb_path, encoding="utf-8") as f:
        lines = f.readlines()

    # 按链号+残基号分组 ATOM/HETATM 行
    res_atoms: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for i, line in enumerate(lines):
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        chain = line[21] if len(line) > 21 else " "
        resnum = line[22:26].strip()
        resname = line[17:20].strip()
        atom = line[12:16].strip()
        res_atoms[(chain, resnum, resname)].append((i, atom))

    skip_lines: set[int] = set()
    rename_map: dict[int, str] = {}

    for (chain, resnum, resname), atoms in res_atoms.items():
        if resname not in ("HIS", "HID", "HIE", "HIP"):
            continue

        atom_names = {a for _, a in atoms}
        has_hd1 = "HD1" in atom_names
        has_he2 = "HE2" in atom_names

        if has_hd1 and has_he2:
            new_name = "HIP"
        elif has_hd1:
            new_name = "HID"
            # HIE 不应保留 HE2
            for idx, an in atoms:
                if an == "HE2":
                    skip_lines.add(idx)
        elif has_he2:
            new_name = "HIE"
            for idx, an in atoms:
                if an == "HD1":
                    skip_lines.add(idx)
        else:
            # 无明确质子时默认 ε 型 (HIE)
            new_name = "HIE"
            for idx, an in atoms:
                if an == "HD1":
                    skip_lines.add(idx)

        for idx, _ in atoms:
            rename_map[idx] = new_name

        logger.info(
            "组氨酸 %s %s → %s (HD1=%s, HE2=%s)",
            chain, resnum, new_name, has_hd1, has_he2,
        )

    out_lines = []
    for i, line in enumerate(lines):
        if i in skip_lines:
            continue
        if i in rename_map:
            line = line[:17] + f"{rename_map[i]:<3s}" + line[20:]
        out_lines.append(line)

    with open(pdb_path, "w", encoding="utf-8") as f:
        f.writelines(out_lines)


def prepare_protein(pdb_path: str, work_dir: str) -> str:
    """PDBFixer 修复蛋白并修正组氨酸命名，返回修复后 PDB 路径。"""
    work = Path(work_dir)

    fixer = PDBFixer(filename=pdb_path)
    fixer.findMissingResidues()
    fixer.findNonstandardResidues()
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(pH=7.0)

    out = str(work / "protein_fixed.pdb")
    with open(out, "w") as f:
        PDBFile.writeFile(fixer.topology, fixer.positions, f)

    _fix_histidine_protonation(out)

    n_atoms = sum(1 for _ in fixer.topology.atoms())
    n_res = sum(1 for _ in fixer.topology.residues())
    logger.info("蛋白修复完成: %d 残基, %d 原子", n_res, n_atoms)
    return out
