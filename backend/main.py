# ==================================================
# 功能说明：WebMD FastAPI 入口（路由、静态页、启动恢复任务）
# 使用方法：uvicorn main:app --host 0.0.0.0 --port 8000
# 依赖环境：pip/conda 见 backend/requirements.txt
# 生成时间：2026-07-21
# ==================================================

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.auth import router as auth_router
from api.routes import router
from config import (
    SKIP_AMBER_REPAIR,
    SKIP_AUTODL_DISPATCH,
    SKIP_TASK_CLEANUP,
    TASK_CLEANUP_INTERVAL_SEC,
    TASK_RETENTION_DAYS,
    TASKS_DIR,
    USERS_DB,
    assert_production_secrets,
    cors_allow_origins,
)
from models import load_tasks_from_disk
from user_store import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用启动/关闭：恢复任务、调度、环境修复（均可配置跳过）。"""
    assert_production_secrets()
    load_tasks_from_disk(TASKS_DIR)
    init_db(Path(USERS_DB))

    # 把现存已完成 MD 补进永久累计（幂等），避免升级前历史被任务清理抹掉
    try:
        from analytics_util import sync_md_completion_from_tasks
        from config import ANALYTICS_FILE
        from models import tasks as _tasks

        n = sync_md_completion_from_tasks(Path(ANALYTICS_FILE), _tasks.values())
        if n:
            logger.info("已补录 %d 条历史 MD 完成到永久统计", n)
    except Exception as e:
        logger.warning("同步 MD 永久统计失败: %s", e)

    if not SKIP_AUTODL_DISPATCH:
        try:
            from engine.autodl_runner import dispatch_queued_jobs

            threading.Thread(target=dispatch_queued_jobs, daemon=True).start()
        except Exception as e:
            logger.warning("启动时 MD 任务调度失败: %s", e)
    else:
        logger.info("已跳过启动时 AutoDL 调度（WEBMD_SKIP_AUTODL_DISPATCH）")

    if not SKIP_AMBER_REPAIR:
        try:
            from engine.env_check import repair_ambertools

            fixed = repair_ambertools()
            if fixed:
                logger.info("启动时 AmberTools 自动修复: %s", ", ".join(fixed))
        except Exception as e:
            logger.warning("启动时 AmberTools 自动修复失败: %s", e)
    else:
        logger.info("已跳过 AmberTools 修复（WEBMD_SKIP_AMBER_REPAIR）")

    if not SKIP_TASK_CLEANUP:
        try:
            from task_cleanup import start_cleanup_scheduler

            start_cleanup_scheduler(
                TASKS_DIR,
                retention_days=TASK_RETENTION_DAYS,
                interval_sec=TASK_CLEANUP_INTERVAL_SEC,
            )
        except Exception as e:
            logger.warning("启动任务清理调度失败: %s", e)
    else:
        logger.info("已跳过任务目录清理（WEBMD_SKIP_TASK_CLEANUP）")

    yield


app = FastAPI(title="GROMACS MD Simulation Setup Tool", lifespan=lifespan)

_origins = cors_allow_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # 通配源时浏览器不允许 credentials；生产白名单可开
    allow_credentials=("*" not in _origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

# 无 hash 的干净路径（刷新 /prepare 时浏览器只请求该路径，不带 #prepare）
_SPA_ENTRY = {
    "prepare": "prepare.html",
    "analysis": "analysis.html",
    "guide": "guide.html",
}


@app.get("/prepare")
@app.get("/analysis")
@app.get("/guide")
async def spa_section_entry(request: Request):
    """返回对应跳转页 HTML，避免刷新时落到 JSON Not Found。"""
    key = request.url.path.strip("/").split("/", 1)[0]
    name = _SPA_ENTRY.get(key, "prepare.html")
    fp = frontend_dir / name
    if not fp.is_file():
        fp = frontend_dir / "index.html"
    return FileResponse(fp, media_type="text/html; charset=utf-8")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc) -> FileResponse | JSONResponse:
    """非 API 的 404 回退到首页，避免前端路由刷新只看到 JSON。"""
    path = request.url.path or "/"
    if (
        path.startswith("/api")
        or path.startswith("/docs")
        or path.startswith("/redoc")
        or path.startswith("/openapi")
    ):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    index = frontend_dir / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html; charset=utf-8")
    return JSONResponse({"detail": "Not Found"}, status_code=404)


if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
