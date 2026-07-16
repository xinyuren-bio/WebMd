# ==================================================
# 功能说明：小分子形式净电荷自动检测（失败可探测候选，禁止静默改用）
# 使用方法：detect_ligand_charge / probe_working_charges
# 依赖环境：pip install rdkit；Open Babel 可选
# 生成时间：2026-07-16
# ==================================================

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from rdkit import Chem
from rdkit.Chem import rdmolops

logger = logging.getLogger(__name__)

# 探测用常见整数电荷（仅寻找可行方案，不得静默采用）
PROBE_CHARGES = (0, 1, -1, 2, -2, -3, 3, -4, 4)

# 常见 antechamber / sqm 失败模式（用于用户确认前提示）
_DIAG_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "scf_not_converge",
        "SCF 未收敛（SCF did not converge）",
        ("scf did not converge",),
    ),
    (
        "odd_electrons",
        "奇数电子（odd number of electrons）",
        ("odd number of electrons",),
    ),
    (
        "unrecognized_atom",
        "无法识别原子（unrecognized atom）",
        ("unrecognized atom", "unknown atom type", "cannot use atom type"),
    ),
    (
        "bad_geometry",
        "几何异常（bad geometry）",
        ("bad geometry", "distorted geometry", "bond angle"),
    ),
    (
        "bond_type",
        "无法分配键型（cannot assign bond type）",
        ("cannot assign bond type", "could not assign bond type"),
    ),
]


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
    # 失败诊断：须用户先阅读日志再确认电荷
    diagnosis_key: str = "unknown"
    diagnosis_label: str = ""
    sqm_excerpt: str = ""
    antechamber_excerpt: str = ""
    ligand_subdir: str = ""

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
        logger.info("探测可行净电荷 nc=%d", q)
        if run_fn(q):
            found.append(q)
            # 自动采用模式：找到 1 个即可，避免多试浪费时间
            break
    return found


def _safe_ligand_stem(name: str) -> str:
    """将配体文件名主干清洗为目录名（与 ligand 模块一致）。"""
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")
    return (s or "ligand")[:80]


def resolve_ligand_work_subdir(
    work_dir: Path | str,
    ligand_index: int,
    ligand_name: str = "",
) -> Path | None:
    """在任务目录中定位配体工作子目录。"""
    work = Path(work_dir)
    if ligand_name:
        p = work / "ligand" / _safe_ligand_stem(ligand_name)
        if p.is_dir():
            return p
    lig_root = work / "ligand"
    if not lig_root.is_dir():
        return None
    subs = sorted(d for d in lig_root.iterdir() if d.is_dir())
    if not subs:
        return None
    idx = max(0, int(ligand_index) - 1)
    return subs[idx] if idx < len(subs) else subs[0]


def _tail_error_lines(text: str, *, max_lines: int = 30) -> str:
    """提取日志中与失败相关的行；若无则取文件末尾。"""
    if not text.strip():
        return ""
    keys = (
        "error", "fatal", "failed", "converge", "electron",
        "qmcharge", "bond", "unrecognized", "geometry",
    )
    picked: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        low = s.lower()
        if any(k in low for k in keys):
            picked.append(s)
    if picked:
        return "\n".join(picked[-max_lines:])
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-max_lines:])


def diagnose_from_text(text: str) -> tuple[str, str]:
    """从任意日志文本归类常见失败原因。"""
    combined = (text or "").lower()
    for key, label, patterns in _DIAG_RULES:
        if any(p in combined for p in patterns):
            return key, label
    return "unknown", "未归类（请查看下方完整日志）"


def collect_charge_failure_diagnostics(
    lig_dir: Path | str,
    extra_text: str = "",
) -> dict[str, Any]:
    """汇总 sqm.out 与 antechamber 日志末尾，并归类常见失败原因。"""
    lig = Path(lig_dir)
    sqm_text = ""
    # 优先读失败快照，避免探测成功后覆盖原失败日志
    for name in ("sqm_fail.out", "sqm.out"):
        fp = lig / name
        if fp.is_file():
            sqm_text = fp.read_text(encoding="utf-8", errors="replace")
            break

    ante_text = ""
    for name in ("antechamber_fail.log", "antechamber_last.log"):
        fp = lig / name
        if fp.is_file():
            ante_text = fp.read_text(encoding="utf-8", errors="replace")
            break

    diagnosis_key, diagnosis_label = diagnose_from_text(
        sqm_text + "\n" + ante_text + "\n" + (extra_text or ""),
    )

    sqm_excerpt = _tail_error_lines(sqm_text) or "（无 sqm.out 或文件为空）"
    ante_excerpt = ante_text[-2500:].strip() if ante_text else "（无 antechamber 输出日志）"
    # 若磁盘上的失败日志已被成功探测覆盖，回退到任务日志摘录
    extra_excerpt = _tail_error_lines(extra_text or "", max_lines=25)
    looks_success_sqm = (
        "calculation completed" in sqm_text.lower()
        and "odd number" not in sqm_text.lower()
    )
    if diagnosis_key != "unknown" and extra_excerpt:
        if looks_success_sqm or "sqm 摘要" in (extra_text or "").lower():
            sqm_lines = [
                ln for ln in extra_excerpt.splitlines()
                if any(k in ln.lower() for k in (
                    "qmcharge", "odd number", "scf", "fatal", "error", "qmmm",
                ))
            ]
            if sqm_lines:
                sqm_excerpt = "\n".join(sqm_lines[-15:])
        if (not ante_text) or ("returncode=0" in ante_text):
            ante_excerpt = extra_excerpt

    return {
        "diagnosis_key": diagnosis_key,
        "diagnosis_label": diagnosis_label,
        "sqm_excerpt": sqm_excerpt,
        "antechamber_excerpt": ante_excerpt,
    }


def build_charge_confirm_message(
    diagnosis_label: str,
    working: list[int],
    original_charge: int | None,
) -> str:
    """生成须先阅日志再确认电荷的提示文案。"""
    charges = "、".join(str(q) for q in working) if working else "—"
    base = (
        f"配体 AM1-BCC 计算失败"
        f"（{diagnosis_label or '原因待查'}）。"
        f"请先查看 sqm.out 与 antechamber 日志末尾，确认失败原因后再选择净电荷。"
    )
    if original_charge is not None:
        base += f" 原尝试净电荷：{original_charge}。"
    if working:
        base += f" 探测到可行净电荷：{charges}。"
    return base
