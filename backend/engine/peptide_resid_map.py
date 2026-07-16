# ==================================================
# 功能说明：将肽设计残基号（如 9001 起）映射为 gro/tpr 中的实际残基号
# 使用方法：python peptide_resid_map.py  # 在任务目录打印 start end；或被分析脚本导入
# 依赖环境：Python 标准库
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

META_NAME = "webmd_cyclic_peptide.json"

# Amber 变体残基名归一化，便于与设计序列比对
_AA_CANON = {
    "HID": "HIS",
    "HIE": "HIS",
    "HIP": "HIS",
    "ASH": "ASP",
    "GLH": "GLU",
    "LYN": "LYS",
    "CYM": "CYS",
}

_STD_AA = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL", "HID", "HIE", "HIP", "ASH", "GLH", "LYN", "CYM",
})


def _canon(rn: str) -> str:
    """将残基名规范为可比较的标准三字母码。"""
    r = (rn or "").strip().upper()
    return _AA_CANON.get(r, r)


def parse_gro_aa_sequence(gro: Path) -> List[Tuple[int, str]]:
    """按出现顺序解析 gro 中的标准氨基酸残基序列 [(resid, resname), ...]。"""
    out: List[Tuple[int, str]] = []
    seen: set[int] = set()
    text = gro.read_text(encoding="utf-8", errors="replace")
    for ln in text.splitlines()[2:]:
        if len(ln) < 10:
            continue
        try:
            resid = int(ln[0:5])
        except ValueError:
            continue
        if resid in seen:
            continue
        rn = ln[5:10].strip().upper()
        if rn not in _STD_AA:
            continue
        seen.add(resid)
        out.append((resid, rn))
    return out


def find_sequence_window(
    residues: Sequence[Tuple[int, str]],
    sequence: Sequence[str],
) -> Optional[Tuple[int, int]]:
    """在残基序列中查找与肽序列匹配的连续窗口；多处匹配时取最后一段（肽通常在蛋白之后）。"""
    if not residues or not sequence:
        return None
    target = [_canon(x) for x in sequence]
    names = [_canon(r[1]) for r in residues]
    n = len(target)
    if n <= 0 or n > len(names):
        return None
    hits: List[Tuple[int, int]] = []
    for i in range(len(names) - n + 1):
        if names[i : i + n] == target:
            hits.append((residues[i][0], residues[i + n - 1][0]))
    if not hits:
        return None
    return hits[-1]


def find_gro_path(wd: Path) -> Optional[Path]:
    """在任务目录中定位结构 gro。"""
    for name in ("md.gro", "npt.gro", "system.gro"):
        p = wd / name
        if p.is_file():
            return p
    return None


def resolve_peptide_gmx_range(
    wd: Path | str,
    *,
    update_meta: bool = True,
) -> Optional[Tuple[int, int]]:
    """根据元数据序列在 gro 中定位肽的实际残基号范围。

    设计思路：tleap combine 后残基号会从设计的 9001 起重排为连续编号，
    不能再用 JSON 里的 resid_start/end 做 make_ndx 的 ri 选组；
    改为按氨基酸序列在 gro 中匹配，并写回 resid_gmx_start/end。
    """
    work = Path(wd)
    meta_p = work / META_NAME
    if not meta_p.is_file():
        return None
    try:
        d = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    # 已有成功映射则直接用
    try:
        a0 = int(d.get("resid_gmx_start") or 0)
        b0 = int(d.get("resid_gmx_end") or 0)
        if a0 > 0 and b0 >= a0:
            return a0, b0
    except (TypeError, ValueError):
        pass

    seq = d.get("sequence") or []
    if not isinstance(seq, list) or not seq:
        n = int(d.get("n_residues") or 0)
        if n <= 0:
            return None
        # 无序列时无法可靠映射
        return None

    gro = find_gro_path(work)
    if gro is None:
        return None

    residues = parse_gro_aa_sequence(gro)
    hit = find_sequence_window(residues, [str(x) for x in seq])
    if hit is None:
        return None

    a, b = hit
    if update_meta:
        d["resid_gmx_start"] = int(a)
        d["resid_gmx_end"] = int(b)
        d["resid_design_start"] = int(d.get("resid_start", 0) or 0)
        d["resid_design_end"] = int(d.get("resid_end", 0) or 0)
        meta_p.write_text(
            json.dumps(d, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return int(a), int(b)


def peptide_ri_range_for_ndx(wd: Path | str) -> Optional[Tuple[int, int]]:
    """供 make_ndx 使用的肽残基号范围（优先 gro 映射，避免 9001 设计号）。"""
    return resolve_peptide_gmx_range(wd, update_meta=True)


def main() -> int:
    """命令行：在当前目录解析并打印 start end。"""
    wd = Path.cwd()
    if len(sys.argv) > 1:
        wd = Path(sys.argv[1])
    r = peptide_ri_range_for_ndx(wd)
    if r is None:
        print("", end="")
        return 1
    print(f"{r[0]} {r[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
