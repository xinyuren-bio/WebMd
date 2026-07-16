# ==================================================
# 功能说明：antechamber + parmchk2 生成 GAFF2；净电荷须用户确认或高置信检测
# 使用方法：由 pipeline 调用 parameterize_ligands(...)
# 依赖环境：AmberTools；pip install rdkit；Open Babel 可选
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rdkit import Chem

from .env_check import check_external_tools, repair_ambertools, source_amber_env
from .ligand_charge import (
    detect_ligand_charge,
    pick_initial_charge,
    probe_working_charges,
)

# 常见不支持作为小分子 GAFF2 参数化的金属元素
_UNSUPPORTED_METALS = frozenset({
    "FE", "ZN", "CU", "MN", "CO", "NI", "MO", "W", "V", "CR", "CD", "HG",
    "AG", "AU", "PT", "PD", "IR", "RH", "RU", "OS", "RE", "TC", "NB", "TA",
})

logger = logging.getLogger(__name__)

# 磷酸基 P–O 键长合理范围 (Å)
_PO_BOND_MIN = 1.35
_PO_BOND_MAX = 1.85


@dataclass
class AtomTypeFix:
    """单条原子类型自动修复记录。"""

    atom_id: int
    atom_name: str
    old_type: str
    new_type: str
    reason: str
    confidence: str  # high | low


@dataclass
class SanitizeReport:
    """MOL2 清洗报告。"""

    fixes: list[AtomTypeFix] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
    block_message: str = ""


def _format_mol2_atom_line(parts: list[str]) -> str:
    """将 MOL2 ATOM 行字段格式化为固定列宽文本。"""
    charge = parts[8] if len(parts) > 8 else "0.000"
    return (
        f"{int(parts[0]):7d} {parts[1]:<7s} {parts[2]:>9s} {parts[3]:>9s} "
        f"{parts[4]:>9s} {parts[5]:<7s} {parts[6]:>3s} {parts[7]:<7s} {charge}\n"
    )


def _safe_mol2_stem(name: str) -> str:
    """将配体文件名主干清洗为仅含字母数字下划线。"""
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")
    return (s or "ligand")[:80]


def _count_mol2_atoms(p: Path) -> int:
    """统计 MOL2 原子数。"""
    n = 0
    in_atom = False
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("@<TRIPOS>ATOM"):
                in_atom = True
                continue
            if s.startswith("@<TRIPOS>"):
                in_atom = False
                continue
            if in_atom and s and not s.startswith("#") and s[0].isdigit():
                n += 1
    return n


def _count_mol2_hydrogens(p: Path) -> int:
    """统计 MOL2 中氢原子个数（按类型/元素前缀）。"""
    n = 0
    in_atom = False
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("@<TRIPOS>ATOM"):
                in_atom = True
                continue
            if s.startswith("@<TRIPOS>"):
                in_atom = False
                continue
            if in_atom and s and not s.startswith("#"):
                parts = s.split()
                if len(parts) >= 6:
                    at = parts[5]
                    nm = parts[1]
                    if at.upper().startswith("H") or nm.upper().startswith("H"):
                        n += 1
    return n


def _parse_mol2_atoms_bonds(p: Path) -> tuple[list[dict], list[tuple[int, int]]]:
    """解析 MOL2 原子与键（用于类型修复证据）。"""
    atoms: list[dict] = []
    bonds: list[tuple[int, int]] = []
    section = ""
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("@<TRIPOS>"):
                section = s
                continue
            if not s or s.startswith("#"):
                continue
            if section == "@<TRIPOS>ATOM":
                parts = s.split()
                if len(parts) >= 6 and parts[0].isdigit():
                    atoms.append({
                        "id": int(parts[0]),
                        "name": parts[1],
                        "x": float(parts[2]),
                        "y": float(parts[3]),
                        "z": float(parts[4]),
                        "type": parts[5],
                        "parts": parts,
                    })
            elif section == "@<TRIPOS>BOND":
                parts = s.split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    bonds.append((int(parts[1]), int(parts[2])))
    return atoms, bonds


def _element_from_type(atype: str) -> str:
    """从 Tripos 原子类型粗取元素符号。"""
    t = (atype or "").strip()
    if not t:
        return ""
    if "." in t:
        t = t.split(".", 1)[0]
    if len(t) >= 2 and t[1].islower():
        return t[0].upper() + t[1].lower()
    return t[0].upper()


def _rdkit_ob_elements(p: Path) -> dict[int, tuple[str | None, str | None]]:
    """按原子序号（1-based）返回 (rdkit_elem, ob_elem)。"""
    out: dict[int, tuple[str | None, str | None]] = {}
    rd_elems: list[str | None] = []
    try:
        m = Chem.MolFromMol2File(
            str(p), sanitize=False, removeHs=False, cleanupSubstructures=False,
        )
        if m is not None:
            for a in m.GetAtoms():
                rd_elems.append(a.GetSymbol())
    except Exception:
        rd_elems = []

    ob_elems: list[str | None] = []
    try:
        from openbabel import pybel

        mol = next(pybel.readfile("mol2", str(p)), None)
        if mol is not None:
            pt = Chem.GetPeriodicTable()
            for a in mol.atoms:
                z = int(getattr(a, "atomicnum", 0) or 0)
                ob_elems.append(pt.GetElementSymbol(z) if z > 0 else None)
    except Exception:
        try:
            from openbabel import openbabel as ob

            conv = ob.OBConversion()
            conv.SetInFormat("mol2")
            om = ob.OBMol()
            if conv.ReadFile(om, str(p)):
                pt = Chem.GetPeriodicTable()
                for a in ob.OBMolAtomIter(om):
                    z = a.GetAtomicNum()
                    ob_elems.append(pt.GetElementSymbol(z) if z else None)
        except Exception:
            ob_elems = []

    n = max(len(rd_elems), len(ob_elems))
    for i in range(n):
        rd = rd_elems[i] if i < len(rd_elems) else None
        oe = ob_elems[i] if i < len(ob_elems) else None
        out[i + 1] = (rd, oe)
    return out


def _evaluate_p_type_fix(
    atom: dict,
    atoms_by_id: dict[int, dict],
    bonds: list[tuple[int, int]],
    elem_map: dict[int, tuple[str | None, str | None]],
) -> AtomTypeFix | None:
    """仅在多重证据支持时，将错误磷类型改为 P.3。"""
    aid = atom["id"]
    nm = (atom["name"] or "").strip()
    at = (atom["type"] or "").strip()
    if at in {"P.3", "P.2"}:
        return None

    # 可疑类型：Pa/Pb/Pt 或裸 P
    suspicious = at in {"Pa", "Pb", "Pt", "P"} or (
        at[:1].upper() == "P" and "." not in at and at.lower() != "p.3"
    )
    name_like_p = nm.upper() in {"PA", "PB", "PG", "P"} or (
        nm.upper().startswith("P") and nm[1:].isdigit()
    )
    if not suspicious and not name_like_p:
        return None

    # 邻居与键长
    nbr_o = 0
    po_ok = 0
    for a, b in bonds:
        other = b if a == aid else a if b == aid else None
        if other is None:
            continue
        oa = atoms_by_id.get(other)
        if not oa:
            continue
        oe = _element_from_type(oa["type"])
        on = (oa["name"] or "").upper()
        is_o = oe == "O" or on.startswith("O") or oa["type"].upper().startswith("O")
        if not is_o:
            continue
        nbr_o += 1
        dx = atom["x"] - oa["x"]
        dy = atom["y"] - oa["y"]
        dz = atom["z"] - oa["z"]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if _PO_BOND_MIN <= dist <= _PO_BOND_MAX:
            po_ok += 1

    rd_e, ob_e = elem_map.get(aid, (None, None))
    rdkit_p = rd_e == "P"

    evidence = []
    if name_like_p:
        evidence.append(f"原子名={nm}")
    if suspicious:
        evidence.append(f"原类型={at} 可疑")
    if nbr_o >= 2:
        evidence.append(f"连接 {nbr_o} 个 O")
    if po_ok >= 2:
        evidence.append(f"{po_ok} 条 P–O 键长合理")
    if rdkit_p:
        evidence.append("RDKit=P")
    if ob_e == "P":
        evidence.append("OpenBabel=P")

    # 高置信：≥2 个 O 邻居、键长合理，且 RDKit 识别为 P（OB 一致或不可用）
    high = (
        nbr_o >= 2
        and po_ok >= 2
        and rdkit_p
        and (ob_e is None or ob_e == "P")
        and (suspicious or name_like_p)
    )
    if high:
        return AtomTypeFix(
            atom_id=aid,
            atom_name=nm,
            old_type=at,
            new_type="P.3",
            reason="; ".join(evidence),
            confidence="high",
        )

    # 低置信：仅名字或仅可疑类型 → 警告，不自动改
    if name_like_p or suspicious:
        return AtomTypeFix(
            atom_id=aid,
            atom_name=nm,
            old_type=at,
            new_type=at,
            reason="证据不足，未自动修改: " + "; ".join(evidence or ["仅名称/类型可疑"]),
            confidence="low",
        )
    return None


def sanitize_mol2_atom_types(p: Path) -> SanitizeReport:
    """基于结构证据清洗原子类型；低置信疑点阻止继续。"""
    atoms, bonds = _parse_mol2_atoms_bonds(p)
    if not atoms:
        return SanitizeReport(warnings=["无法解析 MOL2 原子段"])
    by_id = {a["id"]: a for a in atoms}
    elem_map = _rdkit_ob_elements(p)
    report = SanitizeReport()
    apply_map: dict[int, str] = {}

    for atom in atoms:
        fix = _evaluate_p_type_fix(atom, by_id, bonds, elem_map)
        if fix is None:
            continue
        if fix.confidence == "high" and fix.new_type != fix.old_type:
            report.fixes.append(fix)
            apply_map[fix.atom_id] = fix.new_type
            logger.info(
                "原子类型高置信修复: %s %s→%s (%s)",
                fix.atom_name, fix.old_type, fix.new_type, fix.reason,
            )
        elif fix.confidence == "low":
            report.warnings.append(
                f"原子 {fix.atom_name}(#{fix.atom_id}) 类型 {fix.old_type} 可疑但证据不足，"
                f"未自动修改。依据: {fix.reason}"
            )

    # 低置信且存在可疑 Pa/Pb 等 → 阻断
    low_block = [
        w for w in report.warnings
        if "Pa" in w or "Pb" in w or "Pt" in w or "可疑" in w
    ]
    if low_block and not apply_map:
        # 有可疑类型警告且没有任何高置信修复时，要求用户检查
        suspicious_types = {
            a["type"] for a in atoms if a["type"] in {"Pa", "Pb", "Pt"}
        }
        if suspicious_types:
            report.blocked = True
            report.block_message = (
                "检测到疑似错误原子类型 "
                + ", ".join(sorted(suspicious_types))
                + "，但自动修复证据不足。请检查 MOL2 元素/键级后重新上传。"
                + " 详情: "
                + " | ".join(report.warnings[:5])
            )

    if apply_map:
        lines_out: list[str] = []
        in_atom = False
        with p.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("@<TRIPOS>ATOM"):
                    in_atom = True
                    lines_out.append(line)
                    continue
                if s.startswith("@<TRIPOS>"):
                    in_atom = False
                    lines_out.append(line)
                    continue
                if in_atom and s and not s.startswith("#"):
                    parts = s.split()
                    if len(parts) >= 6 and parts[0].isdigit():
                        aid = int(parts[0])
                        if aid in apply_map:
                            parts[5] = apply_map[aid]
                        while len(parts) < 9:
                            parts.append("0.000" if len(parts) == 8 else "1")
                        lines_out.append(_format_mol2_atom_line(parts))
                        continue
                lines_out.append(line)
        p.write_text("".join(lines_out), encoding="utf-8")

    return report


def _heavy_atom_fingerprint(p: Path) -> list[dict]:
    """重原子指纹（用于补氢前后追踪）。"""
    atoms, _ = _parse_mol2_atoms_bonds(p)
    out = []
    for a in atoms:
        el = _element_from_type(a["type"])
        if el == "H":
            continue
        out.append({
            "id": a["id"],
            "name": a["name"],
            "type": a["type"],
            "element": el,
            "xyz": [a["x"], a["y"], a["z"]],
        })
    return out


def _map_heavy_atoms(before: list[dict], after: list[dict]) -> list[dict]:
    """按坐标最近邻映射补氢前后重原子。"""
    mapping = []
    used: set[int] = set()
    for b in before:
        best_i = None
        best_d = 1e9
        for i, a in enumerate(after):
            if i in used:
                continue
            if a["element"] != b["element"]:
                continue
            dx = b["xyz"][0] - a["xyz"][0]
            dy = b["xyz"][1] - a["xyz"][1]
            dz = b["xyz"][2] - a["xyz"][2]
            d = dx * dx + dy * dy + dz * dz
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is not None and best_d < 0.25:  # ~0.5 Å
            used.add(best_i)
            a = after[best_i]
            mapping.append({
                "before_id": b["id"],
                "after_id": a["id"],
                "name": b["name"],
                "element": b["element"],
                "rmsd2": best_d,
            })
        else:
            mapping.append({
                "before_id": b["id"],
                "after_id": None,
                "name": b["name"],
                "element": b["element"],
                "rmsd2": None,
            })
    return mapping


def _has_explicit_hydrogens(p: Path) -> bool:
    """判断是否已有显式氢且数量相对合理。"""
    n_h = _count_mol2_hydrogens(p)
    n_all = _count_mol2_atoms(p)
    if n_h <= 0 or n_all <= 0:
        return False
    n_heavy = max(1, n_all - n_h)
    # 粗判：每个重原子平均至少约 0.3 个 H（避免仅 1–2 个误标 H）
    return n_h >= max(2, int(0.3 * n_heavy))


def _add_hydrogens_mol2(src: Path, dst: Path) -> bool:
    """Open Babel -h 补氢（结构处理，非 pKa 预测）。"""
    env = source_amber_env()
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        cmd = [exe, "-imol2", str(src), "-omol2", "-O", str(dst), "-h"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            n_before = _count_mol2_hydrogens(src)
            n_after = _count_mol2_hydrogens(dst)
            logger.info(
                "Open Babel 补氢完成: %s → %s（H %d → %d）。"
                "注意：-h 不是目标 pH 下的可靠 pKa/质子化预测。",
                src.name, dst.name, n_before, n_after,
            )
            return True
        logger.warning("Open Babel 补氢失败: %s", (r.stderr or r.stdout or "")[-400:])

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
            m_h = Chem.AddHs(m, addCoords=True)
            sdf = dst.with_suffix(".sdf")
            w = Chem.SDWriter(str(sdf))
            w.write(m_h)
            w.close()
            for cmd0 in ("obabel", "babel"):
                exe = shutil.which(cmd0, path=env.get("PATH"))
                if not exe:
                    continue
                r = subprocess.run(
                    [exe, "-isdf", str(sdf), "-omol2", "-O", str(dst)],
                    capture_output=True, text=True, timeout=60, env=env,
                )
                if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
                    logger.info("RDKit+OpenBabel 补氢完成: %s", dst.name)
                    return True
    except Exception as e:
        logger.warning("RDKit 补氢路径失败: %s", e)
    return False


def _set_mol2_resname(p: str, resname: str) -> None:
    """统一 MOL2 残基名。"""
    rn = (resname or "LIG")[:7]
    lines_out = []
    in_atom = False
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("@<TRIPOS>ATOM"):
                in_atom = True
                lines_out.append(line)
                continue
            if s.startswith("@<TRIPOS>"):
                in_atom = False
                lines_out.append(line)
                continue
            if in_atom and s and not s.startswith("#"):
                parts = s.split()
                if len(parts) >= 8:
                    parts[7] = rn
                    while len(parts) < 9:
                        parts.append("0.000")
                    lines_out.append(_format_mol2_atom_line(parts))
                    continue
            lines_out.append(line)
    Path(p).write_text("".join(lines_out), encoding="utf-8")


def _read_sqm_hint(lig_dir: Path) -> str:
    """读取 sqm.out 中与电荷/电子相关的报错摘要。"""
    sqm_out = lig_dir / "sqm.out"
    if not sqm_out.is_file():
        return ""
    text = sqm_out.read_text(encoding="utf-8", errors="replace")
    hints = []
    for ln in text.splitlines():
        s = ln.strip()
        if any(k in s for k in ("qmcharge", "odd number of electrons", "Fatal", "ERROR")):
            hints.append(s)
    return "\n".join(hints[-6:])


def _save_antechamber_log(lig_dir: Path, r: subprocess.CompletedProcess, net_charge: int) -> None:
    """将 antechamber 完整输出写入工作目录，供确认电荷前查阅。"""
    parts = [
        f"# antechamber nc={net_charge} returncode={r.returncode}",
        "----- stdout -----",
        (r.stdout or "").rstrip(),
        "----- stderr -----",
        (r.stderr or "").rstrip(),
    ]
    text = "\n".join(parts) + "\n"
    (lig_dir / "antechamber_last.log").write_text(text, encoding="utf-8")
    # 失败时额外保留，避免后续探测成功覆盖诊断信息
    if r.returncode != 0:
        (lig_dir / "antechamber_fail.log").write_text(text, encoding="utf-8")
        sqm = lig_dir / "sqm.out"
        if sqm.is_file():
            shutil.copy2(sqm, lig_dir / "sqm_fail.out")


def _run_antechamber(
    mol2_in: Path,
    ac_mol2: Path,
    net_charge: int,
    lig_dir: Path,
    env: dict,
) -> bool:
    """运行 antechamber（单次，失败不改电荷重试）。"""
    cmd = [
        "antechamber",
        "-i", str(mol2_in),
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
    _save_antechamber_log(lig_dir, r, net_charge)
    if r.returncode == 0:
        return True
    err = (r.stderr or r.stdout or "")[-1500:]
    sqm_hint = _read_sqm_hint(lig_dir)
    extra = f"\n\nsqm 摘要:\n{sqm_hint}" if sqm_hint else ""
    logger.error("antechamber 失败 (nc=%d): %s%s", net_charge, err, extra)
    return False


def check_ligand_structure_simple(mol2_path: Path) -> None:
    """参数化前简单结构检查；失败时抛出简短中文提示。"""
    p = Path(mol2_path)
    if not p.is_file() or p.stat().st_size < 32:
        raise RuntimeError("配体文件无法读取或为空，请重新上传 MOL2。")

    text = p.read_text(encoding="utf-8", errors="replace")
    if "@<TRIPOS>ATOM" not in text:
        raise RuntimeError("配体不是有效的 MOL2 文件，请检查格式后重新上传。")

    # 多个分子块
    n_mol = text.count("@<TRIPOS>MOLECULE")
    if n_mol > 1:
        raise RuntimeError("配体文件包含多个分子，请只保留一个小分子后重新上传。")

    atoms, bonds = _parse_mol2_atoms_bonds(p)
    if len(atoms) < 3:
        raise RuntimeError("配体原子过少，请确认上传了完整小分子结构。")

    metals = []
    for a in atoms:
        el = _element_from_type(a["type"]).upper()
        if el in _UNSUPPORTED_METALS:
            metals.append(el)
        # 名称提示（不据此改类型）
        nm = (a["name"] or "").upper()
        if nm in _UNSUPPORTED_METALS:
            metals.append(nm)
    if metals:
        uniq = ", ".join(sorted(set(metals)))
        raise RuntimeError(
            f"配体含不支持的金属原子（{uniq}），当前小分子流程无法参数化。"
        )

    # RDKit 连通片数
    try:
        m = Chem.MolFromMol2File(
            str(p), sanitize=False, removeHs=False, cleanupSubstructures=False,
        )
        if m is not None:
            frags = Chem.GetMolFrags(m, asMols=False)
            if len(frags) > 1:
                raise RuntimeError(
                    "配体包含多个互不连接的片段，请拆成单个分子后重新上传。"
                )
    except RuntimeError:
        raise
    except Exception:
        pass


def _write_ff_summary(
    work: Path,
    *,
    lig_name: str,
    resname: str,
    net_charge: int,
    charge_source: str,
) -> dict[str, Any]:
    """写入并返回力场摘要（供网页与下载包展示）。"""
    summary = {
        "ligand": lig_name,
        "resname": resname,
        "net_charge": net_charge,
        "charge_source": charge_source,
        "force_field": "GAFF2",
        "charge_method": "AM1-BCC",
        "tool": "antechamber + parmchk2",
    }
    out_dir = work / "ligand_ff_summaries"
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"{lig_name}_ff_summary.json"
    fp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 追加到总表
    all_fp = work / "ligand_forcefield_summary.json"
    rows: list[dict] = []
    if all_fp.is_file():
        try:
            rows = json.loads(all_fp.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []
    rows = [r for r in rows if r.get("ligand") != lig_name]
    rows.append(summary)
    all_fp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def parameterize_ligand(
    mol2_path: str,
    work_dir: str,
    resname: str | None = None,
    add_hydrogens: bool = True,
    *,
    ligand_index: int = 1,
    confirmed_charge: int | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """antechamber + parmchk2 参数化。

    自动判电荷；失败时探测其他整数电荷并自动采用第一个可行值。
    返回 (gaff_mol2, frcmod, ff_summary)。
    """
    check_external_tools()
    env = source_amber_env()
    work = Path(work_dir)
    mol2_name = _safe_mol2_stem(Path(mol2_path).name)
    lig_dir = work / "ligand" / mol2_name
    lig_dir.mkdir(parents=True, exist_ok=True)

    check_ligand_structure_simple(Path(mol2_path))

    input_copy = lig_dir / "ligand_input.mol2"
    shutil.copy(mol2_path, str(input_copy))
    mol2_dest = lig_dir / f"{mol2_name}.mol2"
    shutil.copy(mol2_path, str(mol2_dest))

    san = sanitize_mol2_atom_types(mol2_dest)
    if san.blocked:
        raise RuntimeError(
            "配体原子类型异常，请检查元素与键连后重新上传。"
        )
    if san.fixes:
        logger.info("已应用 %d 条高置信原子类型修复", len(san.fixes))

    protonated = lig_dir / "ligand_protonated.mol2"
    if add_hydrogens:
        if _has_explicit_hydrogens(mol2_dest):
            shutil.copy(mol2_dest, str(protonated))
            logger.info("已有显式氢，跳过补氢: %s", mol2_dest.name)
        else:
            h_tmp = lig_dir / f"{mol2_name}_h.mol2"
            if _add_hydrogens_mol2(mol2_dest, h_tmp):
                sanitize_mol2_atom_types(h_tmp)
                shutil.copy(h_tmp, str(protonated))
                mol2_dest = h_tmp
            else:
                logger.warning("补氢未成功，继续使用原结构（请确认氢原子完整）")
                shutil.copy(mol2_dest, str(protonated))
    else:
        shutil.copy(mol2_dest, str(protonated))

    detection = detect_ligand_charge(Path(mol2_dest))
    ac_mol2 = lig_dir / f"{mol2_name}_gaff.mol2"
    ok = False

    if confirmed_charge is not None:
        net_charge = int(confirmed_charge)
        charge_source = "user_confirmed"
        logger.info("使用用户确认的净电荷 nc=%d", net_charge)
    else:
        net_charge = pick_initial_charge(detection)
        charge_source = detection.source or "auto"

    repaired = repair_ambertools()
    if repaired:
        logger.info("antechamber 前 AmberTools 补全: %s", ", ".join(repaired))

    if net_charge is None:
        # 无法自动判断：探测可行电荷，成功即停止（产物已写出，不再重跑）
        def _try(q: int) -> bool:
            return _run_antechamber(mol2_dest, ac_mol2, q, lig_dir, env)

        working = probe_working_charges(_try, None)
        if not working:
            raise RuntimeError(
                "无法判断配体净电荷，且常见电荷均无法完成 AM1-BCC 计算。"
                "请检查结构后重试。"
            )
        net_charge = int(working[0])
        charge_source = "auto_probe"
        ok = ac_mol2.is_file() and ac_mol2.stat().st_size > 0
        logger.warning("无法自动判断净电荷，已自动采用 nc=%d", net_charge)
    else:
        ok = _run_antechamber(mol2_dest, ac_mol2, int(net_charge), lig_dir, env)
        if not ok and confirmed_charge is None:
            # 原电荷失败：探测并采用第一个可行值（探测成功产物已写出）
            def _try(q: int) -> bool:
                return _run_antechamber(mol2_dest, ac_mol2, q, lig_dir, env)

            working = probe_working_charges(_try, int(net_charge))
            if not working:
                raise RuntimeError(
                    f"配体参数化失败（净电荷 {net_charge} 及常见备选均未通过）。"
                    "请检查结构、质子化与键连后重新上传。"
                )
            old_nc = int(net_charge)
            net_charge = int(working[0])
            charge_source = "auto_probe"
            ok = ac_mol2.is_file() and ac_mol2.stat().st_size > 0
            logger.warning("原净电荷 nc=%d 失败，已自动改用 nc=%d", old_nc, net_charge)

    if not ok:
        raise RuntimeError(
            f"配体参数化失败（净电荷 {net_charge}）。请检查结构后重试。"
        )

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
        raise RuntimeError("配体力场参数检查失败，请检查结构后重试。")

    rn = resname or "LIG"
    _set_mol2_resname(str(ac_mol2), rn)
    summary = _write_ff_summary(
        work,
        lig_name=mol2_name,
        resname=rn,
        net_charge=int(net_charge),
        charge_source=charge_source,
    )
    # 详细报告
    (lig_dir / "ligand_charge_report.json").write_text(
        json.dumps(
            {
                **summary,
                "detection": detection.to_dict(),
                "confirmed_charge": confirmed_charge,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info(
        "配体 GAFF2 完成: %s (nc=%d, 来源=%s, 方法=AM1-BCC)",
        mol2_name, net_charge, charge_source,
    )
    return str(ac_mol2), str(frcmod), summary


def parameterize_ligands(
    mol2_paths: list[str],
    work_dir: str,
    add_hydrogens: bool = True,
    *,
    confirmed_charges: dict[int, int] | None = None,
) -> list[dict]:
    """批量参数化；confirmed_charges 为 {配体序号: 净电荷}（用户确认后传入）。"""
    if not mol2_paths:
        raise ValueError("至少需要一个 MOL2 文件")
    if len(mol2_paths) > 3:
        raise ValueError("最多支持 3 个配体")
    conf = confirmed_charges or {}
    out = []
    for i, p in enumerate(mol2_paths, 1):
        rn = f"LIG{i}"
        gaff_mol2, frcmod, summary = parameterize_ligand(
            p,
            work_dir,
            resname=rn,
            add_hydrogens=add_hydrogens,
            ligand_index=i,
            confirmed_charge=conf.get(i),
        )
        out.append({
            "index": i,
            "resname": rn,
            "source": Path(p).name,
            "gaff_mol2": gaff_mol2,
            "frcmod": frcmod,
            "net_charge": summary["net_charge"],
            "force_field": summary["force_field"],
            "charge_method": summary["charge_method"],
            "charge_source": summary["charge_source"],
        })
    return out
