# ==================================================
# 功能说明：配体补氢/去氢准备（类 CHARMM-GUI Ligand Reader）
# 使用方法：由 api/routes 的 /api/ligand/prepare、/api/ligand/edit 调用
# 依赖环境：rdkit；Open Babel（obabel）可选但推荐
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from rdkit import Chem

from .ligand import (
    _add_hydrogens_mol2,
    _count_mol2_atoms,
    _count_mol2_hydrogens,
    _format_mol2_atom_line,
    _has_explicit_hydrogens,
    _parse_mol2_atoms_bonds,
    sanitize_mol2_atom_types,
)
from .ligand_charge import detect_ligand_charge

logger = logging.getLogger(__name__)


def _formal_charge(p: Path) -> int | None:
    """读取配体形式净电荷（RDKit）。"""
    try:
        det = detect_ligand_charge(p)
        return det.detected_charge
    except Exception:
        return None


def _mol2_meta(p: Path, name: str = "", residue_key: str = "") -> dict[str, Any]:
    """汇总配体 MOL2 元信息。"""
    n_all = _count_mol2_atoms(p)
    n_h = _count_mol2_hydrogens(p)
    return {
        "name": name or p.name,
        "residue_key": residue_key,
        "mol2": p.read_text(encoding="utf-8", errors="replace"),
        "n_atoms": n_all,
        "n_h": n_h,
        "n_heavy": max(0, n_all - n_h),
        "formal_charge": _formal_charge(p),
        "has_explicit_h": _has_explicit_hydrogens(p),
    }


def _strip_hydrogens_mol2(src: Path, dst: Path) -> bool:
    """去除全部显式氢，优先 RDKit，失败则用 Open Babel。"""
    try:
        m = Chem.MolFromMol2File(
            str(src), sanitize=True, removeHs=False, cleanupSubstructures=False,
        )
        if m is None:
            m = Chem.MolFromMol2File(
                str(src), sanitize=False, removeHs=False, cleanupSubstructures=False,
            )
        if m is not None:
            try:
                Chem.SanitizeMol(m)
            except Exception:
                pass
            m0 = Chem.RemoveHs(m, sanitize=False)
            # 经 SDF 转回 MOL2，保证格式稳定
            sdf = dst.with_suffix(".sdf")
            w = Chem.SDWriter(str(sdf))
            w.write(m0)
            w.close()
            from .env_check import source_amber_env

            env = source_amber_env()
            for cmd0 in ("obabel", "babel"):
                exe = shutil.which(cmd0, path=env.get("PATH"))
                if not exe:
                    continue
                import subprocess

                r = subprocess.run(
                    [exe, "-isdf", str(sdf), "-omol2", "-O", str(dst)],
                    capture_output=True, text=True, timeout=60, env=env,
                )
                if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
                    logger.info("RDKit+OpenBabel 去氢完成: %s", dst.name)
                    return True
    except Exception as e:
        logger.warning("RDKit 去氢失败: %s", e)

    from .env_check import source_amber_env
    import subprocess

    env = source_amber_env()
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        r = subprocess.run(
            [exe, "-imol2", str(src), "-omol2", "-O", str(dst), "-d"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            logger.info("Open Babel 去氢完成: %s", dst.name)
            return True
    return False


def _remove_atom_from_mol2(src: Path, dst: Path, atom_id: int) -> None:
    """从 MOL2 删除指定原子编号（及相连键），用于点击去氢。"""
    atoms, bonds = _parse_mol2_atoms_bonds(src)
    kept = [a for a in atoms if a["id"] != atom_id]
    if len(kept) == len(atoms):
        raise RuntimeError(f"找不到原子 #{atom_id}")
    id_map = {a["id"]: i for i, a in enumerate(kept, 1)}
    kept_bonds = [(a, b) for a, b in bonds if a in id_map and b in id_map]

    # 读取原分子名
    name = "LIG"
    for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.strip().startswith("@<TRIPOS>"):
            continue
        if name == "LIG" and ln.strip() and not ln.strip()[0].isdigit():
            # MOLECULE 段第一行非数字常为名称
            name = ln.strip()[:80] or "LIG"
            break

    lines = [
        "@<TRIPOS>MOLECULE\n",
        f"{name}\n",
        f"{len(kept)} {len(kept_bonds)} 0 0 0\n",
        "SMALL\n",
        "GASTEIGER\n",
        "@<TRIPOS>ATOM\n",
    ]
    for a in kept:
        parts = list(a.get("parts") or [])
        while len(parts) < 9:
            parts.append("0.000" if len(parts) >= 8 else "1")
        parts[0] = str(id_map[a["id"]])
        # 残基序号占位
        if len(parts) > 6 and not parts[6].isdigit():
            parts.insert(6, "1")
        lines.append(_format_mol2_atom_line(parts))
    lines.append("@<TRIPOS>BOND\n")
    for bi, (a, b) in enumerate(kept_bonds, 1):
        lines.append(f"{bi} {id_map[a]} {id_map[b]} 1\n")
    dst.write_text("".join(lines), encoding="utf-8")


def _add_h_on_atom(src: Path, dst: Path, atom_id: int) -> bool:
    """在指定重原子上补氢（RDKit onlyOnAtoms；atom_id 为 MOL2 1-based）。"""
    m = Chem.MolFromMol2File(
        str(src), sanitize=True, removeHs=False, cleanupSubstructures=False,
    )
    if m is None:
        m = Chem.MolFromMol2File(
            str(src), sanitize=False, removeHs=False, cleanupSubstructures=False,
        )
    if m is None:
        return False
    try:
        Chem.SanitizeMol(m)
    except Exception:
        pass
    # RDKit 原子索引 0-based；MOL2 id 通常按顺序 1..N
    idx = atom_id - 1
    if idx < 0 or idx >= m.GetNumAtoms():
        return False
    atom = m.GetAtomWithIdx(idx)
    if atom.GetAtomicNum() == 1:
        return False
    m_h = Chem.AddHs(m, onlyOnAtoms=(idx,), addCoords=True)
    sdf = dst.with_suffix(".sdf")
    w = Chem.SDWriter(str(sdf))
    w.write(m_h)
    w.close()
    from .env_check import source_amber_env
    import subprocess

    env = source_amber_env()
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        r = subprocess.run(
            [exe, "-isdf", str(sdf), "-omol2", "-O", str(dst)],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            return True
    return False


def prepare_one_mol2(
    mol2_path: Path,
    *,
    add_hydrogens: bool = True,
    name: str = "",
    residue_key: str = "",
) -> dict[str, Any]:
    """清洗并可选补氢，返回配体元信息。"""
    work = Path(tempfile.mkdtemp(prefix="webmd_lig_prep_"))
    try:
        src = work / "input.mol2"
        shutil.copy(mol2_path, src)
        san = sanitize_mol2_atom_types(src)
        warnings = list(san.warnings or [])
        if san.blocked:
            raise RuntimeError("配体原子类型异常，请检查元素与键连后重新上传。")
        if san.fixes:
            warnings.append(f"已自动修复 {len(san.fixes)} 处原子类型")

        out = work / "prepared.mol2"
        if add_hydrogens:
            if _has_explicit_hydrogens(src):
                shutil.copy(src, out)
                warnings.append("已检测到显式氢，跳过自动补氢（可点「去除氢」后重新补氢）")
            else:
                ok = _add_hydrogens_mol2(src, out)
                if not ok:
                    shutil.copy(src, out)
                    warnings.append("自动补氢失败，已保留原结构，请手动检查")
                else:
                    warnings.append(
                        "已用 Open Babel/RDKit 补氢（非 pKa 预测；请核对质子化态）"
                    )
        else:
            shutil.copy(src, out)

        meta = _mol2_meta(out, name=name or mol2_path.name, residue_key=residue_key)
        meta["warnings"] = warnings
        return meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


def edit_mol2_text(
    mol2_text: str,
    action: str,
    *,
    atom_id: int | None = None,
) -> dict[str, Any]:
    """对 MOL2 文本执行补氢/去氢/点选编辑。"""
    action = (action or "").strip().lower()
    work = Path(tempfile.mkdtemp(prefix="webmd_lig_edit_"))
    try:
        src = work / "edit_in.mol2"
        dst = work / "edit_out.mol2"
        src.write_text(mol2_text, encoding="utf-8")

        if action in ("add_h", "protonate"):
            if not _add_hydrogens_mol2(src, dst):
                raise RuntimeError("补氢失败，请检查配体结构")
        elif action in ("strip_h", "deprotonate"):
            if not _strip_hydrogens_mol2(src, dst):
                raise RuntimeError("去氢失败，请检查配体结构")
        elif action == "remove_atom":
            if atom_id is None or atom_id < 1:
                raise RuntimeError("请指定要删除的原子编号")
            atoms, _ = _parse_mol2_atoms_bonds(src)
            target = next((a for a in atoms if a["id"] == atom_id), None)
            if not target:
                raise RuntimeError(f"找不到原子 #{atom_id}")
            el = (target.get("type") or target.get("name") or "").upper()
            if not (el.startswith("H") or (target.get("name") or "").upper().startswith("H")):
                # 允许删非 H，但提示
                logger.info("删除非氢原子 #%s type=%s", atom_id, target.get("type"))
            _remove_atom_from_mol2(src, dst, atom_id)
        elif action == "add_h_on_atom":
            if atom_id is None or atom_id < 1:
                raise RuntimeError("请指定要补氢的重原子编号")
            if not _add_h_on_atom(src, dst, atom_id):
                raise RuntimeError("在该原子上补氢失败（可能已饱和或结构异常）")
        else:
            raise RuntimeError(
                "不支持的操作，可选：add_h / strip_h / remove_atom / add_h_on_atom"
            )

        meta = _mol2_meta(dst)
        meta["warnings"] = []
        meta["action"] = action
        return meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


def mol2_to_editor_formats(mol2_text: str) -> dict[str, Any]:
    """将 MOL2 转为 JSME 可用的 MOL / SMILES。"""
    from .env_check import source_amber_env
    import subprocess

    work = Path(tempfile.mkdtemp(prefix="webmd_to_editor_"))
    try:
        src = work / "in.mol2"
        mol_p = work / "out.mol"
        smi_p = work / "out.smi"
        src.write_text(mol2_text, encoding="utf-8")
        env = source_amber_env()
        mol = ""
        smiles = ""
        for cmd0 in ("obabel", "babel"):
            exe = shutil.which(cmd0, path=env.get("PATH"))
            if not exe:
                continue
            r1 = subprocess.run(
                [exe, "-imol2", str(src), "-omol", "-O", str(mol_p)],
                capture_output=True, text=True, timeout=60, env=env,
            )
            r2 = subprocess.run(
                [exe, "-imol2", str(src), "-osmi", "-O", str(smi_p)],
                capture_output=True, text=True, timeout=60, env=env,
            )
            if r1.returncode == 0 and mol_p.is_file():
                mol = mol_p.read_text(encoding="utf-8", errors="replace")
            if r2.returncode == 0 and smi_p.is_file():
                smiles = smi_p.read_text(encoding="utf-8", errors="replace").split()[0]
            if mol or smiles:
                break
        if not mol and not smiles:
            # RDKit 回退
            m = Chem.MolFromMol2Block(mol2_text, sanitize=True, removeHs=False)
            if m is None:
                m = Chem.MolFromMol2Block(mol2_text, sanitize=False, removeHs=False)
            if m is None:
                raise RuntimeError("无法将 MOL2 转为可编辑结构")
            smiles = Chem.MolToSmiles(m)
            mol = Chem.MolToMolBlock(m)
        return {"mol": mol, "smiles": smiles}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def editor_to_mol2(
    *,
    mol: str = "",
    smiles: str = "",
    gen3d: bool = True,
) -> dict[str, Any]:
    """将 JSME 导出的 MOL/SMILES 转回带三维坐标的 MOL2。"""
    from .env_check import source_amber_env
    import subprocess

    mol = (mol or "").strip()
    smiles = (smiles or "").strip()
    if not mol and not smiles:
        raise RuntimeError("请提供 MOL 或 SMILES")

    work = Path(tempfile.mkdtemp(prefix="webmd_from_editor_"))
    try:
        dst = work / "out.mol2"
        env = source_amber_env()
        ok = False
        for cmd0 in ("obabel", "babel"):
            exe = shutil.which(cmd0, path=env.get("PATH"))
            if not exe:
                continue
            if mol:
                src = work / "in.mol"
                src.write_text(mol + ("\n" if not mol.endswith("\n") else ""), encoding="utf-8")
                cmd = [exe, "-imol", str(src), "-omol2", "-O", str(dst), "-h"]
                if gen3d:
                    cmd.append("--gen3d")
            else:
                src = work / "in.smi"
                src.write_text(smiles + "\n", encoding="utf-8")
                cmd = [exe, "-ismi", str(src), "-omol2", "-O", str(dst), "-h"]
                if gen3d:
                    cmd.append("--gen3d")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
            if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
                ok = True
                break
            logger.warning("Open Babel 转 MOL2 失败: %s", (r.stderr or r.stdout or "")[-400:])
        if not ok:
            raise RuntimeError("无法从编辑器结构生成 MOL2（请检查键连）")

        # 清洗类型后返回
        san = sanitize_mol2_atom_types(dst)
        warnings = list(san.warnings or [])
        if san.blocked:
            raise RuntimeError("编辑后原子类型异常，请检查结构")
        if gen3d:
            warnings.append("已由 SMILES/MOL 重新生成三维坐标（--gen3d），请在 3D 预览中核对姿态")
        meta = _mol2_meta(dst, name="ligand_edited.mol2")
        meta["warnings"] = warnings
        meta["smiles"] = smiles or mol2_to_editor_formats(meta["mol2"]).get("smiles", "")
        return meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


def prepare_from_complex(
    pdb_path: Path,
    protein_chains: list[str],
    ligand_residues: list[str],
    *,
    add_hydrogens: bool = True,
) -> dict[str, Any]:
    """从复合物 PDB 拆分配体并补氢准备。"""
    from .pdb_chains import split_complex_mol2

    work = Path(tempfile.mkdtemp(prefix="webmd_complex_prep_"))
    try:
        prot_out, mol2_paths = split_complex_mol2(
            pdb_path, protein_chains, ligand_residues, work,
        )
        ligands = []
        for i, mp in enumerate(mol2_paths, 1):
            key = ligand_residues[i - 1] if i - 1 < len(ligand_residues) else ""
            meta = prepare_one_mol2(
                Path(mp),
                add_hydrogens=add_hydrogens,
                name=f"ligand_{i}.mol2",
                residue_key=key,
            )
            meta["index"] = i
            ligands.append(meta)
        protein_pdb = Path(prot_out).read_text(encoding="utf-8", errors="replace")
        return {
            "protein_pdb": protein_pdb,
            "ligands": ligands,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
