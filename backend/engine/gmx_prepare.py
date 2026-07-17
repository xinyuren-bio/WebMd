# ==================================================
# 功能说明：为 ACPYPE 拓扑注入位置约束（EM/NVT/NPT）、生成温控 index.ndx，并 grompp 验收
# 使用方法：convert_to_gromacs 之后由 pipeline 调用 prepare_gmx_equilibration
# 依赖环境：GROMACS (gmx)；Python 标准库
# 生成时间：2026-07-17
# ==================================================

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .env_check import resolve_tool_cmd, tool_env

logger = logging.getLogger(__name__)

# 默认位置约束力常数 (kJ mol^-1 nm^-2)
_POSRES_FC = (1000.0, 1000.0, 1000.0)

# 标准蛋白残基（含 Amber 质子化变体）
_PROTEIN_RES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "HID", "HIE", "HIP", "ASH", "GLH", "LYN", "CYM", "CYX", "ACE", "NME",
    "HSD", "HSE", "HSP",
}

# 水与离子残基/分子名
_WATER_NAMES = {"WAT", "SOL", "HOH", "TIP3", "TP3", "T3P"}
_ION_NAMES = {
    "NA", "NA+", "SOD", "K", "K+", "POT", "CL", "CL-", "CLA", "MG", "MG2",
    "CA", "CA2", "ZN", "ZN2",
}


@dataclass
class IndexStats:
    """温控分组统计。"""

    n_protein_ligand: int
    n_water_ions: int
    n_system: int


@dataclass
class PosresStats:
    """位置约束统计。"""

    moltype: str
    n_restrained: int
    path: str


def _is_hydrogen_atom(name: str) -> bool:
    """判断是否为氢原子（按 PDB/GROMACS 原子名惯例）。"""
    n = (name or "").strip()
    if not n:
        return False
    # 去掉数字前缀：如 1HG1
    while n and n[0].isdigit():
        n = n[1:]
    return bool(n) and n[0].upper() == "H"


def _parse_top_moleculetypes(top_text: str) -> list[dict]:
    """解析 top 中各 moleculetype 的名称与 [atoms] 行。

    返回 [{name, atoms:[{nr, type, resname, atom, mass}], start, end}]，
    start/end 为该 moleculetype 在全文中的字符范围（含头不含下一节）。
    """
    # 按 [ moleculetype ] 切分
    parts = list(re.finditer(r"^\[\s*moleculetype\s*\]", top_text, re.M | re.I))
    out: list[dict] = []
    for i, m in enumerate(parts):
        start = m.start()
        end = parts[i + 1].start() if i + 1 < len(parts) else _system_section_start(top_text, start)
        block = top_text[start:end]
        name = _moltype_name(block)
        atoms = _parse_atoms_block(block)
        out.append({"name": name, "atoms": atoms, "start": start, "end": end, "block": block})
    return out


def _system_section_start(text: str, after: int) -> int:
    """定位 [ system ] 段起点，否则返回文末。"""
    m = re.search(r"^\[\s*system\s*\]", text[after:], re.M | re.I)
    return after + m.start() if m else len(text)


def _moltype_name(block: str) -> str:
    """从 moleculetype 块读取分子名。"""
    lines = block.splitlines()
    for ln in lines[1:]:
        s = ln.split(";", 1)[0].strip()
        if not s or s.startswith("["):
            if s.startswith("["):
                break
            continue
        return s.split()[0]
    return ""


def _parse_atoms_block(block: str) -> list[dict]:
    """解析 [ atoms ] 节。"""
    atoms: list[dict] = []
    in_atoms = False
    for ln in block.splitlines():
        raw = ln.split(";", 1)[0].rstrip()
        s = raw.strip()
        if s.lower().startswith("[") and "atoms" in s.lower():
            in_atoms = True
            continue
        if s.startswith("[") and in_atoms:
            break
        if not in_atoms or not s:
            continue
        parts = s.split()
        if len(parts) < 5:
            continue
        try:
            nr = int(parts[0])
        except ValueError:
            continue
        # 兼容 acpype: nr type resi res atom cgnr charge mass
        atype = parts[1]
        if len(parts) >= 8:
            resname = parts[3]
            atom = parts[4]
            try:
                mass = float(parts[7])
            except ValueError:
                mass = 0.0
        else:
            resname = parts[2]
            atom = parts[3]
            mass = float(parts[-1]) if parts[-1].replace(".", "", 1).isdigit() else 0.0
        atoms.append({
            "nr": nr,
            "type": atype,
            "resname": resname,
            "atom": atom,
            "mass": mass,
        })
    return atoms


def _parse_molecules_counts(top_text: str) -> list[tuple[str, int]]:
    """解析 [ molecules ] 列表。"""
    m = re.search(r"^\[\s*molecules\s*\](.*)$", top_text, re.M | re.I | re.S)
    if not m:
        raise RuntimeError("拓扑缺少 [ molecules ] 段")
    rows: list[tuple[str, int]] = []
    for ln in m.group(1).splitlines():
        s = ln.split(";", 1)[0].strip()
        if not s or s.startswith("["):
            if s.startswith("["):
                break
            continue
        parts = s.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            rows.append((parts[0], int(parts[1])))
    if not rows:
        raise RuntimeError("[ molecules ] 为空")
    return rows


def _classify_moltype(name: str, atoms: list[dict], ligand_resnames: set[str]) -> str:
    """将 moleculetype 归类为 solute / water / ion / unknown。"""
    n = (name or "").strip()
    nu = n.upper().replace("+", "").replace("-", "")
    if n.upper() in _WATER_NAMES or nu in {"WAT", "SOL", "HOH"}:
        return "water"
    if n.upper() in _ION_NAMES or nu in {"NA", "K", "CL", "MG", "CA", "ZN"}:
        return "ion"
    # 名称带电荷的离子
    if re.fullmatch(r"(NA|K|CL|MG|CA|ZN)[\+\-]?\d*", n.upper()):
        return "ion"

    resnames = {a["resname"].strip().upper() for a in atoms}
    if not resnames:
        # 单原子离子 moleculetype 可能 atoms 里残基名=分子名
        if n.upper() in _ION_NAMES or nu in {"NA", "K", "CL"}:
            return "ion"
        return "unknown"

    if resnames <= _WATER_NAMES:
        return "water"
    if resnames <= {x.upper() for x in _ION_NAMES} or resnames <= {"NA+", "CL-", "K+", "NA", "CL", "K"}:
        return "ion"

    # 溶质：含蛋白残基、配体残基，或 acpype 合并的 system
    lig_u = {x.upper() for x in ligand_resnames}
    if n.lower() == "system" or resnames & (_PROTEIN_RES | lig_u):
        # 若混入水/离子残基则报错
        bad = resnames & (_WATER_NAMES | {x.upper() for x in _ION_NAMES} | {"NA+", "CL-", "K+"})
        # 合并拓扑中偶发读到的头注释不算；真实溶质不应含水
        water_like = resnames & _WATER_NAMES
        if water_like and len(resnames) > 3:
            # 溶质大分子中不应出现 WAT
            raise RuntimeError(
                f"moleculetype「{n}」同时含溶质与水残基 {sorted(water_like)}，无法安全施加位置约束"
            )
        return "solute"

    # 纯配体小分子（全部残基为配体名或未知有机残基）
    if lig_u and resnames <= lig_u:
        return "solute"
    if len(resnames) == 1 and list(resnames)[0] not in _WATER_NAMES:
        # 单独配体 moleculetype（少见）；若明确在配体列表中则溶质
        only = list(resnames)[0]
        if only in lig_u or only.startswith("LIG") or only in {"UNL", "MOL", "DRG"}:
            return "solute"

    return "unknown"


def generate_posres_for_moltype(
    atoms: list[dict],
    out_path: Path,
    fc: tuple[float, float, float] = _POSRES_FC,
) -> int:
    """为 moleculetype 内非氢原子生成位置约束（局部原子编号）。"""
    lines = [
        "; WebMD 自动生成的位置约束（仅非氢原子）",
        "[ position_restraints ]",
        ";  atom  type      fx      fy      fz",
    ]
    n = 0
    fx, fy, fz = fc
    for a in atoms:
        if _is_hydrogen_atom(a["atom"]) or (a["mass"] and a["mass"] < 1.5):
            continue
        lines.append(f"{a['nr']:6d}     1  {fx:.0f}  {fy:.0f}  {fz:.0f}")
        n += 1
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def _insert_posres_include(top_text: str, mol: dict, include_name: str) -> str:
    """在指定 moleculetype 作用域末尾插入 #ifdef POSRES include。"""
    block = mol["block"]
    if "POSRES" in block and include_name in block:
        logger.info("moleculetype %s 已含 POSRES/%s，跳过插入", mol["name"], include_name)
        return top_text

    insert = (
        "\n; WebMD 位置约束（EM/NVT/NPT 通过 -DPOSRES 启用）\n"
        "#ifdef POSRES\n"
        f'#include "{include_name}"\n'
        "#endif\n\n"
    )
    # 插在该 moleculetype 块末尾（下一节之前）
    pos = mol["end"]
    return top_text[:pos] + insert + top_text[pos:]


def ensure_position_restraints(
    work_dir: str | Path,
    ligand_resnames: list[str] | None = None,
) -> list[PosresStats]:
    """为溶质 moleculetype 生成/挂接位置约束；禁止约束水与离子。"""
    work = Path(work_dir)
    top_path = work / "system.top"
    if not top_path.is_file():
        raise FileNotFoundError(f"缺少 system.top: {top_path}")

    lig_set = {x.strip().upper() for x in (ligand_resnames or []) if x.strip()}
    text = top_path.read_text(encoding="utf-8", errors="replace")
    mols = _parse_top_moleculetypes(text)
    if not mols:
        raise RuntimeError("拓扑中未找到 [ moleculetype ]")

    # 先生成全部 posre 文件并记录插入点（自后向前改 top）
    planned: list[tuple[dict, str, int]] = []
    for mol in mols:
        kind = _classify_moltype(mol["name"], mol["atoms"], lig_set)
        if kind in ("water", "ion"):
            continue
        if kind == "unknown":
            raise RuntimeError(
                f"无法判定 moleculetype「{mol['name']}」属于蛋白/配体/水/离子，"
                "已停止以免误约束。请检查拓扑或辅因子。"
            )
        if kind != "solute":
            continue
        if not mol["atoms"]:
            raise RuntimeError(f"溶质 moleculetype「{mol['name']}」无原子")
        safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", mol["name"]) or "solute"
        posre_name = f"posre_{safe}.itp"
        n = generate_posres_for_moltype(mol["atoms"], work / posre_name)
        if n <= 0:
            raise RuntimeError(f"溶质「{mol['name']}」未找到可约束的非氢原子")
        planned.append((mol, posre_name, n))
        logger.info("位置约束: %s → %s (%d 个非氢原子)", mol["name"], posre_name, n)

    if not planned:
        raise RuntimeError("未找到任何溶质 moleculetype，无法生成位置约束")

    stats: list[PosresStats] = []
    for mol, posre_name, n in reversed(planned):
        # 每次插入后重解析以保持字符偏移正确
        mols_now = _parse_top_moleculetypes(text)
        cur = next((m for m in mols_now if m["name"] == mol["name"]), None)
        if cur is None:
            raise RuntimeError(f"插入 POSRES 时丢失 moleculetype「{mol['name']}」")
        text = _insert_posres_include(text, cur, posre_name)
        stats.append(PosresStats(moltype=mol["name"], n_restrained=n, path=str(work / posre_name)))

    top_path.write_text(text, encoding="utf-8")
    return list(reversed(stats))


def _moltype_n_atoms(mols: list[dict], name: str) -> int:
    """按名称查找 moleculetype 原子数。"""
    for m in mols:
        if m["name"] == name:
            return len(m["atoms"])
    raise RuntimeError(f"molecules 引用了未定义的 moleculetype: {name}")


def build_temperature_index(
    work_dir: str | Path,
    ligand_resnames: list[str] | None = None,
) -> IndexStats:
    """生成 index.ndx：Protein_Ligand 与 Water_and_ions（互斥且覆盖全体）。"""
    work = Path(work_dir)
    top_path = work / "system.top"
    gro_path = work / "system.gro"
    text = top_path.read_text(encoding="utf-8", errors="replace")
    mols = _parse_top_moleculetypes(text)
    counts = _parse_molecules_counts(text)
    lig_set = {x.strip().upper() for x in (ligand_resnames or []) if x.strip()}

    # 按 [molecules] 展开全局原子编号
    pl: list[int] = []
    wi: list[int] = []
    gid = 1
    for mol_name, nmol in counts:
        nat = _moltype_n_atoms(mols, mol_name)
        mol = next(m for m in mols if m["name"] == mol_name)
        kind = _classify_moltype(mol_name, mol["atoms"], lig_set)
        if kind == "unknown":
            raise RuntimeError(
                f"无法归类 moleculetype「{mol_name}」（可能为辅因子/HETATM），"
                "请处理后重试；不得默认并入水组。"
            )
        for _ in range(nmol):
            ids = list(range(gid, gid + nat))
            if kind == "solute":
                pl.extend(ids)
            elif kind in ("water", "ion"):
                wi.extend(ids)
            else:
                raise RuntimeError(f"未处理的分类: {kind} ({mol_name})")
            gid += nat

    n_total = gid - 1
    # 与 gro 原子数核对
    if gro_path.is_file():
        gro_n = _count_gro_atoms(gro_path)
        if gro_n != n_total:
            raise RuntimeError(f"index 原子数 {n_total} 与 gro {gro_n} 不一致")

    if not pl:
        raise RuntimeError("Protein_Ligand 组为空")
    if not wi:
        raise RuntimeError("Water_and_ions 组为空")
    if set(pl) & set(wi):
        raise RuntimeError("Protein_Ligand 与 Water_and_ions 存在重叠原子")
    if len(pl) + len(wi) != n_total:
        raise RuntimeError(
            f"温控组未覆盖全体: PL={len(pl)} WI={len(wi)} total={n_total}"
        )

    ndx = work / "index.ndx"
    _write_ndx(ndx, [("Protein_Ligand", pl), ("Water_and_ions", wi), ("System", list(range(1, n_total + 1)))])
    logger.info(
        "index.ndx: Protein_Ligand=%d, Water_and_ions=%d, System=%d",
        len(pl), len(wi), n_total,
    )
    return IndexStats(n_protein_ligand=len(pl), n_water_ions=len(wi), n_system=n_total)


def _count_gro_atoms(fp: Path) -> int:
    """读取 gro 第二行原子数。"""
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 2:
        raise RuntimeError(f"gro 无效: {fp}")
    return int(lines[1].split()[0])


def _write_ndx(path: Path, groups: list[tuple[str, list[int]]]) -> None:
    """写入 GROMACS ndx 文件。"""
    chunks: list[str] = []
    for name, ids in groups:
        chunks.append(f"[ {name} ]")
        row: list[str] = []
        for i, a in enumerate(ids, 1):
            row.append(f"{a:5d}")
            if i % 15 == 0:
                chunks.append(" ".join(row))
                row = []
        if row:
            chunks.append(" ".join(row))
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")


def count_ions_in_top(top_path: Path, cation: str = "Na+") -> tuple[int, int]:
    """从 [ molecules ] 统计阳离子与 Cl- 数量。"""
    text = top_path.read_text(encoding="utf-8", errors="replace")
    rows = _parse_molecules_counts(text)
    want = cation.upper().replace("+", "").replace("-", "")
    if want in ("NA", "SOD"):
        cat_keys = {"NA", "SOD"}
    elif want in ("K", "POT"):
        cat_keys = {"K", "POT"}
    else:
        cat_keys = {want}
    n_cat = n_ani = 0
    for name, n in rows:
        key = name.upper().replace("+", "").replace("-", "")
        if key in cat_keys:
            n_cat += n
        if key in ("CL", "CLA"):
            n_ani += n
    return n_cat, n_ani


def validate_grompp_stages(work_dir: str | Path) -> None:
    """对 EM/NVT/NPT/MD 运行 gmx grompp（不用 -maxwarn）；失败则抛错。

    不真正跑 mdrun：用 system.gro 作为各阶段 -c/-r 的占位坐标，
    仅验证拓扑、POSRES、index 与 MDP 可被 grompp 接受。
    """
    work = Path(work_dir)
    gmx = resolve_tool_cmd("gmx")
    env = tool_env()
    gro = work / "system.gro"
    top = work / "system.top"
    ndx = work / "index.ndx"
    mdp_dir = work / "mdp"
    if not all(p.is_file() for p in (gro, top, ndx)):
        raise RuntimeError("grompp 验收缺少 system.gro / system.top / index.ndx")

    stages = [
        ("em", ["-f", str(mdp_dir / "em.mdp"), "-c", str(gro), "-r", str(gro),
                "-p", str(top), "-n", str(ndx), "-o", str(work / "_check_em.tpr")]),
        ("nvt", ["-f", str(mdp_dir / "nvt.mdp"), "-c", str(gro), "-r", str(gro),
                 "-p", str(top), "-n", str(ndx), "-o", str(work / "_check_nvt.tpr")]),
        ("npt", ["-f", str(mdp_dir / "npt.mdp"), "-c", str(gro), "-r", str(gro),
                 "-p", str(top), "-n", str(ndx), "-o", str(work / "_check_npt.tpr")]),
        ("md", ["-f", str(mdp_dir / "md.mdp"), "-c", str(gro), "-p", str(top),
                "-n", str(ndx), "-o", str(work / "_check_md.tpr")]),
    ]
    for name, args in stages:
        cmd = gmx + ["grompp"] + args
        logger.info("grompp 验收 [%s]: %s", name, " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work), env=env)
        if r.returncode != 0:
            raise RuntimeError(
                f"gmx grompp 验收失败（阶段 {name}，未使用 -maxwarn）。\n"
                f"STDOUT:\n{(r.stdout or '')[-2500:]}\n"
                f"STDERR:\n{(r.stderr or '')[-2500:]}"
            )
        logger.info("grompp 验收通过: %s", name)

    # 清理临时 tpr
    for p in work.glob("_check_*.tpr"):
        try:
            p.unlink()
        except OSError:
            pass


def _parse_gro_atoms(fp: Path) -> tuple[str, list[str], str]:
    """解析 gro：标题、原子行列表、盒行。"""
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 3:
        raise RuntimeError(f"gro 无效: {fp}")
    title = lines[0]
    natom = int(lines[1].split()[0])
    atoms = lines[2:2 + natom]
    if len(atoms) != natom:
        raise RuntimeError(f"gro 原子行数不足: {fp}")
    box = lines[2 + natom] if len(lines) > 2 + natom else "0 0 0"
    return title, atoms, box


def _gro_resname(line: str) -> str:
    """读取 gro 残基名（列 6–10）。"""
    if len(line) >= 10:
        return line[5:10].strip()
    parts = line.split()
    return parts[0][-3:] if parts else ""


def _moltype_key(name: str) -> str:
    """分子名归一化键，用于匹配 gro 残基与 top moleculetype。"""
    return name.upper().replace("+", "").replace("-", "")


def sync_gro_order_and_names_with_top(work_dir: str | Path) -> None:
    """按 system.top 的 [molecules] 顺序重排 system.gro，并同步离子原子名。

    设计思路：ACPYPE 常把 Na+/Cl- 在 gro 中交错排列，而 top 按整块
    CL- 再 NA+ 列出，导致 grompp 报 atom name mismatch。禁止用 -maxwarn
    掩盖；改为按拓扑顺序重建 gro，并用 moleculetype 原子名覆盖离子名。
    """
    work = Path(work_dir)
    top_path = work / "system.top"
    gro_path = work / "system.gro"
    text = top_path.read_text(encoding="utf-8", errors="replace")
    mols = _parse_top_moleculetypes(text)
    counts = _parse_molecules_counts(text)
    title, atom_lines, box = _parse_gro_atoms(gro_path)

    # 按归一化键分桶（溶质合并为 system 桶）
    buckets: dict[str, list[str]] = { _moltype_key(m["name"]): [] for m in mols }
    solute_keys = set()
    for m in mols:
        kind = _classify_moltype(m["name"], m["atoms"], set())
        if kind == "solute":
            solute_keys.add(_moltype_key(m["name"]))

    for line in atom_lines:
        res = _gro_resname(line)
        key = _moltype_key(res)
        if key in {"WAT", "SOL", "HOH", "TIP3", "TP3", "T3P"}:
            # 映射到拓扑中的水 moleculetype 名
            wkey = next((k for k in buckets if k in {"WAT", "SOL", "HOH"}), None)
            if wkey is None:
                raise RuntimeError(f"gro 含水残基 {res}，但 top 无对应水 moleculetype")
            buckets[wkey].append(line)
        elif key in {"NA", "SOD", "K", "POT", "CL", "CLA"}:
            if key not in buckets:
                # top 可能叫 NA+ / CL-
                match = next((k for k in buckets if k == key), None)
                if match is None:
                    raise RuntimeError(f"gro 离子残基 {res} 在 top 中无对应 moleculetype")
                buckets[match].append(line)
            else:
                buckets[key].append(line)
        else:
            # 溶质（含配体残基上的 CL 原子名，残基不是 Cl-）
            if not solute_keys:
                raise RuntimeError("top 中无溶质 moleculetype，无法归类 gro 溶质原子")
            # 通常仅一个 system
            skey = next(iter(solute_keys))
            buckets[skey].append(line)

    # 按 [molecules] 展开，并修正离子原子名
    out_atoms: list[str] = []
    for mol_name, nmol in counts:
        mdef = next(m for m in mols if m["name"] == mol_name)
        nat = len(mdef["atoms"])
        key = _moltype_key(mol_name)
        need = nat * nmol
        got = buckets.get(key, [])
        if len(got) < need:
            raise RuntimeError(
                f"重排 gro 失败：{mol_name} 需要 {need} 原子，gro 桶中仅 {len(got)}"
            )
        chunk = got[:need]
        buckets[key] = got[need:]
        # 按 moleculetype 模板重写原子名（尤其 Na+/Cl-）
        for i_mol in range(nmol):
            for j, a in enumerate(mdef["atoms"]):
                line = chunk[i_mol * nat + j]
                atom = f"{a['atom']:>5s}"[:5]
                resn = f"{a['resname']:>5s}"[:5]
                if len(line) >= 15:
                    new_line = line[:5] + resn + atom + line[15:]
                else:
                    new_line = line
                out_atoms.append(new_line)

    leftover = sum(len(v) for v in buckets.values())
    if leftover:
        raise RuntimeError(f"重排 gro 后仍有 {leftover} 个未归类原子")

    # 重编号原子序号（列 16–20）
    renum: list[str] = []
    for i, line in enumerate(out_atoms, 1):
        if len(line) >= 20:
            renum.append(line[:15] + f"{i:5d}" + line[20:])
        else:
            renum.append(line)

    text_out = title + "\n" + f"{len(renum)}\n" + "\n".join(renum) + "\n" + box + "\n"
    bak = gro_path.with_suffix(".gro.bak_acpype_order")
    if not bak.exists():
        bak.write_text(gro_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    gro_path.write_text(text_out, encoding="utf-8")
    logger.info("已按 top [molecules] 重排 system.gro 并同步离子原子名（备份 %s）", bak.name)


def prepare_gmx_equilibration(
    work_dir: str | Path,
    ligand_resnames: list[str] | None = None,
    *,
    run_grompp_check: bool = True,
) -> dict:
    """同步 gro/top、注入 POSRES、生成 index.ndx，并可选 grompp 验收。"""
    work = Path(work_dir)
    amb = work / "system.amb2gmx"
    if amb.is_dir():
        for extra in amb.glob("posre_*.itp"):
            logger.debug("acpype 附带约束文件: %s", extra.name)

    # 先对齐 gro 与 top，避免 ACPYPE 离子交错导致的 grompp 警告升格为失败
    sync_gro_order_and_names_with_top(work)
    posres = ensure_position_restraints(work, ligand_resnames)
    index = build_temperature_index(work, ligand_resnames)
    if run_grompp_check:
        validate_grompp_stages(work)
    return {
        "posres": [s.__dict__ for s in posres],
        "index": index.__dict__,
    }


def copy_acpype_posres_aside(work: Path) -> None:
    """将 acpype 原始 posre 复制到工作目录备查（不自动启用）。"""
    for d in (work / "system.amb2gmx", work / "system.acpype"):
        if not d.is_dir():
            continue
        for src in d.glob("posre_*.itp"):
            dst = work / f"acpype_{src.name}"
            if not dst.exists():
                shutil.copy2(src, dst)
