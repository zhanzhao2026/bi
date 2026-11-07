"""PyMySQL 访问层:短连接 + DictCursor,连接失败优雅降级。"""
import logging
from contextlib import contextmanager

import pymysql
import pymysql.cursors

from .config import DB_CONFIG

logger = logging.getLogger("biz.db")


def get_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset=DB_CONFIG["charset"],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=3,
        read_timeout=30,
        write_timeout=10,
        autocommit=True,
    )


@contextmanager
def _conn():
    conn = get_connection()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def query(sql: str, params=None) -> list[dict]:
    """执行查询,返回 dict 行列表。参数化查询,防注入。"""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


def query_one(sql: str, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=None) -> int:
    """执行写语句,返回受影响行数。"""
    with _conn() as conn:
        with conn.cursor() as cur:
            return cur.execute(sql, params or ())


def db_ok() -> bool:
    try:
        query("SELECT 1")
        return True
    except Exception as e:  # 数据库不可用时优雅降级,不抛出
        logger.warning("db check failed: %s", e)
        return False


def render_sql(sql: str, params=None) -> str:
    """把 %s 参数内联进 SQL,仅供展示(口径弹层/审计详情),不执行。"""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                return cur.mogrify(sql, params or ())
    except Exception:
        return sql


def inline_sql(sql: str, params, charset: str = "utf8mb4") -> str:
    """纯 Python 版参数内联(不经数据库连接),用于审计/展示。

    注意:SQL 中的字面 % 已按 pymysql 约定写成 %%,不会被误认为占位符。
    """
    if not params:
        return sql
    parts = sql.split("%s")
    if len(parts) != len(params) + 1:
        return sql
    try:
        from pymysql.converters import escape_item

        out = parts[0]
        for i, p in enumerate(params):
            out += escape_item(p, charset) + parts[i + 1]
        return out
    except Exception:
        return sql
