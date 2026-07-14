# ==================================================
# 功能说明：使用 antechamber + parmchk2 为配体生成 GAFF2 力场参数
# 使用方法：由 pipeline 调用 parameterize_ligand(mol2_path, work_dir)
# 依赖环境：AmberTools (antechamber, parmchk2); pip install rdkit; Open Babel 可选
# 生成时间：2026-07-14
# ==================================================

import logging
import re
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


def _count_mol2_atoms(p: str) -> int:
    """统计 MOL2 中原子数。"""
    n = 0
    in_atom = False
    with open(p, encoding="utf-8", errors="replace") as f:
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
                if parts and parts[0].isdigit():
                    n += 1
    return n


def _charge_from_mol2_partial_sum(p: str) -> int | None:
    """由 MOL2 原子部分电荷求和推断形式电荷（适用于 USER_CHARGES 全为 0 等情况）。"""
    charges: list[float] = []
    in_atom = False
    with open(p, encoding="utf-8", errors="replace") as f:
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
                if len(parts) >= 9:
                    try:
                        charges.append(float(parts[8]))
                    except ValueError:
                        pass
    if not charges:
        return None
    total = sum(charges)
    if all(abs(c) < 1e-6 for c in charges):
        logger.info("配体电荷 (MOL2 部分电荷全为 0): 0")
        return 0
    q = int(round(total))
    logger.info("配体电荷 (MOL2 部分电荷求和): %d", q)
    return q


def _charge_plausible(q: int, n_atoms: int) -> bool:
    """判断形式电荷是否在合理范围（避免 RDKit 对异常 MOL2 给出离谱值）。"""
    if n_atoms <= 0:
        return False
    # 小分子配体形式电荷通常 |q| <= 6；按原子数略放宽
    lim = max(6, min(12, n_atoms // 5 + 2))
    return abs(q) <= lim


def _detect_mol2_charge(p: str) -> int:
    """检测配体净电荷，供 antechamber -nc 使用。

    优先级：MOL2 部分电荷 → MOL2 头信息 → Open Babel → RDKit（须通过合理性检查）→ 0。
    """
    n_atoms = _count_mol2_atoms(p)
    candidates: list[tuple[str, int | None]] = []

    for fn, name in (
        (_charge_from_mol2_partial_sum, "MOL2 部分电荷"),
        (_charge_from_mol2_header, "MOL2 头信息"),
        (_charge_from_openbabel, "Open Babel"),
        (_charge_from_rdkit, "RDKit"),
    ):
        try:
            q = fn(p)
            candidates.append((name, q))
            if q is None:
                continue
            if _charge_plausible(q, n_atoms):
                logger.info("采用 %s 电荷: %d", name, q)
                return int(q)
            logger.warning(
                "%s 电荷 %d 超出合理范围（原子数 %d），尝试下一来源",
                name, q, n_atoms,
            )
        except Exception as e:
            logger.warning("%s 电荷检测失败: %s", name, e)

    logger.warning("无法可靠推断配体电荷，默认使用 0（中性）")
    return 0


def _format_mol2_atom_line(parts: list[str]) -> str:
    """将 MOL2 ATOM 行字段格式化为固定列宽文本。"""
    charge = parts[8] if len(parts) > 8 else "0.000"
    return (
        f"{int(parts[0]):7d} {parts[1]:<7s} {parts[2]:>9s} {parts[3]:>9s} "
        f"{parts[4]:>9s} {parts[5]:<7s} {parts[6]:>3s} {parts[7]:<7s} {charge}\n"
    )


def _fix_pymol_atom_type(name: str, atype: str) -> str:
    """修复 PyMOL 导出中常见的错误 Tripos 原子类型。

    例如磷原子 PA/PB 被误标为元素型 Pa/Pb（钯/铅），导致 antechamber/sqm 失败。
    """
    nm = (name or "").strip().upper()
    at = (atype or "").strip()
    # 核酸/NTP 磷原子常见命名
    if nm in {"PA", "PB", "PG", "P", "P1", "P2", "P3"} or (
        nm.startswith("P") and nm[1:].isdigit()
    ):
        if at not in {"P.3", "P.2"}:
            return "P.3"
    # 裸元素符号误用（Pa/Pb/Pt 等不是磷酸）
    if at in {"Pa", "Pb", "Pt", "P"} and (
        nm.startswith("P") or "P" in nm[:2]
    ):
        return "P.3"
    return at


def _sanitize_mol2_file(p: Path) -> list[str]:
    """清洗 MOL2：修正错误原子类型，返回已修复项说明。"""
    fixed: list[str] = []
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
                if len(parts) >= 6:
                    old = parts[5]
                    new = _fix_pymol_atom_type(parts[1], old)
                    if new != old:
                        parts[5] = new
                        fixed.append(f"{parts[1]}: {old}→{new}")
                    # 保证字段齐全
                    while len(parts) < 9:
                        parts.append("0.000" if len(parts) == 8 else "1")
                    lines_out.append(_format_mol2_atom_line(parts))
                    continue
            lines_out.append(line)
    if fixed:
        p.write_text("".join(lines_out), encoding="utf-8")
    return fixed


def _count_mol2_hydrogens(p: Path) -> int:
    """统计 MOL2 中氢原子个数。"""
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


def _add_hydrogens_mol2(src: Path, dst: Path) -> bool:
    """为 MOL2 补氢并写出坐标（优先 Open Babel -h，其次 RDKit+obabel）。"""
    env = source_amber_env()

    # 优先：Open Babel CLI 补氢（生产环境最稳）
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        cmd = [exe, "-imol2", str(src), "-omol2", "-O", str(dst), "-h"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            n_before = _count_mol2_hydrogens(src)
            n_after = _count_mol2_hydrogens(dst)
            if n_after > n_before or n_after > 0:
                logger.info(
                    "Open Babel 配体补氢完成: %s → %s（氢原子 %d → %d）",
                    src.name, dst.name, n_before, n_after,
                )
                return True
        logger.warning("Open Babel 补氢失败: %s", (r.stderr or r.stdout or "")[-400:])

    # 次选：RDKit AddHs → SDF → obabel 转 mol2
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
                    n_before = _count_mol2_hydrogens(src)
                    n_after = _count_mol2_hydrogens(dst)
                    logger.info(
                        "RDKit+OpenBabel 配体补氢完成: %s → %s（氢原子 %d → %d）",
                        src.name, dst.name, n_before, n_after,
                    )
                    return True
    except Exception as e:
        logger.warning("RDKit 补氢路径失败: %s", e)

    return False


def _rebuild_mol2_with_obabel(src: Path, dst: Path, add_h: bool = False) -> bool:
    """用 Open Babel 重建键级/原子类型（可选同时补氢）。"""
    env = source_amber_env()
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        cmd = [exe, "-imol2", str(src), "-omol2", "-O", str(dst)]
        if add_h:
            cmd.append("-h")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env)
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            logger.info("Open Babel 已重建 MOL2%s: %s", "（含补氢）" if add_h else "", dst.name)
            return True
    return False


def _set_mol2_resname(p: str, resname: str) -> None:
    """将 MOL2 中 subst_name（残基名）统一改为指定名称（如 LIG1）。"""
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
    with open(p, "w", encoding="utf-8") as f:
        f.writelines(lines_out)


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


def _run_antechamber(
    mol2_in: Path,
    ac_mol2: Path,
    net_charge: int,
    lig_dir: Path,
    env: dict,
) -> tuple[bool, int]:
    """运行 antechamber；失败时返回 (False, charge_used)。"""
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
    if r.returncode == 0:
        return True, net_charge
    err = (r.stderr or r.stdout or "")[-1500:]
    sqm_hint = _read_sqm_hint(lig_dir)
    extra = f"\n\nsqm 摘要:\n{sqm_hint}" if sqm_hint else ""
    logger.error("antechamber 失败 (nc=%d): %s%s", net_charge, err, extra)
    return False, net_charge


def _safe_mol2_stem(name: str) -> str:
    """将配体文件名主干清洗为仅含字母数字下划线，避免空格/括号破坏下游命令。"""
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")
    return (s or "ligand")[:80]


def parameterize_ligand(
    mol2_path: str,
    work_dir: str,
    resname: str | None = None,
    add_hydrogens: bool = True,
) -> tuple[str, str]:
    """antechamber + parmchk2 参数化，返回 (gaff_mol2, frcmod) 路径。"""
    check_external_tools()
    env = source_amber_env()
    work = Path(work_dir)
    # 原始名如 "ligand (1)" 会令 tleap 脚本与目录名冲突，须消毒
    mol2_name = _safe_mol2_stem(Path(mol2_path).name)
    # 每个配体独立子目录，避免 sqm.in/out 等临时文件互相覆盖
    lig_dir = work / "ligand" / mol2_name
    lig_dir.mkdir(parents=True, exist_ok=True)

    mol2_dest = lig_dir / f"{mol2_name}.mol2"
    shutil.copy(mol2_path, str(mol2_dest))

    # 修复 PyMOL 常见错误（如 PA/PB → Pa/Pb）
    fixed_types = _sanitize_mol2_file(mol2_dest)
    if fixed_types:
        logger.info("已修正 MOL2 原子类型: %s", ", ".join(fixed_types[:12]))

    # 默认补氢（PyMOL 拆出的配体常缺氢，否则 AM1-BCC 易失败或电荷偏差）
    if add_hydrogens:
        h_out = lig_dir / f"{mol2_name}_h.mol2"
        if _add_hydrogens_mol2(mol2_dest, h_out):
            _sanitize_mol2_file(h_out)
            mol2_dest = h_out
        else:
            rebuilt = lig_dir / f"{mol2_name}_obabel.mol2"
            if _rebuild_mol2_with_obabel(mol2_dest, rebuilt, add_h=True):
                _sanitize_mol2_file(rebuilt)
                mol2_dest = rebuilt
            else:
                logger.warning("配体补氢未成功，将使用原 MOL2 继续参数化")
    else:
        rebuilt = lig_dir / f"{mol2_name}_obabel.mol2"
        if _rebuild_mol2_with_obabel(mol2_dest, rebuilt, add_h=False):
            _sanitize_mol2_file(rebuilt)
            mol2_dest = rebuilt

    net_charge = _detect_mol2_charge(str(mol2_dest))

    repaired = repair_ambertools()
    if repaired:
        logger.info("antechamber 前 AmberTools 补全: %s", ", ".join(repaired))

    ac_mol2 = lig_dir / f"{mol2_name}_gaff.mol2"
    ok, used_q = _run_antechamber(mol2_dest, ac_mol2, net_charge, lig_dir, env)

    # sqm 电荷/电子数不匹配时，依次尝试常见形式电荷（含核苷酸磷酸典型 -2/-3/-4）
    if not ok:
        fallbacks = [0, 1, -1, 2, -2, -3, 3, -4, 4]
        for q in fallbacks:
            if q == used_q:
                continue
            logger.info("antechamber 重试，改用形式电荷 nc=%d", q)
            ok, used_q = _run_antechamber(mol2_dest, ac_mol2, q, lig_dir, env)
            if ok:
                net_charge = q
                break

    if not ok:
        sqm_hint = _read_sqm_hint(lig_dir)
        msg = (
            "antechamber 失败（AM1-BCC 电荷计算未通过）。\n"
            "常见原因：\n"
            "1) PyMOL 导出 MOL2 原子类型错误（如磷 PA/PB 被写成 Pa/Pb）；\n"
            "2) 形式电荷与分子电子数不匹配；\n"
            "3) 缺氢或键级异常。\n"
            "建议：开启「配体自动补氢」，或用 Open Babel："
            "obabel ligand.pdb -O ligand.mol2 -h。\n"
        )
        if sqm_hint:
            msg += f"\nsqm 输出摘要:\n{sqm_hint}"
        raise RuntimeError(msg)

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

    if resname:
        _set_mol2_resname(str(ac_mol2), resname)

    logger.info(
        "配体 GAFF2 参数化完成: %s (电荷=%d, 残基名=%s, 补氢=%s)",
        mol2_name, net_charge, resname or "-", add_hydrogens,
    )
    return str(ac_mol2), str(frcmod)


def parameterize_ligands(
    mol2_paths: list[str],
    work_dir: str,
    add_hydrogens: bool = True,
) -> list[dict]:
    """批量参数化多个配体，残基名依次为 LIG1、LIG2、LIG3。"""
    if not mol2_paths:
        raise ValueError("至少需要一个 MOL2 文件")
    if len(mol2_paths) > 3:
        raise ValueError("最多支持 3 个配体")
    out = []
    for i, p in enumerate(mol2_paths, 1):
        rn = f"LIG{i}"
        gaff_mol2, frcmod = parameterize_ligand(
            p, work_dir, resname=rn, add_hydrogens=add_hydrogens,
        )
        out.append({
            "index": i,
            "resname": rn,
            "source": Path(p).name,
            "gaff_mol2": gaff_mol2,
            "frcmod": frcmod,
        })
    return out
