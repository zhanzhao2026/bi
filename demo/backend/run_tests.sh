#!/usr/bin/env bash
# 指标单测一键执行(契约 §15.2,代理 B2)
# 用法:bash demo/backend/run_tests.sh
# 退出码:0 = 全部通过;非 0 = 存在失败(CI 可直接使用)。
set -u
cd "$(dirname "$0")"

export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "[run_tests] 未找到 backend/.venv/bin/python,回退系统 python3"
  PY=python3
fi

echo "==== 指标单测体系(biz_demo · 契约 §15.2) ===="
echo "[run_tests] python = $("$PY" --version 2>&1)"

# 数据库预检(配置复用 backend/.env,与被测管道一致)
if ! "$PY" -c "
import sys; sys.path.insert(0, '.')
from app.core.db import query
query('SELECT 1')
" 2>/dev/null; then
  echo "[run_tests] 警告:数据库不可达(${BIZ_DB_HOST:-127.0.0.1}:${BIZ_DB_PORT:-3306}/biz_demo),依赖 DB 的用例将失败"
  echo "[run_tests] 提示:MySQL 停了可执行 docker start <容器名>(docker ps -a 可查);首次建库执行 bash demo/db/run_all.sh"
fi

echo "[run_tests] 运行: $PY -m unittest discover -s tests -v"
echo "-------------------------------------------------------------"

OUT=$("$PY" -m unittest discover -s tests -v 2>&1)
STATUS=$?
printf '%s\n' "$OUT"

TOTAL=$(printf '%s\n' "$OUT" | sed -n 's/^Ran \([0-9][0-9]*\) test.*/\1/p' | tail -1)
echo "-------------------------------------------------------------"
echo "==== 指标单测结果 ===="
echo "用例总数: ${TOTAL:-未知}"
if [ "$STATUS" -eq 0 ]; then
  echo "结果: ALL PASS"
else
  echo "结果: FAIL(退出码 $STATUS)"
fi
exit "$STATUS"
