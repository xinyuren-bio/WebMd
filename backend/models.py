import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

META_FILENAME = "task_meta.json"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING_PROTEIN = "processing_protein"
    PROCESSING_LIGAND = "processing_ligand"
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

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
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
        }

    def save(self) -> None:
        """将任务元数据持久化到工作目录。"""
        if not self.work_dir:
            return
        meta = Path(self.work_dir) / META_FILENAME
        data = {
            "task_id": self.task_id,
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
        }
        meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, p: Path) -> "Task | None":
        """从 meta.json 恢复任务对象。"""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            task = cls(
                task_id=data["task_id"],
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
