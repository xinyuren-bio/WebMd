# ==================================================
# 功能说明：非标准对接肽 PDB 检测；用户单字母序列严格核实后按 3D 重建标准 PDB
# 使用方法：由 prepare_linear_peptide / API 肽序列确认调用
# 依赖环境：Python 标准库；补氢可选 pdbfixer + openmm.app
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 标准氨基酸单字母
_AA1 = "ACDEFGHIKLMNPQRSTVWY"
_AA1_TO3 = {
    "A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE",
    "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
    "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG",
    "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR",
}

# 中性链中残基重原子元素组成（不含末端额外 OXT）
_AA_HEAVY_ELEM: dict[str, Counter] = {
    "A": Counter({"C": 3, "N": 1, "O": 1}),
    "C": Counter({"C": 3, "N": 1, "O": 1, "S": 1}),
    "D": Counter({"C": 4, "N": 1, "O": 3}),
    "E": Counter({"C": 5, "N": 1, "O": 3}),
    "F": Counter({"C": 9, "N": 1, "O": 1}),
    "G": Counter({"C": 2, "N": 1, "O": 1}),
    "H": Counter({"C": 6, "N": 3, "O": 1}),
    "I": Counter({"C": 6, "N": 1, "O": 1}),
    "K": Counter({"C": 6, "N": 2, "O": 1}),
    "L": Counter({"C": 6, "N": 1, "O": 1}),
    "M": Counter({"C": 5, "N": 1, "O": 1, "S": 1}),
    "N": Counter({"C": 4, "N": 2, "O": 2}),
    "P": Counter({"C": 5, "N": 1, "O": 1}),
    "Q": Counter({"C": 5, "N": 2, "O": 2}),
    "R": Counter({"C": 6, "N": 4, "O": 1}),
    "S": Counter({"C": 3, "N": 1, "O": 2}),
    "T": Counter({"C": 4, "N": 1, "O": 2}),
    "V": Counter({"C": 5, "N": 1, "O": 1}),
    "W": Counter({"C": 11, "N": 2, "O": 1}),
    "Y": Counter({"C": 9, "N": 1, "O": 2}),
}

_BOND_LIM = {
    "CC": 1.75, "CN": 1.60, "CO": 1.55, "CS": 2.05,
    "NN": 1.55, "NO": 1.55, "NS": 1.95, "OS": 1.90,
    "OO": 1.55, "SS": 2.20,
}


class NeedPeptideSequence(Exception):
    """非标准肽 PDB，需用户提供单字母序列后再继续。"""

    def __init__(self, message: str, *, hint_n_res: int | None = None):
        super().__init__(message)
        self.hint_n_res = hint_n_res


def _dist(a: dict[str, Any], b: dict[str, Any]) -> float:
    """欧氏距离。"""
    return math.sqrt(
        (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2
    )


def _elem_of(name: str, elem_field: str) -> str:
    """推断元素符号。"""
    e = (elem_field or "").strip().upper()
    if e in {"C", "N", "O", "S", "H", "P", "F", "CL", "BR", "I"}:
        return e
    n = (name or "").strip().upper()
    if n.startswith("CL"):
        return "CL"
    if n.startswith("BR"):
        return "BR"
    if n[:1] in {"C", "N", "O", "S", "H", "P", "F"}:
        return n[:1]
    return re.sub(r"\d", "", n)[:1].upper() or "?"


def parse_pdb_atoms(fp: str | Path) -> list[dict[str, Any]]:
    """读取 ATOM/HETATM 为原子字典列表。"""
    atoms: list[dict[str, Any]] = []
    path = Path(fp)
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not (ln.startswith("ATOM") or ln.startswith("HETATM")) or len(ln) < 54:
            continue
        try:
            x, y, z = float(ln[30:38]), float(ln[38:46]), float(ln[46:54])
        except ValueError:
            continue
        name = ln[12:16].strip()
        elem = _elem_of(name, ln[76:78] if len(ln) >= 78 else "")
        try:
            resi = int(ln[22:26])
        except ValueError:
            resi = 1
        atoms.append({
            "line": ln,
            "name": name,
            "resname": ln[17:20].strip().upper(),
            "chain": ln[21] if len(ln) > 21 else " ",
            "resi": resi,
            "x": x, "y": y, "z": z,
            "element": elem,
            "serial": ln[6:11].strip(),
        })
    return atoms


def is_nonstandard_peptide_pdb(fp: str | Path) -> bool:
    """判断是否为对接常见非标准肽 PDB（空残基名/单残基号/无 CA）。"""
    atoms = parse_pdb_atoms(fp)
    if len(atoms) < 8:
        return False
    resnames = {a["resname"] for a in atoms}
    resis = {a["resi"] for a in atoms}
    has_ca = any(a["name"].upper() == "CA" for a in atoms)
    empty_rn = "" in resnames or resnames <= {""}
    # 标准：多个残基号且有 CA、残基名为标准 AA
    std_aa = {
        "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
        "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
        "TYR", "VAL", "HID", "HIE", "HIP",
    }
    if has_ca and len(resis) >= 2 and resnames <= std_aa:
        return False
    if empty_rn and len(resis) <= 1:
        return True
    if not has_ca and len(resis) <= 1:
        return True
    # 原子名几乎全是单字母元素
    names = [a["name"].upper() for a in atoms]
    elem_like = sum(1 for n in names if n in {"C", "N", "O", "S", "H", "HN"})
    if elem_like >= 0.85 * len(names) and len(resis) <= 1:
        return True
    return False


def normalize_peptide_sequence(seq: str) -> str:
    """规范化单字母序列；非法则抛出中文错误。"""
    s = re.sub(r"\s+", "", (seq or "").upper())
    if len(s) < 2:
        raise ValueError("肽序列至少需要 2 个氨基酸（单字母）")
    bad = sorted({c for c in s if c not in _AA1})
    if bad:
        raise ValueError("序列含非法字符（仅支持标准氨基酸单字母）: " + "".join(bad))
    return s


def expected_heavy_counter(seq: str, *, with_oxt: bool = False) -> Counter:
    """序列对应的重原子元素计数。"""
    total: Counter = Counter()
    for c in seq:
        total += _AA_HEAVY_ELEM[c]
    if with_oxt:
        total["O"] += 1
    return total


def heavy_element_counter(atoms: list[dict[str, Any]]) -> Counter:
    """统计重原子元素。"""
    return Counter(a["element"] for a in atoms if a["element"] != "H")


def verify_sequence_composition(seq: str, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    """核实序列与坐标重原子组成是否严格一致；失败抛 ValueError。"""
    seq = normalize_peptide_sequence(seq)
    got = heavy_element_counter(atoms)
    exp0 = expected_heavy_counter(seq, with_oxt=False)
    exp1 = expected_heavy_counter(seq, with_oxt=True)
    if got == exp0:
        return {"sequence": seq, "with_oxt": False, "heavy": dict(got)}
    if got == exp1:
        return {"sequence": seq, "with_oxt": True, "heavy": dict(got)}
    raise ValueError(
        "序列与结构重原子组成不一致："
        f"结构={dict(got)}，无OXT期望={dict(exp0)}，含OXT期望={dict(exp1)}。"
        "请核对单字母序列或重新导出肽 PDB。"
    )


def _bond_limit(ea: str, eb: str) -> float:
    """两元素成键距离上限。"""
    key = "".join(sorted(ea + eb))
    return _BOND_LIM.get(key, 1.70)


def _heavy_bond_graph(heavies: list[dict[str, Any]]) -> list[list[int]]:
    """按距离构建重原子邻接表。"""
    n = len(heavies)
    adj: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _dist(heavies[i], heavies[j])
            if 0.5 < d < _bond_limit(heavies[i]["element"], heavies[j]["element"]):
                adj[i].append(j)
                adj[j].append(i)
    return adj


def _find_backbone_units(
    heavies: list[dict[str, Any]],
    adj: list[list[int]],
) -> list[dict[str, int]]:
    """识别 N–CA–C(=O) 主链单元。"""
    units: list[dict[str, int]] = []
    used_c: set[int] = set()
    n_idx = [i for i, a in enumerate(heavies) if a["element"] == "N"]
    for ni in n_idx:
        candidates: list[tuple[tuple, int, int, int, int]] = []
        for ca in adj[ni]:
            if heavies[ca]["element"] != "C":
                continue
            for ci in adj[ca]:
                if heavies[ci]["element"] != "C" or ci == ni:
                    continue
                o_neis = [k for k in adj[ci] if heavies[k]["element"] == "O"]
                if not o_neis:
                    continue
                # 主链羰基氧通常 1 个；C 端可为 2
                n_c_on_ca = sum(1 for j in adj[ca] if heavies[j]["element"] == "C")
                score = (len(o_neis) > 2, abs(n_c_on_ca - 2), len(o_neis))
                oi = min(o_neis, key=lambda k: _dist(heavies[k], heavies[ci]))
                candidates.append((score, ni, ca, ci, oi))
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0])
        _, ni2, ca, ci, oi = candidates[0]
        if ci in used_c:
            continue
        used_c.add(ci)
        units.append({"N": ni2, "CA": ca, "C": ci, "O": oi})
    return units


def _order_backbone_units(
    heavies: list[dict[str, Any]],
    adj: list[list[int]],
    units: list[dict[str, int]],
) -> list[dict[str, int]]:
    """按肽键 C→下一残基 N 将主链单元排序。"""
    if not units:
        return []
    # 下一残基：本残基 C 连接到另一单元的 N
    nxt: dict[int, int] = {}
    prv: dict[int, int] = {}
    for i, u in enumerate(units):
        for j, v in enumerate(units):
            if i == j:
                continue
            if v["N"] in adj[u["C"]]:
                nxt[i] = j
                prv[j] = i
                break
    starts = [i for i in range(len(units)) if i not in prv]
    if len(starts) != 1:
        # 回退：选任意未入度点；若环状或多链则失败
        if not starts:
            raise ValueError("无法确定肽链起点（可能成环或多条链），请导出标准 PDB")
        if len(starts) > 1:
            raise ValueError(
                f"检测到 {len(starts)} 条主链起点，当前仅支持单条线形肽，请检查结构"
            )
    order = []
    cur = starts[0]
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        order.append(units[cur])
        cur = nxt.get(cur)
    if len(order) != len(units):
        raise ValueError(
            f"主链排序不完整（得到 {len(order)}/{len(units)}），无法唯一重建"
        )
    return order


def _residue_heavy_indices(
    heavies: list[dict[str, Any]],
    adj: list[list[int]],
    unit: dict[str, int],
    next_n: int | None,
    prev_c: int | None,
) -> list[int]:
    """从主链单元 BFS 收集本残基重原子（不跨肽键）。"""
    ban_edge: set[tuple[int, int]] = set()
    if next_n is not None:
        ban_edge.add((unit["C"], next_n))
        ban_edge.add((next_n, unit["C"]))
    if prev_c is not None:
        ban_edge.add((unit["N"], prev_c))
        ban_edge.add((prev_c, unit["N"]))
    seed = {unit["N"], unit["CA"], unit["C"], unit["O"]}
    # C 端第二氧
    for j in adj[unit["C"]]:
        if heavies[j]["element"] == "O" and j != unit["O"]:
            seed.add(j)
    out: list[int] = []
    stack = list(seed)
    seen = set(seed)
    while stack:
        i = stack.pop()
        out.append(i)
        for j in adj[i]:
            if j in seen:
                continue
            if (i, j) in ban_edge:
                continue
            seen.add(j)
            stack.append(j)
    return sorted(out)


def _format_atom_line(
    serial: int,
    name: str,
    resname: str,
    chain: str,
    resi: int,
    x: float,
    y: float,
    z: float,
    elem: str,
) -> str:
    """写出标准 PDB ATOM 行。"""
    if len(name) >= 4:
        nm = name[:4]
    else:
        nm = f" {name:<3s}"
    return (
        f"ATOM  {serial:5d} {nm} {resname:>3s} {chain}{resi:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {elem:>2s}\n"
    )


def _add_hydrogens_pdbfixer(pdb_in: Path, pdb_out: Path) -> None:
    """用 PDBFixer 补氢；不可用则原样复制并告警。"""
    try:
        from pdbfixer import PDBFixer
        from openmm.app import PDBFile
    except Exception as e:
        logger.warning("PDBFixer 不可用，保留无氢结构: %s", e)
        if pdb_in.resolve() != pdb_out.resolve():
            pdb_out.write_text(pdb_in.read_text(encoding="utf-8"), encoding="utf-8")
        return
    fixer = PDBFixer(filename=str(pdb_in))
    fixer.addMissingHydrogens(7.0)
    with pdb_out.open("w", encoding="utf-8") as f:
        PDBFile.writeFile(fixer.topology, fixer.positions, f, keepIds=True)


def rebuild_peptide_pdb_from_sequence(
    src: str | Path,
    seq: str,
    out: str | Path,
) -> dict[str, Any]:
    """严格核实序列后按 3D 重建标准肽 PDB，返回元数据。

    设计思路：组成必须与模板完全一致；主链单元数必须等于序列长度；
    每个残基重原子元素计数必须与该位氨基酸一致。随后写出残基名/号，
    再调用原子名重命名与补氢。任一条件失败则抛错，不产出“半修复”文件。
    """
    from .pdb_sanitize import rename_std_aa_atoms_in_pdb, assert_peptide_amber_names

    src_p = Path(src)
    out_p = Path(out)
    atoms = parse_pdb_atoms(src_p)
    if not atoms:
        raise ValueError("肽 PDB 无原子")

    comp = verify_sequence_composition(seq, atoms)
    seq = comp["sequence"]
    with_oxt = bool(comp["with_oxt"])
    n_res = len(seq)

    heavies = [a for a in atoms if a["element"] != "H"]
    adj = _heavy_bond_graph(heavies)
    units = _find_backbone_units(heavies, adj)
    if len(units) != n_res:
        raise ValueError(
            f"主链残基数与序列不一致：检测到 {len(units)} 个 N–CA–C 单元，"
            f"序列长度为 {n_res}。请核对序列或重新导出结构。"
        )
    ordered = _order_backbone_units(heavies, adj, units)
    if len(ordered) != n_res:
        raise ValueError("主链排序后残基数仍与序列不一致")

    # 按序列核对每个残基元素组成
    residue_groups: list[tuple[str, list[int]]] = []
    for i, unit in enumerate(ordered):
        next_n = ordered[i + 1]["N"] if i + 1 < n_res else None
        prev_c = ordered[i - 1]["C"] if i > 0 else None
        idxs = _residue_heavy_indices(heavies, adj, unit, next_n, prev_c)
        aa = seq[i]
        got = Counter(heavies[j]["element"] for j in idxs)
        exp = Counter(_AA_HEAVY_ELEM[aa])
        if i == n_res - 1 and with_oxt:
            exp = exp + Counter({"O": 1})
        if got != exp:
            raise ValueError(
                f"第 {i + 1} 位氨基酸 {aa}（期望 {dict(exp)}）与结构块 "
                f"{dict(got)} 不一致，无法唯一匹配，已中止重建。"
            )
        residue_groups.append((_AA1_TO3[aa], idxs))

    # 写出重原子标准 PDB（临时），再重命名原子、补氢
    tmp = out_p.with_suffix(".heavy.pdb")
    lines: list[str] = []
    serial = 1
    for ri, (rname, idxs) in enumerate(residue_groups, start=1):
        for j in idxs:
            a = heavies[j]
            # 先用元素作临时名，交给 rename 按键连改 Amber 名
            tname = a["element"][:1] if a["element"] != "CL" else "Cl"
            lines.append(
                _format_atom_line(
                    serial, tname, rname, "C", ri,
                    a["x"], a["y"], a["z"], a["element"],
                )
            )
            serial += 1
    lines.append("END\n")
    tmp.write_text("".join(lines), encoding="utf-8")

    info = rename_std_aa_atoms_in_pdb(tmp)
    missing = info.get("missing_ca") or []
    if missing:
        raise ValueError(
            "原子名严格重建失败，以下残基无法识别 CA: "
            + ", ".join(missing)
            + "。请检查序列或改用标准 PDB 导出。"
        )

    # 每个残基必须已有 CA
    assert_peptide_amber_names(tmp)
    _add_hydrogens_pdbfixer(tmp, out_p)
    if tmp.exists() and tmp.resolve() != out_p.resolve():
        try:
            tmp.unlink()
        except OSError:
            pass

    # 补氢后再确认 CA
    assert_peptide_amber_names(out_p)
    logger.info(
        "肽序列严格重建成功: %s (%d 残基, OXT=%s) → %s",
        seq, n_res, with_oxt, out_p.name,
    )
    return {
        "sequence": seq,
        "n_residues": n_res,
        "with_oxt": with_oxt,
        "three_letter": [_AA1_TO3[c] for c in seq],
        "path": str(out_p),
    }


def hint_n_residues_from_pdb(fp: str | Path) -> int | None:
    """由重原子 N 计数推断可能残基数（仅作提示，不作定论）。"""
    atoms = parse_pdb_atoms(fp)
    n_n = sum(1 for a in atoms if a["element"] == "N" and a["element"] != "H")
    # 上面条件冗余；重原子 N：
    n_n = sum(1 for a in atoms if a["element"] == "N")
    if n_n >= 2:
        return n_n
    return None
