import json
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    BackgroundTasks,
    Request,
    Depends,
    Query,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import (
    TASKS_DIR, DEFAULT_PARAMS, MD_MAX_NS, SITE_BASE_URL, ALLOWED_SIM_NS,
    ALLOWED_SALT_TYPES,
    PAYMENT_ENABLED, PAYMENT_AMOUNT, PAYMENT_QR_URL, WECHAT_QR_URL, PAYMENT_CURRENCY,
    TIP_ENABLED, TIP_QR_URL, ANALYTICS_FILE, AUTODL_MARKET_URL, MD_CALLBACK_SECRET,
)
from payment_util import calc_payment_amount, verify_admin_key, price_for_sim_ns, qr_urls_for_sim_ns
from analytics_util import record_visit, get_analytics_stats, collect_md_completion_stats
from email_util import send_admin_payment_notify, send_admin_need_autodl_notify, send_admin_md_completed_notify, send_user_md_completed_notify
from user_store import get_user_by_id, count_users, list_recent_users
from config import USERS_DB
from api.deps import get_current_user
from models import Task, TaskStatus, tasks
from engine.pipeline import run_pipeline
from engine.ligand_charge import ChargeConfirmNeeded
from engine.autodl_runner import (
    submit_md_job, dispatch_queued_jobs, pull_analysis_from_remote,
    pull_deliverables_from_remote, finalize_md_delivery,
)
from ssh_util import parse_ssh_command
from engine.structure_export import ensure_complex_pdb
from engine.ligand_ff import ensure_ligand_forcefield_json, _find_gaff_mol2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class TaskLogHandler(logging.Handler):
    """将 pipeline 日志写入任务对象的 log_lines。"""

    def __init__(self, task: Task):
        super().__init__()
        self.task = task

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self.task.log_lines.append(msg)
        if len(self.task.log_lines) > 500:
            self.task.log_lines = self.task.log_lines[-500:]


def _execute_task(
    task_id: str,
    pdb_path: str,
    mol2_paths: list[str] | None,
    params: dict,
    cyclic_pdb_path: str | None = None,
):
    """后台执行任务。"""
    task = tasks.get(task_id)
    if not task:
        return

    log_handler = TaskLogHandler(task)
    log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    engine_logger = logging.getLogger("engine")
    engine_logger.addHandler(log_handler)
    engine_logger.setLevel(logging.INFO)

    def update_status(status_str: str):
        task.status = TaskStatus(status_str)
        task.save()
        logger.info("任务 %s → %s", task_id, status_str)

    try:
        # 合并任务上已保存的确认电荷（弹窗确认后写入）
        if task.params.get("confirmed_charges"):
            params = dict(params)
            params["confirmed_charges"] = task.params["confirmed_charges"]
        output_file = run_pipeline(
            task.work_dir,
            pdb_path,
            mol2_paths,
            params,
            update_status,
            cyclic_pdb_path=cyclic_pdb_path,
        )
        task.params = params
        task.output_file = output_file
        task.status = TaskStatus.COMPLETED
        task.error_message = ""
        task.params.pop("charge_confirm", None)
        from gro_util import count_gro_atoms
        gro_p = Path(task.work_dir) / "system.gro"
        task.atom_count = count_gro_atoms(gro_p)
    except ChargeConfirmNeeded as e:
        # 不标失败：等待用户确认可行净电荷
        task.status = TaskStatus.AWAITING_CHARGE_CONFIRM
        task.error_message = e.req.message
        task.params = dict(task.params or params)
        task.params["charge_confirm"] = e.req.to_dict()
        # 记住原始路径以便确认后续跑
        task.params["_resume_pdb"] = pdb_path
        task.params["_resume_mol2s"] = mol2_paths or []
        task.params["_resume_cyclic"] = cyclic_pdb_path
        logger.info("任务 %s 等待确认净电荷: %s", task_id, e.req.working_charges)
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error_message = str(e)
        logger.exception("任务 %s 失败", task_id)
    finally:
        engine_logger.removeHandler(log_handler)
        task.save()


def _task_owner_or_404(task_id: str, user: dict) -> Task:
    """校验任务存在且属于当前用户。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.user_id and task.user_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return task


def _payment_payload(task: Task) -> dict:
    """组装任务支付信息（金额与收款码随模拟时长变化）。"""
    sim_ns = float(task.params.get("simulation_time_ns", 100))
    base = price_for_sim_ns(sim_ns)
    qr_url, wechat_qr_url = qr_urls_for_sim_ns(sim_ns)

    if task.payment_status in ("pending", "paid") and task.payment_amount is not None:
        amount = task.payment_amount
    else:
        amount = calc_payment_amount(task.task_id, base, task.user_id)
        if task.payment_amount is None:
            task.payment_amount = amount
            task.save()

    return {
        "task_id": task.task_id,
        "user_id": task.user_id,
        "paid": task.paid,
        "paid_at": task.paid_at,
        "payment_status": task.payment_status,
        "payment_amount": amount,
        "payment_claimed_at": task.payment_claimed_at,
        "amount": amount,
        "simulation_time_ns": sim_ns,
        "currency": PAYMENT_CURRENCY,
        "qr_url": qr_url,
        "wechat_qr_url": wechat_qr_url,
    }


@router.post("/tasks")
async def create_task(
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    pdb_file: UploadFile = File(...),
    mol2_file: Optional[UploadFile] = File(None),
    mol2_file_2: Optional[UploadFile] = File(None),
    mol2_file_3: Optional[UploadFile] = File(None),
    cyclic_peptide_file: Optional[UploadFile] = File(None),
    ligand_type: str = Form(default="mol2"),
    is_cyclic_peptide: str = Form(default="0"),
    is_linear_peptide: str = Form(default="0"),
    peptide_upload_mode: str = Form(default="separate"),
    protein_chains: str = Form(default=""),
    peptide_chain: str = Form(default=""),
    temperature: float = Form(default=DEFAULT_PARAMS["temperature"]),
    pressure: float = Form(default=DEFAULT_PARAMS["pressure"]),
    timestep: float = Form(default=DEFAULT_PARAMS["timestep"]),
    simulation_time_ns: float = Form(default=DEFAULT_PARAMS["simulation_time_ns"]),
    constraints: str = Form(default=DEFAULT_PARAMS["constraints"]),
    nonbonded_cutoff: float = Form(default=DEFAULT_PARAMS["nonbonded_cutoff"]),
    box_padding: float = Form(default=DEFAULT_PARAMS["box_padding"]),
    ion_conc: float = Form(default=DEFAULT_PARAMS["ion_conc"]),
    salt_type: str = Form(default=DEFAULT_PARAMS["salt_type"]),
    tau_t: float = Form(default=DEFAULT_PARAMS["tau_t"]),
    tau_p: float = Form(default=DEFAULT_PARAMS["tau_p"]),
    report_interval_ps: float = Form(default=DEFAULT_PARAMS["report_interval_ps"]),
    ligand_add_hydrogens: str = Form(default="1"),
):
    """创建新的 GROMACS MD 前处理任务（需登录）。"""
    if simulation_time_ns not in ALLOWED_SIM_NS:
        raise HTTPException(status_code=400, detail="模拟时长仅支持 10 ns、100 ns 或 200 ns")

    if simulation_time_ns > MD_MAX_NS:
        raise HTTPException(status_code=400, detail=f"模拟时长不能超过 {MD_MAX_NS} ns")

    salt_key = (salt_type or "nacl").strip().lower()
    if salt_key not in ALLOWED_SALT_TYPES:
        raise HTTPException(status_code=400, detail="盐种类仅支持 NaCl 或 KCl")

    # 配体补氢开关：表单 "1"/"true"/"on" 为开启（默认开）
    add_h = ligand_add_hydrogens.strip().lower() not in ("0", "false", "no", "off")
    # 配体类型：mol2 | cyclic | linear（兼容旧字段 is_cyclic_peptide / is_linear_peptide）
    lt = (ligand_type or "mol2").strip().lower()
    if lt not in ("mol2", "cyclic", "linear"):
        if is_cyclic_peptide.strip().lower() in ("1", "true", "yes", "on"):
            lt = "cyclic"
        elif is_linear_peptide.strip().lower() in ("1", "true", "yes", "on"):
            lt = "linear"
        else:
            lt = "mol2"
    is_cyc = lt == "cyclic"
    is_lin = lt == "linear"
    is_pep = is_cyc or is_lin

    task = Task()
    task.user_id = user["user_id"]
    task_id = task.task_id

    work_dir = Path(TASKS_DIR) / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    pep_mode = (peptide_upload_mode or "separate").strip().lower()
    if pep_mode not in ("separate", "complex"):
        pep_mode = "separate"

    mol2_paths: list[str] = []
    cyclic_pdb_path: Optional[str] = None
    pdb_path = work_dir / "protein.pdb"

    if is_pep and pep_mode == "complex":
        # 复合物 PDB：用户选择蛋白链与肽链后由服务端拆分
        from engine.pdb_chains import split_complex

        raw = Path(pdb_file.filename or "complex.pdb").name
        if not raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="复合物须为 PDB 格式")
        complex_path = work_dir / "complex_upload.pdb"
        with open(complex_path, "wb") as f:
            f.write(await pdb_file.read())

        def _norm_ch(s: str) -> str:
            t = (s or "").strip()
            if t in ("", "_", "(空白链号)"):
                return " "
            return t[:1]

        # 前端空白链用 "_"；多链用逗号分隔
        raw_prot = [x for x in (protein_chains or "").split(",") if x != ""]
        prot_list = [_norm_ch(x) for x in raw_prot]
        if not (peptide_chain or "").strip() and peptide_chain != "_":
            raise HTTPException(status_code=400, detail="复合物模式请选择肽链")
        pep_ch = _norm_ch(peptide_chain)
        if not prot_list:
            raise HTTPException(status_code=400, detail="复合物模式请至少选择一条蛋白链")
        try:
            prot_out, pep_out = split_complex(
                complex_path, prot_list, pep_ch, work_dir,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        # split_complex 已写入 protein.pdb；肽文件再拷贝为标准上传名
        pdb_path = Path(prot_out)
        cyc_dest = work_dir / (
            "cyclic_peptide_upload.pdb" if is_cyc else "linear_peptide_upload.pdb"
        )
        if Path(pep_out).resolve() != cyc_dest.resolve():
            cyc_dest.write_bytes(Path(pep_out).read_bytes())
        cyclic_pdb_path = str(cyc_dest)
    else:
        # 蛋白文件名消毒，避免空格
        pdb_raw = Path(pdb_file.filename or "protein.pdb").name
        if not pdb_raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="蛋白须为 PDB 格式")
        with open(pdb_path, "wb") as f:
            f.write(await pdb_file.read())

    if is_pep and pep_mode != "complex":
        label = "环肽" if is_cyc else "线形肽"
        if cyclic_peptide_file is None or not cyclic_peptide_file.filename:
            raise HTTPException(status_code=400, detail=f"{label}模式请上传肽 PDB 文件")
        cyc_raw = Path(cyclic_peptide_file.filename).name
        if not cyc_raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail=f"{label}须为 PDB 格式（标准氨基酸）")
        cyc_dest = work_dir / (
            "cyclic_peptide_upload.pdb" if is_cyc else "linear_peptide_upload.pdb"
        )
        with open(cyc_dest, "wb") as f:
            f.write(await cyclic_peptide_file.read())
        cyclic_pdb_path = str(cyc_dest)
    elif not is_pep:
        for idx, up in enumerate([mol2_file, mol2_file_2, mol2_file_3], 1):
            if up is None or not up.filename:
                continue
            raw_name = Path(up.filename).name
            if not raw_name.lower().endswith(".mol2"):
                raise HTTPException(status_code=400, detail=f"配体 {idx} 须为 MOL2 格式")
            dest = work_dir / f"ligand_{idx}.mol2"
            with open(dest, "wb") as f:
                f.write(await up.read())
            mol2_paths.append(str(dest))

        if not mol2_paths:
            raise HTTPException(status_code=400, detail="至少上传一个 MOL2 配体文件")
        if len(mol2_paths) > 3:
            raise HTTPException(status_code=400, detail="最多支持 3 个配体")

    params = {
        "temperature": temperature,
        "pressure": pressure,
        "timestep": timestep,
        "simulation_time_ns": simulation_time_ns,
        "constraints": constraints,
        "nonbonded_cutoff": nonbonded_cutoff,
        "box_padding": box_padding,
        "ion_conc": ion_conc,
        "salt_type": salt_key,
        "tau_t": tau_t,
        "tau_p": tau_p,
        "report_interval_ps": report_interval_ps,
        "ligand_add_hydrogens": add_h,
        "ligand_type": lt,
        "is_cyclic_peptide": is_cyc,
        "is_linear_peptide": is_lin,
        "peptide_upload_mode": pep_mode if is_pep else "separate",
        "protein_chains": protein_chains if is_pep and pep_mode == "complex" else "",
        "peptide_chain": peptide_chain if is_pep and pep_mode == "complex" else "",
    }

    task.work_dir = str(work_dir)
    task.params = params
    tasks[task_id] = task
    task.save()

    background_tasks.add_task(
        _execute_task,
        task_id,
        str(pdb_path),
        mol2_paths if not is_pep else None,
        params,
        cyclic_pdb_path,
    )

    logger.info("任务 %s 已创建（ligand_type=%s）", task_id, lt)
    return task.to_dict()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: dict = Depends(get_current_user)):
    task = _task_owner_or_404(task_id, user)
    return task.to_dict()


class ChargeConfirmBody(BaseModel):
    """用户确认采用的配体净电荷。"""

    ligand_index: int = Field(..., ge=1, le=3)
    charge: int


@router.post("/tasks/{task_id}/confirm-charge")
async def confirm_ligand_charge(
    task_id: str,
    body: ChargeConfirmBody,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """确认净电荷后继续前处理（仅 awaiting_charge_confirm 状态可用）。"""
    task = _task_owner_or_404(task_id, user)
    if task.status != TaskStatus.AWAITING_CHARGE_CONFIRM:
        raise HTTPException(status_code=400, detail="当前任务不需要确认净电荷")
    conf = task.params.get("charge_confirm") or {}
    allowed = list(conf.get("working_charges") or [])
    if body.charge not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"请选择提示中的可行净电荷：{allowed}",
        )
    confirmed = dict(task.params.get("confirmed_charges") or {})
    confirmed[str(body.ligand_index)] = body.charge
    task.params["confirmed_charges"] = confirmed
    task.params.pop("charge_confirm", None)
    task.error_message = ""
    task.status = TaskStatus.PROCESSING_LIGAND
    task.save()

    pdb_path = task.params.get("_resume_pdb") or str(Path(task.work_dir) / "protein.pdb")
    mol2s = task.params.get("_resume_mol2s")
    if not mol2s:
        mol2s = sorted(str(p) for p in Path(task.work_dir).glob("ligand_*.mol2"))
    cyclic = task.params.get("_resume_cyclic")
    background_tasks.add_task(
        _execute_task, task_id, pdb_path, mol2s, dict(task.params), cyclic,
    )
    return {"ok": True, "task_id": task_id, "confirmed_charges": confirmed}


@router.get("/tasks/{task_id}/public")
async def get_task_public(task_id: str):
    """公开任务状态（扫码查看，无需登录）。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    d = task.to_public_dict()
    if d.get("payment_amount") is None:
        sim_ns = float(task.params.get("simulation_time_ns", 100))
        d["payment_amount"] = calc_payment_amount(task.task_id, price_for_sim_ns(sim_ns), task.user_id)
    return d


@router.get("/payment/config")
async def get_payment_config():
    """返回付费/打赏配置。"""
    from payment_util import pricing_table

    tip_on = TIP_ENABLED and not PAYMENT_ENABLED
    return {
        "enabled": PAYMENT_ENABLED,
        "tip_enabled": tip_on,
        "amount": PAYMENT_AMOUNT,
        "currency": PAYMENT_CURRENCY,
        "qr_url": PAYMENT_QR_URL,
        "wechat_qr_url": WECHAT_QR_URL,
        "tip_qr_url": TIP_QR_URL,
        "verify_mode": "manual",
        "md_max_ns": MD_MAX_NS,
        "pricing": pricing_table(),
    }


@router.get("/tasks/{task_id}/payment")
async def get_task_payment(task_id: str, user: dict = Depends(get_current_user)):
    task = _task_owner_or_404(task_id, user)
    return _payment_payload(task)


@router.post("/tasks/{task_id}/payment/confirm")
async def confirm_task_payment(
    task_id: str,
    payer_note: str = Form(default=""),
    user: dict = Depends(get_current_user),
):
    """用户声明已支付，进入待核实状态（需管理员确认后才可下载）。"""
    if not PAYMENT_ENABLED:
        raise HTTPException(status_code=403, detail="付费功能未开启")
    task = _task_owner_or_404(task_id, user)
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="前处理尚未完成，暂不可支付")
    if task.paid or task.payment_status == "paid":
        return _payment_payload(task)
    if task.payment_status == "pending":
        return _payment_payload(task)

    task.payment_amount = calc_payment_amount(
        task.task_id,
        price_for_sim_ns(float(task.params.get("simulation_time_ns", 100))),
        task.user_id,
    )
    task.payment_status = "pending"
    task.payment_claimed_at = time.time()
    task.payment_note = (payer_note or "").strip()[:120]
    task.save()
    logger.info("任务 %s 支付待核实，金额 ¥%.2f", task_id, task.payment_amount)

    send_admin_payment_notify(
        task_id=task_id,
        user_id=task.user_id,
        user_email=user.get("email", ""),
        amount=task.payment_amount,
        sim_ns=float(task.params.get("simulation_time_ns", 0)),
        note=task.payment_note,
        site_base=SITE_BASE_URL.rstrip("/"),
    )
    return _payment_payload(task)


@router.get("/admin/payments/pending")
async def list_pending_payments(admin_key: str = ""):
    """管理员：列出待核实的支付。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")

    pending = []
    for t in tasks.values():
        if t.payment_status == "pending":
            u = get_user_by_id(Path(USERS_DB), t.user_id) if t.user_id else None
            pending.append({
                "task_id": t.task_id,
                "user_id": t.user_id,
                "user_email": u.get("email") if u else "",
                "payment_amount": t.payment_amount or calc_payment_amount(
                    t.task_id,
                    price_for_sim_ns(float(t.params.get("simulation_time_ns", 100))),
                    t.user_id,
                ),
                "payment_claimed_at": t.payment_claimed_at,
                "payment_note": t.payment_note,
                "created_at": t.created_at,
                "simulation_time_ns": t.params.get("simulation_time_ns"),
            })
    pending.sort(key=lambda x: x.get("payment_claimed_at") or 0, reverse=True)
    return {"items": pending}


@router.post("/admin/payments/{task_id}/approve")
async def approve_task_payment(
    task_id: str,
    background_tasks: BackgroundTasks,
    admin_key: str = "",
):
    """管理员：核实到账后批准下载，并排队提交 AutoDL。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    task.paid = True
    task.paid_at = time.time()
    task.payment_status = "paid"
    if not task.payment_amount:
        task.payment_amount = calc_payment_amount(
            task.task_id,
            price_for_sim_ns(float(task.params.get("simulation_time_ns", 100))),
            task.user_id,
        )
    if task.md_status in ("none", "failed"):
        task.md_status = "queued"
    task.save()
    logger.info("任务 %s 支付已核实通过，MD 排队", task_id)

    background_tasks.add_task(_dispatch_after_approve, task_id)

    return _payment_payload(task)


def _dispatch_after_approve(task_id: str) -> None:
    """核实通过后发邮件通知管理员开 AutoDL；若已配置 SSH 则自动提交。"""
    task = tasks.get(task_id)
    if not task:
        return

    if not task.atom_count and task.work_dir:
        from gro_util import count_gro_atoms
        task.atom_count = count_gro_atoms(Path(task.work_dir) / "system.gro")
        task.save()

    u = get_user_by_id(Path(USERS_DB), task.user_id) if task.user_id else None
    send_admin_need_autodl_notify(
        task_id=task_id,
        user_email=u.get("email", "") if u else "",
        atom_count=task.atom_count,
        sim_ns=float(task.params.get("simulation_time_ns", 0)),
        ssh_host=task.autodl_ssh_host,
        site_base=SITE_BASE_URL.rstrip("/"),
        market_url=AUTODL_MARKET_URL,
    )

    if task.autodl_ssh_host and task.autodl_ssh_password:
        submit_md_job(task_id)
    dispatch_queued_jobs()


class AutodlSshBody(BaseModel):
    ssh_command: str = Field(min_length=8, max_length=300)
    password: str = Field(min_length=1, max_length=200)
    server_id: str = Field(min_length=1, max_length=64, description="AutoDL 实例 ID，便于完成后关机")


@router.post("/admin/payments/{task_id}/reject")
async def reject_task_payment(
    task_id: str,
    admin_key: str = Query(default=""),
    reason: str = Query(default=""),
):
    """管理员：驳回付款申请，用户可重新支付。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.payment_status != "pending":
        raise HTTPException(status_code=400, detail="该任务不在待核实状态")

    note = (reason or "").strip()[:80]
    if note:
        prev = (task.payment_note or "").strip()
        task.payment_note = f"{prev} [驳回:{note}]".strip()[:120]

    task.paid = False
    task.paid_at = None
    task.payment_status = "unpaid"
    task.payment_claimed_at = None
    task.save()
    logger.info("任务 %s 支付核实被驳回：%s", task_id, note or "(无原因)")
    return {"ok": True, "task_id": task_id, "payment_status": "unpaid"}


@router.post("/admin/md/{task_id}/dispatch")
async def admin_dispatch_md(
    task_id: str,
    background_tasks: BackgroundTasks,
    admin_key: str = "",
):
    """管理员：手动触发 AutoDL 提交（用于 SSH 配置后重试排队任务）。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.payment_status != "paid":
        raise HTTPException(status_code=400, detail="任务尚未支付核实")
    if task.md_status == "running":
        raise HTTPException(status_code=400, detail="MD 已在运行中")
    if task.md_status == "completed":
        raise HTTPException(status_code=400, detail="MD 已完成")

    task.md_status = "queued"
    task.save()

    background_tasks.add_task(_dispatch_after_approve, task_id)

    return {"ok": True, "task_id": task_id, "md_status": "queued"}


@router.get("/admin/md/queue")
async def admin_md_queue(admin_key: str = ""):
    """管理员：查看 MD 排队/运行中的任务。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")

    items = []
    for t in tasks.values():
        if t.payment_status == "paid" and t.md_status in ("queued", "running", "failed"):
            u = get_user_by_id(Path(USERS_DB), t.user_id) if t.user_id else None
            items.append({
                "task_id": t.task_id,
                "user_email": u.get("email") if u else "",
                "md_status": t.md_status,
                "md_status_label": t.to_public_dict().get("md_status_label", t.md_status),
                "simulation_time_ns": t.params.get("simulation_time_ns"),
                "atom_count": t.atom_count,
                "ssh_configured": bool(t.autodl_ssh_host and t.autodl_ssh_password),
                "ssh_host": t.autodl_ssh_host or "",
                "ssh_port": t.autodl_ssh_port,
                "ssh_command": t.autodl_ssh_command or "",
                "server_id": t.autodl_server_id or "",
                "error_message": t.error_message,
            })
    items.sort(key=lambda x: x["task_id"])
    return {"items": items, "market_url": AUTODL_MARKET_URL}


@router.post("/admin/tasks/{task_id}/autodl")
async def save_task_autodl_ssh(
    task_id: str,
    body: AutodlSshBody,
    background_tasks: BackgroundTasks,
    admin_key: str = "",
):
    """管理员：为任务配置 AutoDL SSH，保存后自动提交 MD。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.payment_status != "paid":
        raise HTTPException(status_code=400, detail="任务尚未支付核实")

    try:
        parsed = parse_ssh_command(body.ssh_command)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    task.autodl_ssh_command = body.ssh_command.strip()
    task.autodl_ssh_host = parsed["host"]
    task.autodl_ssh_port = parsed["port"]
    task.autodl_ssh_user = parsed["user"]
    task.autodl_ssh_password = body.password
    task.autodl_server_id = body.server_id.strip()
    if task.md_status in ("none", "failed"):
        task.md_status = "queued"
    task.save()

    background_tasks.add_task(submit_md_job, task_id)

    return {
        "ok": True,
        "task_id": task_id,
        "ssh_host": task.autodl_ssh_host,
        "ssh_port": task.autodl_ssh_port,
        "server_id": task.autodl_server_id,
        "md_status": task.md_status,
    }


@router.post("/tasks/{task_id}/md-callback")
async def md_completion_callback(
    task_id: str,
    background_tasks: BackgroundTasks,
    key: str = "",
    status: str = "completed",
    summary: UploadFile = File(None),
    sim_zip: UploadFile = File(None),
    analysis_zip: UploadFile = File(None),
):
    """远程 run_md.sh 完成后的回调（无需登录，密钥校验）。"""
    if key != MD_CALLBACK_SECRET:
        raise HTTPException(status_code=403, detail="无效回调密钥")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    deliver_dir = Path(task.work_dir) / "md_deliverables"
    deliver_dir.mkdir(parents=True, exist_ok=True)

    async def _存压缩包(uf: UploadFile | None, name: str) -> str:
        if uf is None:
            return ""
        try:
            raw = await uf.read()
            if len(raw) < 512:
                return ""
            p = deliver_dir / name
            p.write_bytes(raw)
            return str(p)
        except OSError:
            return ""

    text = ""
    if summary is not None:
        try:
            raw = await summary.read()
            text = raw.decode("utf-8", errors="replace")[:8000]
        except OSError:
            pass

    # 回调上传的 zip 常为空，正式交付改由 SSH 拉取
    task.md_sim_zip = await _存压缩包(sim_zip, "simulation_deliverables.zip")
    task.md_analysis_zip = await _存压缩包(analysis_zip, "analysis_deliverables.zip")

    if not text:
        text = pull_analysis_from_remote(task)

    task.analysis_summary = text
    task.md_status = "failed" if status == "failed" else "completed"
    task.md_completed_at = time.time()
    task.save()

    u = get_user_by_id(Path(USERS_DB), task.user_id) if task.user_id else None
    user_email = u.get("email", "") if u else ""
    failed = task.md_status == "failed"

    if failed:
        send_admin_md_completed_notify(
            task_id=task_id,
            user_email=user_email,
            ssh_host=task.autodl_ssh_host,
            ssh_port=int(task.autodl_ssh_port or 22),
            server_id=task.autodl_server_id,
            analysis_summary=text,
            site_base=SITE_BASE_URL.rstrip("/"),
        )
        send_user_md_completed_notify(
            user_email=user_email,
            task_id=task_id,
            analysis_summary=text,
            site_base=SITE_BASE_URL.rstrip("/"),
            md_failed=True,
        )
    else:
        # 成功：后台 SSH 拉取压缩包后再发邮件（含附件）
        background_tasks.add_task(finalize_md_delivery, task_id)

    return {"ok": True, "task_id": task_id, "md_status": task.md_status}


@router.post("/admin/tasks/{task_id}/resend-delivery")
async def admin_resend_delivery(task_id: str, admin_key: str = "", background_tasks: BackgroundTasks = None):
    """管理员：重新拉取压缩包并发送用户交付邮件。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.md_status != "completed":
        raise HTTPException(status_code=400, detail="任务 MD 尚未完成")
    background_tasks.add_task(finalize_md_delivery, task_id)
    return {"ok": True, "task_id": task_id, "message": "已触发重新拉取并发送交付邮件"}


@router.get("/tasks/{task_id}/download")
async def download_task(task_id: str, user: dict = Depends(get_current_user)):
    task = _task_owner_or_404(task_id, user)
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")
    if PAYMENT_ENABLED and (not task.paid or task.payment_status != "paid"):
        raise HTTPException(status_code=402, detail="请先完成支付并等待核实通过后再下载")
    if not task.output_file or not Path(task.output_file).exists():
        raise HTTPException(status_code=400, detail="输出文件不存在")

    return FileResponse(
        task.output_file,
        media_type="application/gzip",
        filename=f"gromacs_md_{task_id}.tar.gz",
    )


def _md_download_guard(task: Task) -> None:
    """MD 交付物下载前置检查。"""
    if task.md_status != "completed":
        raise HTTPException(status_code=400, detail="MD 模拟尚未完成")
    if PAYMENT_ENABLED and (not task.paid or task.payment_status != "paid"):
        raise HTTPException(status_code=402, detail="请先完成支付并等待核实通过后再下载")


@router.get("/tasks/{task_id}/download/md-simulation")
async def download_md_simulation(task_id: str, user: dict = Depends(get_current_user)):
    """下载 MD 模拟数据包（ndx/top/tpr/pdb/xtc）。"""
    task = _task_owner_or_404(task_id, user)
    _md_download_guard(task)
    if not task.md_sim_zip or not Path(task.md_sim_zip).is_file():
        raise HTTPException(status_code=404, detail="模拟数据包尚未生成")
    return FileResponse(
        task.md_sim_zip,
        media_type="application/zip",
        filename=f"{task_id}_simulation.zip",
    )


@router.get("/tasks/{task_id}/download/md-analysis")
async def download_md_analysis(task_id: str, user: dict = Depends(get_current_user)):
    """下载 MD 分析结果包（CSV + 图片）。"""
    task = _task_owner_or_404(task_id, user)
    _md_download_guard(task)
    if not task.md_analysis_zip or not Path(task.md_analysis_zip).is_file():
        raise HTTPException(status_code=404, detail="分析结果包尚未生成")
    return FileResponse(
        task.md_analysis_zip,
        media_type="application/zip",
        filename=f"{task_id}_analysis.zip",
    )


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, user: dict = Depends(get_current_user)):
    task = _task_owner_or_404(task_id, user)
    return {"logs": task.log_lines}


@router.get("/tasks/{task_id}/structure/complex.pdb")
async def get_complex_structure(task_id: str):
    """返回蛋白-配体复合物 PDB（不含水与离子），供 NGL 可视化。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成，暂无可视化结构")

    pdb = ensure_complex_pdb(task.work_dir)
    if not pdb or not Path(pdb).exists():
        raise HTTPException(status_code=404, detail="复合物结构文件不存在")

    return FileResponse(
        pdb,
        media_type="chemical/x-pdb",
        filename="complex.pdb",
    )


@router.get("/tasks/{task_id}/ligand/forcefield")
async def get_ligand_forcefield(task_id: str):
    """返回配体 GAFF2 力场 JSON（原子类型、电荷、键/角/二面角参数）。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    try:
        return ensure_ligand_forcefield_json(task.work_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (OSError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"力场解析失败: {e}") from e


@router.get("/tasks/{task_id}/ligand/structure.mol2")
async def get_ligand_mol2(task_id: str):
    """返回 GAFF2 参数化后的配体 mol2，供 NGL 渲染。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    mol2 = _find_gaff_mol2(Path(task.work_dir))
    if not mol2:
        raise HTTPException(status_code=404, detail="配体 mol2 不存在")

    return FileResponse(
        mol2,
        media_type="chemical/x-mol2",
        filename="ligand_gaff.mol2",
    )


class VisitBody(BaseModel):
    path: str = "/"


def _client_ip(r: Request) -> str:
    """从请求头获取客户端 IP（兼容反向代理）。"""
    xff = r.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if r.client:
        return r.client.host or "unknown"
    return "unknown"


@router.post("/analytics/visit")
async def track_visit(r: Request, body: VisitBody):
    """记录页面访问（前端上报，不含个人身份信息）。"""
    record_visit(
        Path(ANALYTICS_FILE),
        body.path,
        _client_ip(r),
        r.headers.get("user-agent", ""),
    )
    return {"ok": True}


@router.get("/admin/analytics/stats")
async def admin_analytics_stats(admin_key: str = ""):
    """管理员：查看访问统计与 MD 完成分档。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")
    data = get_analytics_stats(Path(ANALYTICS_FILE))
    md = collect_md_completion_stats(tasks.values())
    # 为排行补充邮箱，便于对照
    db = Path(USERS_DB)
    enriched = []
    for row in md.get("md_top_users", []):
        u = get_user_by_id(db, row["user_id"]) if row.get("user_id") else None
        enriched.append({
            **row,
            "email": u.get("email", "") if u else "",
        })
    md["md_top_users"] = enriched
    data.update(md)
    return data


@router.get("/admin/users/stats")
async def admin_users_stats(admin_key: str = ""):
    """管理员：查看注册用户统计（含每人已完成模拟数）。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")
    db = Path(USERS_DB)
    md = collect_md_completion_stats(tasks.values())
    per_user = md.get("md_per_user") or {}
    recent = list_recent_users(db, limit=50)
    for u in recent:
        u["md_completed"] = int(per_user.get(u.get("user_id", ""), 0))
    return {
        "total": count_users(db),
        "recent": recent,
        "md_completed_total": md.get("md_completed_total", 0),
        "md_by_ns": md.get("md_by_ns", {}),
    }
