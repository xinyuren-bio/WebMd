# ==================================================
# 功能说明：PDB 断链补 TER、去 altLoc 只留一套构象、按片段修末端、标准氨基酸原子重命名（兼容 tleap）
# 使用方法：由 protein/peptide/system_builder 在写 tleap 输入前调用
# 依赖环境：Python 标准库
# 生成时间：2026-07-20（链边界插入 TER）
# ==================================================
"""复合物/蛋白 PDB 清洗：altLoc、空间断链、末端氢、Amber 原子名。"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# CA–CA 超过该距离视为链断裂（Å），插入 TER
_CA_BREAK_A = 4.5
# 重原子成键距离上限（Å）
_BOND_A = 1.90

# 标准氨基酸常见原子名：用于在双构象中优先保留“命名正常”的一套
# （避免保留被写成 C01/C02 的那套 altLoc）
_STD_ATOM_NAMES = {
    "N",
    "CA",
    "C",
    "O",
    "OXT",
    "CB",
    "CG",
    "CG1",
    "CG2",
    "CD",
    "CD1",
    "CD2",
    "CE",
    "CE1",
    "CE2",
    "CE3",
    "CZ",
    "CZ2",
    "CZ3",
    "CH2",
    "ND1",
    "ND2",
    "NE",
    "NE1",
    "NE2",
    "NZ",
    "NH1",
    "NH2",
    "OD1",
    "OD2",
    "OE1",
    "OE2",
    "OG",
    "OG1",
    "OH",
    "SD",
    "SG",
    "H",
    "H1",
    "H2",
    "H3",
    "HA",
    "HA2",
    "HA3",
    "HB",
    "HB1",
    "HB2",
    "HB3",
    "HD1",
    "HD2",
    "HE1",
    "HE2",
    "HG",
    "HH",
    "HZ",
}

_STD_AA = {
    "ALA",
    "ARG",
    "ASN",
    "ASP",
    "CYS",
    "GLN",
    "GLU",
    "GLY",
    "HIS",
    "ILE",
    "LEU",
    "LYS",
    "MET",
    "PHE",
    "PRO",
    "SER",
    "THR",
    "TRP",
    "TYR",
    "VAL",
    "HID",
    "HIE",
    "HIP",
    "HSD",
    "HSE",
    "HSP",
}


def _dist(a: dict[str, Any], b: dict[str, Any]) -> float:
    """计算两原子欧氏距离。"""
    return math.sqrt(
        (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2
    )


def _parse_atom_line(ln: str) -> dict[str, Any] | None:
    """解析 ATOM/HETATM 行。"""
    if not (ln.startswith("ATOM") or ln.startswith("HETATM")) or len(ln) < 54:
        return None
    try:
        x = float(ln[30:38])
        y = float(ln[38:46])
        z = float(ln[46:54])
    except ValueError:
        return None
    name = ln[12:16].strip()
    elem = ln[76:78].strip().upper() if len(ln) >= 78 else ""
    if not elem:
        elem = re.sub(r"\d", "", name)[:1].upper() or "?"
    try:
        occ = float(ln[54:60]) if len(ln) >= 60 else 1.0
    except ValueError:
        occ = 1.0
    return {
        "line": ln,
        "name": name,
        "altloc": ln[16] if len(ln) > 16 else " ",
        "resname": ln[17:20].strip().upper(),
        "chain": ln[21] if len(ln) > 21 else " ",
        "resi": int(ln[22:26]),
        "icode": ln[26] if len(ln) > 26 else " ",
        "x": x,
        "y": y,
        "z": z,
        "occ": occ,
        "element": elem,
        "serial": ln[6:11],
    }


def _clear_altloc_occupancy(ln: str) -> str:
    """清空 altLoc，并将 occupancy 置为 1.00。"""
    raw = ln.rstrip("\n\r")
    nl = ln[len(raw) :]
    if len(raw) < 54:
        return ln
    # altLoc 列置空格
    raw = raw[:16] + " " + raw[17:]
    # occupancy 列：54–60
    if len(raw) < 60:
        raw = raw.ljust(60)
    raw = raw[:54] + f"{1.0:6.2f}" + raw[60:]
    return raw + nl


def _score_altloc(atoms: list[dict[str, Any]]) -> tuple[int, float, int]:
    """给一套 altLoc 打分（越大越好）：标准原子名数、平均 occupancy、字母序（A>B）。"""
    if not atoms:
        return (0, 0.0, 0)
    n_std = sum(1 for a in atoms if a["name"] in _STD_ATOM_NAMES)
    mean_occ = sum(float(a["occ"]) for a in atoms) / len(atoms)
    tag = str(atoms[0]["altloc"] or " ")
    # -ord：A(65) → -65 > B 的 -66，同分时优先 A
    letter_rank = -ord(tag[0]) if tag.strip() else 0
    return (n_std, mean_occ, letter_rank)


def resolve_altloc_lines(lines: list[str]) -> list[str]:
    """每个残基只保留一套交替构象（altLoc）。

    设计思路：
    1. altLoc 为空格的原子视为各构象共享（常见主链），一律保留；
    2. 同一残基存在 A/B/... 时，优先保留「标准氨基酸原子名更多」的一套，
       其次比平均 occupancy，再比字母序（A 优于 B）；
    3. 保留原子清空 altLoc、occupancy=1.00，避免 tleap/PDBFixer 再歧义。
    """
    by_res_alt: dict[tuple[str, int, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    parsed: list[dict[str, Any] | None] = []

    for ln in lines:
        atom = _parse_atom_line(ln)
        parsed.append(atom)
        if atom is None:
            continue
        key = (atom["chain"], atom["resi"], atom["icode"], atom["resname"])
        alt = atom["altloc"] if atom["altloc"] not in ("", " ") else " "
        by_res_alt[key][alt].append(atom)

    chosen: dict[tuple[str, int, str, str], str] = {}
    n_multi = 0
    for key, alt_map in by_res_alt.items():
        labeled = {a: atoms for a, atoms in alt_map.items() if a != " "}
        if not labeled:
            continue
        if len(labeled) == 1:
            chosen[key] = next(iter(labeled.keys()))
            continue
        best_alt = max(labeled.keys(), key=lambda a: _score_altloc(labeled[a]))
        chosen[key] = best_alt
        n_multi += 1
        dropped = sorted(a for a in labeled if a != best_alt)
        logger.info(
            "altLoc 择优: %s%s%s 保留 %s，丢弃 %s",
            key[3],
            key[0].strip() or "-",
            key[1],
            best_alt,
            ",".join(dropped),
        )

    out: list[str] = []
    n_kept = 0
    n_dropped = 0
    for ln, atom in zip(lines, parsed):
        if atom is None:
            out.append(ln)
            continue
        key = (atom["chain"], atom["resi"], atom["icode"], atom["resname"])
        alt = atom["altloc"] if atom["altloc"] not in ("", " ") else " "
        pick = chosen.get(key)
        if alt == " ":
            # 共享原子：仅在 occupancy 异常时规范化
            out.append(_clear_altloc_occupancy(ln) if abs(atom["occ"] - 1.0) > 1e-3 else ln)
            n_kept += 1
            continue
        if pick is None or alt == pick:
            out.append(_clear_altloc_occupancy(ln))
            n_kept += 1
        else:
            n_dropped += 1

    if n_multi or n_dropped:
        logger.info(
            "altLoc 处理完成: %d 个残基多构象择优，保留原子 %d，丢弃 %d",
            n_multi,
            n_kept,
            n_dropped,
        )
    return out


def insert_ter_at_ca_breaks(lines: list[str], cutoff: float = _CA_BREAK_A) -> list[str]:
    """在同链连续残基 CA–CA 过远处插入 TER，避免 tleap 强行成键。"""
    out: list[str] = []
    n_ter = 0

    # 先收集每个残基的 CA
    res_order: list[tuple[str, int]] = []
    ca_of: dict[tuple[str, int], dict[str, Any]] = {}
    atoms_of: dict[tuple[str, int], list[str]] = defaultdict(list)
    other: list[str] = []

    for ln in lines:
        at = _parse_atom_line(ln)
        if at is None:
            if ln.startswith("TER"):
                continue  # 稍后按检测结果重写
            other.append(ln)
            continue
        key = (at["chain"], at["resi"])
        if key not in ca_of and key not in atoms_of:
            res_order.append(key)
        atoms_of[key].append(ln if ln.endswith("\n") else ln + "\n")
        if at["name"] == "CA":
            ca_of[key] = at

    for i, key in enumerate(res_order):
        if i > 0:
            pk = res_order[i - 1]
            # 链号变化必须分段：原 PDB 的 TER 会被本函数丢弃后重写，
            # 若不在此补回，多链会被合成一段，后续会剥掉非首链 N 端 H2/H3，tleap FATAL。
            if pk[0] != key[0]:
                out.append("TER\n")
                n_ter += 1
                logger.info("链边界插入 TER: 链 %s → %s", pk[0], key[0])
            # 同链且残基号递增时检查空间连续性
            elif key[1] >= pk[1]:
                ca0 = ca_of.get(pk)
                ca1 = ca_of.get(key)
                if ca0 and ca1 and _dist(ca0, ca1) > cutoff:
                    out.append("TER\n")
                    n_ter += 1
                    logger.warning(
                        "检测到链断裂: chain %s 残基 %d–%d CA–CA=%.2f Å，已插入 TER",
                        key[0],
                        pk[1],
                        key[1],
                        _dist(ca0, ca1),
                    )
        out.extend(atoms_of[key])

    # 保留非原子行中的 END 等
    for ln in other:
        if ln.startswith("END"):
            out.append(ln if ln.endswith("\n") else ln + "\n")
    if n_ter:
        logger.info("共插入 %d 处 TER（空间断链）", n_ter)
    return out


def fix_terminal_atoms_by_segment(lines: list[str]) -> list[str]:
    """按 TER/链片段修正末端：仅片段首尾保留 N/C 端特殊原子。

    片段内部残基去掉 H1/H2/H3/OXT，避免 Amber 模板找不到原子类型。
    """
    segments: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if ln.startswith("TER"):
            if cur:
                segments.append(cur)
                cur = []
            segments.append([ln if ln.endswith("\n") else ln + "\n"])
            continue
        cur.append(ln if ln.endswith("\n") else ln + "\n")
    if cur:
        segments.append(cur)

    out: list[str] = []
    for seg in segments:
        if len(seg) == 1 and seg[0].startswith("TER"):
            out.extend(seg)
            continue
        # 片段内残基顺序
        keys: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for ln in seg:
            at = _parse_atom_line(ln)
            if at is None:
                continue
            key = (at["chain"], at["resi"])
            if key not in seen:
                seen.add(key)
                keys.append(key)
        if not keys:
            out.extend(seg)
            continue
        n_term = keys[0]
        c_term = keys[-1]
        for ln in seg:
            at = _parse_atom_line(ln)
            if at is None:
                out.append(ln)
                continue
            key = (at["chain"], at["resi"])
            name = at["name"].upper()
            # 非 N 端：去掉铵端氢
            if key != n_term and name in {"H1", "H2", "H3", "HT1", "HT2", "HT3"}:
                continue
            # 非 C 端：去掉 OXT
            if key != c_term and name in {"OXT", "O2"}:
                continue
            # 非 N 端：H → H（保留）；N 端 PDBFixer 的 H 可改为 H1
            if key == n_term and name == "H":
                ln = ln[:12] + " H1 " + ln[16:]
            out.append(ln if ln.endswith("\n") else ln + "\n")
    return out


def residue_has_ca(atom_lines: list[str]) -> bool:
    """残基是否已有标准 CA 原子名。"""
    for ln in atom_lines:
        at = _parse_atom_line(ln)
        if at and at["name"] == "CA":
            return True
    return False


def _bond_graph(
    atoms: list[dict[str, Any]],
    conect: dict[int, list[int]] | None = None,
) -> dict[int, list[int]]:
    """构建重原子键图：优先 CONECT，否则按距离。"""
    g: dict[int, list[int]] = defaultdict(list)
    # atoms[i] 可能带 serial（PDB 序号），用于 CONECT
    serial_to_i = {}
    for i, a in enumerate(atoms):
        try:
            serial_to_i[int(str(a.get("serial", "")).strip())] = i
        except ValueError:
            pass

    if conect and serial_to_i:
        for s, nbrs in conect.items():
            if s not in serial_to_i:
                continue
            i = serial_to_i[s]
            if atoms[i]["element"] == "H":
                continue
            for t in nbrs:
                if t not in serial_to_i:
                    continue
                j = serial_to_i[t]
                if atoms[j]["element"] == "H":
                    continue
                if j not in g[i]:
                    g[i].append(j)
                if i not in g[j]:
                    g[j].append(i)
        if g:
            return g

    heavy = [i for i, a in enumerate(atoms) if a["element"] != "H"]
    for ii, i in enumerate(heavy):
        for j in heavy[ii + 1 :]:
            if _dist(atoms[i], atoms[j]) <= _BOND_A:
                g[i].append(j)
                g[j].append(i)
    return g


def _parse_conect(lines: list[str]) -> dict[int, list[int]]:
    """解析 CONECT 为 serial → 邻居 serial 列表。"""
    g: dict[int, list[int]] = defaultdict(list)
    for ln in lines:
        if not ln.startswith("CONECT"):
            continue
        parts = ln.split()
        if len(parts) < 3:
            continue
        try:
            a = int(parts[1])
            for p in parts[2:]:
                b = int(p)
                g[a].append(b)
                g[b].append(a)
        except ValueError:
            continue
    return g


def _rename_one_residue(
    atoms: list[dict[str, Any]],
    conect: dict[int, list[int]] | None = None,
) -> list[dict[str, Any]]:
    """基于元素与键连为单个标准氨基酸重命名原子（无法识别则原样返回）。"""
    if not atoms:
        return atoms
    rn = atoms[0]["resname"]
    if rn not in _STD_AA:
        return atoms
    # 已有 CA 则认为命名基本可用
    if any(a["name"] == "CA" for a in atoms):
        # 仅修常见错名：MET 的 S→SD
        for a in atoms:
            if rn == "MET" and a["element"] == "S" and a["name"] == "S":
                a["name"] = "SD"
            if rn == "CYS" and a["element"] == "S" and a["name"] == "S":
                a["name"] = "SG"
        return atoms

    g = _bond_graph(atoms, conect)
    n_idx = [i for i, a in enumerate(atoms) if a["element"] == "N"]
    o_idx = [i for i, a in enumerate(atoms) if a["element"] == "O"]
    c_idx = [i for i, a in enumerate(atoms) if a["element"] == "C"]
    s_idx = [i for i, a in enumerate(atoms) if a["element"] == "S"]
    if not n_idx or not c_idx or not o_idx:
        return atoms

    # 主链 N–CA–C(=O)：避免把 ASP/GLU 侧链羧基碳当成羰基 C
    backbone_hits: list[tuple[int, int, int, list[int]]] = []
    for ni in n_idx:
        for ca_i in g.get(ni, []):
            if atoms[ca_i]["element"] != "C":
                continue
            for c_i in g.get(ca_i, []):
                if atoms[c_i]["element"] != "C" or c_i == ni:
                    continue
                o_neis = [
                    k for k in g.get(c_i, []) if atoms[k]["element"] == "O"
                ]
                if not o_neis:
                    continue
                # CA 还应连有非 C 的骨架特征；排除 N–C–C 误路径时用氧数量排序
                backbone_hits.append((ni, ca_i, c_i, o_neis))

    if not backbone_hits:
        return atoms

    def _bb_score(item: tuple[int, int, int, list[int]]) -> tuple:
        """优先：C 上氧较少（侧链羧基常为 2，主链多为 1；C 端可为 2）。"""
        ni, ca_i, c_i, o_neis = item
        # CA 的碳邻居数（主链 CA 常连 C+CB，或 GLY 仅 C）
        n_c = sum(1 for j in g.get(ca_i, []) if atoms[j]["element"] == "C")
        # 主链 C 还应只连 CA（及可能下一残基 N）；侧链 CG 连 CB
        return (len(o_neis) > 2, abs(n_c - 2), len(o_neis), ni, ca_i, c_i)

    backbone_hits.sort(key=_bb_score)
    bb_n, ca, carbonyl_c, o_on_c = backbone_hits[0]
    o_on_c = sorted(o_on_c, key=lambda j: _dist(atoms[j], atoms[carbonyl_c]))
    carbonyl_o = o_on_c[0]

    names: dict[int, str | None] = {i: None for i in range(len(atoms))}
    names[bb_n] = "N"
    names[ca] = "CA"
    names[carbonyl_c] = "C"
    names[carbonyl_o] = "O"
    # 仅当该残基像 C 端（主链 C 上有第二氧）时标 OXT，避免误标 ASP OD
    if len(o_on_c) > 1:
        names[o_on_c[1]] = "OXT"

    # CB：与 CA 相连且非 C 的碳
    cb = None
    for j in g.get(ca, []):
        if atoms[j]["element"] == "C" and names.get(j) is None:
            cb = j
            names[j] = "CB"
            break

    # 侧链按残基类型启发式命名
    used_c = {ca, carbonyl_c, cb}
    used_c.discard(None)

    def _next_carbons(start: int | None) -> list[int]:
        if start is None:
            return []
        return [
            j
            for j in g.get(start, [])
            if atoms[j]["element"] == "C" and names.get(j) is None
        ]

    if rn == "MET":
        # CB-CG-SD-CE
        cgs = _next_carbons(cb)
        if cgs:
            names[cgs[0]] = "CG"
            for j in g.get(cgs[0], []):
                if atoms[j]["element"] == "S":
                    names[j] = "SD"
                    for k in g.get(j, []):
                        if atoms[k]["element"] == "C" and names.get(k) is None:
                            names[k] = "CE"
        for si in s_idx:
            if names.get(si) is None:
                names[si] = "SD"
    elif rn == "CYS":
        for si in s_idx:
            names[si] = "SG"
    elif rn in {"SER", "THR"}:
        for oi in o_idx:
            if names.get(oi) is None:
                names[oi] = "OG" if rn == "SER" else "OG1"
        if rn == "THR":
            for j in _next_carbons(cb):
                if names.get(j) is None:
                    names[j] = "CG2"
                    break
    elif rn == "VAL":
        cgs = _next_carbons(cb)
        for k, j in enumerate(cgs[:2]):
            names[j] = "CG1" if k == 0 else "CG2"
    elif rn == "LEU":
        cgs = _next_carbons(cb)
        if cgs:
            names[cgs[0]] = "CG"
            cds = _next_carbons(cgs[0])
            for k, j in enumerate(cds[:2]):
                names[j] = "CD1" if k == 0 else "CD2"
    elif rn == "ILE":
        cgs = _next_carbons(cb)
        # CG1 在主侧链，CG2 甲基
        if len(cgs) >= 2:
            # 邻居更多的为 CG1
            cgs_sorted = sorted(cgs, key=lambda i: -len(g.get(i, [])))
            names[cgs_sorted[0]] = "CG1"
            names[cgs_sorted[1]] = "CG2"
            cds = _next_carbons(cgs_sorted[0])
            if cds:
                names[cds[0]] = "CD1"
        elif len(cgs) == 1:
            names[cgs[0]] = "CG1"
    elif rn in {"ASP", "ASN"}:
        cgs = _next_carbons(cb)
        if cgs:
            names[cgs[0]] = "CG"
            # 其余 O / N
            for j in g.get(cgs[0], []):
                if atoms[j]["element"] == "O" and names.get(j) is None:
                    if names.get(j) is None:
                        # 两个氧
                        pass
            o_side = [
                j
                for j in g.get(cgs[0], [])
                if atoms[j]["element"] == "O" and names.get(j) is None
            ]
            for k, j in enumerate(o_side[:2]):
                names[j] = "OD1" if k == 0 else "OD2"
            for j in g.get(cgs[0], []):
                if atoms[j]["element"] == "N" and names.get(j) is None:
                    names[j] = "ND2"
    elif rn in {"GLU", "GLN"}:
        cgs = _next_carbons(cb)
        if cgs:
            names[cgs[0]] = "CG"
            cds = _next_carbons(cgs[0])
            if cds:
                names[cds[0]] = "CD"
                o_side = [
                    j
                    for j in g.get(cds[0], [])
                    if atoms[j]["element"] == "O" and names.get(j) is None
                ]
                for k, j in enumerate(o_side[:2]):
                    names[j] = "OE1" if k == 0 else "OE2"
                for j in g.get(cds[0], []):
                    if atoms[j]["element"] == "N" and names.get(j) is None:
                        names[j] = "NE2"
    elif rn == "LYS":
        cur = cb
        for lab in ("CG", "CD", "CE"):
            nxt = _next_carbons(cur)
            if not nxt:
                break
            names[nxt[0]] = lab
            cur = nxt[0]
        for j in n_idx:
            if names.get(j) is None:
                names[j] = "NZ"
    elif rn == "ARG":
        cur = cb
        for lab in ("CG", "CD"):
            nxt = _next_carbons(cur)
            if not nxt:
                break
            names[nxt[0]] = lab
            cur = nxt[0]
        # NE, CZ, NH1, NH2
        for j in n_idx:
            if names.get(j) is None:
                # 与 CD 相连为 NE
                if cur is not None and j in g.get(cur, []):
                    names[j] = "NE"
        ne = next((j for j, n in names.items() if n == "NE"), None)
        if ne is not None:
            for j in g.get(ne, []):
                if atoms[j]["element"] == "C" and names.get(j) is None:
                    names[j] = "CZ"
                    for k in g.get(j, []):
                        if atoms[k]["element"] == "N" and names.get(k) is None:
                            # NH1/NH2
                            pass
                    nhs = [
                        k
                        for k in g.get(j, [])
                        if atoms[k]["element"] == "N" and names.get(k) is None
                    ]
                    for t, k in enumerate(nhs[:2]):
                        names[k] = "NH1" if t == 0 else "NH2"
        for j in n_idx:
            if names.get(j) is None:
                names[j] = "NH2"
    elif rn in {"PHE", "TYR", "TRP", "HIS", "HID", "HIE", "HIP", "HSD", "HSE", "HSP"}:
        # 芳香侧链：能认出 CB/CG 即可，环原子按遍历编号
        cgs = _next_carbons(cb)
        if cgs:
            names[cgs[0]] = "CG"
            ring = [
                j
                for j in g.get(cgs[0], [])
                if atoms[j]["element"] in {"C", "N"} and names.get(j) is None
            ]
            labels_phe = ["CD1", "CD2", "CE1", "CE2", "CZ"]
            labels_his = ["ND1", "CD2", "CE1", "NE2"]
            labels = labels_his if rn.startswith("HI") or rn in {"HID", "HIE", "HIP", "HSD", "HSE", "HSP"} else labels_phe
            # BFS 从 CG
            q = [cgs[0]]
            seen = {cgs[0], ca, cb, carbonyl_c}
            seen.discard(None)
            order: list[int] = []
            while q:
                u = q.pop(0)
                for v in g.get(u, []):
                    if v in seen or atoms[v]["element"] == "H":
                        continue
                    if names.get(v) is not None:
                        continue
                    seen.add(v)
                    order.append(v)
                    q.append(v)
            if rn == "TYR":
                for j in o_idx:
                    if names.get(j) is None:
                        names[j] = "OH"
            if rn == "TRP":
                trp_labs = ["CD1", "CD2", "NE1", "CE2", "CE3", "CZ2", "CZ3", "CH2"]
                for lab, j in zip(trp_labs, order):
                    names[j] = lab
            else:
                li = 0
                for j in order:
                    if li >= len(labels):
                        break
                    if atoms[j]["element"] == "N" and rn.startswith("H"):
                        # HIS 氮
                        if "ND1" not in names.values():
                            names[j] = "ND1"
                        else:
                            names[j] = "NE2"
                    else:
                        names[j] = labels[li]
                        li += 1
    elif rn == "ALA":
        pass  # 仅 CB
    elif rn == "GLY":
        # GLY 无 CB：若误将侧链碳标为 CB 则取消
        if cb is not None:
            names[cb] = None

    # 氢：按所连重原子命名；N 上 1 个 H→H（链中），3 个→H1/H2/H3（N 端）
    h_parent: dict[int, str | None] = {}
    for i, a in enumerate(atoms):
        if a["element"] != "H":
            continue
        best_j = None
        best_d = 1.35
        for j, b in enumerate(atoms):
            if b["element"] == "H":
                continue
            d = _dist(a, b)
            if d < best_d:
                best_d = d
                best_j = j
        h_parent[i] = names.get(best_j) if best_j is not None else None

    n_hyd = [i for i, p in h_parent.items() if p == "N"]
    if len(n_hyd) >= 3:
        for k, i in enumerate(n_hyd[:3]):
            names[i] = ("H1", "H2", "H3")[k]
        for i in n_hyd[3:]:
            names[i] = "H"
    elif len(n_hyd) == 2:
        names[n_hyd[0]] = "H"
        names[n_hyd[1]] = "H2"
    elif len(n_hyd) == 1:
        names[n_hyd[0]] = "H"

    h_counts: dict[str, int] = defaultdict(int)
    drop_h: set[int] = set()
    for i, parent in h_parent.items():
        if parent == "N":
            continue
        # 带电 C 端无羧基氢；去掉连在 O/OXT 上的 H
        if parent in {"O", "OXT"}:
            drop_h.add(i)
            continue
        if parent == "CA":
            h_counts["CA"] += 1
            names[i] = "HA" if h_counts["CA"] == 1 else f"HA{h_counts['CA']}"
        elif parent in {"OG", "OG1", "OH"}:
            names[i] = "HG" if parent != "OH" else "HH"
        elif parent in {"NH1", "NH2"}:
            # Amber：HH11/HH12、HH21/HH22
            h_counts[parent] += 1
            n = h_counts[parent]
            prefix = "HH1" if parent == "NH1" else "HH2"
            names[i] = f"{prefix}{n}"
        elif parent == "NE":
            names[i] = "HE"
        elif parent:
            h_counts[parent] += 1
            if parent.startswith("C") and len(parent) >= 2:
                base = "H" + parent[1:]
            elif parent.startswith("N") and len(parent) >= 2:
                base = "H" + parent[1:]
            elif parent.startswith("S"):
                base = "H" + parent[1:]
            else:
                base = "H"
            n = h_counts[parent]
            names[i] = base if n == 1 else f"{base}{n}"
        else:
            names[i] = "H"

    # 未命名重原子：占位名（仍可能导致 tleap 失败，由上层校验 CA）
    misc_c = 0
    for i, a in enumerate(atoms):
        if names.get(i) is None and a["element"] != "H":
            misc_c += 1
            names[i] = f"{a['element']}{misc_c}" if a["element"] != "C" else f"CX{misc_c}"

    out_atoms: list[dict[str, Any]] = []
    for i, a in enumerate(atoms):
        if i in drop_h:
            continue
        if names.get(i):
            a["name"] = str(names[i])
        out_atoms.append(a)
    return out_atoms


def _format_atom_line(template: str, name: str) -> str:
    """写回 4 列原子名到 PDB 行。"""
    nm = name.strip()
    if len(nm) >= 4:
        padded = nm[:4]
    elif len(nm) == 1:
        padded = f" {nm}  "
    elif len(nm) == 2:
        padded = f" {nm} "
    else:
        padded = f" {nm} " if len(nm) == 3 else f"{nm:4s}"
        if len(nm) == 3:
            padded = f" {nm}"
    ln = template[:12] + f"{padded:4s}" + template[16:]
    return ln if ln.endswith("\n") else ln + "\n"


def rename_std_aa_atoms_in_pdb(pdb_path: str | Path) -> dict[str, Any]:
    """对标准氨基酸按键连重命名原子名；返回统计信息。"""
    fp = Path(pdb_path)
    text = fp.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    conect = _parse_conect(lines)

    # 按残基分组
    groups: list[tuple[tuple[str, int, str], list[int]]] = []
    cur_key = None
    cur_idxs: list[int] = []
    for i, ln in enumerate(lines):
        at = _parse_atom_line(ln)
        if at is None:
            continue
        key = (at["chain"], at["resi"], at["resname"])
        if cur_key is None:
            cur_key = key
            cur_idxs = [i]
        elif key == cur_key:
            cur_idxs.append(i)
        else:
            groups.append((cur_key, cur_idxs))
            cur_key = key
            cur_idxs = [i]
    if cur_key is not None:
        groups.append((cur_key, cur_idxs))

    n_fix = 0
    missing_ca: list[str] = []
    for key, idxs in groups:
        atoms = []
        for i in idxs:
            at = _parse_atom_line(lines[i])
            if at:
                atoms.append(at)
        before_ok = any(a["name"] == "CA" for a in atoms)
        atoms2 = _rename_one_residue(atoms, conect)
        after_ok = any(a["name"] == "CA" for a in atoms2)
        if after_ok and not before_ok:
            n_fix += 1
        if not after_ok:
            missing_ca.append(f"{key[2]}{key[1]}")
        # 可能删除羧基氢，按 serial 写回/删除行
        keep_serials = {int(str(a["serial"]).strip()) for a in atoms2}
        for i in idxs:
            at = _parse_atom_line(lines[i])
            if at is None:
                continue
            try:
                ser = int(str(at["serial"]).strip())
            except ValueError:
                continue
            if ser not in keep_serials:
                lines[i] = ""
                continue
            a = next(x for x in atoms2 if int(str(x["serial"]).strip()) == ser)
            lines[i] = _format_atom_line(lines[i], a["name"])
            if len(lines[i]) >= 78:
                elem = a["element"][:2]
                lines[i] = lines[i][:76] + f"{elem:>2s}" + lines[i][78:]

    fp.write_text("".join(ln for ln in lines if ln), encoding="utf-8")
    info = {
        "residues_renamed": n_fix,
        "path": str(fp),
        "missing_ca": missing_ca,
    }
    if n_fix:
        logger.info("已重命名 %d 个残基的原子名: %s", n_fix, fp.name)
    return info


def assert_peptide_amber_names(pdb_path: str | Path) -> None:
    """校验肽链每个残基均有 CA；失败时抛出中文说明。"""
    residues = []
    fp = Path(pdb_path)
    cur = None
    atoms: list[str] = []
    for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        at = _parse_atom_line(ln + "\n")
        if at is None:
            continue
        key = (at["chain"], at["resi"], at["resname"])
        if cur is None:
            cur = key
            atoms = [ln]
        elif key == cur:
            atoms.append(ln)
        else:
            residues.append((cur, atoms))
            cur = key
            atoms = [ln]
    if cur is not None:
        residues.append((cur, atoms))

    bad = []
    for key, atom_lines in residues:
        if not residue_has_ca(atom_lines):
            bad.append(f"{key[2]}{key[1]}")
    if bad:
        raise ValueError(
            "肽链原子名无法识别为 Amber/ff14SB 标准名（缺少 CA）: "
            + ", ".join(bad)
            + "。请使用保留标准 PDB 原子名（N/CA/C/O/…）的结构；"
            "UFF/对接输出若把碳一律写成 C、硫写成 S，需先还原原子名后再提交。"
        )


def sanitize_protein_lines(lines: list[str]) -> list[str]:
    """蛋白 PDB 行：去 altLoc → 断链 TER → 按片段修末端。"""
    lines1 = resolve_altloc_lines(lines)
    lines2 = insert_ter_at_ca_breaks(lines1)
    return fix_terminal_atoms_by_segment(lines2)


def sanitize_protein_pdb(pdb_path: str | Path, out_path: str | Path | None = None) -> Path:
    """清洗蛋白 PDB 文件。"""
    fp = Path(pdb_path)
    out = Path(out_path) if out_path else fp
    raw = fp.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    cleaned = sanitize_protein_lines(raw)
    out.write_text("".join(cleaned), encoding="utf-8")
    return out
