# ==================================================
# 功能说明：tleap 构建 Amber 溶剂化体系，acpype 转换为 GROMACS 拓扑
# 使用方法：由 pipeline 调用 build_full_system / convert_to_gromacs
# 依赖环境：AmberTools (tleap) + acpype (pip install acpype)
# 生成时间：2026-07-14
# ==================================================

import logging
import math
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from .env_check import repair_ambertools, resolve_tool_cmd, source_amber_env, tool_env

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

TLEAP_TEMPLATE_BASE = """\
source leaprc.protein.ff14SB
source leaprc.gaff2
source leaprc.water.tip3p

prot = loadpdb {protein_pdb}
{ligand_load_lines}
{ligand_param_lines}

complex = combine {{ prot {ligand_vars} }}
solvatebox complex TIP3PBOX {box_padding}
addions complex Cl- 0
addions complex {cation} 0
{salt_line}
saveamberparm complex {prmtop} {inpcrd}
quit
"""


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


def _estimate_box_volume_A3_coords(
    protein_pdb: str, gaff_mol2_paths: list[str], pad: float,
) -> float:
    """估算溶剂化盒子体积 (Å³)，合并蛋白与全部配体坐标。"""
    coords = _read_coords_pdb(protein_pdb)
    for mp in gaff_mol2_paths:
        coords.extend(_read_coords_mol2(mp))
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
    """由摩尔浓度和体积估算一价盐离子对数（teLeap addIonsRand 需整数）。"""
    if c <= 0 or vol_A3 <= 0:
        return 0
    # 1 Å³ = 10⁻²⁷ L
    n = c * (vol_A3 * 1e-27) * 6.02214076e23
    return max(0, int(round(n)))


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
    """生成 tleap 输入脚本（支持 1~3 个配体与 NaCl/KCl）。

    ligands 每项需含 leap_var、gaff_mol2、frcmod（相对 work_dir 的文件名）。
    """
    salt = _normalize_salt_type(salt_type)
    cation = _SALT_CATION[salt]

    n_ions = 0
    gaff_paths = gaff_mol2_paths or [lg.get("gaff_mol2_path", "") for lg in ligands]
    if ion_conc > 0 and protein_pdb_path and gaff_paths:
        vol = _estimate_box_volume_A3_coords(protein_pdb_path, gaff_paths, box_padding)
        n_ions = _ion_pairs_from_conc(ion_conc, vol)
        logger.info(
            "离子对估算: 盐=%s, 浓度=%.3f M, 体积≈%.0f Å³ → %d 对 %s/Cl-",
            salt.upper(), ion_conc, vol, n_ions, cation,
        )

    if n_ions > 0:
        salt_line = f"addionsrand complex {cation} {n_ions} Cl- {n_ions}"
    else:
        salt_line = "# 跳过加盐（浓度为 0 或估算离子数为 0）"

    load_lines = []
    param_lines = []
    var_names = []
    for lg in ligands:
        v = lg["leap_var"]
        var_names.append(v)
        load_lines.append(f"{v} = loadmol2 {lg['gaff_mol2']}")
        param_lines.append(f"loadamberparams {lg['frcmod']}")

    return TLEAP_TEMPLATE_BASE.format(
        protein_pdb=protein_pdb,
        ligand_load_lines="\n".join(load_lines),
        ligand_param_lines="\n".join(param_lines),
        ligand_vars=" ".join(var_names),
        box_padding=box_padding,
        cation=cation,
        salt_line=salt_line,
        prmtop=prmtop,
        inpcrd=inpcrd,
    )


def _clean_pdb_for_tleap(pdb_path: str, out_path: str) -> str:
    """清理 PDB 以兼容 tleap，写入 out_path。"""
    cleaned_lines = []
    rename = {
        "CYM": "CYS",
        "ASH": "ASP", "GLH": "GLU",
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

    # 修正 PDBFixer 与 Amber 末端模板不一致的氢名 / OXT
    cleaned_lines = _fix_terminal_atoms_for_tleap(cleaned_lines)

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)
    return out_path


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
    """tleap 构建溶剂化 Amber 体系，返回 (prmtop, inpcrd) 路径。

    salt_type: nacl 或 kcl；中和与背景盐阳离子一致。
    ligand_specs 可选，格式 [{gaff_mol2, frcmod, resname}]；未传时使用单配体参数。

    注意：复制到工作目录的配体文件统一命名为 ligN_gaff.mol2 / ligN.frcmod，
    避免原文件名含空格或括号时 tleap 解析失败（如 ligand (1).mol2）。
    """
    # 单配体与多配体统一走同一复制/命名逻辑
    specs = list(ligand_specs) if ligand_specs else [
        {"gaff_mol2": gaff_mol2, "frcmod": frcmod},
    ]
    leap_ligands = []
    copy_srcs = []
    gaff_abs = []
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
        gaff_abs.append(str(gaff_src))

    work = Path(work_dir)

    clean_pdb = _clean_pdb_for_tleap(protein_pdb, str(work / "protein_clean.pdb"))
    logger.info("PDB 已清理: %s", clean_pdb)

    prmtop = work / "system.prmtop"
    inpcrd = work / "system.inpcrd"

    tleap_in = work / "tleap.in"
    tleap_in.write_text(_make_tleap_script(
        protein_pdb=Path(clean_pdb).name,
        ligands=leap_ligands,
        box_padding=box_padding,
        ion_conc=ion_conc,
        prmtop=prmtop.name,
        inpcrd=inpcrd.name,
        protein_pdb_path=str(clean_pdb),
        gaff_mol2_paths=gaff_abs,
        salt_type=salt_type,
    ), encoding="utf-8")

    logger.info("tleap 输入脚本:\n%s", tleap_in.read_text(encoding="utf-8"))

    for src, name in copy_srcs:
        s, d = src, (work / name).resolve()
        if s != d:
            shutil.copy(str(s), str(d))

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
        raise RuntimeError(
            f"tleap 失败。\n\nSTDOUT:\n{r.stdout[-3000:]}\n\nSTDERR:\n{r.stderr[-3000:]}"
        )

    logger.info("Amber 体系构建完成: %s, %s", prmtop.name, inpcrd.name)
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
