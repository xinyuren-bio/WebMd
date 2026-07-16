# ==================================================
# 功能说明：检测 AmberTools / GROMACS / acpype 等外部工具，并从 conda 缓存自动恢复
# 使用方法：check_external_tools() 在流水线启动前调用
# 依赖环境：无额外 Python 依赖
# 生成时间：2026-06-23
# ==================================================

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# bin/ 下必需的非 wrapped 二进制
_AMBER_BIN = ("teLeap",)
_REQUIRED_CMDS = ("antechamber", "parmchk2", "tleap", "acpype", "gmx")


def _conda_prefix() -> Path | None:
    """获取当前 conda 环境前缀（优先 sys.executable，避免 CONDA_PREFIX 指向 base）。"""
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "bin" and (exe.parent.parent / "conda-meta").is_dir():
        return exe.parent.parent
    p = os.environ.get("CONDA_PREFIX")
    return Path(p) if p else None


def tool_env() -> dict[str, str]:
    """返回供 subprocess 使用的环境变量，确保 conda bin 在 PATH 中。"""
    env = os.environ.copy()
    prefix = _conda_prefix()
    if prefix:
        bin_dir = str(prefix / "bin")
        path = env.get("PATH", "")
        if bin_dir not in path.split(":"):
            env["PATH"] = f"{bin_dir}:{path}" if path else bin_dir
        env.setdefault("CONDA_PREFIX", str(prefix))
    ah = _find_amberhome()
    if ah:
        env["AMBERHOME"] = str(ah)
    return env


def resolve_tool_cmd(n: str) -> list[str]:
    """解析外部工具为 subprocess 命令列表（优先绝对路径）。"""
    e = tool_env()
    p = shutil.which(n, path=e.get("PATH"))
    if p:
        return [p]
    prefix = _conda_prefix()
    if prefix:
        fp = prefix / "bin" / n
        if fp.is_file():
            return [str(fp)]
    # acpype 兜底：python -m acpype
    if n == "acpype":
        return [sys.executable, "-m", "acpype"]
    return [n]


def source_amber_env() -> dict[str, str]:
    """兼容旧接口，返回 tool_env()。"""
    return tool_env()


def _find_amberhome() -> Path | None:
    """推断 AMBERHOME（优先当前 conda 环境，避免误用 base 环境）。"""
    prefix = _conda_prefix()
    if prefix:
        return prefix
    w = shutil.which("antechamber")
    if w:
        return Path(w).resolve().parent.parent
    return None


def _pkgs_roots() -> list[Path]:
    """收集可能的 conda pkgs 缓存目录。"""
    roots: list[Path] = []
    prefix = _conda_prefix()
    if prefix:
        roots.append(prefix.parent.parent / "pkgs")
    for p in (
        Path.home() / "miniconda3" / "pkgs",
        Path.home() / "anaconda3" / "pkgs",
        Path.home() / "mambaforge" / "pkgs",
    ):
        if p.is_dir() and p not in roots:
            roots.append(p)
    return roots


def _find_ambertools_pkg_root() -> Path | None:
    """在 conda pkgs 缓存中查找完整的 ambertools 包目录。"""
    candidates: list[Path] = []
    for pkgs in _pkgs_roots():
        candidates.extend(pkgs.glob("ambertools-*/"))
    # 优先选用含 teLeap 的最新包
    valid = [c for c in candidates if (c / "bin" / "teLeap").is_file()]
    valid.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return valid[0] if valid else None


def _acpype_amber_bin_dir() -> Path | None:
    """acpype 内置 Amber bin 目录（macOS / Linux）。"""
    try:
        import acpype
    except ImportError:
        return None
    root = Path(acpype.__file__).resolve().parent
    sub = "amber_macos" if sys.platform == "darwin" else "amber_linux"
    d = root / sub / "bin"
    if (d / "teLeap").is_file() or (d / "wrapped_progs" / "antechamber").is_file():
        return d
    return None


def _copy_if_missing(src: Path, dest: Path) -> bool:
    """源文件存在且目标缺失时复制，返回是否执行了复制。"""
    if dest.is_file() or not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    dest.chmod(dest.stat().st_mode | 0o111)
    logger.info("已从 conda 缓存恢复: %s ← %s", dest, src)
    return True


def _sync_dir_missing(src: Path, dest: Path, label: str) -> list[str]:
    """将 src 目录中 dest 缺失的文件全部复制过去。"""
    fixed: list[str] = []
    if not src.is_dir():
        return fixed
    dest.mkdir(parents=True, exist_ok=True)
    for s in src.iterdir():
        if not s.is_file():
            continue
        d = dest / s.name
        if _copy_if_missing(s, d):
            fixed.append(f"{label}/{s.name}")
    return fixed


def repair_ambertools() -> list[str]:
    """从 conda pkgs 缓存或 acpype 内置目录恢复 AmberTools 全部缺失文件。"""
    ah = _find_amberhome()
    if ah is None:
        logger.warning("未找到 AMBERHOME，跳过 AmberTools 自动修复")
        return []

    pkg = _find_ambertools_pkg_root()
    acpype_bin = _acpype_amber_bin_dir()
    fixed: list[str] = []

    # 同步 sqm 等 antechamber 依赖
    for b in ("sqm", "nmogel", "prepgen"):
        dest = ah / "bin" / b
        if dest.is_file():
            continue
        if pkg and _copy_if_missing(pkg / "bin" / b, dest):
            fixed.append(f"bin/{b}")
        elif acpype_bin and _copy_if_missing(acpype_bin / b, dest):
            fixed.append(f"bin/{b}")

    # 恢复 bin/teLeap
    for b in _AMBER_BIN:
        dest = ah / "bin" / b
        if dest.is_file():
            continue
        if pkg and _copy_if_missing(pkg / "bin" / b, dest):
            fixed.append(f"bin/{b}")
        elif acpype_bin and _copy_if_missing(acpype_bin / b, dest):
            fixed.append(f"bin/{b}")

    # 完整同步 wrapped_progs/
    dest_wp = ah / "bin" / "wrapped_progs"
    if pkg:
        fixed.extend(_sync_dir_missing(
            pkg / "bin" / "wrapped_progs", dest_wp, "wrapped_progs"
        ))
    if acpype_bin:
        fixed.extend(_sync_dir_missing(
            acpype_bin / "wrapped_progs", dest_wp, "wrapped_progs"
        ))

    # 恢复 bin/ 包装脚本（bondtype、antechamber 等 shell 入口）
    if pkg:
        for s in (pkg / "bin").iterdir():
            if not s.is_file() or not os.access(s, os.X_OK):
                continue
            try:
                head = s.read_text(encoding="utf-8", errors="ignore")[:200]
            except OSError:
                continue
            if "wrapped_progs" not in head:
                continue
            if _copy_if_missing(s, ah / "bin" / s.name):
                fixed.append(f"bin/{s.name}")

    # 恢复 tleap 包装脚本
    tleap_dest = ah / "bin" / "tleap"
    if not tleap_dest.is_file():
        for src in [
            pkg / "bin" / "tleap" if pkg else None,
            acpype_bin / "tleap" if acpype_bin else None,
        ]:
            if src and _copy_if_missing(src, tleap_dest):
                fixed.append("bin/tleap")
                break

    return fixed


def _antechamber_usable(env: dict) -> bool:
    try:
        cmd = resolve_tool_cmd("antechamber") + ["-h"]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, env=env,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return "Usage" in out or "usage" in out.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _tleap_usable(env: dict) -> bool:
    """tleap 需通过 -f 脚本运行；检查 teLeap 存在并试跑 quit。"""
    ah = _find_amberhome()
    if ah is None or not (ah / "bin" / "teLeap").is_file():
        return False
    if shutil.which("tleap", path=env.get("PATH")) is None:
        return False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".in", delete=False, encoding="utf-8"
        ) as f:
            f.write("quit\n")
            fin = f.name
        r = subprocess.run(
            ["tleap", "-f", fin],
            capture_output=True, text=True, timeout=60, env=env,
        )
        Path(fin).unlink(missing_ok=True)
        out = (r.stdout or "") + (r.stderr or "")
        return "Exiting LEaP" in out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _acpype_usable(env: dict) -> bool:
    """acpype 可用性检测（支持 python -m acpype）。"""
    try:
        cmd = resolve_tool_cmd("acpype") + ["-h"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        return bool(out.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _generic_usable(c: str, env: dict) -> bool:
    try:
        r = subprocess.run(
            [c, "-h"], capture_output=True, text=True, timeout=15, env=env,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return bool(out.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def check_external_tools() -> None:
    """检查外部依赖；先自动修复 AmberTools，再验证命令可用性。"""
    def _validate() -> tuple[list[str], list[str]]:
        e = tool_env()
        miss = [
            c for c in _REQUIRED_CMDS
            if shutil.which(c, path=e.get("PATH")) is None
            and not (c == "acpype" and _acpype_usable(e))
        ]
        bad: list[str] = []
        if "antechamber" not in miss and not _antechamber_usable(e):
            bad.append("antechamber")
        if "parmchk2" not in miss and not _generic_usable("parmchk2", e):
            bad.append("parmchk2")
        if "tleap" not in miss and not _tleap_usable(e):
            bad.append("tleap")
        if "acpype" not in miss and not _acpype_usable(e):
            bad.append("acpype")
        ah = _find_amberhome()
        if ah and not (ah / "bin" / "teLeap").is_file() and "teLeap" not in bad:
            bad.append("teLeap")
        return miss, bad

    try:
        fixed = repair_ambertools()
        if fixed:
            logger.info("AmberTools 自动修复: %s", ", ".join(fixed))
    except Exception as e:
        logger.warning("AmberTools 自动修复异常: %s", e)

    missing, broken = _validate()

    # 首次检测失败则再尝试修复一次
    if missing or broken:
        try:
            fixed2 = repair_ambertools()
            if fixed2:
                logger.info("二次自动修复: %s", ", ".join(fixed2))
            missing, broken = _validate()
        except Exception as e:
            logger.warning("二次自动修复异常: %s", e)

    if missing or broken:
        parts = []
        if missing:
            parts.append(f"未找到命令: {', '.join(missing)}")
        if broken:
            parts.append(f"命令不可用: {', '.join(broken)}")
        pkg = _find_ambertools_pkg_root()
        if pkg:
            parts.append(f"检测到 conda 缓存: {pkg}")
        parts.append(
            "手动修复:\n"
            "  conda activate md_web\n"
            "  conda install -c conda-forge ambertools --force-reinstall -y"
        )
        raise RuntimeError("\n".join(parts))

    logger.info("外部工具检测通过: %s", ", ".join(_REQUIRED_CMDS))
