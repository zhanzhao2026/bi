"""db 脚本共用连接配置:与后端共用 demo/backend/.env,环境变量优先,缺省值与契约一致。

参数(环境变量 / backend/.env,二者同名,已导出的环境变量优先):
  BIZ_DB_HOST / BIZ_DB_PORT        —— 主机与端口(默认 127.0.0.1:3306)
  BIZ_DB_NAME                      —— 库名(默认 biz_demo,仅建库脚本用)
  BIZ_DB_USER / BIZ_DB_PASS        —— 应用账号(默认 appuser/apppass,契约 §2.1)
  BIZ_DB_ROOT_USER / BIZ_DB_ROOT_PASS —— root 账号,仅 init_db.py 建库建号用(默认 root/root)
"""
import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / "backend" / ".env"

if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)  # 默认不覆盖已导出的环境变量
    except ImportError:  # 无 python-dotenv 时手写解析,保持同等语义
        for _line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip("'").strip('"'))


def _get(key: str, default: str) -> str:
    v = os.getenv(key, default)
    return v.strip() if isinstance(v, str) else default


HOST = _get("BIZ_DB_HOST", "127.0.0.1")
PORT = int(_get("BIZ_DB_PORT", "3306") or "3306")
NAME = _get("BIZ_DB_NAME", "biz_demo")
APP_USER = _get("BIZ_DB_USER", "appuser")
APP_PASS = _get("BIZ_DB_PASS", "apppass")
ROOT_USER = _get("BIZ_DB_ROOT_USER", "root")
ROOT_PASS = _get("BIZ_DB_ROOT_PASS", "root")

# 标识符会拼进 DDL/GRANT,只放行安全字符
for _key, _val in (("BIZ_DB_NAME", NAME), ("BIZ_DB_USER", APP_USER), ("BIZ_DB_ROOT_USER", ROOT_USER)):
    if not re.fullmatch(r"[A-Za-z0-9_]+", _val):
        raise SystemExit(f"[dbenv] 非法 {_key}: {_val!r}(仅允许字母/数字/下划线)")

# 密码拼进 IDENTIFIED BY 字面量,双写引号与反斜杠
APP_PASS_SQL = APP_PASS.replace("\\", "\\\\").replace("'", "''")

ROOT = dict(host=HOST, port=PORT, user=ROOT_USER, password=ROOT_PASS, charset="utf8mb4")
APP = dict(host=HOST, port=PORT, user=APP_USER, password=APP_PASS, database=NAME, charset="utf8mb4")
