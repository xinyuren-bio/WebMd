# ==================================================
# 功能说明：读取 GROMACS gro 文件元信息
# 使用方法：由 pipeline / routes 调用 count_gro_atoms
# 依赖环境：Python 标准库
# 生成时间：2026-07-13
# ==================================================

from pathlib import Path


def count_gro_atoms(p: Path) -> int:
    """读取 gro 文件第二行的原子总数。"""
    if not p.is_file():
        return 0
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) < 2:
            return 0
        return int(lines[1].strip())
    except (OSError, ValueError):
        return 0
