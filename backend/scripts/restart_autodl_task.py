# ==================================================
# 功能说明：修复远程 run_md.sh 并重启指定任务的 MD
# 使用方法：AUTODL_SSH_PASSWORD=xxx python restart_autodl_task.py <task_id>
# 依赖环境：paramiko；在 backend 目录运行
# 生成时间：2026-07-21
# ==================================================

import json
import os
import sys
from pathlib import Path

import paramiko

from engine.autodl_runner import _normalize_run_md_script

TASK_ID = sys.argv[1] if len(sys.argv) > 1 else "01db93911e19"
meta = json.loads(Path(f"tasks/{TASK_ID}/task_meta.json").read_text())
remote = f"/root/webmd_jobs/{TASK_ID}"

# 密码仅从环境变量读取，不再依赖 task_meta 明文
password = os.environ.get("AUTODL_SSH_PASSWORD", "").strip()
if not password:
    raise SystemExit("请设置环境变量 AUTODL_SSH_PASSWORD（不再从 task_meta 读取明文密码）")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    meta["autodl_ssh_host"],
    port=int(meta["autodl_ssh_port"]),
    username=meta["autodl_ssh_user"],
    password=password,
    timeout=30,
)

sftp = c.open_sftp()
script_p = f"{remote}/run_md.sh"
with sftp.open(script_p, "r") as f:
    content = _normalize_run_md_script(f.read().decode("utf-8"))
with sftp.open(script_p, "w") as f:
    f.write(content)
sftp.close()

cmds = [
    f"chmod +x {remote}/run_md.sh",
    "pkill -f 'run_md.sh' 2>/dev/null; pkill -f 'gmx mdrun' 2>/dev/null; true",
    f"cd {remote} && nohup bash run_md.sh > md_remote.log 2>&1 &",
    "sleep 4",
    f"grep mdrun {remote}/run_md.sh",
    f"tail -8 {remote}/md_remote.log",
    "ps aux | grep gmx | grep -v grep || echo NO_GMX",
]
for cmd in cmds:
    print(">>>", cmd)
    _, stdout, _ = c.exec_command(cmd, timeout=60)
    print(stdout.read().decode("utf-8", errors="replace").strip())
    print()

c.close()
print("完成")
