# ==================================================
# 功能说明：使用 PDBFixer 修复蛋白（补内部缺失残基/原子/氢）并修正组氨酸命名
# 使用方法：由 pipeline 调用 prepare_protein(pdb_path, work_dir)
# 依赖环境：pip install pdbfixer openmm
# 生成时间：2026-07-17
# ==================================================

import logging
from collections import defaultdict
from pathlib import Path

from pdbfixer import PDBFixer
from openmm.app import PDBFile

from .pdb_sanitize import resolve_altloc_lines

logger = logging.getLogger(__name__)

# 单次补全缺失残基上限，避免异常 PDB 补出超长假 loop
_MAX_GAP_RESIDUES = 20


def _keep_internal_missing_residues(fixer: PDBFixer) -> list[str]:
    """仿 PRISM：只保留链内部空洞，去掉 N/C 端缺失段，返回将补全的摘要。"""
    chains = list(fixer.topology.chains())
    kept: list[str] = []
    drop_keys = []
    for key, names in list(fixer.missingResidues.items()):
        chain_i, res_i = key
        if chain_i < 0 or chain_i >= len(chains):
            drop_keys.append(key)
            continue
        n_res = len(list(chains[chain_i].residues()))
        # res_i==0 或 ==n_res 表示末端延伸，不自动补（与常见 OpenMM/PRISM 用法一致）
        if res_i == 0 or res_i >= n_res:
            drop_keys.append(key)
            continue
        if len(names) > _MAX_GAP_RESIDUES:
            logger.warning(
                "链 %d 内部缺失 %d 个残基超过上限 %d，跳过该段以免乱补长 loop",
                chain_i,
                len(names),
                _MAX_GAP_RESIDUES,
            )
            drop_keys.append(key)
            continue
        ch_id = chains[chain_i].id if getattr(chains[chain_i], "id", None) else str(chain_i)
        kept.append(f"chain={ch_id} @{res_i}:{'/'.join(names)}")

    for k in drop_keys:
        del fixer.missingResidues[k]
    return kept


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


def _fix_carboxylic_protonation(pdb_path: str) -> None:
    """将带羧基氢的 ASP/GLU 重命名为 Amber 的 ASH/GLH。

    设计思路：PDBFixer 在 pH=7 时仍可能给侧链羧基加 HD2/HE2，但残基名仍写 ASP/GLU；
    ff14SB 中质子化形式必须为 ASH/GLH，否则 tleap 报 does not have a type。
    """
    with open(pdb_path, encoding="utf-8") as f:
        lines = f.readlines()

    res_atoms: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for i, line in enumerate(lines):
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        chain = line[21] if len(line) > 21 else " "
        resnum = line[22:26].strip()
        resname = line[17:20].strip()
        res_atoms[(chain, resnum, resname)].append(i)

    rename_map: dict[int, str] = {}
    for (chain, resnum, resname), idxs in res_atoms.items():
        atom_names = {lines[i][12:16].strip() for i in idxs}
        new_name = None
        if resname in ("ASP", "ASH") and "HD2" in atom_names:
            new_name = "ASH"
        elif resname in ("GLU", "GLH") and "HE2" in atom_names:
            new_name = "GLH"
        if new_name is None or new_name == resname:
            continue
        for i in idxs:
            rename_map[i] = new_name
        logger.info("羧基质子化命名: 链%s 残基%s %s → %s", chain, resnum, resname, new_name)

    if not rename_map:
        return

    out_lines = []
    for i, line in enumerate(lines):
        if i in rename_map:
            line = line[:17] + f"{rename_map[i]:<3s}" + line[20:]
        out_lines.append(line)
    with open(pdb_path, "w", encoding="utf-8") as f:
        f.writelines(out_lines)


def prepare_protein(pdb_path: str, work_dir: str) -> str:
    """PDBFixer 修复蛋白并修正组氨酸命名，返回修复后 PDB 路径。

    对齐 PRISM：对链内部缺失残基执行 addMissingResidues，再补重原子与氢。
    进入 PDBFixer 前先去掉晶体双构象（每个残基只留一套 altLoc）。
    """
    work = Path(work_dir)

    # 先解析 altLoc，避免 Fixer/tleap 同时吃进 A/B 两套坐标
    raw = Path(pdb_path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    resolved = resolve_altloc_lines(raw)
    pdb_for_fixer = work / "protein_no_altloc.pdb"
    pdb_for_fixer.write_text("".join(resolved), encoding="utf-8")

    fixer = PDBFixer(filename=str(pdb_for_fixer))
    fixer.findMissingResidues()
    gaps = _keep_internal_missing_residues(fixer)
    if gaps:
        logger.info("将补全内部缺失残基 %d 段: %s", len(gaps), "; ".join(gaps[:12]))
        fixer.addMissingResidues()
    else:
        logger.info("未发现需补全的内部缺失残基（或仅有末端空洞已跳过）")

    fixer.findNonstandardResidues()
    if fixer.nonstandardResidues:
        logger.info("替换非标准残基 %d 处", len(fixer.nonstandardResidues))
        fixer.replaceNonstandardResidues()

    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(pH=7.0)

    out = str(work / "protein_fixed.pdb")
    with open(out, "w") as f:
        PDBFile.writeFile(fixer.topology, fixer.positions, f)

    _fix_histidine_protonation(out)
    _fix_carboxylic_protonation(out)

    n_atoms = sum(1 for _ in fixer.topology.atoms())
    n_res = sum(1 for _ in fixer.topology.residues())
    logger.info("蛋白修复完成: %d 残基, %d 原子", n_res, n_atoms)
    return out
