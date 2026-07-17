# ==================================================
# 功能说明：解析 PDBQT 多 MODEL 构象并转换为 MOL2
# 使用方法：count_poses / extract_pose / pose_to_mol2 / pdbqt_to_mol2
# 依赖环境：Python 标准库；Open Babel（obabel）用于 PDBQT/PDB→MOL2
# 生成时间：2026-07-17 21:20
# ==================================================

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .env_check import source_amber_env
from .pdb_chains import pdb_to_mol2

logger = logging.getLogger(__name__)

# 对接树等非坐标记录：写入干净 PDB 时丢弃
_SKIP_PREFIXES = (
    "ROOT",
    "ENDROOT",
    "BRANCH",
    "ENDBRANCH",
    "TORSDOF",
    "BEGIN_RES",
    "END_RES",
)


def _should_skip(ln: str) -> bool:
    """判断是否为对接树/空行等非坐标行。"""
    s = (ln or "").strip()
    if not s:
        return True
    return any(s.startswith(p) for p in _SKIP_PREFIXES)


def _pose_block_to_pdb_text(block: str) -> str:
    """将单构象文本转为标准 PDB（仅 ATOM/HETATM/TER/END）。"""
    out: list[str] = []
    for ln in block.splitlines():
        if not ln:
            continue
        tag = ln[:6].strip() if len(ln) >= 6 else ln.strip()
        if tag in ("MODEL", "ENDMDL"):
            continue
        if _should_skip(ln):
            continue
        if ln.startswith(("ATOM", "HETATM")):
            # 截断 PDBQT 电荷/类型列，保留标准坐标区
            out.append(ln[:66] if len(ln) > 66 else ln)
        elif ln.startswith("TER"):
            out.append(ln)
    if not out:
        return ""
    out.append("END")
    return "\n".join(out) + "\n"


def _split_pose_blocks(text: str) -> list[str]:
    """按 MODEL/ENDMDL 切分原始构象块；无 MODEL 则整文件一块。"""
    lines = text.splitlines()
    blocks: list[str] = []
    cur: list[str] = []
    in_model = False
    saw_model = False

    for ln in lines:
        head = ln[:5].strip() if len(ln) >= 5 else ln.strip()
        if head == "MODEL":
            saw_model = True
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            in_model = True
            continue
        if head == "ENDMDL":
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            in_model = False
            continue
        if saw_model and not in_model:
            continue
        cur.append(ln)
    if cur:
        blocks.append("\n".join(cur))

    poses = [b for b in (_pose_block_to_pdb_text(x) for x in blocks) if b]
    if not poses:
        one = _pose_block_to_pdb_text(text)
        if one:
            poses.append(one)
    return poses


def count_poses(fp: str | Path) -> int:
    """返回 PDBQT 中可解析的构象数量。"""
    p = Path(fp)
    text = p.read_text(encoding="utf-8", errors="replace")
    return len(_split_pose_blocks(text))


def extract_pose(fp: str | Path, index: int, out: str | Path) -> str:
    """抽取第 index 个构象（0-based）为干净 PDB，返回输出路径。"""
    p = Path(fp)
    text = p.read_text(encoding="utf-8", errors="replace")
    poses = _split_pose_blocks(text)
    if not poses:
        raise ValueError("未能从 PDBQT 中解析出任何构象（需含 ATOM/HETATM）")
    if index < 0 or index >= len(poses):
        raise ValueError(
            f"构象下标无效：{index}（共 {len(poses)} 个，合法范围 0–{len(poses) - 1}）"
        )
    dst = Path(out)
    dst.write_text(poses[index], encoding="utf-8")
    logger.info("抽取 PDBQT 构象 %d/%d → %s", index + 1, len(poses), dst.name)
    return str(dst)


def _obabel_convert(src: Path, dst: Path, in_fmt: str) -> bool:
    """用 Open Babel 将源文件转为 MOL2。"""
    env = source_amber_env()
    last_err = ""
    for cmd0 in ("obabel", "babel"):
        exe = shutil.which(cmd0, path=env.get("PATH"))
        if not exe:
            continue
        r = subprocess.run(
            [exe, f"-i{in_fmt}", str(src), "-omol2", "-O", str(dst)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            logger.info("Open Babel %s→MOL2: %s → %s", in_fmt, src.name, dst.name)
            return True
        last_err = (r.stderr or r.stdout or "")[-500:]
        logger.warning("Open Babel %s→MOL2 失败 (%s): %s", in_fmt, cmd0, last_err)
    return False


def pose_to_mol2(pose_pdb: str | Path, out_mol2: str | Path) -> str:
    """将已抽取的构象 PDB 转为 MOL2。"""
    src = Path(pose_pdb)
    dst = Path(out_mol2)
    if _obabel_convert(src, dst, "pdb"):
        return str(dst)
    # 回退到现有 PDB→MOL2
    return pdb_to_mol2(src, dst)


def pdbqt_to_mol2(
    pdbqt_path: str | Path,
    out_mol2: str | Path,
    index: int = 0,
    work_dir: str | Path | None = None,
) -> tuple[str, int]:
    """从 PDBQT 抽取指定构象并转为 MOL2。

    返回 (mol2路径, 构象总数)。
    """
    src = Path(pdbqt_path)
    n = count_poses(src)
    if n < 1:
        raise ValueError("未能从 PDBQT 中解析出任何构象（需含 ATOM/HETATM）")
    if index < 0 or index >= n:
        raise ValueError(
            f"构象下标无效：{index}（共 {n} 个，合法范围 0–{n - 1}）"
        )

    wd = Path(work_dir) if work_dir else src.parent
    wd.mkdir(parents=True, exist_ok=True)

    # 优先整文件 PDBQT 直接转（仅当只有一个构象或 OpenBabel 支持选 MODEL）
    # 为保证选定构象正确，统一先抽 PDB 再转
    pose_pdb = wd / f"ligand_pose_{index}.pdb"
    extract_pose(src, index, pose_pdb)

    # 也尝试用原始 PDBQT 单构象文件（写入仅该 MODEL 的 pdbqt）
    pose_qt = wd / f"ligand_pose_{index}.pdbqt"
    # 用干净 PDB 内容作转换输入即可
    dst = Path(out_mol2)
    try:
        pose_to_mol2(pose_pdb, dst)
    except RuntimeError:
        # 再试：把抽出的原子写成简易 pdbqt 再转
        qt_lines = ["MODEL        1\n"]
        for ln in pose_pdb.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith(("ATOM", "HETATM")):
                qt_lines.append(ln + "\n" if not ln.endswith("\n") else ln)
        qt_lines.append("ENDMDL\n")
        pose_qt.write_text("".join(qt_lines), encoding="utf-8")
        if not _obabel_convert(pose_qt, dst, "pdbqt"):
            raise RuntimeError(
                "无法将选定 PDBQT 构象转为 MOL2，请确认服务器已安装 Open Babel (obabel)。"
            )
    return str(dst), n
