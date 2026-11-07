#!/usr/bin/env bash
# 数据层一键重建:建库建表 → 造数 → 校验。可重复执行(先 DROP 重建)。
set -euo pipefail
cd "$(dirname "$0")"

PY="/home/link/code/bi/demo/backend/.venv/bin/python"

echo "==> [1/3] init_db.py(建库 biz_demo + 授权 appuser + 重建全部表)"
"$PY" init_db.py

echo "==> [2/3] seed_data.py(固定随机种子 42 造数)"
"$PY" seed_data.py

echo "==> [3/3] verify.py(故事线与结构校验)"
"$PY" verify.py

echo "==> 数据层 run_all 全部完成"
