#!/usr/bin/env bash
# 前端开发模式(5174,proxy /api -> 8310)。生产演示请用 run_backend.sh(单地址)。
set -e
cd "$(dirname "$0")/../frontend"
npm run dev
