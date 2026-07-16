# ==================================================
# 功能说明：解析 AutoDL SSH 登录命令
# 使用方法：parse_ssh_command("ssh -p 50977 root@host")
# 依赖环境：Python 标准库 re
# 生成时间：2026-07-13
# ==================================================

import re


_SSH_RE = re.compile(
    r"ssh\s+(?:-p\s+(\d+)\s+)?(\w+)@([^\s]+)",
    re.IGNORECASE,
)


def parse_ssh_command(cmd: str) -> dict:
    """从 ssh 命令字符串解析 host/port/user。"""
    s = (cmd or "").strip()
    m = _SSH_RE.search(s)
    if not m:
        raise ValueError("无法解析 SSH 命令，示例：ssh -p 50977 root@connect.westx.seetacloud.com")
    port = int(m.group(1)) if m.group(1) else 22
    return {
        "user": m.group(2),
        "host": m.group(3),
        "port": port,
    }
