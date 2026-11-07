"""应用配置:读取 backend/.env,提供数据库与 LLM 缺省配置。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录(BASE_DIR = app/core/config.py → 上三级)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    v = os.getenv(key, default)
    return v.strip() if isinstance(v, str) else default


DB_CONFIG = {
    "host": _env("BIZ_DB_HOST", "127.0.0.1"),
    "port": int(_env("BIZ_DB_PORT", "3306") or "3306"),
    "user": _env("BIZ_DB_USER", "appuser"),
    "password": _env("BIZ_DB_PASS", "apppass"),
    "database": _env("BIZ_DB_NAME", "biz_demo"),
    "charset": "utf8mb4",
}

# LLM 网关缺省值(优先级:sys_settings 表 > 这里)
LLM_BASE_URL_DEFAULT = _env("BIZ_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
LLM_API_KEY_DEFAULT = _env("BIZ_LLM_API_KEY", "")
LLM_MODEL_DEFAULT = _env("BIZ_LLM_MODEL", "glm-4-flash")

# 内置演示用户(契约 §12)
DEMO_USERS = {
    "ceo": {"id": "ceo", "name": "张总 · 总经理", "scope": "全部类目", "category": None},
    "beauty": {"id": "beauty", "name": "林岚 · 美妆负责人", "scope": "仅美妆类目", "category": "美妆"},
}
