# ==================================================
# 功能说明：邮件发送（管理员通知、注册验证码）
# 使用方法：由 api 模块调用
# 依赖环境：Python 标准库 smtplib；需配置 SMTP_* 环境变量
# 生成时间：2026-07-13
# ==================================================

import logging
import smtplib
import time
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ADMIN_NOTIFY_EMAIL, MAX_EMAIL_ATTACH_BYTES
from summary_util import sanitize_user_summary

logger = logging.getLogger(__name__)


def _send_mail(to: str, subject: str, body: str) -> bool:
    """发送纯文本邮件。"""
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("未配置 SMTP，无法发信")
        return False
    if not to:
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_USER, [to], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_USER, [to], msg.as_string())
        return True
    except OSError as e:
        logger.error("发送邮件失败: %s", e)
        return False


def _send_mail_with_attachments(
    to: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, str]],
) -> bool:
    """发送带附件的邮件；attachments 为 [(文件路径, 显示文件名), ...]。"""
    if not SMTP_USER or not SMTP_PASSWORD or not to:
        return False

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for fp, name in attachments:
        p = Path(fp)
        if not p.is_file() or p.stat().st_size < 100:
            logger.warning("跳过无效附件: %s", fp)
            continue
        part = MIMEBase("application", "zip")
        part.set_payload(p.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)

    if len(msg.get_payload()) <= 1:
        logger.warning("无有效附件，改为纯文本邮件")
        return _send_mail(to, subject, body + "\n\n（附件未能附加，请登录网站下载）")

    total = sum(Path(fp).stat().st_size for fp, _ in attachments if Path(fp).is_file())
    logger.info("发送带附件邮件至 %s，共 %d 个附件，合计 %d KB", to, len(msg.get_payload()) - 1, total // 1024)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=120) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_USER, [to], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=120) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_USER, [to], msg.as_string())
        return True
    except OSError as e:
        logger.error("发送带附件邮件失败: %s", e)
        return False


def send_verification_code(email: str, code: str) -> bool:
    """向用户邮箱发送注册验证码。"""
    subject = "[WebMD] 注册验证码"
    body = (
        f"您的 WebMD 注册验证码为：{code}\n\n"
        f"验证码 10 分钟内有效，请勿泄露给他人。\n"
        f"如非本人操作，请忽略此邮件。"
    )
    ok = _send_mail(email, subject, body)
    if ok:
        logger.info("已发送注册验证码至 %s", email)
    return ok


def send_admin_payment_notify(
    task_id: str,
    user_id: str,
    user_email: str,
    amount: float,
    sim_ns: float,
    note: str,
    site_base: str,
) -> bool:
    """向管理员发送付款待核实通知邮件。"""
    if not ADMIN_NOTIFY_EMAIL:
        logger.warning("未配置 WEBMD_ADMIN_NOTIFY_EMAIL，跳过邮件通知")
        return False

    subject = f"[WebMD] 新任务待核实付款 · {task_id}"
    body = (
        f"任务 ID：{task_id}\n"
        f"用户 ID：{user_id}\n"
        f"用户邮箱：{user_email}\n"
        f"应付金额：¥{amount:.2f}\n"
        f"模拟时长：{sim_ns} ns\n"
        f"用户备注：{note or '（无）'}\n"
        f"前处理：已完成\n\n"
        f"核实页面：{site_base}/admin.html\n"
        f"任务状态：{site_base}/status.html?id={task_id}\n"
    )
    ok = _send_mail(ADMIN_NOTIFY_EMAIL, subject, body)
    if ok:
        logger.info("已发送付款通知邮件：任务 %s", task_id)
    return ok


def send_admin_new_user_notify(
    user_email: str,
    user_id: str,
    total_users: int,
    created_at: float,
    site_base: str,
) -> bool:
    """向管理员发送新用户注册通知。"""
    if not ADMIN_NOTIFY_EMAIL:
        logger.warning("未配置 WEBMD_ADMIN_NOTIFY_EMAIL，跳过新用户通知")
        return False

    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at))
    subject = f"[WebMD] 新用户注册（累计第 {total_users} 位）"
    body = (
        "恭喜！WebMD 又有新用户注册了。\n\n"
        f"用户邮箱：{user_email}\n"
        f"用户 ID：{user_id}\n"
        f"注册时间：{when}\n"
        f"累计注册用户：{total_users} 人\n\n"
        f"管理后台：{site_base}/admin.html\n"
        f"访问统计：{site_base}/stats.html\n"
    )
    ok = _send_mail(ADMIN_NOTIFY_EMAIL, subject, body)
    if ok:
        logger.info("已发送新用户注册通知：%s（累计 %d 人）", user_email, total_users)
    return ok


def send_admin_need_autodl_notify(
    task_id: str,
    user_email: str,
    atom_count: int,
    sim_ns: float,
    ssh_host: str,
    site_base: str,
    market_url: str,
) -> bool:
    """付款核实通过后：通知管理员去 AutoDL 开机并配置 SSH。"""
    if not ADMIN_NOTIFY_EMAIL:
        return False

    subject = f"[WebMD] 请开 AutoDL · 任务 {task_id} · {atom_count} 原子"
    body = (
        "有一笔 MD 任务已核实付款，请按任务单独申请 GPU 实例。\n\n"
        f"任务 ID：{task_id}\n"
        f"用户邮箱：{user_email or '—'}\n"
        f"体系原子数：{atom_count}（来自 system.gro，供选 GPU 参考）\n"
        f"模拟时长：{sim_ns} ns\n\n"
        f"1. 打开 AutoDL 算力市场：{market_url}\n"
        f"2. 根据原子数选择合适的 GPU 实例并开机\n"
        f"3. 在管理后台为该任务粘贴 SSH 与密码：{site_base}/admin.html\n"
        f"   示例：ssh -p 50977 root@connect.westx.seetacloud.com\n"
        f"4. 保存后将自动上传并启动 MD\n\n"
        f"任务状态页：{site_base}/status.html?id={task_id}\n"
    )
    ok = _send_mail(ADMIN_NOTIFY_EMAIL, subject, body)
    if ok:
        logger.info("已发送 AutoDL 开机通知：任务 %s", task_id)
    return ok


def send_admin_md_completed_notify(
    task_id: str,
    user_email: str,
    ssh_host: str,
    ssh_port: int,
    analysis_summary: str,
    site_base: str,
    server_id: str = "",
) -> bool:
    """MD 模拟完成后通知管理员（可关实例）。"""
    if not ADMIN_NOTIFY_EMAIL:
        return False

    sid = server_id.strip() if server_id else "—"
    subject = f"[WebMD] MD 已完成 · 任务 {task_id} · 实例 {sid} · 可关"
    body = (
        f"任务 {task_id} 的 MD 模拟已在 AutoDL 上完成。\n\n"
        f"【请关闭实例】AutoDL 服务器 ID：{sid}\n"
        f"用户邮箱：{user_email or '—'}\n"
        f"SSH 地址：{ssh_host}:{ssh_port}\n"
        f"请登录 AutoDL 控制台，根据上述实例 ID 关闭对应机器以节省费用。\n\n"
        f"--- 自动分析摘要 ---\n"
        f"{analysis_summary or '（无分析输出）'}\n\n"
        f"管理后台：{site_base}/admin.html\n"
        f"任务状态：{site_base}/status.html?id={task_id}\n"
    )
    ok = _send_mail(ADMIN_NOTIFY_EMAIL, subject, body)
    if ok:
        logger.info("已发送 MD 完成通知：任务 %s", task_id)
    return ok


def send_user_md_completed_notify(
    user_email: str,
    task_id: str,
    analysis_summary: str,
    site_base: str,
    md_failed: bool = False,
    sim_zip: str = "",
    analysis_zip: str = "",
) -> bool:
    """MD 完成后通知用户；成功时附带两个压缩包（体积过大则仅发下载链接）。"""
    if not user_email:
        return False

    base = site_base.rstrip("/")
    sim_dl = f"{base}/api/tasks/{task_id}/download/md-simulation"
    anal_dl = f"{base}/api/tasks/{task_id}/download/md-analysis"

    if md_failed:
        subject = f"[WebMD] 您的 MD 模拟未成功完成 · 任务 {task_id}"
        hint = "模拟过程中出现错误，请联系售后微信 biomd777 协助处理。"
        body = (
            f"您好，\n\n"
            f"任务 {task_id} 的分子动力学模拟未成功完成。\n"
            f"{hint}\n\n"
            f"查看任务状态：{base}/status.html?id={task_id}\n"
            f"售后微信：biomd777\n"
        )
        return _send_mail(user_email, subject, body)

    subject = f"[WebMD] 您的 MD 模拟已完成 · 任务 {task_id}"
    attach_list: list[tuple[str, str]] = []
    attach_note = ""

    for fp, label, fname in (
        (sim_zip, "模拟数据包", f"{task_id}_simulation.zip"),
        (analysis_zip, "分析结果包", f"{task_id}_analysis.zip"),
    ):
        p = Path(fp) if fp else None
        if p and p.is_file() and p.stat().st_size >= 100:
            sz = p.stat().st_size
            if sz <= MAX_EMAIL_ATTACH_BYTES:
                attach_list.append((str(p), fname))
            else:
                attach_note += (
                    f"\n- {label}（{sz // (1024 * 1024)} MB）体积较大，请登录下载："
                    f"{sim_dl if 'simulation' in fname else anal_dl}"
                )
        elif p and p.is_file():
            attach_note += f"\n- {label} 文件无效（为空），请登录下载"

    body = (
        f"您好，\n\n"
        f"任务 {task_id} 的分子动力学模拟已完成。\n\n"
        f"附件说明：\n"
        f"1. {task_id}_simulation.zip — 含 ndx、top、tpr、pdb（有轨迹时含 xtc）\n"
        f"2. {task_id}_analysis.zip — 含 analysis_csv/（数据表）与 analysis_plots/（图片）\n"
    )
    if attach_note:
        body += f"\n以下文件未随信附上（邮箱大小限制）：{attach_note}\n"
    if not attach_list:
        body += (
            f"\n请登录网站下载：\n"
            f"- 模拟数据包：{sim_dl}\n"
            f"- 分析结果包：{anal_dl}\n"
        )
    body += (
        f"\n--- 分析摘要 ---\n"
        f"{sanitize_user_summary(analysis_summary)}\n\n"
        f"任务状态：{base}/status.html?id={task_id}\n"
        f"售后微信：biomd777\n"
    )

    if attach_list:
        ok = _send_mail_with_attachments(user_email, subject, body, attach_list)
    else:
        ok = _send_mail(user_email, subject, body)
    if ok:
        n = len(attach_list)
        logger.info("已发送用户 MD 完成通知：%s 任务 %s（附件 %d 个）", user_email, task_id, n)
    return ok
