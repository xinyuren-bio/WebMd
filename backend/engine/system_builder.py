# ==================================================
# 功能说明：tleap 构建 Amber 溶剂化体系，acpype 转换为 GROMACS 拓扑
# 使用方法：由 pipeline 调用 build_full_system / convert_to_gromacs
# 依赖环境：AmberTools (tleap) + acpype (pip install acpype)
# 生成时间：2026-06-23
# ==================================================

import logging
import shutil
import subprocess
from pathlib import Path

from .env_check import repair_ambertools, resolve_tool_cmd, source_amber_env, tool_env

logger = logging.getLogger(__name__)

TLEAP_TEMPLATE_BASE = """\
source leaprc.protein.ff14SB
source leaprc.gaff2
source leaprc.water.tip3p

prot = loadpdb {protein_pdb}
lig = loadmol2 {gaff_mol2}
loadamberparams {frcmod}

complex = combine {{ prot lig }}
solvatebox complex TIP3PBOX {box_padding}
addions complex Cl- 0
addions complex Na+ 0
{salt_line}
saveamberparm complex {prmtop} {inpcrd}
quit
"""


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


def _estimate_box_volume_A3(
    protein_pdb: str, gaff_mol2: str, pad: float,
) -> float:
    """估算溶剂化盒子体积 (Å³)，与 tleap solvatebox TIP3PBOX 逻辑一致。"""
    coords = _read_coords_pdb(protein_pdb) + _read_coords_mol2(gaff_mol2)
    if not coords:
        return 0.0
    xs, ys, zs = zip(*coords)
    dx = max(xs) - min(xs) + 2 * pad
    dy = max(ys) - min(ys) + 2 * pad
    dz = max(zs) - min(zs) + 2 * pad
    return dx * dy * dz


def _ion_pairs_from_conc(c: float, vol_A3: float) -> int:
    """由摩尔浓度和体积估算 NaCl 离子对数（teLeap addIonsRand 需整数）。"""
    if c <= 0 or vol_A3 <= 0:
        return 0
    # 1 Å³ = 10⁻²⁷ L
    n = c * (vol_A3 * 1e-27) * 6.02214076e23
    return max(0, int(round(n)))


def _make_tleap_script(
    protein_pdb: str,
    gaff_mol2: str,
    frcmod: str,
    box_padding: float,
    ion_conc: float,
    prmtop: str,
    inpcrd: str,
    protein_pdb_path: str = "",
    gaff_mol2_path: str = "",
) -> str:
    """生成 tleap 输入脚本。

    电荷中和：Cl- 0 / Na+ 0 各执行一次，分别处理正/负电荷。
    加盐：teLeap addIonsRand 仅接受整数离子数，由浓度+体积估算。
    """
    n_ions = 0
    if ion_conc > 0 and protein_pdb_path and gaff_mol2_path:
        vol = _estimate_box_volume_A3(protein_pdb_path, gaff_mol2_path, box_padding)
        n_ions = _ion_pairs_from_conc(ion_conc, vol)
        logger.info(
            "离子对估算: 浓度=%.3f M, 体积≈%.0f Å³ → %d 对 Na+/Cl-",
            ion_conc, vol, n_ions,
        )

    if n_ions > 0:
        salt_line = f"addionsrand complex Na+ {n_ions} Cl- {n_ions}"
    else:
        salt_line = "# 跳过加盐（浓度为 0 或估算离子数为 0）"

    return TLEAP_TEMPLATE_BASE.format(
        protein_pdb=protein_pdb,
        gaff_mol2=gaff_mol2,
        frcmod=frcmod,
        box_padding=box_padding,
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
) -> tuple[str, str]:
    """tleap 构建溶剂化 Amber 体系，返回 (prmtop, inpcrd) 路径。"""
    work = Path(work_dir)

    clean_pdb = _clean_pdb_for_tleap(protein_pdb, str(work / "protein_clean.pdb"))
    logger.info("PDB 已清理: %s", clean_pdb)

    prmtop = work / "system.prmtop"
    inpcrd = work / "system.inpcrd"

    tleap_in = work / "tleap.in"
    tleap_in.write_text(_make_tleap_script(
        protein_pdb=Path(clean_pdb).name,
        gaff_mol2=Path(gaff_mol2).name,
        frcmod=Path(frcmod).name,
        box_padding=box_padding,
        ion_conc=ion_conc,
        prmtop=prmtop.name,
        inpcrd=inpcrd.name,
        protein_pdb_path=str(clean_pdb),
        gaff_mol2_path=str(gaff_mol2),
    ), encoding="utf-8")

    logger.info("tleap 输入脚本:\n%s", tleap_in.read_text(encoding="utf-8"))

    # 配体文件在 ligand/ 子目录，需复制到 work_dir；蛋白已直接写入
    for src in [gaff_mol2, frcmod]:
        s, d = Path(src).resolve(), (work / Path(src).name).resolve()
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
