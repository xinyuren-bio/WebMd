# ==================================================
# 功能说明：WebMD REST API（任务创建、支付、管理与 MD 回调）
# 使用方法：由 FastAPI 挂载；前处理经后台任务全局串行执行
# 依赖环境：fastapi、本仓库 backend 包
# 生成时间：2026-07-20
# ==================================================

import json
import logging
import tempfile
import threading
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
    ALLOWED_SALT_TYPES, MAX_ACTIVE_PREP_TASKS, MAX_PROTEIN_RESIDUES,
    is_protein_aa_limit_exempt,
    PAYMENT_ENABLED, PAYMENT_AMOUNT, PAYMENT_QR_URL, WECHAT_QR_URL, PAYMENT_CURRENCY,
    TIP_ENABLED, TIP_QR_URL, ANALYTICS_FILE, AUTODL_MARKET_URL, MD_CALLBACK_SECRET,
)
from payment_util import (
    calc_payment_amount,
    verify_admin_key,
    price_for_sim_ns,
    qr_urls_for_sim_ns,
    price_for,
    qr_urls_for,
    size_tier_for,
    can_use_free_10ns,
    free_10ns_remaining,
    FREE_10NS_QUOTA,
)
from analytics_util import record_visit, get_analytics_stats, collect_md_completion_stats
from email_util import (
    send_admin_payment_notify,
    send_admin_need_autodl_notify,
    send_admin_md_completed_notify,
    send_admin_prep_done_notify,
    send_user_md_completed_notify,
    send_user_prep_done_notify,
)
from user_store import get_user_by_id, get_user_by_email, count_users, list_recent_users
from config import USERS_DB
from api.deps import get_current_user
from models import Task, TaskStatus, tasks
from engine.pipeline import run_pipeline
from engine.peptide_seq_rebuild import NeedPeptideSequence, normalize_peptide_sequence
from engine.autodl_runner import (
    submit_md_job, dispatch_queued_jobs, pull_analysis_from_remote,
    pull_deliverables_from_remote, finalize_md_delivery,
)
from ssh_util import parse_ssh_command
from engine.structure_export import ensure_complex_pdb
from engine.ligand_ff import ensure_ligand_forcefield_json, _find_gaff_mol2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# 全局前处理互斥锁：多用户可同时提交，但同一时刻只跑一个 tleap/antechamber 流水线
_PREP_LOCK = threading.Lock()


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
    """后台执行前处理任务（全局串行，避免并发 tleap 打爆内存）。"""
    task = tasks.get(task_id)
    if not task:
        return

    # 排队等待：状态保持 pending，用户可见「等待开始」
    if _PREP_LOCK.locked():
        logger.info("任务 %s 排队等待全局前处理名额", task_id)
        task.status = TaskStatus.PENDING
        task.save()

    with _PREP_LOCK:
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
            if task.params.get("confirmed_peptide_sequence"):
                params = dict(params)
                params["confirmed_peptide_sequence"] = task.params["confirmed_peptide_sequence"]
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
            task.params.pop("peptide_sequence_needed", None)
            from gro_util import count_gro_atoms
            gro_p = Path(task.work_dir) / "system.gro"
            task.atom_count = count_gro_atoms(gro_p)
        except NeedPeptideSequence as e:
            task.status = TaskStatus.AWAITING_PEPTIDE_SEQUENCE
            task.error_message = str(e)
            task.params = params
            task.params["peptide_sequence_needed"] = True
            if e.hint_n_res is not None:
                task.params["peptide_sequence_hint_n"] = int(e.hint_n_res)
            logger.info("任务 %s 等待肽序列确认", task_id)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            logger.exception("任务 %s 失败", task_id)
        finally:
            engine_logger.removeHandler(log_handler)
            # 落盘/刷新处理报告（成功路径 pipeline 已写；失败路径在此补写）
            try:
                from engine.processing_report import finalize_report, clear_report
                st = "completed" if task.status == TaskStatus.COMPLETED else (
                    "failed" if task.status == TaskStatus.FAILED else str(task.status.value)
                )
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    finalize_report(
                        task.work_dir,
                        task.params,
                        status=st,
                        error_message=task.error_message or "",
                    )
                clear_report()
            except Exception:
                logger.exception("任务 %s 处理报告落盘异常", task_id)
            task.save()
            # 前处理终态发邮件，避免用户因串行排队一直盯页面
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                u = get_user_by_id(Path(USERS_DB), task.user_id) if task.user_id else None
                em = (u or {}).get("email") or ""
                try:
                    send_user_prep_done_notify(
                        em,
                        task_id,
                        SITE_BASE_URL,
                        ok=(task.status == TaskStatus.COMPLETED),
                        error_message=task.error_message or "",
                    )
                except Exception:
                    logger.exception("任务 %s 前处理结果邮件发送异常", task_id)
                # 管理员：体系处理报告（用户邮件保持不变）
                try:
                    pr = task.params.get("processing_report") or {}
                    report_txt = pr.get("report_txt") or str(
                        Path(task.work_dir) / "PROCESSING_REPORT.txt"
                    )
                    send_admin_prep_done_notify(
                        task_id,
                        em,
                        SITE_BASE_URL,
                        ok=(task.status == TaskStatus.COMPLETED),
                        error_message=task.error_message or "",
                        report_summary=pr.get("email_summary") or "\n".join(
                            pr.get("summary_lines") or []
                        ),
                        report_txt_path=report_txt,
                        atom_count=int(task.atom_count or 0),
                    )
                except Exception:
                    logger.exception("任务 %s 管理员处理报告邮件发送异常", task_id)


def _task_owner_or_404(task_id: str, user: dict) -> Task:
    """校验任务存在且属于当前用户。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.user_id and task.user_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return task




def _resolved_amount(task: Task):
    """计算任务应付金额；面议档（>30 万原子）返回 None，交由客服线下确认。"""
    atoms = int(getattr(task, "atom_count", 0) or 0)
    base = price_for(
        float(task.params.get("simulation_time_ns", 100)),
        atom_count=atoms,
        user_id=task.user_id,
        all_tasks=tasks,
    )
    if base is None:
        return None
    return calc_payment_amount(task.task_id, base, task.user_id)


def _payment_payload(task: Task) -> dict:
    """组装任务支付信息（金额与收款码按体系大小分档；含免费额度与面议）。"""
    sim_ns = float(task.params.get("simulation_time_ns", 100))
    atoms = int(getattr(task, "atom_count", 0) or 0)
    free_ok = can_use_free_10ns(task, tasks)
    remain = free_10ns_remaining(task.user_id, tasks) if task.user_id else 0
    # 体系分档信息（原子数未知时为空 dict，走时长定价回退）
    size_info = size_tier_for(atoms)
    negotiable = bool(size_info.get("negotiable"))
    base = price_for(sim_ns, atom_count=atoms, user_id=task.user_id, all_tasks=tasks)
    qr_url, wechat_qr_url = qr_urls_for(sim_ns, atom_count=atoms)

    if task.payment_status in ("pending", "paid") and task.payment_amount is not None:
        amount = task.payment_amount
    elif base is None:
        # 面议（>30 万原子）：不自动定价，等待客服确认
        amount = None
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
        "atom_count": atoms,
        "size_tier": size_info.get("tier"),
        "negotiable": negotiable,
        "support_qr_url": size_info.get("qr_url") if negotiable else "",
        "currency": PAYMENT_CURRENCY,
        "qr_url": qr_url,
        "wechat_qr_url": wechat_qr_url,
        "free_eligible": bool(free_ok and task.payment_status == "unpaid"),
        "free_quota_remaining": remain,
        "free_quota_total": FREE_10NS_QUOTA,
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
    pdbqt_file: Optional[UploadFile] = File(None),
    ligand_type: str = Form(default="mol2"),
    is_cyclic_peptide: str = Form(default="0"),
    is_linear_peptide: str = Form(default="0"),
    peptide_upload_mode: str = Form(default="separate"),
    protein_chains: str = Form(default=""),
    peptide_chain: str = Form(default=""),
    ligand_residues: str = Form(default=""),
    ligand_pose_index: str = Form(default="0"),
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

    # 每用户最多同时保留 N 个未释放的前处理任务（含待付款）
    uid = user["user_id"]
    active = [
        t for t in tasks.values()
        if t.user_id == uid and t.occupies_prep_slot()
    ]
    if len(active) >= MAX_ACTIVE_PREP_TASKS:
        ids = "、".join(t.task_id for t in sorted(active, key=lambda x: x.created_at)[:8])
        raise HTTPException(
            status_code=429,
            detail=(
                f"每位用户最多同时提交 {MAX_ACTIVE_PREP_TASKS} 个参数化任务"
                f"（待付款也计入名额）。请先对已有任务完成付款并启动模拟后再提交。"
                f"当前占用任务：{ids}"
            ),
        )

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
    if pep_mode not in ("separate", "complex", "pdbqt"):
        pep_mode = "separate"
    # 环肽暂不支持 PDBQT；线形肽可用对接构象
    if is_cyc and pep_mode == "pdbqt":
        raise HTTPException(status_code=400, detail="环肽暂不支持 PDBQT 对接构象模式")

    mol2_paths: list[str] = []
    cyclic_pdb_path: Optional[str] = None
    pdb_path = work_dir / "protein.pdb"
    lig_res_keys: list[str] = []
    pose_idx = 0
    pose_count = 0

    def _norm_ch(s: str) -> str:
        from engine.pdb_chains import norm_chain
        return norm_chain(s)

    if is_pep and pep_mode == "complex":
        # 复合物 PDB：用户选择蛋白链与肽链后由服务端拆分
        from engine.pdb_chains import split_complex

        raw = Path(pdb_file.filename or "complex.pdb").name
        if not raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="复合物须为 PDB 格式")
        complex_path = work_dir / "complex_upload.pdb"
        with open(complex_path, "wb") as f:
            f.write(await pdb_file.read())

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
        pdb_path = Path(prot_out)
        cyc_dest = work_dir / (
            "cyclic_peptide_upload.pdb" if is_cyc else "linear_peptide_upload.pdb"
        )
        if Path(pep_out).resolve() != cyc_dest.resolve():
            cyc_dest.write_bytes(Path(pep_out).read_bytes())
        cyclic_pdb_path = str(cyc_dest)
    elif is_lin and pep_mode == "pdbqt":
        # 蛋白 PDB + 线形肽对接 PDBQT：抽取选定构象为肽 PDB
        from engine.pdbqt_util import pdbqt_to_pdb

        pdb_raw = Path(pdb_file.filename or "protein.pdb").name
        if not pdb_raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="蛋白须为 PDB 格式")
        with open(pdb_path, "wb") as f:
            f.write(await pdb_file.read())

        if pdbqt_file is None or not pdbqt_file.filename:
            raise HTTPException(status_code=400, detail="PDBQT 模式请上传线形肽 PDBQT 文件")
        qt_raw = Path(pdbqt_file.filename).name
        if not qt_raw.lower().endswith(".pdbqt"):
            raise HTTPException(status_code=400, detail="线形肽须为 PDBQT 格式")
        qt_dest = work_dir / "ligand_upload.pdbqt"
        with open(qt_dest, "wb") as f:
            f.write(await pdbqt_file.read())

        try:
            pose_idx = int(str(ligand_pose_index or "0").strip())
        except ValueError as e:
            raise HTTPException(status_code=400, detail="构象下标须为整数") from e

        pep_out = work_dir / "linear_peptide_upload.pdb"
        try:
            cyclic_pdb_path, pose_count = pdbqt_to_pdb(
                qt_dest, pep_out, index=pose_idx, work_dir=work_dir,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif not is_pep and pep_mode == "complex":
        # 小分子复合物：蛋白链 + HETATM 配体残基 → 自动转 MOL2
        from engine.pdb_chains import split_complex_mol2

        raw = Path(pdb_file.filename or "complex.pdb").name
        if not raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="复合物须为 PDB 格式")
        complex_path = work_dir / "complex_upload.pdb"
        with open(complex_path, "wb") as f:
            f.write(await pdb_file.read())

        raw_prot = [x for x in (protein_chains or "").split(",") if x != ""]
        prot_list = [_norm_ch(x) for x in raw_prot]
        lig_res_keys = [x.strip() for x in (ligand_residues or "").split(",") if x.strip()]
        if not prot_list:
            raise HTTPException(status_code=400, detail="复合物模式请至少选择一条蛋白链")
        if not lig_res_keys:
            raise HTTPException(status_code=400, detail="复合物模式请至少选择一个配体残基")
        if len(lig_res_keys) > 3:
            raise HTTPException(status_code=400, detail="最多选择 3 个配体残基")
        try:
            prot_out, mol2_paths = split_complex_mol2(
                complex_path, prot_list, lig_res_keys, work_dir,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        pdb_path = Path(prot_out)
    elif not is_pep and pep_mode == "pdbqt":
        # 蛋白 PDB + 对接配体 PDBQT：抽取选定构象并转 MOL2
        from engine.pdbqt_util import pdbqt_to_mol2

        pdb_raw = Path(pdb_file.filename or "protein.pdb").name
        if not pdb_raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="蛋白须为 PDB 格式")
        with open(pdb_path, "wb") as f:
            f.write(await pdb_file.read())

        if pdbqt_file is None or not pdbqt_file.filename:
            raise HTTPException(status_code=400, detail="PDBQT 模式请上传配体 PDBQT 文件")
        qt_raw = Path(pdbqt_file.filename).name
        if not qt_raw.lower().endswith(".pdbqt"):
            raise HTTPException(status_code=400, detail="配体须为 PDBQT 格式")
        qt_dest = work_dir / "ligand_upload.pdbqt"
        with open(qt_dest, "wb") as f:
            f.write(await pdbqt_file.read())

        try:
            pose_idx = int(str(ligand_pose_index or "0").strip())
        except ValueError as e:
            raise HTTPException(status_code=400, detail="构象下标须为整数") from e

        mol2_out = work_dir / "ligand_1.mol2"
        try:
            mol2_path, pose_count = pdbqt_to_mol2(
                qt_dest, mol2_out, index=pose_idx, work_dir=work_dir,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        mol2_paths = [mol2_path]
    else:
        # 蛋白文件名消毒，避免空格
        pdb_raw = Path(pdb_file.filename or "protein.pdb").name
        if not pdb_raw.lower().endswith(".pdb"):
            raise HTTPException(status_code=400, detail="蛋白须为 PDB 格式")
        with open(pdb_path, "wb") as f:
            f.write(await pdb_file.read())

    if is_pep and pep_mode == "separate":
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
    elif not is_pep and pep_mode == "separate":
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

    # 蛋白标准氨基酸数上限：超限直接拒绝，避免小内存机溶剂化 OOM（白名单用户豁免）
    from engine.pdb_chains import count_std_aa_residues

    try:
        n_aa = count_std_aa_residues(pdb_path)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无法读取蛋白 PDB：{e}") from e
    if n_aa > MAX_PROTEIN_RESIDUES and not is_protein_aa_limit_exempt(user.get("email", "")):
        raise HTTPException(
            status_code=400,
            detail=(
                f"蛋白共 {n_aa} 个氨基酸，超过当前上限 {MAX_PROTEIN_RESIDUES}。"
                f"超大体系请微信联系管理员 biomd777 处理。"
            ),
        )

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
        "peptide_upload_mode": pep_mode if (is_pep or lt == "mol2") else "separate",
        "protein_chains": protein_chains if pep_mode == "complex" else "",
        "peptide_chain": peptide_chain if is_pep and pep_mode == "complex" else "",
        "ligand_residues": ",".join(lig_res_keys) if not is_pep and pep_mode == "complex" else "",
        "ligand_pose_index": pose_idx if pep_mode == "pdbqt" else 0,
        "pdbqt_pose_count": pose_count if pep_mode == "pdbqt" else 0,
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


def _task_list_item(task: Task) -> dict:
    """组装「我的任务」列表项（不含日志等大字段）。"""
    sim_ns = task.params.get("simulation_time_ns")
    lt = (task.params.get("ligand_type") or "").strip().lower()
    if not lt:
        if task.params.get("is_cyclic_peptide"):
            lt = "cyclic"
        elif task.params.get("is_linear_peptide"):
            lt = "linear"
        else:
            lt = "mol2"
    ligand_label = {
        "mol2": "小分子",
        "cyclic": "环肽",
        "linear": "线形肽",
    }.get(lt, lt or "—")
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "status_label": task.status.label,
        "payment_status": task.payment_status,
        "paid": task.paid,
        "md_status": task.md_status,
        "md_status_label": {
            "none": "未开始",
            "queued": "排队等待",
            "running": "模拟运行中",
            "completed": "模拟已完成",
            "failed": "模拟失败",
        }.get(task.md_status, task.md_status),
        "simulation_time_ns": sim_ns,
        "ligand_type": lt,
        "ligand_label": ligand_label,
        "created_at": task.created_at,
        "error_message": (task.error_message or "")[:200],
        "status_url": f"/status.html?id={task.task_id}",
    }


@router.get("/tasks")
async def list_my_tasks(
    user: dict = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=500),
):
    """列出当前登录用户提交的任务（按创建时间倒序）。"""
    uid = user["user_id"]
    mine = [t for t in tasks.values() if t.user_id == uid]
    mine.sort(key=lambda t: t.created_at or 0.0, reverse=True)
    items = [_task_list_item(t) for t in mine[:limit]]
    return {"tasks": items, "total": len(mine)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: dict = Depends(get_current_user)):
    task = _task_owner_or_404(task_id, user)
    return task.to_dict()


class PeptideSequenceBody(BaseModel):
    """用户提交的线形肽单字母序列。"""
    sequence: str = Field(min_length=2, max_length=200)


@router.post("/tasks/{task_id}/peptide-sequence")
async def confirm_peptide_sequence(
    task_id: str,
    body: PeptideSequenceBody,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """确认非标准肽 PDB 的单字母序列并续跑前处理。"""
    task = _task_owner_or_404(task_id, user)
    if task.status != TaskStatus.AWAITING_PEPTIDE_SEQUENCE:
        raise HTTPException(status_code=400, detail="当前任务不需要确认肽序列")
    try:
        seq = normalize_peptide_sequence(body.sequence)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    task.params["confirmed_peptide_sequence"] = seq
    task.params["peptide_sequence_needed"] = True
    task.error_message = ""
    task.status = TaskStatus.PROCESSING_LIGAND
    task.save()

    work = Path(task.work_dir)
    pdb_path = str(work / "protein.pdb")
    # 肽文件：与创建任务时命名一致
    pep_candidates = [
        work / "linear_peptide_upload.pdb",
        work / "peptide_from_complex.pdb",
        work / "cyclic_peptide_upload.pdb",
    ]
    cyclic_pdb = next((str(p) for p in pep_candidates if p.is_file()), None)
    if not cyclic_pdb:
        raise HTTPException(status_code=400, detail="找不到肽 PDB 文件，请重新提交任务")

    params = dict(task.params)
    background_tasks.add_task(
        _execute_task,
        task_id,
        pdb_path,
        None,
        params,
        cyclic_pdb,
    )
    return {
        "ok": True,
        "task_id": task_id,
        "status": task.status.value,
        "confirmed_peptide_sequence": seq,
    }


@router.get("/tasks/{task_id}/public")
async def get_task_public(task_id: str):
    """公开任务状态（扫码查看，无需登录）。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    d = task.to_public_dict()
    atoms = int(getattr(task, "atom_count", 0) or 0)
    size_info = size_tier_for(atoms)
    d["size_tier"] = size_info.get("tier")
    d["negotiable"] = bool(size_info.get("negotiable"))
    if size_info.get("negotiable"):
        d["support_qr_url"] = size_info.get("qr_url")
    if d.get("payment_amount") is None and not size_info.get("negotiable"):
        sim_ns = float(task.params.get("simulation_time_ns", 100))
        base = price_for(sim_ns, atom_count=atoms, user_id=task.user_id, all_tasks=tasks)
        if base is not None:
            d["payment_amount"] = calc_payment_amount(task.task_id, base, task.user_id)
    return d


@router.get("/payment/config")
async def get_payment_config():
    """返回付费/打赏配置。"""
    from payment_util import pricing_table, size_pricing_table

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
        "size_pricing": size_pricing_table(),
        "free_10ns_quota": FREE_10NS_QUOTA,
    }


@router.get("/tasks/{task_id}/payment")
async def get_task_payment(task_id: str, user: dict = Depends(get_current_user)):
    task = _task_owner_or_404(task_id, user)
    return _payment_payload(task)


@router.post("/tasks/{task_id}/payment/confirm")
async def confirm_task_payment(
    task_id: str,
    background_tasks: BackgroundTasks,
    payer_note: str = Form(default=""),
    user: dict = Depends(get_current_user),
):
    """用户声明已支付，进入待核实；若符合 10 ns 免费额度则立即解锁。"""
    if not PAYMENT_ENABLED:
        raise HTTPException(status_code=403, detail="付费功能未开启")
    task = _task_owner_or_404(task_id, user)
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="前处理尚未完成，暂不可支付")
    if task.paid or task.payment_status == "paid":
        return _payment_payload(task)
    if task.payment_status == "pending":
        return _payment_payload(task)

    # 10 ns 免费额度：直接标记已支付，无需管理员核实
    if can_use_free_10ns(task, tasks):
        task.payment_amount = 0.0
        task.paid = True
        task.paid_at = time.time()
        task.payment_status = "paid"
        task.payment_claimed_at = time.time()
        task.payment_note = "10ns免费额度"
        if task.md_status in ("none", "failed"):
            task.md_status = "queued"
        task.save()
        remain = free_10ns_remaining(task.user_id, tasks)
        logger.info(
            "任务 %s 使用 10 ns 免费额度解锁，用户剩余 %d/%d",
            task_id, remain, FREE_10NS_QUOTA,
        )
        background_tasks.add_task(_dispatch_after_approve, task_id)
        return _payment_payload(task)

    atoms = int(getattr(task, "atom_count", 0) or 0)
    base = price_for(
        float(task.params.get("simulation_time_ns", 100)),
        atom_count=atoms,
        user_id=task.user_id,
        all_tasks=tasks,
    )
    # 面议档（>30 万原子）：不支持自助付款，引导联系客服微信
    if base is None:
        raise HTTPException(
            status_code=400,
            detail="该体系较大（>30 万原子），价格需面议，请添加客服微信确认后开通。",
        )
    task.payment_amount = calc_payment_amount(task.task_id, base, task.user_id)
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
                "payment_amount": t.payment_amount or _resolved_amount(t),
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
        # 面议档金额为 None，此处保持不动，由管理员按线下确认金额入账
        task.payment_amount = _resolved_amount(task)
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


def _resolve_rerun_inputs(task: Task) -> tuple[str, list[str] | None, str | None]:
    """从任务工作目录解析重跑所需的蛋白/配体/肽路径。"""
    work = Path(task.work_dir)
    params = task.params or {}
    pdb_path = params.get("_resume_pdb") or str(work / "protein.pdb")
    if not Path(pdb_path).is_file():
        raise HTTPException(status_code=400, detail=f"缺少蛋白文件: {pdb_path}")

    lt = str(params.get("ligand_type") or "").strip().lower()
    if not lt:
        if params.get("is_cyclic_peptide"):
            lt = "cyclic"
        elif params.get("is_linear_peptide"):
            lt = "linear"
        else:
            lt = "mol2"

    if lt in ("cyclic", "linear"):
        cyclic = params.get("_resume_cyclic")
        if not cyclic or not Path(str(cyclic)).is_file():
            for name in (
                "linear_peptide_upload.pdb",
                "peptide_from_complex.pdb",
                "cyclic_peptide_upload.pdb",
                "linear_peptide.pdb",
            ):
                cand = work / name
                if cand.is_file():
                    cyclic = str(cand)
                    break
        if not cyclic or not Path(str(cyclic)).is_file():
            raise HTTPException(status_code=400, detail="缺少肽 PDB，无法重跑")
        return pdb_path, None, str(cyclic)

    mol2s = params.get("_resume_mol2s")
    if not mol2s:
        mol2s = sorted(str(p) for p in work.glob("ligand_*.mol2"))
    mol2s = [m for m in (mol2s or []) if Path(m).is_file()]
    if not mol2s:
        raise HTTPException(status_code=400, detail="缺少配体 MOL2，无法重跑")
    return pdb_path, mol2s, None


@router.post("/admin/tasks/{task_id}/rerun")
async def admin_rerun_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    admin_key: str = "",
):
    """管理员：从前处理流水线重跑卡住/失败的任务（不改支付状态）。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in (
        TaskStatus.FAILED,
        TaskStatus.SOLVATING,
        TaskStatus.PROCESSING_PROTEIN,
        TaskStatus.PROCESSING_LIGAND,
        TaskStatus.CONVERTING_GMX,
        TaskStatus.GENERATING_MDP,
        TaskStatus.PACKAGING,
        TaskStatus.PENDING,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {task.status.value} 不允许重跑（仅失败或前处理中可重跑）",
        )

    pdb_path, mol2s, cyclic = _resolve_rerun_inputs(task)
    task.error_message = ""
    task.status = TaskStatus.PENDING
    task.output_file = ""
    task.save()
    background_tasks.add_task(
        _execute_task, task_id, pdb_path, mol2s, dict(task.params), cyclic,
    )
    logger.info("管理员触发重跑任务 %s", task_id)
    return {
        "ok": True,
        "task_id": task_id,
        "message": "已触发前处理重跑",
        "pdb": pdb_path,
        "cyclic": cyclic,
        "mol2_count": len(mol2s or []),
    }


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
async def get_complex_structure(task_id: str, user: dict = Depends(get_current_user)):
    """返回蛋白-配体复合物 PDB（不含水与离子），供 NGL 可视化。仅任务所有者可访问。"""
    task = _task_owner_or_404(task_id, user)
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
async def get_ligand_forcefield(task_id: str, user: dict = Depends(get_current_user)):
    """返回配体 GAFF2 力场 JSON（原子类型、电荷、键/角/二面角参数）。仅任务所有者可访问。"""
    task = _task_owner_or_404(task_id, user)
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")

    try:
        return ensure_ligand_forcefield_json(task.work_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (OSError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"力场解析失败: {e}") from e


@router.get("/tasks/{task_id}/ligand/structure.mol2")
async def get_ligand_mol2(task_id: str, user: dict = Depends(get_current_user)):
    """返回 GAFF2 参数化后的配体 mol2，供 NGL 渲染。仅任务所有者可访问。"""
    task = _task_owner_or_404(task_id, user)
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
    # 每用户任务数（含未支付/失败）
    task_counts: dict[str, int] = {}
    for t in tasks.values():
        if t.user_id:
            task_counts[t.user_id] = task_counts.get(t.user_id, 0) + 1
    recent = list_recent_users(db, limit=50)
    for u in recent:
        uid = u.get("user_id", "")
        u["md_completed"] = int(per_user.get(uid, 0))
        u["task_count"] = int(task_counts.get(uid, 0))
    return {
        "total": count_users(db),
        "recent": recent,
        "md_completed_total": md.get("md_completed_total", 0),
        "md_by_ns": md.get("md_by_ns", {}),
        "tasks_total": len(tasks),
    }


@router.get("/admin/users/{user_id}/tasks")
async def admin_user_tasks(
    user_id: str,
    admin_key: str = "",
    limit: int = Query(default=200, ge=1, le=500),
):
    """管理员：查看指定注册用户提交的全部任务。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")
    u = get_user_by_id(Path(USERS_DB), user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    mine = [t for t in tasks.values() if t.user_id == user_id]
    mine.sort(key=lambda t: t.created_at or 0.0, reverse=True)
    items = [_task_list_item(t) for t in mine[:limit]]
    for it in items:
        it["user_id"] = user_id
        it["email"] = u.get("email", "")
    return {
        "user": {
            "user_id": u.get("user_id"),
            "email": u.get("email"),
            "created_at": u.get("created_at"),
        },
        "tasks": items,
        "total": len(mine),
    }


@router.get("/admin/tasks")
async def admin_list_tasks(
    admin_key: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    email: str = Query(default=""),
    user_id: str = Query(default=""),
):
    """管理员：任务总览（可按邮箱或用户 ID 筛选）。"""
    if not verify_admin_key(admin_key):
        raise HTTPException(status_code=403, detail="管理员密钥无效")
    db = Path(USERS_DB)
    uid_filter = (user_id or "").strip()
    email_q = (email or "").strip().lower()
    if email_q and not uid_filter:
        found = get_user_by_email(db, email_q)
        if not found:
            return {"tasks": [], "total": 0, "filter": {"email": email_q}}
        uid_filter = found["user_id"]

    items_src = list(tasks.values())
    if uid_filter:
        items_src = [t for t in items_src if t.user_id == uid_filter]
    items_src.sort(key=lambda t: t.created_at or 0.0, reverse=True)
    out = []
    for t in items_src[:limit]:
        row = _task_list_item(t)
        row["user_id"] = t.user_id
        u = get_user_by_id(db, t.user_id) if t.user_id else None
        row["email"] = (u or {}).get("email", "")
        out.append(row)
    return {
        "tasks": out,
        "total": len(items_src),
        "filter": {"user_id": uid_filter, "email": email_q},
    }
