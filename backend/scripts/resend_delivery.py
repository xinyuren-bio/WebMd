# ==================================================
# 功能说明：为已完成 MD 任务重新拉取压缩包并发送用户邮件
# 使用方法：在 backend 目录执行
#   python scripts/resend_delivery.py <task_id>
# 依赖环境：与 WebMD 生产环境相同
# 生成时间：2026-07-14
# ==================================================

from __future__ import annotations

import sys
from pathlib import Path

# 保证可导入 backend 包内模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Task, tasks  # noqa: E402
from engine.autodl_runner import finalize_md_delivery  # noqa: E402


def main() -> int:
    """加载任务并触发交付重发。"""
    if len(sys.argv) < 2:
        print("用法: python scripts/resend_delivery.py <task_id>")
        return 2
    tid = sys.argv[1].strip()
    meta = Path(__file__).resolve().parents[1] / "tasks" / tid / "task_meta.json"
    t = Task.load(meta)
    if t is None:
        print(f"无法加载任务 {tid}")
        return 1
    if t.md_status != "completed":
        print(f"任务状态不是 completed（当前 {t.md_status}），仍尝试发送…")
        t.md_status = "completed"
        t.save()
    tasks[tid] = t
    print(f"开始重新打包/拉取并发送：{tid}")
    finalize_md_delivery(tid)
    t2 = tasks.get(tid) or t
    print("sim_zip", t2.md_sim_zip)
    print("analysis_zip", t2.md_analysis_zip)
    print("完成，请查看任务日志中的邮件发送结果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
