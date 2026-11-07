#!/usr/bin/env bash
# 重建演示数据库(重新造数,seed=42 结果可复现)
cd "$(dirname "$0")/../db"
bash run_all.sh
