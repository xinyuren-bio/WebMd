import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
