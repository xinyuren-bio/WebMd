# ==================================================
# 功能说明：使用 antechamber + parmchk2 为配体生成 GAFF2 力场参数
# 使用方法：由 pipeline 调用 parameterize_ligand(mol2_path, work_dir)
# 依赖环境：AmberTools (antechamber, parmchk2); pip install rdkit; Open Babel 可选
# 生成时间：2026-06-23
# ==================================================

import logging
import shutil
import subprocess
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdmolops

from .env_check import check_external_tools, repair_ambertools, source_amber_env

logger = logging.getLogger(__name__)


def _charge_from_rdkit(p: str) -> int | None:
    """RDKit 读取 mol2，从键级/价态推断形式电荷（antechamber -nc 标准做法）。"""
    m = Chem.MolFromMol2File(
        p, sanitize=True, removeHs=False, cleanupSubstructures=False,
    )
    if m is None:
        # 部分非标准 mol2 需关闭 sanitize 后再尝试
        m = Chem.MolFromMol2File(
            p, sanitize=False, removeHs=False, cleanupSubstructures=False,
        )
        if m is None:
            return None
        try:
            Chem.SanitizeMol(m)
        except Exception:
            pass
    q = rdmolops.GetFormalCharge(m)
    logger.info("配体电荷 (RDKit 形式电荷): %d", q)
    return int(q)


def _charge_from_openbabel(p: str) -> int | None:
    """Open Babel 读取 mol2 并返回分子总电荷。"""
    try:
        from openbabel import pybel
    except ImportError:
        return None
    mol = next(pybel.readfile("mol2", p), None)
    if mol is None:
        return None
    q = int(round(mol.charge))
    logger.info("配体电荷 (Open Babel): %d", q)
    return q


def _charge_from_mol2_header(p: str) -> int | None:
    """读取 MOL2 @<TRIPOS>MOLECULE 段中的总电荷字段（若存在）。"""
    in_mol = False
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("@<TRIPOS>MOLECULE"):
                in_mol = True
                continue
            if in_mol and s.startswith("@<TRIPOS>"):
                break
            if in_mol and s and not s.startswith("#"):
                parts = s.split()
                # 第二行格式: num_atoms num_bonds num_subst num_feat charge
                if len(parts) >= 5 and parts[0].isdigit():
                    try:
                        q = int(round(float(parts[4])))
                        logger.info("配体电荷 (MOL2 分子记录): %d", q)
                        return q
                    except ValueError:
                        pass
                break
    return None


def _detect_mol2_charge(p: str) -> int:
    """检测配体净电荷，供 antechamber -nc 使用。

    优先级：RDKit 形式电荷 → Open Babel → MOL2 分子记录 → 0（默认中性）。
    """
    for fn, name in (
        (_charge_from_rdkit, "RDKit"),
        (_charge_from_openbabel, "Open Babel"),
        (_charge_from_mol2_header, "MOL2 头信息"),
    ):
        try:
            q = fn(p)
            if q is not None:
                return q
        except Exception as e:
            logger.warning("%s 电荷检测失败: %s", name, e)

    logger.warning("无法从 mol2 推断电荷，默认使用 0（中性）")
    return 0


def parameterize_ligand(mol2_path: str, work_dir: str) -> tuple[str, str]:
    """antechamber + parmchk2 参数化，返回 (gaff_mol2, frcmod) 路径。"""
    check_external_tools()
    env = source_amber_env()
    work = Path(work_dir)
    lig_dir = work / "ligand"
    lig_dir.mkdir(exist_ok=True)

    mol2_name = Path(mol2_path).stem
    mol2_dest = lig_dir / f"{mol2_name}.mol2"
    shutil.copy(mol2_path, str(mol2_dest))

    net_charge = _detect_mol2_charge(str(mol2_dest))

    # antechamber 运行前再次补全 wrapped_progs（防止 acpype 覆盖导致 bondtype 等缺失）
    repaired = repair_ambertools()
    if repaired:
        logger.info("antechamber 前 AmberTools 补全: %s", ", ".join(repaired))

    ac_mol2 = lig_dir / f"{mol2_name}_gaff.mol2"
    cmd = [
        "antechamber",
        "-i", str(mol2_dest),
        "-fi", "mol2",
        "-o", str(ac_mol2),
        "-fo", "mol2",
        "-c", "bcc",
        "-s", "2",
        "-nc", str(net_charge),
        "-at", "gaff2",
    ]
    logger.info("运行 antechamber: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(lig_dir), env=env)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-1500:]
        raise RuntimeError(f"antechamber 失败:\n{err}")

    frcmod = lig_dir / f"{mol2_name}.frcmod"
    cmd = [
        "parmchk2",
        "-i", str(ac_mol2),
        "-f", "mol2",
        "-o", str(frcmod),
        "-s", "gaff2",
    ]
    logger.info("运行 parmchk2: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(lig_dir), env=env)
    if r.returncode != 0:
        raise RuntimeError(f"parmchk2 失败:\n{r.stderr[-1500:]}")

    logger.info("配体 GAFF2 参数化完成: %s (电荷=%d)", mol2_name, net_charge)
    return str(ac_mol2), str(frcmod)
