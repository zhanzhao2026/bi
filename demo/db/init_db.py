#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化 biz_demo 数据库:建库、授权、按契约 §2.2 重建全部表。

使用 root 连库(仅本脚本用 root,连接配置见 dbenv.py);应用运行时统一用应用账号。
可重复执行(DROP TABLE IF EXISTS + CREATE)。
"""
import pymysql

import dbenv

ROOT = dbenv.ROOT
DB_NAME = dbenv.NAME
APP_USER = dbenv.APP_USER
APP_PASS_SQL = dbenv.APP_PASS_SQL

# 契约 §2.1:建库 + 应用账号
SQL_DB = (f"CREATE DATABASE IF NOT EXISTS {DB_NAME} "
          "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
SQL_USER = [
    f"CREATE USER IF NOT EXISTS '{APP_USER}'@'%' IDENTIFIED BY '{APP_PASS_SQL}'",
    f"ALTER USER '{APP_USER}'@'%' IDENTIFIED BY '{APP_PASS_SQL}'",
    f"GRANT ALL ON {DB_NAME}.* TO '{APP_USER}'@'%'",
    "FLUSH PRIVILEGES",
]

# 契约 §2.2 表结构(列名/类型/索引严格照契约;ENGINE/CHARSET 为工程默认)
TABLES = [
    ("dim_date", """
        CREATE TABLE dim_date (
          d DATE NOT NULL PRIMARY KEY,
          dow TINYINT NULL,
          is_weekend TINYINT(1) NULL,
          month CHAR(7) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("dim_category", """
        CREATE TABLE dim_category (
          category_id TINYINT NOT NULL PRIMARY KEY,
          category_name VARCHAR(32) NULL,
          sort INT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("dim_channel", """
        CREATE TABLE dim_channel (
          channel_id TINYINT NOT NULL PRIMARY KEY,
          channel_name VARCHAR(32) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("dim_product", """
        CREATE TABLE dim_product (
          product_id INT NOT NULL PRIMARY KEY,
          product_name VARCHAR(64) NULL,
          category_id TINYINT NULL,
          price DECIMAL(10,2) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("dim_user", """
        CREATE TABLE dim_user (
          user_id INT NOT NULL PRIMARY KEY,
          reg_date DATE NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("fact_order", """
        CREATE TABLE fact_order (
          order_id BIGINT NOT NULL PRIMARY KEY,
          pay_date DATE NOT NULL,
          product_id INT NOT NULL,
          category_id TINYINT NOT NULL,
          channel_id TINYINT NOT NULL,
          user_id INT NOT NULL,
          user_type VARCHAR(4) NOT NULL,
          item_cnt INT NOT NULL,
          pay_amount DECIMAL(12,2) NOT NULL,
          refund_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
          is_refund TINYINT(1) NOT NULL DEFAULT 0,
          KEY idx_date (pay_date),
          KEY idx_cat (category_id, pay_date),
          KEY idx_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("audit_log", """
        CREATE TABLE audit_log (
          id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
          trace_id VARCHAR(24) NULL,
          ts DATETIME NULL,
          session_id VARCHAR(40) NULL,
          demo_user VARCHAR(32) NULL,
          question TEXT NULL,
          intent VARCHAR(32) NULL,
          metric_key VARCHAR(32) NULL,
          ir_json TEXT NULL,
          sql_text TEXT NULL,
          row_count INT NULL,
          result_json TEXT NULL,
          narrative TEXT NULL,
          llm_used TINYINT(1) NULL,
          llm_model VARCHAR(64) NULL,
          latency_ms INT NULL,
          stage_timings TEXT NULL,
          status VARCHAR(16) DEFAULT 'ok',
          KEY idx_ts (ts),
          KEY idx_sess (session_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("feedback_log", """
        CREATE TABLE feedback_log (
          id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
          ts DATETIME NULL,
          trace_id VARCHAR(24) NULL,
          session_id VARCHAR(40) NULL,
          demo_user VARCHAR(32) NULL,
          rating VARCHAR(4) NULL,
          tags TEXT NULL,
          comment TEXT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("golden_qa", """
        CREATE TABLE golden_qa (
          id INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
          question TEXT NULL,
          kind VARCHAR(12) NULL,
          expect_metric VARCHAR(32) NULL,
          expect_dims TEXT NULL,
          ref_sql TEXT NULL,
          ref_value DECIMAL(18,4) NULL,
          tolerance_pct DECIMAL(5,2) DEFAULT 2.0,
          tag VARCHAR(16) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("semantic_version", """
        CREATE TABLE semantic_version (
          id INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
          version INT NULL,
          released DATE NULL,
          author VARCHAR(32) NULL,
          change_desc TEXT NULL,
          yaml_snapshot LONGTEXT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("eval_history", """
        CREATE TABLE eval_history (
          id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
          ts DATETIME NULL,
          mode VARCHAR(8) NULL,
          pass_rate DECIMAL(5,2) NULL,
          total INT NULL,
          passed INT NULL,
          report_json LONGTEXT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
    ("sys_settings", """
        CREATE TABLE sys_settings (
          skey VARCHAR(32) NOT NULL PRIMARY KEY,
          svalue TEXT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""),
]


def main() -> None:
    conn = pymysql.connect(**ROOT)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_DB)
            for sql in SQL_USER:
                cur.execute(sql)
            cur.execute(f"USE {DB_NAME}")
            # 先 DROP(逆序),再 CREATE,保证可重复执行
            for name, _ in reversed(TABLES):
                cur.execute(f"DROP TABLE IF EXISTS {name}")
            for name, ddl in TABLES:
                cur.execute(ddl)
                print(f"[init] created table {name}")
        conn.commit()
    finally:
        conn.close()

    # 用应用账号验证连通
    app = pymysql.connect(**dbenv.APP)
    with app.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (DB_NAME,))
        n = cur.fetchone()[0]
    app.close()
    print(f"[init] database {DB_NAME} ready, {APP_USER} ok, tables={n}")


if __name__ == "__main__":
    main()
