#!/usr/bin/env bash
# 重建演示数据库(重新造数,seed=42 结果可复现)
cd "$(dirname "$0")/../db"
bash run_all.sh
# 语义版本登记(v13 结构化拆分记录,幂等)
cd ../backend && .venv/bin/python migrate_v13.py
