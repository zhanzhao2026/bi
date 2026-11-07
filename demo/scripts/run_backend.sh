#!/usr/bin/env bash
# 启动后端(8310)。若 frontend/dist 存在则同端口托管前端,单地址演示。
set -e
cd "$(dirname "$0")/../backend"
mkdir -p /tmp/biz_demo_logs
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8310 2>&1 | tee -a /tmp/biz_demo_logs/backend.log
