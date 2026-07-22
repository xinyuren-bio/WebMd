# ==================================================
# 功能说明：AutoDL 远程 MD 任务提交（按任务 SSH 配置）
# 使用方法：管理员在后台配置 SSH 后自动调用 submit_md_job
# 依赖环境：pip install paramiko
# 生成时间：2026-07-14
# ==================================================

import logging
import re
import threading
import time
from pathlib import Path
from typing import Union

import paramiko

from config import AUTODL_REMOTE_DIR, SITE_BASE_URL, MD_CALLBACK_SECRET
from models import tasks, Task

logger = logging.getLogger(__name__)

_ENGINE_DIR = Path(__file__).resolve().parent
_lock = threading.Lock()


def _append_log(t: Task, msg: str) -> None:
    """追加任务日志并持久化。"""
    line = f"[AutoDL] {msg}"
    t.log_lines.append(line)
    if len(t.log_lines) > 500:
        t.log_lines = t.log_lines[-500:]
    t.save()
    logger.info("任务 %s: %s", t.task_id, msg)


def _task_ssh_ready(t: Task) -> bool:
    """检查任务是否已配置 SSH。"""
    return bool(t.autodl_ssh_host and t.autodl_ssh_password)


def _ssh_client_for_task(t: Task) -> paramiko.SSHClient:
    """按任务配置建立 SSH 连接。"""
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        t.autodl_ssh_host,
        port=int(t.autodl_ssh_port or 22),
        username=t.autodl_ssh_user or "root",
        password=t.autodl_ssh_password or None,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def _normalize_run_md_script(orig: str) -> str:
    """规范化 run_md.sh（兼容旧版、AutoDL GPU，线程数由系统默认）。"""
    s = orig.replace("${{GMX:-gmx}}", "${GMX:-gmx}")
    s = re.sub(r"\nNTOMP=.*\n", "\n", s)
    s = s.replace(" -ntomp $NTOMP", "")
    if "gromacs/bin" not in s:
        s = s.replace(
            "set -euo pipefail\n",
            'set -euo pipefail\n\nexport PATH="/usr/local/gromacs/bin:${PATH}"\n',
            1,
        )
    if "-ntmpi" not in s:
        s = s.replace("mdrun -v -deffnm", "mdrun -v -ntmpi 1 -deffnm")
    return s


def _copy_analysis_scripts(work_dir: Path) -> None:
    """将后处理、分析与打包脚本复制到任务目录。"""
    names = (
        "postprocess_traj.sh",
        "run_traj_analysis.sh",
        "pack_deliverables.sh",
        "gmx_only_analyze.sh",
        "traj_analyze.py",
        "advanced_analyze.py",
        "peptide_resid_map.py",
        "hbond_residue_timeline.py",
        "fel_plot.py",
        "plot_style.py",
        "install_dssp.sh",
        "USER_RESULT_GUIDE.md",
    )
    for name in names:
        src = _ENGINE_DIR / name
        if not src.is_file():
            continue
        dst = work_dir / name
        dst.write_bytes(src.read_bytes())
        if name.endswith(".sh"):
            dst.chmod(0o755)


def _sync_analysis_scripts_remote(t: Task) -> None:
    """将最新分析脚本上传到 AutoDL 任务目录。"""
    if not _task_ssh_ready(t):
        return
    remote_base = f"{AUTODL_REMOTE_DIR.rstrip('/')}/{t.task_id}"
    try:
        cli = _ssh_client_for_task(t)
        sftp = cli.open_sftp()
        try:
            for name in (
                "postprocess_traj.sh",
                "run_traj_analysis.sh",
                "pack_deliverables.sh",
                "gmx_only_analyze.sh",
                "traj_analyze.py",
                "advanced_analyze.py",
                "peptide_resid_map.py",
                "hbond_residue_timeline.py",
                "fel_plot.py",
                "plot_style.py",
                "install_dssp.sh",
                "USER_RESULT_GUIDE.md",
            ):
                src = _ENGINE_DIR / name
                if not src.is_file():
                    continue
                remote = f"{remote_base}/{name}"
                sftp.put(str(src), remote)
                if name.endswith(".sh"):
                    cli.exec_command(f"chmod +x {remote}", timeout=10)
        finally:
            sftp.close()
            cli.close()
        logger.info("任务 %s 已同步最新分析脚本到远程", t.task_id)
    except OSError as e:
        logger.warning("任务 %s 同步分析脚本失败: %s", t.task_id, e)


def _append_callback_to_script(work_dir: Path, t: Task) -> None:
    """在 run_md.sh 末尾追加远程分析与完成回调。"""
    script = work_dir / "run_md.sh"
    if not script.is_file():
        return

    base = SITE_BASE_URL.rstrip("/")
    cb = (
        f"{base}/api/tasks/{t.task_id}/md-callback"
        f"?key={MD_CALLBACK_SECRET}&status=completed"
    )
    tail = f"""

echo "=== [5/5] 轨迹分析与完成通知 ==="
ANALYSIS_FILE="analysis_summary.txt"

if [ -f run_traj_analysis.sh ]; then
  bash run_traj_analysis.sh >> md_analysis.log 2>&1 || true
fi

# 摘要仅由 traj_analyze.py 写入；缺失时生成简短说明（不含 GROMACS 日志）
if [ ! -s "$ANALYSIS_FILE" ]; then
  {{
    echo "任务 ID: {t.task_id}"
    echo "体系原子数: {t.atom_count}"
    echo "完成时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "提示：模拟已完成，详细数据请查看邮件附件。"
  }} > "$ANALYSIS_FILE"
fi

CURL_ARGS=(-F "summary=@$ANALYSIS_FILE")
curl -sS -X POST "{cb}" "${{CURL_ARGS[@]}}" || true
echo "（压缩包将由 WebMD 服务器通过 SSH 拉取后邮件发送）"
echo "=== 已通知 WebMD 服务器，可关闭此 AutoDL 实例 ==="
"""
    orig = _normalize_run_md_script(script.read_text(encoding="utf-8"))
    if "md-callback" not in orig:
        orig = orig.rstrip() + "\n" + tail
    script.write_text(orig, encoding="utf-8")
    script.chmod(0o755)


def _upload_and_run(t: Task) -> None:
    """上传结果包并在远程解压、后台执行 run_md.sh。"""
    tar_p = Path(t.output_file) if t.output_file else None
    if not tar_p or not tar_p.is_file():
        raise FileNotFoundError("找不到前处理结果包，无法提交 AutoDL")

    work = Path(t.work_dir)
    _copy_analysis_scripts(work)
    _append_callback_to_script(work, t)

    # 重新打包（含更新后的 run_md.sh）
    import tarfile
    repack = work / "_upload_package.tar.gz"
    with tarfile.open(repack, "w:gz") as tar:
        for f in work.iterdir():
            if f.name.endswith(".tar.gz"):
                continue
            tar.add(str(f), arcname=f.name)

    remote_base = f"{AUTODL_REMOTE_DIR.rstrip('/')}/{t.task_id}"
    remote_tar = f"{remote_base}/package.tar.gz"

    cli = _ssh_client_for_task(t)
    try:
        host = f"{t.autodl_ssh_host}:{t.autodl_ssh_port}"
        _append_log(t, f"连接 {host} 成功，开始上传…")
        cli.exec_command(f"mkdir -p {remote_base}")
        # 预装分析依赖与 DSSP（失败不阻断 MD）
        cli.exec_command(
            "export PATH=/usr/local/gromacs/bin:/usr/bin:/bin:/root/miniconda3/bin:$PATH; "
            "python -m pip install -q numpy matplotlib 2>/dev/null || true; "
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>/dev/null && "
            "apt-get install -y -qq dssp 2>/dev/null || true",
            timeout=300,
        )
        time.sleep(0.5)

        sftp = cli.open_sftp()
        try:
            sftp.put(str(repack), remote_tar)
        finally:
            sftp.close()

        _append_log(t, "上传完成，开始远程解压…")
        # 解压与启动拆开：避免 「tar && nohup … &」导致 SSH 会话拖到 MD 跑完才返回，
        # 进而触发 paramiko 读超时，把已成功启动的任务误标为 failed。
        extract_cmd = f"cd {remote_base} && tar xzf package.tar.gz"
        _, stdout, stderr = cli.exec_command(extract_cmd, timeout=600)
        extract_out = stdout.read().decode("utf-8", errors="replace").strip()
        extract_err = stderr.read().decode("utf-8", errors="replace").strip()
        extract_rc = stdout.channel.recv_exit_status()
        if extract_rc != 0:
            raise RuntimeError(
                f"远程解压失败 (exit={extract_rc}): "
                f"{(extract_err or extract_out)[:300]}"
            )
        if extract_err:
            _append_log(t, f"解压 stderr: {extract_err[:200]}")

        _append_log(t, "解压完成，后台启动 run_md.sh …")
        # 用 setsid 完全脱离 SSH 会话；启动后单独探活，避免 stdout.read 阻塞误判失败
        run_cmd = (
            f"cd {remote_base} && "
            f"(setsid bash -c 'nohup bash run_md.sh > md_remote.log 2>&1 </dev/null &' "
            f"> /dev/null 2>&1 &); echo STARTED"
        )
        try:
            _, stdout, stderr = cli.exec_command(run_cmd, timeout=120)
            # 只读有限输出，避免通道挂起
            stdout.channel.settimeout(30)
            out = stdout.read(256).decode("utf-8", errors="replace").strip()
            err = stderr.read(512).decode("utf-8", errors="replace").strip()
            if err:
                _append_log(t, f"远程 stderr: {err[:200]}")
            if out:
                _append_log(t, f"远程启动回执: {out}")
        except TimeoutError:
            # 启动命令读超时：常见于 nohup 后通道未立刻关闭，需探活再决定是否失败
            _append_log(t, "启动命令读超时，正在确认远程是否已在运行…")

        # 探活：run_md / mdrun / md.tpr / md_remote.log 任一成立即视为提交成功
        time.sleep(2)
        probe = (
            f"pgrep -af 'run_md.sh|gmx mdrun|mdrun' | grep -F '{t.task_id}' | head -5; "
            f"test -f {remote_base}/md.tpr && echo HAS_TPR; "
            f"test -s {remote_base}/md_remote.log && echo HAS_LOG; "
            f"wc -c < {remote_base}/md_remote.log 2>/dev/null || echo 0"
        )
        try:
            _, pso, _ = cli.exec_command(probe, timeout=60)
            pso.channel.settimeout(30)
            probe_out = pso.read().decode("utf-8", errors="replace").strip()
        except TimeoutError as e:
            raise TimeoutError(
                "启动后探活也超时，请到 AutoDL 确认是否已在跑 MD"
            ) from e
        _append_log(t, f"远程探活: {probe_out.replace(chr(10), ' | ')[:300]}")
        ok = bool(
            probe_out
            and (
                "run_md" in probe_out
                or "mdrun" in probe_out
                or "HAS_TPR" in probe_out
                or "HAS_LOG" in probe_out
            )
        )
        if not ok:
            raise RuntimeError(
                "远程未检测到 MD 进程或日志，提交可能未成功。"
                f" 探活输出: {probe_out[:300]}"
            )
        _append_log(t, f"已在 AutoDL ({host}) 后台启动 MD")
    finally:
        cli.close()
        if repack.is_file():
            repack.unlink()


def submit_md_job(task_id: str) -> None:
    """尝试将排队任务提交到该任务绑定的 AutoDL 实例。"""
    with _lock:
        t = tasks.get(task_id)
        if not t:
            return
        if t.md_status != "queued":
            return
        if not _task_ssh_ready(t):
            _append_log(t, "尚未在管理后台配置 SSH，请在 admin 页面粘贴后保存")
            return
        t.md_status = "running"
        t.save()

    try:
        _upload_and_run(t)
    except Exception as e:
        logger.exception("AutoDL 提交失败: %s", task_id)
        detail = str(e).strip() or repr(e)
        t.md_status = "failed"
        t.error_message = f"AutoDL 提交失败: {detail}"
        _append_log(t, f"提交失败: {detail}")
        t.save()


def dispatch_queued_jobs() -> None:
    """尝试提交所有已配置 SSH 的排队任务。"""
    for t in list(tasks.values()):
        if t.md_status == "queued" and _task_ssh_ready(t):
            submit_md_job(t.task_id)


def _remote_prepare_deliverables(t: Task) -> None:
    """在 AutoDL 上同步脚本并补跑后处理/分析/打包。"""
    if not _task_ssh_ready(t):
        return
    _sync_analysis_scripts_remote(t)
    remote_base = f"{AUTODL_REMOTE_DIR.rstrip('/')}/{t.task_id}"
    cmd = (
        f"export PATH=/usr/local/gromacs/bin:/usr/bin:/bin:/root/miniconda3/bin; "
        f"cd {remote_base} && "
        f"NEED=0; "
        f"[ ! -s simulation_deliverables.zip ] && NEED=1; "
        f"[ ! -s analysis_deliverables.zip ] && NEED=1; "
        f"[ ! -f analysis_plots/gibbs_fel_2d.png ] && NEED=1; "
        f"[ ! -f analysis_plots/rmsd_ligand.png ] && NEED=1; "
        f"[ ! -f analysis_csv/secondary_structure.csv ] && NEED=1; "
        f"if [ \"$NEED\" = 1 ]; then "
        f"  if [ -f run_traj_analysis.sh ]; then /bin/bash run_traj_analysis.sh || true; "
        f"  elif [ -f postprocess_traj.sh ]; then /bin/bash postprocess_traj.sh || true; fi; "
        f"  if [ -f pack_deliverables.sh ]; then /bin/bash pack_deliverables.sh || true; fi; "
        f"fi"
    )
    try:
        cli = _ssh_client_for_task(t)
        _, stdout, stderr = cli.exec_command(cmd, timeout=900)
        out = stdout.read().decode("utf-8", errors="replace")[-2000:]
        err = stderr.read().decode("utf-8", errors="replace")[-500:]
        cli.close()
        if out:
            logger.info("任务 %s 远程打包: %s", t.task_id, out[-300:])
        if err:
            logger.warning("任务 %s 远程打包 stderr: %s", t.task_id, err[-200:])
    except OSError as e:
        logger.warning("任务 %s 远程打包失败: %s", t.task_id, e)


def pull_deliverables_from_remote(t: Task) -> tuple[str, str]:
    """从远程 SFTP 拉取两个交付压缩包，返回 (sim_zip路径, analysis_zip路径)。"""
    if not _task_ssh_ready(t):
        return "", ""
    remote_base = f"{AUTODL_REMOTE_DIR.rstrip('/')}/{t.task_id}"
    local_dir = Path(t.work_dir) / "md_deliverables"
    local_dir.mkdir(parents=True, exist_ok=True)
    sim_local = local_dir / "simulation_deliverables.zip"
    anal_local = local_dir / "analysis_deliverables.zip"
    mapping = {
        f"{remote_base}/simulation_deliverables.zip": sim_local,
        f"{remote_base}/analysis_deliverables.zip": anal_local,
    }
    try:
        cli = _ssh_client_for_task(t)
        sftp = cli.open_sftp()
        try:
            for remote, local in mapping.items():
                try:
                    sftp.get(remote, str(local))
                    sz = local.stat().st_size if local.is_file() else 0
                    logger.info("任务 %s 拉取 %s -> %s (%d bytes)", t.task_id, remote, local.name, sz)
                except OSError as e:
                    logger.warning("任务 %s 拉取失败 %s: %s", t.task_id, remote, e)
        finally:
            sftp.close()
            cli.close()
    except OSError as e:
        logger.warning("拉取交付压缩包失败 %s: %s", t.task_id, e)
    sim_p = str(sim_local) if _zip_valid(sim_local) else ""
    anal_p = str(anal_local) if _zip_valid(anal_local) else ""
    return sim_p, anal_p


def _zip_valid(p: Union[Path, str], min_bytes: int = 100) -> bool:
    """压缩包存在且非空。"""
    fp = Path(p)
    return fp.is_file() and fp.stat().st_size >= min_bytes


def finalize_md_delivery(task_id: str) -> None:
    """MD 回调后：SSH 拉取压缩包，再向用户发送带附件的邮件。"""
    from config import SITE_BASE_URL, USERS_DB
    from user_store import get_user_by_id
    from email_util import send_admin_md_completed_notify, send_user_md_completed_notify

    t = tasks.get(task_id)
    if not t:
        return
    if t.md_status != "completed":
        return

    _append_log(t, "开始拉取交付压缩包…")
    sim_p, anal_p = "", ""
    for i in range(4):
        if i:
            time.sleep(15)
        _remote_prepare_deliverables(t)
        sim_p, anal_p = pull_deliverables_from_remote(t)
        if sim_p or anal_p:
            break
        _append_log(t, f"第 {i + 1} 次拉取未获得有效压缩包，重试…")

    if sim_p:
        t.md_sim_zip = sim_p
    if anal_p:
        t.md_analysis_zip = anal_p
    if not t.analysis_summary:
        t.analysis_summary = pull_analysis_from_remote(t)
    t.save()

    u = get_user_by_id(Path(USERS_DB), t.user_id) if t.user_id else None
    user_email = u.get("email", "") if u else ""

    send_admin_md_completed_notify(
        task_id=task_id,
        user_email=user_email,
        ssh_host=t.autodl_ssh_host,
        ssh_port=int(t.autodl_ssh_port or 22),
        server_id=t.autodl_server_id,
        analysis_summary=t.analysis_summary,
        site_base=SITE_BASE_URL.rstrip("/"),
    )
    ok = send_user_md_completed_notify(
        user_email=user_email,
        task_id=task_id,
        analysis_summary=t.analysis_summary,
        site_base=SITE_BASE_URL.rstrip("/"),
        md_failed=False,
        sim_zip=t.md_sim_zip,
        analysis_zip=t.md_analysis_zip,
    )
    parts = []
    if t.md_sim_zip:
        parts.append(f"模拟包 {Path(t.md_sim_zip).stat().st_size // 1024} KB")
    if t.md_analysis_zip:
        parts.append(f"分析包 {Path(t.md_analysis_zip).stat().st_size // 1024} KB")
    _append_log(t, f"交付邮件已发送（{'、'.join(parts) if parts else '无有效附件，已附下载链接'}）")
    if not ok:
        _append_log(t, "用户邮件发送失败，请检查 SMTP 配置")
    logger.info("任务 %s 交付完成：sim=%s anal=%s mail=%s", task_id, bool(sim_p), bool(anal_p), ok)


def pull_analysis_from_remote(t: Task) -> str:
    """从远程拉取 analysis_summary.txt（回调失败时的备用）。"""
    if not _task_ssh_ready(t):
        return ""
    remote = f"{AUTODL_REMOTE_DIR.rstrip('/')}/{t.task_id}/analysis_summary.txt"
    local = Path(t.work_dir) / "analysis_summary.txt"
    try:
        cli = _ssh_client_for_task(t)
        sftp = cli.open_sftp()
        try:
            sftp.get(remote, str(local))
        finally:
            sftp.close()
            cli.close()
        if local.is_file():
            return local.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError as e:
        logger.warning("拉取分析结果失败 %s: %s", t.task_id, e)
    return ""
