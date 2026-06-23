# ==================================================
# 功能说明：解析配体 GAFF2 mol2/frcmod，导出力场 JSON 供网页可视化
# 使用方法：由 pipeline 或 API 调用 parse_ligand_forcefield / export_ligand_forcefield_json
# 依赖环境：Python 标准库
# 生成时间：2026-06-23
# ==================================================

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# frcmod 各段落关键字
_FRCMOD_SECTIONS = ("MASS", "BOND", "ANGLE", "DIHE", "IMPROPER", "NONBON")


def _find_gaff_mol2(w: Path) -> Path | None:
    """在任务目录中定位 antechamber 输出的 GAFF2 mol2。"""
    cands = [
        w / "ligand_gaff.mol2",
        *sorted(w.glob("ligand/*_gaff.mol2")),
        *sorted(w.glob("*_gaff.mol2")),
    ]
    for p in cands:
        if p.is_file():
            return p
    return None


def _find_frcmod(w: Path) -> Path | None:
    """在任务目录中定位 parmchk2 输出的 frcmod。"""
    cands = [
        w / "ligand.frcmod",
        *sorted(w.glob("ligand/*.frcmod")),
        *sorted(w.glob("*.frcmod")),
    ]
    for p in cands:
        if p.is_file():
            return p
    return None


def _parse_mol2(p: Path) -> dict:
    """解析 MOL2 中的原子、键与 GAFF 原子类型、部分电荷。"""
    atoms: list[dict] = []
    bonds: list[dict] = []
    mol_name = p.stem
    net_charge = 0.0
    section = None

    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("@<TRIPOS>"):
                section = s.split("@<TRIPOS>")[-1].strip()
                continue
            if not s or s.startswith("#"):
                continue

            if section == "MOLECULE":
                parts = s.split()
                if parts and parts[0].isdigit() and len(parts) >= 5:
                    try:
                        net_charge = float(parts[4])
                    except ValueError:
                        pass
                elif not parts[0].isdigit() and mol_name == p.stem:
                    mol_name = parts[0][:80]
                continue

            if section == "ATOM":
                parts = s.split()
                if len(parts) < 8:
                    continue
                aid = int(parts[0])
                atoms.append({
                    "id": aid,
                    "name": parts[1],
                    "x": float(parts[2]),
                    "y": float(parts[3]),
                    "z": float(parts[4]),
                    "atom_type": parts[5],
                    "subst_id": int(parts[6]),
                    "subst_name": parts[7],
                    "charge": float(parts[8]) if len(parts) > 8 else 0.0,
                })
                continue

            if section == "BOND":
                parts = s.split()
                if len(parts) < 4:
                    continue
                bonds.append({
                    "id": int(parts[0]),
                    "atom1": int(parts[1]),
                    "atom2": int(parts[2]),
                    "order": parts[3],
                })

    id2atom = {a["id"]: a for a in atoms}
    for b in bonds:
        a1 = id2atom.get(b["atom1"], {})
        a2 = id2atom.get(b["atom2"], {})
        b["atom1_name"] = a1.get("name", "?")
        b["atom2_name"] = a2.get("name", "?")
        b["label"] = f"{b['atom1_name']}-{b['atom2_name']}"

    return {
        "name": mol_name,
        "net_charge": round(net_charge, 4),
        "atoms": atoms,
        "bonds": bonds,
    }


def _parse_frcmod(p: Path) -> dict:
    """解析 frcmod 中的键、角、二面角、异常二面角参数。"""
    data: dict[str, list] = {k.lower(): [] for k in _FRCMOD_SECTIONS}
    section = None

    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("Remark"):
                continue
            up = s.upper()
            if up in _FRCMOD_SECTIONS:
                section = up
                continue
            if section is None or section == "MASS" or section == "NONBON":
                continue

            parts = s.split()
            if len(parts) < 3:
                continue

            typ = parts[0]
            comment = " ".join(parts[3:]) if len(parts) > 3 else ""

            if section == "BOND":
                data["bond"].append({
                    "type": typ,
                    "k": float(parts[1]),
                    "r0": float(parts[2]),
                    "comment": comment,
                })
            elif section == "ANGLE":
                data["angle"].append({
                    "type": typ,
                    "k": float(parts[1]),
                    "theta0": float(parts[2]),
                    "comment": comment,
                })
            elif section in ("DIHE", "IMPROPER"):
                key = "dihe" if section == "DIHE" else "improper"
                entry = {
                    "type": typ,
                    "comment": comment,
                }
                if len(parts) >= 4:
                    entry["v1"] = float(parts[1])
                    entry["n"] = float(parts[2])
                    entry["gamma"] = float(parts[3])
                data[key].append(entry)

    return data


def _build_adjacency(bonds: list[dict]) -> dict[int, set[int]]:
    """由键列表构建邻接表。"""
    adj: dict[int, set[int]] = {}
    for b in bonds:
        a, c = b["atom1"], b["atom2"]
        adj.setdefault(a, set()).add(c)
        adj.setdefault(c, set()).add(a)
    return adj


def _match_type_pattern(types: list[str], pattern: str) -> bool:
    """判断原子类型序列是否匹配 frcmod 类型模板（如 c1-c2-n2）。"""
    pts = pattern.split("-")
    if len(pts) != len(types):
        return False
    return all(t == p for t, p in zip(types, pts))


def _assign_instanced_terms(mol: dict, frc: dict) -> None:
    """将 frcmod 类型模板匹配到具体原子组合，便于 3D 高亮与表格展示。"""
    atoms = mol["atoms"]
    bonds = mol["bonds"]
    id2atom = {a["id"]: a for a in atoms}
    adj = _build_adjacency(bonds)

    def atom_type(aid: int) -> str:
        return id2atom[aid]["atom_type"]

    # 角：i-j-k，j 为顶点
    angle_instances: list[dict] = []
    seen_ang: set[tuple] = set()
    for entry in frc.get("angle", []):
        pts = entry["type"].split("-")
        if len(pts) != 3:
            continue
        for j, neighbors in adj.items():
            if atom_type(j) != pts[1]:
                continue
            for i in neighbors:
                if atom_type(i) != pts[0]:
                    continue
                for k in neighbors:
                    if k == i or atom_type(k) != pts[2]:
                        continue
                    key = (min(i, k), j, max(i, k))
                    if key in seen_ang:
                        continue
                    seen_ang.add(key)
                    angle_instances.append({
                        **entry,
                        "atoms": [i, j, k],
                        "atom_names": [
                            id2atom[i]["name"],
                            id2atom[j]["name"],
                            id2atom[k]["name"],
                        ],
                        "label": f"{id2atom[i]['name']}-{id2atom[j]['name']}-{id2atom[k]['name']}",
                    })

    # 二面角：i-j-k-l
    dihe_instances: list[dict] = []
    seen_dih: set[tuple] = set()
    for entry in frc.get("dihe", []):
        pts = entry["type"].split("-")
        if len(pts) != 4:
            continue
        for b in bonds:
            j, k = b["atom1"], b["atom2"]
            for j2, k2 in ((j, k), (k, j)):
                for i in adj.get(j2, []):
                    if i == k2 or atom_type(i) != pts[0]:
                        continue
                    for l in adj.get(k2, []):
                        if l == j2 or atom_type(l) != pts[3]:
                            continue
                        types = [atom_type(i), atom_type(j2), atom_type(k2), atom_type(l)]
                        if not _match_type_pattern(types, entry["type"]):
                            continue
                        key = (min(i, l), j2, k2, max(i, l))
                        if key in seen_dih:
                            continue
                        seen_dih.add(key)
                        dihe_instances.append({
                            **entry,
                            "atoms": [i, j2, k2, l],
                            "atom_names": [
                                id2atom[i]["name"],
                                id2atom[j2]["name"],
                                id2atom[k2]["name"],
                                id2atom[l]["name"],
                            ],
                            "label": "-".join([
                                id2atom[i]["name"],
                                id2atom[j2]["name"],
                                id2atom[k2]["name"],
                                id2atom[l]["name"],
                            ]),
                        })

    # 异常二面角（improper）：中心原子在第二位或按 Amber 惯例；此处按四元组匹配
    improper_instances: list[dict] = []
    seen_imp: set[tuple] = set()
    for entry in frc.get("improper", []):
        pts = entry["type"].split("-")
        if len(pts) != 4:
            continue
        ids = [a["id"] for a in atoms]
        for i in ids:
            for j in ids:
                if j == i:
                    continue
                for k in ids:
                    if k in (i, j):
                        continue
                    for l in ids:
                        if l in (i, j, k):
                            continue
                        types = [atom_type(i), atom_type(j), atom_type(k), atom_type(l)]
                        if not _match_type_pattern(types, entry["type"]):
                            continue
                        key = tuple(sorted([i, j, k, l]))
                        if key in seen_imp:
                            continue
                        seen_imp.add(key)
                        improper_instances.append({
                            **entry,
                            "atoms": [i, j, k, l],
                            "atom_names": [
                                id2atom[i]["name"],
                                id2atom[j]["name"],
                                id2atom[k]["name"],
                                id2atom[l]["name"],
                            ],
                            "label": "-".join([
                                id2atom[i]["name"],
                                id2atom[j]["name"],
                                id2atom[k]["name"],
                                id2atom[l]["name"],
                            ]),
                        })

    mol["angle_instances"] = angle_instances
    mol["dihe_instances"] = dihe_instances
    mol["improper_instances"] = improper_instances


def parse_ligand_forcefield(w: str) -> dict:
    """解析任务目录中的配体力场，返回 JSON 可序列化字典。"""
    work = Path(w)
    mol2 = _find_gaff_mol2(work)
    if not mol2:
        raise FileNotFoundError("未找到 GAFF2 mol2 文件")

    mol = _parse_mol2(mol2)
    frcmod = _find_frcmod(work)
    frc = _parse_frcmod(frcmod) if frcmod else {
        "bond": [], "angle": [], "dihe": [], "improper": [],
    }

    _assign_instanced_terms(mol, frc)

    charges = [a["charge"] for a in mol["atoms"]]
    summary = {
        "atom_count": len(mol["atoms"]),
        "bond_count": len(mol["bonds"]),
        "angle_param_count": len(frc.get("angle", [])),
        "angle_instance_count": len(mol["angle_instances"]),
        "dihe_param_count": len(frc.get("dihe", [])),
        "improper_param_count": len(frc.get("improper", [])),
        "charge_min": round(min(charges), 4) if charges else 0,
        "charge_max": round(max(charges), 4) if charges else 0,
        "charge_sum": round(sum(charges), 4),
        "atom_types": sorted({a["atom_type"] for a in mol["atoms"]}),
    }

    return {
        "mol2_path": str(mol2.relative_to(work)) if mol2.is_relative_to(work) else mol2.name,
        "frcmod_path": (
            str(frcmod.relative_to(work)) if frcmod and frcmod.is_relative_to(work)
            else (frcmod.name if frcmod else None)
        ),
        "molecule": mol,
        "frcmod": frc,
        "summary": summary,
    }


def export_ligand_forcefield_json(w: str, out_name: str = "ligand_forcefield.json") -> str:
    """导出配体力场 JSON 文件，返回路径。"""
    work = Path(w)
    data = parse_ligand_forcefield(w)
    out = work / out_name
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("配体力场 JSON 已导出: %s", out)
    return str(out)


def ensure_ligand_forcefield_json(w: str) -> dict:
    """确保存在力场 JSON，若缺失则按需生成。"""
    work = Path(w)
    cached = work / "ligand_forcefield.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    data = parse_ligand_forcefield(w)
    cached.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
