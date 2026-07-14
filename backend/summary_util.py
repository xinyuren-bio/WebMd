# ==================================================
# 功能说明：分析摘要清洗（用户邮件/状态页仅展示可读结论）
# 使用方法：由 email_util、models 调用 sanitize_user_summary
# 依赖环境：Python 标准库
# 生成时间：2026-07-13
# ==================================================

import re


# 用户可见的摘要行前缀/关键词
_USER_LINE_PREFIX = (
    "=== WebMD",
    "任务 ID:",
    "体系原子数:",
    "完成时间:",
    "分析轨迹:",
    "提示：",
    "错误：",
    "[输出]",
    "[RMSD]",
    "[Rg]",
    "[RMSF]",
    "[氢键]",
    "[能量]",
)

_USER_LINE_KEYWORDS = (
    "分析完成",
    "无轨迹模式",
    "无有效轨迹",
)


def _是用户可读行(ln: str) -> bool:
    """判断是否为应展示给用户的摘要行。"""
    s = ln.strip()
    if not s:
        return False
    if s.startswith(_USER_LINE_PREFIX):
        return True
    if any(k in s for k in _USER_LINE_KEYWORDS):
        return True
    # 排除 GROMACS / 系统日志特征
    low = s.lower()
    if "gromacs" in low or low.startswith("gmx ") or "executable" in low:
        return False
    if "working directory" in low or "command line" in low or "data prefix" in low:
        return False
    if "reading structure" in low or "going to read" in low:
        return False
    if "analysing" in low or "residue" in low:
        return False
    if re.match(r"^\d+\s+\S+\s*:\s*\d+\s+atoms", s):
        return False
    if s.startswith(":-)") or "quote" in low:
        return False
    if s.startswith("===") and "WebMD" not in s:
        return False
    if s.startswith("=== [") or "安装分析依赖" in s or "轨迹后处理" in s:
        return False
    if "SSH 主机" in s or "AutoDL 实例" in s:
        return False
    if "工作目录:" in s:
        return False
    return False


def sanitize_user_summary(raw: str) -> str:
    """从原始分析输出中提取用户可读摘要。"""
    if not raw or not raw.strip():
        return "模拟已完成，详细数据请查看邮件附件或登录网站下载。"

    lines = [ln for ln in raw.splitlines() if _是用户可读行(ln)]
    if not lines:
        return "模拟已完成，详细数据请查看邮件附件或登录网站下载。"

    text = "\n".join(lines).strip()
    return text[:4000]
