"""v14 语义层版本登记(契约 §16.1/§16.2):幂等插入 biz_demo.semantic_version。

- 幂等:先查后插(version=14 已存在则跳过,可重复执行)
- yaml_snapshot = 新语义层全部 YAML 文件拼接文本
- change_desc = 时间表达式层+层级维度(§16.1 timelex / §16.2 product 下钻)
- 用法:.venv/bin/python migrate_v14.py(连接配置读 backend/.env)
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE_DIR / ".env")

import pymysql  # noqa: E402

VERSION = 14
AUTHOR = "数据小组"
RELEASED = "2026-11-07"
CHANGE_DESC = "时间表达式层+层级维度"
SNAPSHOT_FILES = [
    "models.yml",
    "metrics/base.yml",
    "metrics/derived.yml",
    "dims.yml",
    "terms.yml",
    "version.yml",
]


def build_snapshot() -> str:
    parts = []
    for rel in SNAPSHOT_FILES:
        text = (BASE_DIR / "semantic" / rel).read_text(encoding="utf-8")
        parts.append(f"-- ==== semantic/{rel} ====\n{text.rstrip()}\n")
    return "\n".join(parts)


def main() -> int:
    conn = pymysql.connect(
        host=os.getenv("BIZ_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("BIZ_DB_PORT", "3306") or "3306"),
        user=os.getenv("BIZ_DB_USER", "appuser"),
        password=os.getenv("BIZ_DB_PASS", "apppass"),
        database=os.getenv("BIZ_DB_NAME", "biz_demo"),
        charset="utf8mb4",
    )
    snapshot = build_snapshot()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM semantic_version WHERE version = %s", (VERSION,))
            row = cur.fetchone()
            if row:
                print(f"semantic_version v{VERSION} 已存在(id={row[0]}),幂等跳过,无需重复插入")
                return 0
            cur.execute(
                "INSERT INTO semantic_version (version, released, author, change_desc, yaml_snapshot)"
                " VALUES (%s, %s, %s, %s, %s)",
                (VERSION, RELEASED, AUTHOR, CHANGE_DESC, snapshot),
            )
        conn.commit()
        print(f"semantic_version v{VERSION} 插入成功(author={AUTHOR}, released={RELEASED},"
              f" yaml_snapshot={len(snapshot)} 字符)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
