# ==================================================
# 功能说明：以 UTF-8 文件名标志写入 zip，避免 macOS/Windows 解压中文乱码
# 使用方法：python zip_utf8.py out.zip file1 [file2 ...] 或 --from-dir DIR 相对路径
# 依赖环境：Python 标准库
# 生成时间：2026-07-22
# ==================================================

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def add_utf8(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    """写入带 UTF-8 标志位的 zip 条目。"""
    # flag bit 11：通用标志中的 Language encoding flag (EFS)，声明文件名为 UTF-8
    info = zipfile.ZipInfo(filename=arcname)
    info.flag_bits |= 0x800
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def zip_files(out: Path, pairs: list[tuple[str, Path]]) -> None:
    """pairs: (压缩包内路径, 本地文件)。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        for arc, fp in pairs:
            add_utf8(zf, arc, fp.read_bytes())


def main() -> None:
    """命令行入口：将若干文件打成 UTF-8 文件名 zip。"""
    p = argparse.ArgumentParser(description="UTF-8 文件名 zip 打包")
    p.add_argument("zip_path", help="输出 zip 路径")
    p.add_argument("files", nargs="*", help="文件或目录（目录按相对名递归）")
    p.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="ARC=PATH",
        help="指定压缩包内名=本地路径，可重复",
    )
    p.add_argument(
        "--chdir",
        default="",
        help="先切换到该目录再解析相对路径（便于 analysis_csv/ 等）",
    )
    args = p.parse_args()
    if args.chdir:
        import os
        os.chdir(args.chdir)

    pairs: list[tuple[str, Path]] = []
    for m in args.map:
        if "=" not in m:
            raise SystemExit(f"--map 格式应为 ARC=PATH，收到: {m}")
        arc, path = m.split("=", 1)
        pairs.append((arc, Path(path)))
    for f in args.files:
        fp = Path(f)
        if fp.is_dir():
            for sub in sorted(fp.rglob("*")):
                if sub.is_file():
                    pairs.append((sub.as_posix(), sub))
        elif fp.is_file():
            pairs.append((fp.as_posix() if "/" in f or "\\" in f else fp.name, fp))
        else:
            raise SystemExit(f"路径不存在: {fp}")
    if not pairs:
        raise SystemExit("未指定任何文件")
    for _, fp in pairs:
        if not fp.is_file():
            raise SystemExit(f"文件不存在: {fp}")
    zip_files(Path(args.zip_path), pairs)
    print(f"已写入 {args.zip_path}（{len(pairs)} 个文件，UTF-8 文件名）")


if __name__ == "__main__":
    main()
