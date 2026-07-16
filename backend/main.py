# ==================================================
# 功能说明：WebMD FastAPI 入口（路由、静态页、启动恢复任务）
# 使用方法：uvicorn main:app --host 0.0.0.0 --port 8000
# 依赖环境：pip/conda 见 backend/requirements.txt
# 生成时间：2026-07-16
# ==================================================
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.auth import router as auth_router
from api.routes import router
from config import TASKS_DIR, USERS_DB
from models import load_tasks_from_disk
from user_store import init_db
from engine.env_check import repair_ambertools

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="GROMACS MD Simulation Setup Tool")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 启动时恢复历史任务，并尝试从 conda 缓存修复 AmberTools
load_tasks_from_disk(TASKS_DIR)
init_db(Path(USERS_DB))

# 启动后尝试调度排队中的 MD 任务
try:
    from engine.autodl_runner import dispatch_queued_jobs
    import threading

    threading.Thread(target=dispatch_queued_jobs, daemon=True).start()
except Exception as _md_e:
    logging.warning("启动时 MD 任务调度失败: %s", _md_e)
try:
    _fixed = repair_ambertools()
    if _fixed:
        logging.info("启动时 AmberTools 自动修复: %s", ", ".join(_fixed))
except Exception as _e:
    logging.warning("启动时 AmberTools 自动修复失败: %s", _e)

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
