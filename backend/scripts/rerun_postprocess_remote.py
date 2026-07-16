# ==================================================
# 功能说明：在 AutoDL 上重跑指定任务的轨迹后处理与分析
# 使用方法：在 WebMD 服务器 backend 目录执行
#   python scripts/rerun_postprocess_remote.py <task_id>
# 依赖环境：pip install paramiko
# 生成时间：2026-07-14
# ==================================================

from __future__ import annotations

import json
import sys
from pathlib import Path

import paramiko

ENGINE = Path(__file__).resolve().parents[1] / "engine"
TASKS = Path(__file__).resolve().parents[1] / "tasks"
REMOTE_BASE = "/root/webmd_jobs"
SCRIPTS = [
    "postprocess_traj.sh",
    "run_traj_analysis.sh",
    "traj_analyze.py",
    "advanced_analyze.py",
    "peptide_resid_map.py",
    "pack_deliverables.sh",
    "plot_style.py",
    "fel_plot.py",
    "gmx_only_analyze.sh",
]


def main() -> int:
    """连接 AutoDL，上传脚本并重跑后处理与分析。"""
    if len(sys.argv) < 2:
        print("用法: python scripts/rerun_postprocess_remote.py <task_id>")
        return 2
    tid = sys.argv[1].strip()
    meta = TASKS / tid / "task_meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    remote = f"{REMOTE_BASE}/{tid}"

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        m["autodl_ssh_host"],
        port=int(m["autodl_ssh_port"]),
        username=m.get("autodl_ssh_user") or "root",
        password=m["autodl_ssh_password"],
        timeout=25,
        allow_agent=False,
        look_for_keys=False,
    )
    sftp = c.open_sftp()
    for name in SCRIPTS:
        p = ENGINE / name
        if p.is_file():
            sftp.put(str(p), f"{remote}/{name}")
            print("uploaded", name)
    sftp.close()

    cmd = f"""
set -uo pipefail
export PATH=/usr/local/gromacs/bin:/usr/bin:/bin:/root/miniconda3/bin:$PATH
cd {remote}
chmod +x postprocess_traj.sh run_traj_analysis.sh pack_deliverables.sh 2>/dev/null || true
rm -f fit.xtc mol.xtc nojump.xtc
echo "===== POSTPROCESS ====="
bash postprocess_traj.sh 2>&1 | tee postprocess_rerun.log | tail -100
echo "===== ANALYSIS ====="
python traj_analyze.py --workdir . --out analysis_summary.txt 2>&1 | tee analysis_rerun.log | tail -80
echo "===== PACK ====="
bash pack_deliverables.sh 2>&1 | tee pack_rerun.log | tail -40
ls -lah fit.xtc to.ndx complex.pdb 2>&1
ls -lah analysis_plots/rmsd*.png 2>&1 | head -20
echo "----- ndx groups -----"
grep '^\\[' to.ndx | head -40
"""
    print("running remote...")
    _, stdout, stderr = c.exec_command(cmd, timeout=1800)
    print(stdout.read().decode("utf-8", "replace")[-8000:])
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        print("STDERR", err[-2000:])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
