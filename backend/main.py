import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router
from config import TASKS_DIR
from models import load_tasks_from_disk
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
try:
    _fixed = repair_ambertools()
    if _fixed:
        logging.info("启动时 AmberTools 自动修复: %s", ", ".join(_fixed))
except Exception as _e:
    logging.warning("启动时 AmberTools 自动修复失败: %s", _e)

app.include_router(router)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
