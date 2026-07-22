# ==================================================
# 功能说明：前处理「体系处理报告」收集、落盘与邮件摘要
# 使用方法：pipeline 开头 begin_report；各模块用 add_event 埋点；结束 finalize_report
# 依赖环境：Python 标准库
# 生成时间：2026-07-22
# ==================================================

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 当前任务的处理报告（后台线程内有效；无上下文时 add_event 静默忽略）
_CURRENT: ContextVar["ProcessingReport | None"] = ContextVar(
    "webmd_processing_report", default=None,
)


@dataclass
class ProcessingEvent:
    """单条处理记录。"""

    category: str
    action: str
    detail: str = ""
    level: str = "info"  # info | note | warn
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingReport:
    """一次前处理任务的处理事件集合。"""

    task_id: str = ""
    work_dir: str = ""
    events: list[ProcessingEvent] = field(default_factory=list)
    sealed: bool = False

    def add(
        self,
        category: str,
        action: str,
        detail: str = "",
        *,
        level: str = "info",
        **meta: Any,
    ) -> None:
        """追加一条处理事件。"""
        if self.sealed:
            return
        self.events.append(
            ProcessingEvent(
                category=str(category),
                action=str(action),
                detail=str(detail or ""),
                level=str(level or "info"),
                meta={k: v for k, v in meta.items() if v is not None},
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化结构。"""
        return {
            "task_id": self.task_id,
            "work_dir": self.work_dir,
            "n_events": len(self.events),
            "events": [asdict(e) for e in self.events],
        }

    def to_text(self, *, status: str = "", error_message: str = "") -> str:
        """生成管理员可读的纯文本报告。"""
        lines = [
            "WebMD 体系处理报告",
            "==================",
            f"任务 ID: {self.task_id or '—'}",
            f"事件数: {len(self.events)}",
        ]
        if status:
            lines.append(f"前处理结果: {status}")
        if error_message:
            lines.append(f"错误信息: {error_message[:500]}")
        lines.append("")
        if not self.events:
            lines.append("（本次前处理未记录到特殊自动修复/改动；或尚未埋点的步骤）")
            lines.append("")
            return "\n".join(lines)

        by_cat: dict[str, list[ProcessingEvent]] = {}
        for e in self.events:
            by_cat.setdefault(e.category, []).append(e)

        for cat, items in by_cat.items():
            lines.append(f"【{cat}】")
            for e in items:
                tag = {"info": "", "note": "[注意] ", "warn": "[警告] "}.get(e.level, "")
                body = f"{tag}{e.action}"
                if e.detail:
                    body += f"：{e.detail}"
                lines.append(f"  - {body}")
            lines.append("")
        return "\n".join(lines)

    def email_summary(self, *, max_items: int = 40) -> str:
        """邮件正文用的精简摘要。"""
        if not self.events:
            return "（无自动修复/改动记录）"
        lines: list[str] = []
        for e in self.events[:max_items]:
            tag = {"note": "注意·", "warn": "警告·"}.get(e.level, "")
            line = f"· [{e.category}] {tag}{e.action}"
            if e.detail:
                line += f"：{e.detail}"
            lines.append(line)
        if len(self.events) > max_items:
            lines.append(f"· …另有 {len(self.events) - max_items} 条，见附件 PROCESSING_REPORT.txt")
        return "\n".join(lines)


def begin_report(task_id: str, work_dir: str | Path) -> ProcessingReport:
    """开启当前任务的处理报告上下文。"""
    rep = ProcessingReport(task_id=str(task_id or ""), work_dir=str(work_dir))
    _CURRENT.set(rep)
    return rep


def get_report() -> ProcessingReport | None:
    """获取当前上下文中的报告（可能为 None）。"""
    return _CURRENT.get()


def add_event(
    category: str,
    action: str,
    detail: str = "",
    *,
    level: str = "info",
    **meta: Any,
) -> None:
    """向当前报告追加事件；无上下文时忽略。"""
    rep = _CURRENT.get()
    if rep is None:
        return
    rep.add(category, action, detail, level=level, **meta)


def finalize_report(
    work_dir: str | Path,
    params: dict | None = None,
    *,
    status: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    """将报告写入任务目录，并可选写入 params['processing_report']。

    返回写入的精简字典（供邮件使用）。即使多次调用也只落盘一次内容更新。
    """
    work = Path(work_dir)
    rep = _CURRENT.get()
    if rep is None:
        # 失败很早、尚未 begin 时仍写出空壳，避免邮件无附件
        rep = ProcessingReport(task_id=work.name, work_dir=str(work))
    if not rep.task_id:
        rep.task_id = work.name

    data = rep.to_dict()
    if status:
        data["status"] = status
    if error_message:
        data["error_message"] = error_message[:1000]
    text = rep.to_text(status=status, error_message=error_message)
    json_fp = work / "processing_report.json"
    txt_fp = work / "PROCESSING_REPORT.txt"
    try:
        work.mkdir(parents=True, exist_ok=True)
        json_fp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        txt_fp.write_text(text, encoding="utf-8")
    except OSError as e:
        logger.warning("写入处理报告失败: %s", e)

    slim = {
        "n_events": len(rep.events),
        "status": status,
        "summary_lines": [
            f"[{e.category}] {e.action}" + (f"：{e.detail}" if e.detail else "")
            for e in rep.events
        ],
        "email_summary": rep.email_summary(),
        "report_txt": str(txt_fp) if txt_fp.is_file() else "",
        "report_json": str(json_fp) if json_fp.is_file() else "",
    }
    if isinstance(params, dict):
        params["processing_report"] = slim
    rep.sealed = True
    return slim


def clear_report() -> None:
    """清除当前线程报告上下文。"""
    _CURRENT.set(None)
