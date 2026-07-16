# ==================================================
# 功能说明：小分子形式净电荷自动检测（失败可探测候选，禁止静默改用）
# 使用方法：detect_ligand_charge / probe_working_charges
# 依赖环境：pip install rdkit；Open Babel 可选
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from rdkit import Chem
from rdkit.Chem import rdmolops

logger = logging.getLogger(__name__)

# 探测用常见整数电荷（仅寻找可行方案，不得静默采用）
PROBE_CHARGES = (0, 1, -1, 2, -2, -3, 3, -4, 4)


@dataclass
class LigandChargeResult:
    """配体净电荷自动检测结果。"""

    detected_charge: int | None
    source: str | None
    confidence: str  # high | medium | low | none
    candidates: dict[str, int | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化字典。"""
        return asdict(self)


@dataclass
class ChargeConfirmRequest:
    """需要用户确认净电荷时的载荷。"""

    ligand_index: int
    ligand_name: str
    original_charge: int | None
    working_charges: list[int]
    message: str

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化字典。"""
        return asdict(self)


class ChargeConfirmNeeded(Exception):
    """AM1-BCC 在自动电荷下失败，但探测到其他可行电荷，须用户确认。"""

    def __init__(self, req: ChargeConfirmRequest):
        self.req = req
        super().__init__(req.message)


def _charge_from_rdkit(p: Path) -> int | None:
    """RDKit 形式电荷。"""
    m = Chem.MolFromMol2File(
        str(p), sanitize=True, removeHs=False, cleanupSubstructures=False,
    )
    if m is None:
        m = Chem.MolFromMol2File(
            str(p), sanitize=False, removeHs=False, cleanupSubstructures=False,
        )
        if m is None:
            return None
        try:
            Chem.SanitizeMol(m)
        except Exception:
            pass
    return int(rdmolops.GetFormalCharge(m))


def _charge_from_openbabel(p: Path) -> int | None:
    """Open Babel 总电荷。"""
    try:
        from openbabel import pybel
    except ImportError:
        return None
    mol = next(pybel.readfile("mol2", str(p)), None)
    if mol is None:
        return None
    return int(round(mol.charge))


def detect_ligand_charge(mol2_path: Path) -> LigandChargeResult:
    """自动判断净电荷：优先 RDKit/Open Babel 形式电荷一致结果。"""
    p = Path(mol2_path)
    warnings: list[str] = []
    candidates: dict[str, int | None] = {"rdkit": None, "openbabel": None}

    try:
        candidates["rdkit"] = _charge_from_rdkit(p)
    except Exception as e:
        warnings.append(f"RDKit 检测失败: {e}")

    try:
        candidates["openbabel"] = _charge_from_openbabel(p)
    except Exception as e:
        warnings.append(f"Open Babel 检测失败: {e}")

    rd, ob = candidates["rdkit"], candidates["openbabel"]
    if rd is not None and ob is not None and rd == ob:
        return LigandChargeResult(rd, "rdkit+openbabel", "high", candidates, warnings)
    if rd is not None and ob is not None and rd != ob:
        warnings.append(f"RDKit({rd}) 与 Open Babel({ob}) 不一致，暂用 RDKit")
        return LigandChargeResult(rd, "rdkit", "medium", candidates, warnings)
    if rd is not None:
        return LigandChargeResult(rd, "rdkit", "medium", candidates, warnings)
    if ob is not None:
        return LigandChargeResult(ob, "openbabel", "medium", candidates, warnings)
    warnings.append("未能自动判断净电荷")
    return LigandChargeResult(None, None, "none", candidates, warnings)


def pick_initial_charge(det: LigandChargeResult) -> int | None:
    """选择首次 antechamber 使用的电荷；无法判断时返回 None。"""
    return det.detected_charge


def probe_working_charges(
    run_fn: Callable[[int], bool],
    original: int | None,
) -> list[int]:
    """探测能使 antechamber 成功的整数电荷（不含静默采用）。

    run_fn(nc) -> 是否成功。先跳过 original（已失败），再扫常见电荷。
    """
    found: list[int] = []
    tried = set()
    if original is not None:
        tried.add(int(original))
    for q in PROBE_CHARGES:
        if q in tried:
            continue
        tried.add(q)
        logger.info("探测可行净电荷 nc=%d（仅探测，不自动采用）", q)
        if run_fn(q):
            found.append(q)
            # 找到 1～2 个即可提示用户，避免过长
            if len(found) >= 2:
                break
    return found
