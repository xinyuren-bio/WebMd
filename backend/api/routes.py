import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from config import TASKS_DIR, DEFAULT_PARAMS, PAYMENT_AMOUNT, PAYMENT_QR_URL, PAYMENT_CURRENCY
from models import Task, TaskStatus, tasks
from engine.pipeline import run_pipeline
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


def _execute_task(task_id: str, pdb_path: str, mol2_path: str, params: dict):
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
        output_file = run_pipeline(
            task.work_dir, pdb_path, mol2_path, params, update_status
        )
        task.output_file = output_file
        task.status = TaskStatus.COMPLETED
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error_message = str(e)
        logger.exception("任务 %s 失败", task_id)
    finally:
        engine_logger.removeHandler(log_handler)
        task.save()


@router.post("/tasks")
async def create_task(
    background_tasks: BackgroundTasks,
    pdb_file: UploadFile = File(...),
    mol2_file: UploadFile = File(...),
    temperature: float = Form(default=DEFAULT_PARAMS["temperature"]),
    pressure: float = Form(default=DEFAULT_PARAMS["pressure"]),
    timestep: float = Form(default=DEFAULT_PARAMS["timestep"]),
    simulation_time_ns: float = Form(default=DEFAULT_PARAMS["simulation_time_ns"]),
    constraints: str = Form(default=DEFAULT_PARAMS["constraints"]),
    nonbonded_cutoff: float = Form(default=DEFAULT_PARAMS["nonbonded_cutoff"]),
    box_padding: float = Form(default=DEFAULT_PARAMS["box_padding"]),
    ion_conc: float = Form(default=DEFAULT_PARAMS["ion_conc"]),
    tau_t: float = Form(default=DEFAULT_PARAMS["tau_t"]),
    tau_p: float = Form(default=DEFAULT_PARAMS["tau_p"]),
    report_interval_ps: float = Form(default=DEFAULT_PARAMS["report_interval_ps"]),
):
    """创建新的 GROMACS MD 前处理任务。"""
    task = Task()
    task_id = task.task_id

    work_dir = Path(TASKS_DIR) / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    pdb_path = work_dir / pdb_file.filename
    mol2_path = work_dir / mol2_file.filename
    with open(pdb_path, "wb") as f:
        f.write(await pdb_file.read())
    with open(mol2_path, "wb") as f:
        f.write(await mol2_file.read())

    params = {
        "temperature": temperature,
        "pressure": pressure,
        "timestep": timestep,
        "simulation_time_ns": simulation_time_ns,
        "constraints": constraints,
        "nonbonded_cutoff": nonbonded_cutoff,
        "box_padding": box_padding,
        "ion_conc": ion_conc,
        "tau_t": tau_t,
        "tau_p": tau_p,
        "report_interval_ps": report_interval_ps,
    }

    task.work_dir = str(work_dir)
    task.params = params
    tasks[task_id] = task
    task.save()

    background_tasks.add_task(_execute_task, task_id, str(pdb_path), str(mol2_path), params)

    logger.info("任务 %s 已创建", task_id)
    return task.to_dict()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.to_dict()


@router.get("/payment/config")
async def get_payment_config():
    """返回付费下载配置（金额与收款码）。"""
    return {
        "amount": PAYMENT_AMOUNT,
        "currency": PAYMENT_CURRENCY,
        "qr_url": PAYMENT_QR_URL,
    }


@router.get("/tasks/{task_id}/payment")
async def get_task_payment(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_id": task_id,
        "paid": task.paid,
        "paid_at": task.paid_at,
        "amount": PAYMENT_AMOUNT,
        "currency": PAYMENT_CURRENCY,
        "qr_url": PAYMENT_QR_URL,
    }


@router.post("/tasks/{task_id}/payment/confirm")
async def confirm_task_payment(task_id: str):
    """用户扫码支付后确认，解锁下载。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成，暂不可支付下载")
    if task.paid:
        return {"task_id": task_id, "paid": True, "paid_at": task.paid_at}

    task.paid = True
    task.paid_at = time.time()
    task.save()
    logger.info("任务 %s 已确认支付", task_id)
    return {"task_id": task_id, "paid": True, "paid_at": task.paid_at}


@router.get("/tasks/{task_id}/download")
async def download_task(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务尚未完成")
    if not task.paid:
        raise HTTPException(status_code=402, detail="请先完成支付后再下载")
    if not task.output_file or not Path(task.output_file).exists():
        raise HTTPException(status_code=400, detail="输出文件不存在")

    return FileResponse(
        task.output_file,
        media_type="application/gzip",
        filename=f"gromacs_md_{task_id}.tar.gz",
    )


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
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
