import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from summary_util import sanitize_user_summary

logger = logging.getLogger(__name__)

META_FILENAME = "task_meta.json"


def _md_label(s: str) -> str:
    _m = {
        "none": "未开始",
        "queued": "排队等待",
        "running": "模拟运行中",
        "completed": "模拟已完成",
        "failed": "模拟失败",
    }
    return _m.get(s, s)


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING_PROTEIN = "processing_protein"
    PROCESSING_LIGAND = "processing_ligand"
    AWAITING_CHARGE_CONFIRM = "awaiting_charge_confirm"
    AWAITING_PEPTIDE_SEQUENCE = "awaiting_peptide_sequence"
    SOLVATING = "solvating"
    CONVERTING_GMX = "converting_gmx"
    GENERATING_MDP = "generating_mdp"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def label(self) -> str:
        _labels = {
            TaskStatus.PENDING: "等待开始",
            TaskStatus.PROCESSING_PROTEIN: "修复蛋白 (PDBFixer)",
            TaskStatus.PROCESSING_LIGAND: "小分子 GAFF2 参数化 (antechamber)",
            TaskStatus.AWAITING_CHARGE_CONFIRM: "等待确认配体净电荷",
            TaskStatus.AWAITING_PEPTIDE_SEQUENCE: "等待确认肽序列",
            TaskStatus.SOLVATING: "构建溶剂化体系 (tleap)",
            TaskStatus.CONVERTING_GMX: "转换为 GROMACS 拓扑 (acpype)",
            TaskStatus.GENERATING_MDP: "生成 GROMACS mdp 文件",
            TaskStatus.PACKAGING: "打包结果",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: "失败",
        }
        return _labels.get(self, self.value)


@dataclass
class Task:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    params: dict = field(default_factory=dict)
    work_dir: str = ""
    output_file: str = ""
    error_message: str = ""
    log_lines: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    paid: bool = False
    paid_at: float | None = None
    payment_status: str = "unpaid"  # unpaid | pending | paid
    payment_amount: float | None = None
    payment_claimed_at: float | None = None
    payment_note: str = ""
    md_status: str = "none"  # none | queued | running | completed | failed
    atom_count: int = 0
    autodl_ssh_command: str = ""
    autodl_ssh_host: str = ""
    autodl_ssh_port: int = 22
    autodl_ssh_user: str = "root"
    autodl_ssh_password: str = ""
    autodl_server_id: str = ""
    md_completed_at: float | None = None
    analysis_summary: str = ""
    md_sim_zip: str = ""
    md_analysis_zip: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "status_label": self.status.label,
            "params": self.params,
            "error_message": self.error_message,
            "output_file": self.output_file,
            "created_at": self.created_at,
            "paid": self.paid,
            "paid_at": self.paid_at,
            "payment_status": self.payment_status,
            "payment_amount": self.payment_amount,
            "payment_claimed_at": self.payment_claimed_at,
            "md_status": self.md_status,
            "atom_count": self.atom_count,
            # 净电荷确认弹窗所需字段（若有）
            "charge_confirm": self.params.get("charge_confirm"),
            "ligands_ff": self.params.get("ligands"),
            "peptide_sequence_needed": bool(self.params.get("peptide_sequence_needed")),
            "peptide_sequence_hint_n": self.params.get("peptide_sequence_hint_n"),
            "error_message": self.error_message if self.status == TaskStatus.AWAITING_PEPTIDE_SEQUENCE else "",
        }

    def occupies_prep_slot(self) -> bool:
        """是否占用「参数化」名额。

        设计思路：限制每用户并行前处理数量；待付款/处理中均占名额。
        已付款且模拟已排队/运行/完成/失败后释放；前处理失败不占名额以便重提。
        """
        if self.status == TaskStatus.FAILED:
            return False
        if self.payment_status == "paid" and self.md_status in (
            "queued", "running", "completed", "failed",
        ):
            return False
        return True

    def to_public_dict(self) -> dict:
        """公开状态页（无需登录）。"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "status_label": self.status.label,
            "payment_status": self.payment_status,
            "payment_amount": self.payment_amount,
            "paid": self.paid,
            "md_status": self.md_status,
            "md_status_label": _md_label(self.md_status),
            "simulation_time_ns": self.params.get("simulation_time_ns"),
            "created_at": self.created_at,
            "can_pay": self.status == TaskStatus.COMPLETED and self.payment_status == "unpaid",
            "can_download": self.paid and self.payment_status == "paid",
            "atom_count": self.atom_count,
            "analysis_summary": sanitize_user_summary(self.analysis_summary) if self.analysis_summary else "",
            "can_download_md_sim": bool(self.md_sim_zip and Path(self.md_sim_zip).is_file()),
            "can_download_md_analysis": bool(self.md_analysis_zip and Path(self.md_analysis_zip).is_file()),
            "peptide_sequence_needed": bool(self.params.get("peptide_sequence_needed")),
            "peptide_sequence_hint_n": self.params.get("peptide_sequence_hint_n"),
        }

    def save(self) -> None:
        """将任务元数据持久化到工作目录。"""
        if not self.work_dir:
            return
        meta = Path(self.work_dir) / META_FILENAME
        data = {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "params": self.params,
            "work_dir": self.work_dir,
            "output_file": self.output_file,
            "error_message": self.error_message,
            "log_lines": self.log_lines[-500:],
            "created_at": self.created_at,
            "paid": self.paid,
            "paid_at": self.paid_at,
            "payment_status": self.payment_status,
            "payment_amount": self.payment_amount,
            "payment_claimed_at": self.payment_claimed_at,
            "payment_note": self.payment_note,
            "md_status": self.md_status,
            "atom_count": self.atom_count,
            "autodl_ssh_command": self.autodl_ssh_command,
            "autodl_ssh_host": self.autodl_ssh_host,
            "autodl_ssh_port": self.autodl_ssh_port,
            "autodl_ssh_user": self.autodl_ssh_user,
            "autodl_ssh_password": self.autodl_ssh_password,
            "autodl_server_id": self.autodl_server_id,
            "md_completed_at": self.md_completed_at,
            "analysis_summary": self.analysis_summary,
            "md_sim_zip": self.md_sim_zip,
            "md_analysis_zip": self.md_analysis_zip,
        }
        meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, p: Path) -> "Task | None":
        """从 meta.json 恢复任务对象。"""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            task = cls(
                task_id=data["task_id"],
                user_id=data.get("user_id", ""),
                status=TaskStatus(data["status"]),
                params=data.get("params", {}),
                work_dir=data.get("work_dir", ""),
                output_file=data.get("output_file", ""),
                error_message=data.get("error_message", ""),
                log_lines=data.get("log_lines", []),
                created_at=data.get("created_at", time.time()),
                paid=data.get("paid", False),
                paid_at=data.get("paid_at"),
                payment_status=data.get("payment_status", "paid" if data.get("paid") else "unpaid"),
                payment_amount=data.get("payment_amount"),
                payment_claimed_at=data.get("payment_claimed_at"),
                payment_note=data.get("payment_note", ""),
                md_status=data.get("md_status", "none"),
                atom_count=int(data.get("atom_count", 0) or 0),
                autodl_ssh_command=data.get("autodl_ssh_command", ""),
                autodl_ssh_host=data.get("autodl_ssh_host", ""),
                autodl_ssh_port=int(data.get("autodl_ssh_port", 22) or 22),
                autodl_ssh_user=data.get("autodl_ssh_user", "root"),
                autodl_ssh_password=data.get("autodl_ssh_password", ""),
                autodl_server_id=data.get("autodl_server_id", ""),
                md_completed_at=data.get("md_completed_at"),
                analysis_summary=data.get("analysis_summary", ""),
                md_sim_zip=data.get("md_sim_zip", ""),
                md_analysis_zip=data.get("md_analysis_zip", ""),
            )
            return task
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("无法加载任务 %s: %s", p, e)
            return None


tasks: dict[str, Task] = {}


def load_tasks_from_disk(tasks_dir: str) -> None:
    """启动时从磁盘恢复历史任务。"""
    root = Path(tasks_dir)
    if not root.exists():
        return
    for d in root.iterdir():
        if not d.is_dir():
            continue
        meta = d / META_FILENAME
        if not meta.exists():
            continue
        task = Task.load(meta)
        if task:
            tasks[task.task_id] = task
    logger.info("已从磁盘恢复 %d 个任务", len(tasks))
