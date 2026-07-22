# ==================================================
# 功能说明：统一配体定义（小分子/肽），固化 Receptor/Ligand/Complex 到 index.ndx
# 使用方法：前处理末尾 write_ligand_spec + ensure_standard_ndx_groups；导出/分析读 load_ligand_spec
# 依赖环境：Python 标准库
# 生成时间：2026-07-22
# ==================================================

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SPEC_NAME = "webmd_ligand_spec.json"

# 溶剂与离子（与 gmx_prepare / structure_export 保持一致）
_SOLVENT_ION = frozenset({
    "WAT", "SOL", "HOH", "TIP3", "TIP4", "SPC", "OW", "TP3", "T3P",
    "NA", "CL", "K", "MG", "CA", "ZN", "BR", "CS", "LI", "RB",
    "NA+", "CL-", "K+", "MG2+", "CA2+", "ZN2+", "SOD", "POT", "CLA",
})


def _norm_resname(r: str) -> str:
    """残基名归一化。"""
    return (r or "").strip().upper().replace("+", "").replace("-", "")


def _is_solvent_or_ion(r: str) -> bool:
    """判断是否为水或离子。"""
    s = _norm_resname(r)
    if s in {x.replace("+", "").replace("-", "") for x in _SOLVENT_ION}:
        return True
    return s in {"NA", "CL", "K", "MG", "CA", "ZN"}


def load_ligand_spec(wd: str | Path) -> Optional[dict[str, Any]]:
    """读取任务目录中的统一配体定义；不存在则返回 None。"""
    p = Path(wd) / SPEC_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 %s 失败: %s", p, e)
        return None


def write_ligand_spec(wd: str | Path, params: dict) -> dict[str, Any]:
    """根据 params 与 gro 写 webmd_ligand_spec.json，返回 spec 字典。

    设计思路：小分子用 resname 识别；肽用 gro 实际残基号范围（经 peptide_resid_map）。
    下游 complex.pdb / index.ndx / 氢键分析一律以本文件为准。
    """
    work = Path(wd)
    ligand_type = str(params.get("ligand_type") or "").strip().lower()
    if not ligand_type:
        if params.get("is_cyclic_peptide"):
            ligand_type = "cyclic"
        elif params.get("is_linear_peptide"):
            ligand_type = "linear"
        else:
            ligand_type = "mol2"

    is_pep = ligand_type in ("cyclic", "linear")
    ligands_meta = list(params.get("ligands") or [])
    entries: list[dict[str, Any]] = []

    if is_pep:
        # 映射肽在 gro 中的实际残基号（禁止用设计号 9001）
        resid_start = resid_end = 0
        try:
            from .peptide_resid_map import peptide_ri_range_for_ndx
            mapped = peptide_ri_range_for_ndx(work)
            if mapped is not None:
                resid_start, resid_end = int(mapped[0]), int(mapped[1])
        except Exception as e:
            logger.warning("肽残基号映射失败: %s", e)

        meta0 = ligands_meta[0] if ligands_meta else {}
        entries.append({
            "index": 1,
            "kind": "peptide",
            "type": "cyclic_peptide" if ligand_type == "cyclic" else "linear_peptide",
            "resnames": [],
            "resid_start": resid_start,
            "resid_end": resid_end,
            "n_residues": int(meta0.get("n_residues") or 0) or (
                (resid_end - resid_start + 1) if resid_start > 0 and resid_end >= resid_start else 0
            ),
            "source": meta0.get("source", ""),
        })
        kind = "peptide"
    else:
        for x in ligands_meta:
            rn = str(x.get("resname") or "").strip().upper()
            if not rn:
                continue
            entries.append({
                "index": int(x.get("index") or len(entries) + 1),
                "kind": "small_molecule",
                "type": "small_molecule",
                "resnames": [rn],
                "resid_start": 0,
                "resid_end": 0,
                "n_residues": 1,
                "source": x.get("source", ""),
                "net_charge": x.get("net_charge"),
            })
        kind = "small_molecule"

    spec: dict[str, Any] = {
        "version": 1,
        "kind": kind,
        "ligand_type": ligand_type,
        "ligands": entries,
        "groups": {
            "Receptor": "蛋白受体（不含配体）",
            "Ligand": "配体（小分子或肽，统一组名）",
            "Complex": "Receptor | Ligand（无溶剂）",
            "Protein_Ligand": "温控用溶质组（Receptor+Ligand）",
            "Water_and_ions": "温控用水与离子组",
        },
    }
    out = work / SPEC_NAME
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "已写入统一配体定义 %s（kind=%s, n_ligand=%d）",
        out.name, kind, len(entries),
    )
    return spec


def _parse_gro_atoms(gro: Path) -> list[dict]:
    """解析 gro 原子行（仅残基号/名，用于分组）。"""
    lines = gro.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 3:
        raise ValueError(f"gro 无效: {gro}")
    n = int(lines[1].split()[0])
    atoms: list[dict] = []
    for i, ln in enumerate(lines[2:2 + n], start=1):
        if len(ln) < 20:
            continue
        try:
            resid = int(ln[0:5])
        except ValueError:
            continue
        atoms.append({
            "gid": i,
            "resid": resid,
            "resname": ln[5:10].strip(),
        })
    return atoms


def _parse_ndx_groups(ndx: Path) -> dict[str, list[int]]:
    """解析 ndx 为 {组名: [原子号,...]}。"""
    groups: dict[str, list[int]] = {}
    cur: Optional[str] = None
    if not ndx.is_file():
        return groups
    for ln in ndx.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].strip()
            groups[cur] = []
            continue
        if cur is None or not s:
            continue
        for tok in s.split():
            if tok.isdigit():
                groups[cur].append(int(tok))
    return groups


def _write_ndx(path: Path, groups: list[tuple[str, list[int]]]) -> None:
    """写入 GROMACS ndx（与 gmx_prepare 格式一致）。"""
    chunks: list[str] = []
    for name, ids in groups:
        chunks.append(f"[ {name} ]")
        row: list[str] = []
        for i, a in enumerate(ids, 1):
            row.append(f"{a:5d}")
            if i % 15 == 0:
                chunks.append(" ".join(row))
                row = []
        if row:
            chunks.append(" ".join(row))
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")


def _ligand_atom_ids(atoms: list[dict], spec: dict[str, Any]) -> list[int]:
    """按 spec 选出配体原子全局编号。"""
    lig_ids: list[int] = []
    for ent in spec.get("ligands") or []:
        kind = str(ent.get("kind") or "")
        if kind == "peptide":
            a = int(ent.get("resid_start") or 0)
            b = int(ent.get("resid_end") or 0)
            if a <= 0 or b < a:
                continue
            for at in atoms:
                if a <= at["resid"] <= b and not _is_solvent_or_ion(at["resname"]):
                    lig_ids.append(at["gid"])
        else:
            names = {_norm_resname(x) for x in (ent.get("resnames") or []) if x}
            if not names:
                continue
            for at in atoms:
                if _norm_resname(at["resname"]) in names:
                    lig_ids.append(at["gid"])
    # 去重保序
    seen: set[int] = set()
    out: list[int] = []
    for i in lig_ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def ensure_standard_ndx_groups(wd: str | Path) -> dict[str, int]:
    """在 index.ndx 中固化统一组名 Receptor / Ligand / Complex。

    保留已有 Protein_Ligand / Water_and_ions / System（温控与兼容）。
    小分子与肽均使用相同组名，便于分析与交付一致。
    返回 {组名: 组序号}。
    """
    work = Path(wd)
    ndx = work / "index.ndx"
    gro = work / "system.gro"
    if not ndx.is_file() or not gro.is_file():
        raise FileNotFoundError("ensure_standard_ndx_groups 需要 system.gro 与 index.ndx")

    spec = load_ligand_spec(work)
    if spec is None:
        logger.warning("缺少 %s，跳过 Receptor/Ligand/Complex 固化", SPEC_NAME)
        return {}

    atoms = _parse_gro_atoms(gro)
    existing = _parse_ndx_groups(ndx)
    pl = existing.get("Protein_Ligand") or []
    wi = existing.get("Water_and_ions") or []
    system = existing.get("System") or list(range(1, len(atoms) + 1))

    lig = _ligand_atom_ids(atoms, spec)
    if not lig:
        logger.warning("未能按 spec 选出配体原子，跳过标准组固化")
        return {}

    pl_set = set(pl) if pl else {a["gid"] for a in atoms if not _is_solvent_or_ion(a["resname"])}
    lig_set = set(lig)
    # Receptor = 溶质中去掉配体；若 pl 为空则用非溶剂非配体
    receptor = sorted(pl_set - lig_set)
    if not receptor:
        # 极端兜底：全部非溶剂且非配体
        receptor = sorted(
            a["gid"] for a in atoms
            if a["gid"] not in lig_set and not _is_solvent_or_ion(a["resname"])
        )
    complex_ids = sorted(set(receptor) | lig_set)

    # 重建 ndx：温控组在前，统一分析组在后（组名对小分子/肽一致）
    ordered: list[tuple[str, list[int]]] = [
        ("Protein_Ligand", pl if pl else complex_ids),
        ("Water_and_ions", wi),
        ("System", system),
        ("Receptor", receptor),
        ("Ligand", sorted(lig_set)),
        ("Complex", complex_ids),
    ]
    # 保留其它已有组（避免破坏后续扩展）
    keep_names = {n for n, _ in ordered}
    for name, ids in existing.items():
        if name not in keep_names and ids:
            ordered.append((name, ids))

    _write_ndx(ndx, ordered)
    name_to_idx = {name: i for i, (name, _) in enumerate(ordered)}
    logger.info(
        "index.ndx 已固化标准组: Receptor=%d Ligand=%d Complex=%d 原子",
        len(receptor), len(lig_set), len(complex_ids),
    )
    return name_to_idx


def peptide_resid_range_from_spec(spec: Optional[dict[str, Any]]) -> Optional[tuple[int, int]]:
    """从 spec 读取肽残基号范围。"""
    if not spec:
        return None
    for ent in spec.get("ligands") or []:
        if str(ent.get("kind") or "") != "peptide":
            continue
        a = int(ent.get("resid_start") or 0)
        b = int(ent.get("resid_end") or 0)
        if a > 0 and b >= a:
            return a, b
    return None


def small_molecule_resnames_from_spec(spec: Optional[dict[str, Any]]) -> set[str]:
    """从 spec 读取小分子残基名集合。"""
    out: set[str] = set()
    if not spec:
        return out
    for ent in spec.get("ligands") or []:
        if str(ent.get("kind") or "") == "peptide":
            continue
        for rn in ent.get("resnames") or []:
            s = _norm_resname(str(rn))
            if s:
                out.add(s)
    return out
