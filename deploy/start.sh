#!/bin/bash
# WebMD 启动脚本（加载 .env 后启动 uvicorn）
set -a
[ -f /opt/WebMd/backend/.env ] && source /opt/WebMd/backend/.env
set +a
source /root/miniconda3/etc/profile.d/conda.sh
conda activate md_web
cd /opt/WebMd/backend
exec uvicorn main:app --host 0.0.0.0 --port 8000
