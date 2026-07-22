# ==================================================
# 功能说明：tleap 构建 Amber 溶剂化体系，acpype 转换为 GROMACS 拓扑
# 使用方法：由 pipeline 调用 build_full_system / convert_to_gromacs
# 依赖环境：AmberTools (tleap) + acpype (pip install acpype)
# 生成时间：2026-07-17（两阶段 saveOff/loadOff 加盐）
# ==================================================

import logging
import math
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from .env_check import repair_ambertools, resolve_tool_cmd, source_amber_env, tool_env
from .ion_box import (
    assert_box_volume_consistent,
    build_salt_report,
    format_salt_report,
    log_salt_report,
    plan_salt_pairs,
    read_amber_inpcrd_box,
)
from .pdb_sanitize import sanitize_protein_lines
from .processing_report import add_event

logger = logging.getLogger(__name__)

# C–OXT 超过该距离 (Å) 视为 PDBFixer 错误放置，删除后由 tleap 按模板重建
_OXT_MAX_BOND_A = 2.0


def _pdb_atom_name(line: str) -> str:
    """读取 PDB ATOM/HETATM 行的原子名。"""
    return line[12:16].strip()


def _set_pdb_atom_name(line: str, name: str) -> str:
    """写回 PDB 原子名（列 13–16，右对齐风格兼容常见写法）。"""
    # Amber/PDB 惯例：原子名 ≤3 时从第 14 列起、左边留空
    if len(name) >= 4:
        field = name[:4]
    else:
        field = f" {name:<3s}"
    return line[:12] + field + line[16:]


def _fix_terminal_atoms_for_tleap(lines: list[str]) -> list[str]:
    """修正 N/C 末端原子命名与异常 OXT，使 ff14SB 模板可识别。

    设计思路：
    - OpenMM/PDBFixer 常将质子化 N 端写成 H/H2/H3，而 Amber N* 残基要求 H1/H2/H3；
      若保留 H，tleap 会新建无名类型原子并在 saveAmberParm 时报 FATAL。
    - 仅当同一残基同时存在 H2 与 H3（N 端 NH3+/NPRO 氢签名）时，将 H/HN/HT1 映射为 H1。
    - C 端 OXT 若明显远离羰基 C，删除该原子，交由 tleap 按 C* 模板补全。
    """
    # 按链+残基号分组行号
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, line in enumerate(lines):
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        chain = line[21] if len(line) > 21 else " "
        resnum = line[22:26].strip()
        groups[(chain, resnum)].append(i)

    rename_idx: dict[int, str] = {}
    drop_idx: set[int] = set()

    for key, idxs in groups.items():
        names = {_pdb_atom_name(lines[i]): i for i in idxs}

        # N 端：H/H2/H3 → H1/H2/H3（兼容 HN、HT1）
        has_h2 = "H2" in names or "HT2" in names
        has_h3 = "H3" in names or "HT3" in names
        if has_h2 and has_h3 and "H1" not in names and "HT1" not in names:
            for old in ("H", "HN", "HT1"):
                if old in names:
                    rename_idx[names[old]] = "H1"
                    logger.info(
                        "N 端氢命名修正: 链%s 残基%s %s → H1",
                        key[0], key[1], old,
                    )
                    break
        # 其它常见 PDB 命名 → Amber H1/H2/H3
        for old, new in (("HT1", "H1"), ("HT2", "H2"), ("HT3", "H3")):
            if old in names and new not in names:
                rename_idx[names[old]] = new
                logger.info(
                    "N 端氢命名修正: 链%s 残基%s %s → %s",
                    key[0], key[1], old, new,
                )

        # C 端：异常远的 OXT 删除，避免 teLeap 保留坏坐标
        if "OXT" in names and "C" in names:
            c_line = lines[names["C"]]
            oxt_line = lines[names["OXT"]]
            c_xyz = (
                float(c_line[30:38]),
                float(c_line[38:46]),
                float(c_line[46:54]),
            )
            o_xyz = (
                float(oxt_line[30:38]),
                float(oxt_line[38:46]),
                float(oxt_line[46:54]),
            )
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(c_xyz, o_xyz)))
            if dist > _OXT_MAX_BOND_A:
                drop_idx.add(names["OXT"])
                logger.warning(
                    "删除异常 OXT: 链%s 残基%s (C–OXT=%.3f Å > %.1f)，交由 tleap 重建",
                    key[0], key[1], dist, _OXT_MAX_BOND_A,
                )
            # 带电 C 端模板无羧基氢；PDBFixer 有时写出 HC，tleap 会 FATAL
            for bad_h in ("HC", "HOXT", "HXT"):
                if bad_h in names:
                    drop_idx.add(names[bad_h])
                    logger.info(
                        "删除 C 端非法氢: 链%s 残基%s %s",
                        key[0], key[1], bad_h,
                    )

    out: list[str] = []
    for i, line in enumerate(lines):
        if i in drop_idx:
            continue
        if i in rename_idx:
            line = _set_pdb_atom_name(line, rename_idx[i])
        out.append(line)
    return out

# 盐种类 → Amber 阳离子残基名（阴离子均为 Cl-）
_SALT_CATION = {
    "nacl": "Na+",
    "kcl": "K+",
}

# 第一阶段：仅溶剂化，不加盐（盒体积以本阶段 inpcrd 为准）
TLEAP_TEMPLATE_SOLVATE_MOL2 = """\
source leaprc.protein.ff14SB
source leaprc.gaff2
source leaprc.water.tip3p

prot = loadpdb {protein_pdb}
{ligand_load_lines}
{ligand_param_lines}

complex = combine {{ prot {ligand_vars} }}
solvatebox complex TIP3PBOX {box_padding}
saveOff complex {solv_off}
saveamberparm complex {prmtop} {inpcrd}
quit
"""

TLEAP_TEMPLATE_SOLVATE_CYCLIC = """\
source leaprc.protein.ff14SB
source leaprc.water.tip3p

prot = loadpdb {protein_pdb}
cyc = loadPdbUsingSeq {cyclic_pdb} {leap_seq}
bond cyc.{resid_start}.N cyc.{resid_end}.C

complex = combine {{ prot cyc }}
solvatebox complex TIP3PBOX {box_padding}
saveOff complex {solv_off}
saveamberparm complex {prmtop} {inpcrd}
quit
"""

TLEAP_TEMPLATE_SOLVATE_LINEAR = """\
source leaprc.protein.ff14SB
source leaprc.water.tip3p

prot = loadpdb {protein_pdb}
pep = loadpdb {peptide_pdb}

complex = combine {{ prot pep }}
solvatebox complex TIP3PBOX {box_padding}
saveOff complex {solv_off}
saveamberparm complex {prmtop} {inpcrd}
quit
"""

# 第二阶段：本机 teLeap 无 loadAmberParm，用 saveOff/loadOff 复用同一溶剂化 unit
# loadOff 后 unit 名仍为 complex；须重新 loadamberparams，否则 GAFF 角参数会丢失
TLEAP_TEMPLATE_ADD_IONS = """\
source leaprc.protein.ff14SB
source leaprc.gaff2
source leaprc.water.tip3p
{ligand_param_lines}

loadoff {solv_off}
addions complex Cl- 0
addions complex {cation} 0
{salt_line}
saveamberparm complex {prmtop} {inpcrd}
quit
"""

# 第一阶段写出的 OFF 库名（第二阶段 loadoff 读入）
_SOLVATED_OFF = "solvated.lib"

# 兼容旧名（避免外部引用断裂）
TLEAP_TEMPLATE_BASE = TLEAP_TEMPLATE_SOLVATE_MOL2
TLEAP_TEMPLATE_CYCLIC = TLEAP_TEMPLATE_SOLVATE_CYCLIC
TLEAP_TEMPLATE_LINEAR = TLEAP_TEMPLATE_SOLVATE_LINEAR


def _normalize_salt_type(salt_type: str) -> str:
    """规范化盐种类为 nacl/kcl；未知值回退 nacl。"""
    key = (salt_type or "nacl").strip().lower()
    if key not in _SALT_CATION:
        logger.warning("未知盐种类 %s，回退为 NaCl", salt_type)
        return "nacl"
    return key


def _read_coords_pdb(p: str) -> list[tuple[float, float, float]]:
    """从 PDB 读取原子坐标。"""
    coords = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(("ATOM", "HETATM")):
                coords.append((
                    float(line[30:38]), float(line[38:46]), float(line[46:54]),
                ))
    return coords


def _read_coords_mol2(p: str) -> list[tuple[float, float, float]]:
    """从 MOL2 读取原子坐标。"""
    coords = []
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
            if not in_atom or not s:
                continue
            parts = s.split()
            if len(parts) >= 4:
                try:
                    coords.append((float(parts[2]), float(parts[3]), float(parts[4])))
                except ValueError:
                    pass
    return coords


def _read_coords_any(p: str) -> list[tuple[float, float, float]]:
    """按扩展名读取 PDB 或 MOL2 坐标。"""
    s = str(p).lower()
    if s.endswith(".pdb"):
        return _read_coords_pdb(p)
    return _read_coords_mol2(p)


def _estimate_box_volume_A3_coords(
    protein_pdb: str, ligand_paths: list[str], pad: float,
) -> float:
    """估算溶剂化盒子体积 (Å³)，合并蛋白与全部配体/环肽坐标。"""
    coords = _read_coords_pdb(protein_pdb)
    for mp in ligand_paths:
        coords.extend(_read_coords_any(mp))
    if not coords:
        return 0.0
    xs, ys, zs = zip(*coords)
    dx = max(xs) - min(xs) + 2 * pad
    dy = max(ys) - min(ys) + 2 * pad
    dz = max(zs) - min(zs) + 2 * pad
    return dx * dy * dz


def _estimate_box_volume_A3(
    protein_pdb: str, gaff_mol2: str, pad: float,
) -> float:
    """估算溶剂化盒子体积 (Å³)，与 tleap solvatebox TIP3PBOX 逻辑一致。"""
    return _estimate_box_volume_A3_coords(protein_pdb, [gaff_mol2], pad)


def _ion_pairs_from_conc(c: float, vol_A3: float) -> int:
    """兼容旧接口：由浓度与体积估算一价盐对数。"""
    from .ion_box import ion_pairs_from_conc
    return ion_pairs_from_conc(c, vol_A3)


def _salt_line_extra_pairs(cation: str, n_pair: int) -> str:
    """生成额外盐对的 addionsrand 行（中和已由 addions ... 0 完成）。"""
    if n_pair > 0:
        return f"addionsrand complex {cation} {n_pair} Cl- {n_pair}"
    return "# 跳过额外加盐（目标浓度为 0 或 N_pair=0）"


def _write_stage1_mol2_script(
    protein_pdb_name: str,
    ligands: list[dict],
    box_padding: float,
    solv_prmtop: str,
    solv_inpcrd: str,
) -> str:
    """生成第一阶段（仅溶剂化）tleap 脚本。"""
    load_lines = []
    param_lines = []
    var_names = []
    for lg in ligands:
        v = lg["leap_var"]
        var_names.append(v)
        load_lines.append(f"{v} = loadmol2 {lg['gaff_mol2']}")
        param_lines.append(f"loadamberparams {lg['frcmod']}")
    return TLEAP_TEMPLATE_SOLVATE_MOL2.format(
        protein_pdb=protein_pdb_name,
        ligand_load_lines="\n".join(load_lines),
        ligand_param_lines="\n".join(param_lines),
        ligand_vars=" ".join(var_names),
        box_padding=box_padding,
        solv_off=_SOLVATED_OFF,
        prmtop=solv_prmtop,
        inpcrd=solv_inpcrd,
    )


def _ligand_frcmod_lines(ligands: list[dict] | None) -> str:
    """生成第二阶段需重新加载的 frcmod 行（肽体系可为空）。"""
    if not ligands:
        return ""
    lines = [f"loadamberparams {lg['frcmod']}" for lg in ligands if lg.get("frcmod")]
    return "\n".join(lines)


def _write_stage2_ions_script(
    cation: str,
    n_pair: int,
    prmtop: str,
    inpcrd: str,
    ligand_param_lines: str = "",
) -> str:
    """生成第二阶段（中和 + 额外盐对）tleap 脚本。"""
    params = ligand_param_lines.strip()
    return TLEAP_TEMPLATE_ADD_IONS.format(
        ligand_param_lines=(params + "\n") if params else "",
        solv_off=_SOLVATED_OFF,
        cation=cation,
        salt_line=_salt_line_extra_pairs(cation, n_pair),
        prmtop=prmtop,
        inpcrd=inpcrd,
    )


def _two_stage_solvate_and_ions(
    work: Path,
    stage1_script: str,
    *,
    ion_conc: float,
    salt_type: str,
    ligand_param_lines: str = "",
) -> tuple[Path, Path]:
    """执行两阶段 tleap：溶剂化 → 按实际盒体积加盐，返回最终 prmtop/inpcrd。"""
    salt = _normalize_salt_type(salt_type)
    cation = _SALT_CATION[salt]

    solv_prmtop = work / "solvated.prmtop"
    solv_inpcrd = work / "solvated.inpcrd"
    prmtop = work / "system.prmtop"
    inpcrd = work / "system.inpcrd"

    tleap1 = work / "tleap_stage1_solvate.in"
    tleap1.write_text(stage1_script, encoding="utf-8")
    logger.info("tleap 第一阶段（仅溶剂化）:\n%s", stage1_script)
    _run_tleap(work, tleap1, solv_prmtop, solv_inpcrd)

    box1 = read_amber_inpcrd_box(solv_inpcrd)
    vol1 = box1.volume_A3
    plan = plan_salt_pairs(salt, cation, ion_conc, vol1)
    logger.info(
        "按实际盒体积规划额外盐对: 目标浓度=%.4g M, V=%.3f Å³ (%s), N_pair=%d",
        plan.target_conc_M, vol1,
        "正交" if box1.is_orthogonal else "非正交",
        plan.n_pair,
    )

    stage2 = _write_stage2_ions_script(
        cation, plan.n_pair, prmtop.name, inpcrd.name,
        ligand_param_lines=ligand_param_lines,
    )
    tleap2 = work / "tleap_stage2_ions.in"
    tleap2.write_text(stage2, encoding="utf-8")
    # 保留总入口名，便于排查
    (work / "tleap.in").write_text(
        f"; 两阶段构建：见 {tleap1.name} 与 {tleap2.name}\n" + stage2,
        encoding="utf-8",
    )
    logger.info("tleap 第二阶段（中和+额外盐对）:\n%s", stage2)
    _run_tleap(work, tleap2, prmtop, inpcrd)

    box2 = read_amber_inpcrd_box(inpcrd)
    vol2 = box2.volume_A3
    assert_box_volume_consistent(vol1, vol2)

    # 离子计数暂存到工作目录 JSON，供转 GMX 后写正式报告
    meta = {
        "salt_type": salt,
        "cation": cation,
        "target_conc_M": ion_conc,
        "volume_stage1_A3": vol1,
        "volume_final_A3": vol2,
        "n_pair": plan.n_pair,
        "box_stage1": {
            "lx": box1.lx, "ly": box1.ly, "lz": box1.lz,
            "alpha": box1.alpha, "beta": box1.beta, "gamma": box1.gamma,
        },
    }
    (work / "salt_plan.json").write_text(
        __import__("json").dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Amber 两阶段体系构建完成: %s, %s", prmtop.name, inpcrd.name)
    return prmtop, inpcrd


def finalize_salt_report_from_gmx(work_dir: str | Path) -> None:
    """在 GMX 拓扑就绪后，结合 salt_plan.json 写出最终盐报告。"""
    import json
    from .gmx_prepare import count_ions_in_top

    work = Path(work_dir)
    plan_fp = work / "salt_plan.json"
    top = work / "system.top"
    if not plan_fp.is_file() or not top.is_file():
        logger.warning("缺少 salt_plan.json 或 system.top，跳过盐报告")
        return
    plan = json.loads(plan_fp.read_text(encoding="utf-8"))
    n_cat, n_ani = count_ions_in_top(top, plan.get("cation", "Na+"))
    report = build_salt_report(
        salt_type=plan["salt_type"],
        cation=plan["cation"],
        target_conc_M=float(plan["target_conc_M"]),
        volume_stage1_A3=float(plan["volume_stage1_A3"]),
        volume_final_A3=float(plan["volume_final_A3"]),
        n_pair=int(plan["n_pair"]),
        n_cation_total=n_cat,
        n_anion_total=n_ani,
    )
    log_salt_report(report)
    (work / "SALT_REPORT.txt").write_text(format_salt_report(report), encoding="utf-8")
    add_event(
        "盐离子",
        "溶剂化与离子化",
        (
            f"{report.salt_type.upper()} 目标额外盐 {report.target_conc_M:.4g} M；"
            f"N_pair={int(report.n_pair)}；"
            f"中和阳离子={report.n_neutralize_cation}、阴离子={report.n_neutralize_anion}；"
            f"实际额外盐对浓度={report.actual_pair_conc_M:.4g} M"
        ),
        n_pair=int(report.n_pair),
        n_neutralize_cation=report.n_neutralize_cation,
        n_neutralize_anion=report.n_neutralize_anion,
        salt_type=report.salt_type,
    )


def _make_tleap_script(
    protein_pdb: str,
    ligands: list[dict],
    box_padding: float,
    ion_conc: float,
    prmtop: str,
    inpcrd: str,
    protein_pdb_path: str = "",
    gaff_mol2_paths: list[str] | None = None,
    salt_type: str = "nacl",
) -> str:
    """兼容旧接口：仅生成第一阶段溶剂化脚本（不再预估离子数）。"""
    _ = (ion_conc, protein_pdb_path, gaff_mol2_paths, salt_type, prmtop, inpcrd)
    return _write_stage1_mol2_script(
        protein_pdb, ligands, box_padding, "solvated.prmtop", "solvated.inpcrd",
    )


def _salt_line_for(
    salt_type: str,
    ion_conc: float,
    protein_pdb_path: str,
    ligand_paths: list[str],
    box_padding: float,
) -> tuple[str, str]:
    """兼容旧接口：不再用溶质包围盒估离子；返回阳离子与占位注释。"""
    _ = (ion_conc, protein_pdb_path, ligand_paths, box_padding)
    salt = _normalize_salt_type(salt_type)
    cation = _SALT_CATION[salt]
    return cation, "# 旧接口占位：离子对数改由两阶段实际盒体积计算"


def _clean_pdb_for_tleap(pdb_path: str, out_path: str) -> str:
    """清理 PDB 以兼容 tleap，写入 out_path。"""
    cleaned_lines = []
    rename = {
        "CYM": "CYS",
        # 保留 ASH/GLH：ff14SB 支持质子化 Asp/Glu；若改回 ASP/GLU 会残留 HD2/HE2 导致无类型
        "LYN": "LYS", "HYP": "PRO",
    }

    with open(pdb_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("CONECT") or line.startswith("END"):
                continue
            if line.startswith("ATOM") or line.startswith("HETATM"):
                res = line[17:20].strip()
                if res in rename:
                    line = line[:17] + f"{rename[res]:<3s}" + line[20:]
                if len(line) >= 22 and line[21] == " ":
                    line = line[:21] + "A" + line[22:]
            cleaned_lines.append(line)

    # 空间断链插 TER + 按片段修末端，再修正 N 端氢名 / 异常 OXT
    cleaned_lines = sanitize_protein_lines(cleaned_lines)
    cleaned_lines = _fix_terminal_atoms_for_tleap(cleaned_lines)

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)
    return out_path


def _tleap_error_hint(stdout: str, stderr: str) -> str:
    """从 tleap 输出提取 FATAL/长键警告，生成简短中文提示。"""
    text = f"{stdout}\n{stderr}"
    fatals = [ln.strip() for ln in text.splitlines() if "FATAL" in ln.upper()]
    long_bonds = [
        ln.strip()
        for ln in text.splitlines()
        if "bond of" in ln and "angstroms" in ln
    ]
    hints: list[str] = []
    if any("does not have a type" in f for f in fatals):
        hints.append(
            "原子类型失败：常见原因是 (1) 蛋白空间断链却未分段，断点处出现 H1/H2/H3；"
            "(2) 肽链原子名非 Amber 标准（如碳全为 C、硫为 S 而非 SD）。"
        )
    if long_bonds:
        hints.append(
            f"检测到异常长键 {len(long_bonds)} 处（可能断链或坐标损坏），"
            "系统已尝试插入 TER；若仍失败请检查结构。"
        )
    if fatals:
        hints.append("关键错误: " + " | ".join(fatals[:5]))
    return "\n".join(hints)


def _run_tleap(work: Path, tleap_in: Path, prmtop: Path, inpcrd: Path) -> None:
    """执行 tleap，失败时抛出含 stdout/stderr 的异常。"""
    cmd = ["tleap", "-f", str(tleap_in.name)]
    logger.info("运行 tleap: %s", " ".join(cmd))
    repaired = repair_ambertools()
    if repaired:
        logger.info("tleap 前 AmberTools 补全: %s", ", ".join(repaired))
    r = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(work), env=source_amber_env()
    )
    if r.returncode != 0 or not prmtop.exists() or not inpcrd.exists():
        logger.error("tleap stdout:\n%s", r.stdout[-3000:])
        logger.error("tleap stderr:\n%s", r.stderr[-3000:])
        hint = _tleap_error_hint(r.stdout or "", r.stderr or "")
        raise RuntimeError(
            "tleap 失败。\n"
            + (f"\n【诊断】\n{hint}\n" if hint else "")
            + f"\nSTDOUT:\n{r.stdout[-3000:]}\n\nSTDERR:\n{r.stderr[-3000:]}"
        )


def build_full_system_cyclic(
    protein_pdb: str,
    cyclic_meta: dict,
    work_dir: str,
    box_padding: float = 10.0,
    ion_conc: float = 0.15,
    salt_type: str = "nacl",
) -> tuple[str, str]:
    """用 ff14SB 构建蛋白 + 头尾环肽溶剂化体系（两阶段加盐），返回 (prmtop, inpcrd)。"""
    work = Path(work_dir)
    clean_pdb = _clean_pdb_for_tleap(protein_pdb, str(work / "protein_clean.pdb"))
    cyc_src = Path(cyclic_meta["clean_pdb"]).resolve()
    cyc_name = "cyclic_peptide.pdb"
    cyc_dst = work / cyc_name
    if cyc_src != cyc_dst.resolve():
        shutil.copy(str(cyc_src), str(cyc_dst))

    stage1 = TLEAP_TEMPLATE_SOLVATE_CYCLIC.format(
        protein_pdb=Path(clean_pdb).name,
        cyclic_pdb=cyc_name,
        leap_seq=cyclic_meta["leap_seq"],
        resid_start=int(cyclic_meta["resid_start"]),
        resid_end=int(cyclic_meta["resid_end"]),
        box_padding=box_padding,
        solv_off=_SOLVATED_OFF,
        prmtop="solvated.prmtop",
        inpcrd="solvated.inpcrd",
    )
    prmtop, inpcrd = _two_stage_solvate_and_ions(
        work, stage1, ion_conc=ion_conc, salt_type=salt_type,
    )
    _verify_cyclic_peptide_bond(work, cyclic_meta, prmtop, inpcrd)
    return str(prmtop), str(inpcrd)


def build_full_system_linear(
    protein_pdb: str,
    peptide_meta: dict,
    work_dir: str,
    box_padding: float = 10.0,
    ion_conc: float = 0.15,
    salt_type: str = "nacl",
) -> tuple[str, str]:
    """用 ff14SB 构建蛋白 + 线形肽溶剂化体系（两阶段加盐），返回 (prmtop, inpcrd)。"""
    work = Path(work_dir)
    clean_pdb = _clean_pdb_for_tleap(protein_pdb, str(work / "protein_clean.pdb"))
    pep_src = Path(peptide_meta["clean_pdb"]).resolve()
    pep_name = "linear_peptide.pdb"
    pep_dst = work / pep_name
    if pep_src != pep_dst.resolve():
        shutil.copy(str(pep_src), str(pep_dst))
    # 线形肽 N 端：PDBFixer/重建常写 H/H2/H3，Amber NASP 要 H1/H2/H3，否则 FATAL
    pep_lines = pep_dst.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    pep_dst.write_text(
        "".join(_fix_terminal_atoms_for_tleap(pep_lines)),
        encoding="utf-8",
    )

    stage1 = TLEAP_TEMPLATE_SOLVATE_LINEAR.format(
        protein_pdb=Path(clean_pdb).name,
        peptide_pdb=pep_name,
        box_padding=box_padding,
        solv_off=_SOLVATED_OFF,
        prmtop="solvated.prmtop",
        inpcrd="solvated.inpcrd",
    )
    prmtop, inpcrd = _two_stage_solvate_and_ions(
        work, stage1, ion_conc=ion_conc, salt_type=salt_type,
    )
    return str(prmtop), str(inpcrd)


def _verify_cyclic_peptide_bond(
    work: Path,
    cyclic_meta: dict,
    prmtop: Path,
    inpcrd: Path,
) -> None:
    """验证头尾环肽共价键已写入拓扑，且末端无错误 OXT/多余质子。"""
    resid_a = int(cyclic_meta["resid_start"])
    resid_b = int(cyclic_meta["resid_end"])
    leap_log = work / "leap.log"
    if leap_log.is_file():
        log_txt = leap_log.read_text(encoding="utf-8", errors="replace")
        if "FATAL" in log_txt.upper() and "BOND" in log_txt.upper():
            raise RuntimeError("环肽成环失败：tleap 报告键连接错误，请检查首尾几何。")

    # 优先 ParmEd 检查拓扑中是否存在 N–C 键
    try:
        import parmed as pmd

        parm = pmd.load_file(str(prmtop), str(inpcrd))
        n_atom = None
        c_atom = None
        for res in parm.residues:
            if res.number == resid_a or int(getattr(res, "idx", -1) + 1) == 1:
                pass
            # Amber/ParmEd 残基号可能保留 PDB 编号
            rnum = int(res.number)
            if rnum == resid_a:
                for a in res.atoms:
                    if a.name.strip() == "N":
                        n_atom = a
                    if a.name.strip() in {"H2", "H3", "H1"}:
                        # 链中 N 端不应残留 H2/H3；H1 已在清理时改名，若仍在则告警
                        if a.name.strip() in {"H2", "H3"}:
                            raise RuntimeError(
                                "环肽成环验证失败：N 端仍有多余质子，可能未正确闭环。"
                            )
            if rnum == resid_b:
                for a in res.atoms:
                    if a.name.strip() == "C":
                        c_atom = a
                    if a.name.strip() in {"OXT", "O2", "OT2"}:
                        raise RuntimeError(
                            "环肽成环验证失败：C 端仍有 OXT 等末端原子，可能未正确闭环。"
                        )

        if n_atom is None or c_atom is None:
            # 回退：按残基序列首尾
            cyc_res = [r for r in parm.residues if resid_a <= int(r.number) <= resid_b]
            if len(cyc_res) < 2:
                cyc_res = list(parm.residues)[-int(cyclic_meta["n_residues"]):]
            if len(cyc_res) < 2:
                raise RuntimeError("环肽成环验证失败：拓扑中找不到环肽残基。")
            for a in cyc_res[0].atoms:
                if a.name.strip() == "N":
                    n_atom = a
            for a in cyc_res[-1].atoms:
                if a.name.strip() == "C":
                    c_atom = a
                if a.name.strip() in {"OXT", "O2", "OT2"}:
                    raise RuntimeError(
                        "环肽成环验证失败：C 端仍有末端氧原子，请检查结构。"
                    )

        if n_atom is None or c_atom is None:
            raise RuntimeError("环肽成环验证失败：找不到首尾 N/C 原子。")

        bonded = False
        for b in n_atom.bonds:
            if c_atom in (b.atom1, b.atom2):
                bonded = True
                break
        if not bonded:
            # 键列表遍历兜底
            for b in parm.bonds:
                ids = {b.atom1.idx, b.atom2.idx}
                if n_atom.idx in ids and c_atom.idx in ids:
                    bonded = True
                    break
        if not bonded:
            raise RuntimeError(
                "环肽成环验证失败：拓扑中不存在首尾 N–C 共价键，未生成真正闭环。"
            )

        # 重复原子名检查（同残基内）
        for res in parm.residues:
            rnum = int(res.number)
            if not (resid_a <= rnum <= resid_b):
                continue
            names = [a.name.strip() for a in res.atoms]
            if len(names) != len(set(names)):
                raise RuntimeError(
                    f"环肽成环验证失败：残基 {rnum} 存在重复原子名。"
                )
        logger.info("环肽成环验证通过：残基 %d.N — %d.C 已成键", resid_a, resid_b)
        return
    except RuntimeError:
        raise
    except Exception as e:
        logger.warning("ParmEd 环肽验证不可用，改用坐标距离检查: %s", e)

    # 回退：检查清理后 PDB 首尾 N–C 距离应可成键（< 2.0 Å）
    cyc_pdb = work / "cyclic_peptide.pdb"
    if not cyc_pdb.is_file():
        raise RuntimeError("环肽成环验证失败：缺少环肽坐标文件。")
    n_xyz = None
    c_xyz = None
    for ln in cyc_pdb.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.startswith("ATOM"):
            continue
        rn = int(ln[22:26])
        an = ln[12:16].strip()
        xyz = (float(ln[30:38]), float(ln[38:46]), float(ln[46:54]))
        if rn == resid_a and an == "N":
            n_xyz = xyz
        if rn == resid_b and an == "C":
            c_xyz = xyz
        if rn == resid_b and an in {"OXT", "O2", "OT2"}:
            raise RuntimeError("环肽成环验证失败：结构中仍含 C 端 OXT。")
    if n_xyz is None or c_xyz is None:
        raise RuntimeError("环肽成环验证失败：找不到首尾 N/C 坐标。")
    import math

    d = math.sqrt(sum((a - b) ** 2 for a, b in zip(n_xyz, c_xyz)))
    if d > 2.0:
        raise RuntimeError(
            f"环肽成环验证失败：首尾 N–C 距离 {d:.2f} Å 过大，请调整几何后再上传。"
        )
    logger.info("环肽成环距离检查通过：N–C = %.3f Å（拓扑详检不可用）", d)


def build_full_system(
    protein_pdb: str,
    gaff_mol2: str,
    frcmod: str,
    work_dir: str,
    box_padding: float = 10.0,
    ion_conc: float = 0.15,
    salt_type: str = "nacl",
    ligand_specs: list[dict] | None = None,
) -> tuple[str, str]:
    """tleap 两阶段构建溶剂化 Amber 体系，返回 (prmtop, inpcrd) 路径。

    第一阶段仅 solvatebox；第二阶段按实际盒体积加入中和反离子与额外盐对。
    """
    specs = list(ligand_specs) if ligand_specs else [
        {"gaff_mol2": gaff_mol2, "frcmod": frcmod},
    ]
    leap_ligands = []
    copy_srcs = []
    for i, spec in enumerate(specs, 1):
        gaff_src = Path(spec["gaff_mol2"]).resolve()
        frc_src = Path(spec["frcmod"]).resolve()
        gaff_name = f"lig{i}_gaff.mol2"
        frc_name = f"lig{i}.frcmod"
        leap_ligands.append({
            "leap_var": f"lig{i}",
            "gaff_mol2": gaff_name,
            "frcmod": frc_name,
        })
        copy_srcs.append((gaff_src, gaff_name))
        copy_srcs.append((frc_src, frc_name))

    work = Path(work_dir)
    clean_pdb = _clean_pdb_for_tleap(protein_pdb, str(work / "protein_clean.pdb"))
    logger.info("PDB 已清理: %s", clean_pdb)

    for src, name in copy_srcs:
        s, d = src, (work / name).resolve()
        if s != d:
            shutil.copy(str(s), str(d))

    stage1 = _write_stage1_mol2_script(
        Path(clean_pdb).name, leap_ligands, box_padding,
        "solvated.prmtop", "solvated.inpcrd",
    )
    prmtop, inpcrd = _two_stage_solvate_and_ions(
        work, stage1, ion_conc=ion_conc, salt_type=salt_type,
        ligand_param_lines=_ligand_frcmod_lines(leap_ligands),
    )
    return str(prmtop), str(inpcrd)


def _find_acpype_gmx_outputs(w: Path, b: str) -> tuple[Path, Path] | None:
    """查找 acpype 生成的 GROMACS gro/top（兼容新旧版输出目录）。"""
    candidates = [
        (w / f"{b}.amb2gmx" / f"{b}_GMX.gro", w / f"{b}.amb2gmx" / f"{b}_GMX.top"),  # acpype ≥2023
        (w / f"{b}.acpype" / f"{b}_GMX.gro", w / f"{b}.acpype" / f"{b}_GMX.top"),    # 旧版
    ]
    for gro, top in candidates:
        if gro.is_file() and top.is_file():
            return gro, top
    # 兜底：在工作目录搜索 *_GMX.gro / *_GMX.top
    gros = sorted(w.glob("**/*_GMX.gro"))
    tops = sorted(w.glob("**/*_GMX.top"))
    if gros and tops:
        return gros[0], tops[0]
    return None


def convert_to_gromacs(prmtop: str, inpcrd: str, work_dir: str) -> tuple[str, str]:
    """acpype 将 Amber 拓扑转换为 GROMACS gro/top，返回 (gro, top) 路径。"""
    work = Path(work_dir)
    basename = "system"

    cmd = resolve_tool_cmd("acpype") + [
        "-p", Path(prmtop).name,
        "-x", Path(inpcrd).name,
        "-b", basename,
    ]
    logger.info("运行 acpype: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work), env=tool_env())

    found = _find_acpype_gmx_outputs(work, basename)
    if found is None:
        logger.error("acpype stdout:\n%s", r.stdout[-3000:])
        logger.error("acpype stderr:\n%s", r.stderr[-3000:])
        raise RuntimeError(
            f"acpype 未生成 GROMACS 文件（已搜索 .amb2gmx/ 与 .acpype/）。\n\n"
            f"STDOUT:\n{r.stdout[-3000:]}\n\nSTDERR:\n{r.stderr[-3000:]}"
        )

    gmx_gro, gmx_top = found
    if r.returncode != 0:
        logger.warning("acpype 退出码 %d，但 GROMACS 文件已生成: %s", r.returncode, gmx_gro.parent)

    out_gro = work / "system.gro"
    out_top = work / "system.top"
    shutil.copy(str(gmx_gro), str(out_gro))
    shutil.copy(str(gmx_top), str(out_top))

    logger.info("GROMACS 拓扑已生成: %s, %s (来源: %s)", out_gro.name, out_top.name, gmx_gro.parent.name)
    return str(out_gro), str(out_top)
