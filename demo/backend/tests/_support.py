# -*- coding: utf-8 -*-
"""指标单测体系公共支撑(契约 §15.2)。

- DB 连接:复用 backend/app/core/config.py 的 .env 读取方式
  (appuser/apppass@127.0.0.1:3306/biz_demo),全部 SQL 经 cursor.execute 直接执行。
- 手写期望 SQL:11 个指标的聚合表达式在测试代码中逐指标独立编写,不经过
  loader/compiler(与编译器管道形成两条独立代码路径,交叉验证口径)。
- 加载器副本工具:把 backend/semantic/ 整目录复制到临时目录,篡改副本中 gmv 的
  agg,用副本构造 loader 实例,证明"改基础口径 → 派生指标跟着变"(不污染真实文件)。
"""
import datetime as dt
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pymysql
import pymysql.cursors  # noqa: F401

from app.core.config import DB_CONFIG  # 复用 backend/.env 的读取方式

TOL = 1e-6                      # 一致性容差(契约 §15.2)
SEMANTIC_DIR = BACKEND_DIR / "semantic"
ASOF = dt.date(2026, 11, 6)     # 契约 §0:data_as_of = 2026-11-06

# ---------------------------------------------------------------- DB 连接

_conn = None


def db_conn():
    """共享短连接(懒建 + 自动重连),配置来自 backend/.env。"""
    global _conn
    if _conn is None:
        _conn = pymysql.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            database=DB_CONFIG["database"],
            charset=DB_CONFIG["charset"],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    _conn.ping(reconnect=True)
    return _conn


def exec_sql(sql, params=None):
    """直接 cursor.execute(不经过 loader/compiler),返回 dict 行列表。"""
    cur = db_conn().cursor()
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        cur.close()


def scalar(sql, params=None):
    rows = exec_sql(sql, params)
    return list(rows[0].values())[0] if rows else None


# ------------------------------------------------ 手写期望 SQL(独立代码路径)

# 11 个指标的期望聚合表达式:手写、独立于 semantic/ 下的任何 YAML(契约 §3/§15.1)。
# 比率类输出原始小数口径(如 0.0281),与编译器管道的 IR 原始返回一致,不做 ×100。
EXPECTED_AGG = {
    "gmv":               "SUM(f.pay_amount)",
    "order_cnt":         "COUNT(DISTINCT f.order_id)",
    "pay_user_cnt":      "COUNT(DISTINCT f.user_id)",
    "item_cnt":          "SUM(f.item_cnt)",
    "refund_amt":        "SUM(f.refund_amount)",
    "refund_order_cnt":  "SUM(f.is_refund)",
    "new_order_cnt":     "SUM(f.user_type='新客')",
    "arppu":             "SUM(f.pay_amount)/NULLIF(COUNT(DISTINCT f.user_id),0)",
    "refund_rate":       "SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0)",
    "refund_order_rate": "SUM(f.is_refund)/NULLIF(COUNT(DISTINCT f.order_id),0)",
    "new_order_ratio":   "SUM(f.user_type='新客')/NULLIF(COUNT(DISTINCT f.order_id),0)",
}

METRIC_KEYS = list(EXPECTED_AGG.keys())


def data_as_of_handwritten():
    """手写侧的数据水位:MAX(pay_date),不经过 loader 的缓存。"""
    return scalar("SELECT MAX(pay_date) AS d FROM fact_order")


def expected_window(kind):
    """手写侧独立解析时间窗口(闭区间,YYYY-MM-DD 字符串)。

    full = 契约 §2.4 全窗口 2026-08-09~11-06;oct = 2026年10月;
    last30/last7 以手写 MAX(pay_date) 为终点倒推(29 天/6 天)。
    """
    if kind == "full":
        return "2026-08-09", "2026-11-06"
    if kind == "oct":
        return "2026-10-01", "2026-10-31"
    asof = data_as_of_handwritten()
    if kind == "last30":
        return (asof - dt.timedelta(days=29)).isoformat(), asof.isoformat()
    if kind == "last7":
        return (asof - dt.timedelta(days=6)).isoformat(), asof.isoformat()
    raise ValueError(f"未知窗口: {kind}")


def expected_sql(metric, start, end, dim=None, filters=None):
    """手写期望 SQL:逐指标独立编写,直接拿名字过滤(经维表换 id),不经过编译器。

    返回 (sql, params)。维度处理(category/channel/user_type)为测试侧手写模板,
    与 app/semantic/compiler.py 无任何代码复用。
    """
    agg = EXPECTED_AGG[metric]
    conds = ["f.pay_date BETWEEN %s AND %s"]
    params = [start, end]
    filters = filters or {}
    if filters.get("category"):
        marks = ", ".join(["%s"] * len(filters["category"]))
        conds.append("f.category_id IN (SELECT category_id FROM dim_category "
                     f"WHERE category_name IN ({marks}))")
        params.extend(filters["category"])
    if filters.get("channel"):
        marks = ", ".join(["%s"] * len(filters["channel"]))
        conds.append("f.channel_id IN (SELECT channel_id FROM dim_channel "
                     f"WHERE channel_name IN ({marks}))")
        params.extend(filters["channel"])
    if filters.get("user_type"):
        marks = ", ".join(["%s"] * len(filters["user_type"]))
        conds.append(f"f.user_type IN ({marks})")
        params.extend(filters["user_type"])
    where = " AND ".join(conds)

    if dim == "category":
        sql = (f"SELECT c.category_name AS dim_name, {agg} AS val FROM fact_order f "
               f"JOIN dim_category c ON c.category_id = f.category_id "
               f"WHERE {where} GROUP BY c.category_name")
    elif dim == "channel":
        sql = (f"SELECT ch.channel_name AS dim_name, {agg} AS val FROM fact_order f "
               f"JOIN dim_channel ch ON ch.channel_id = f.channel_id "
               f"WHERE {where} GROUP BY ch.channel_name")
    elif dim == "user_type":
        sql = (f"SELECT f.user_type AS dim_name, {agg} AS val FROM fact_order f "
               f"WHERE {where} GROUP BY f.user_type")
    elif dim is None:
        sql = f"SELECT {agg} AS val FROM fact_order f WHERE {where}"
    else:
        raise ValueError(f"未知维度: {dim}")
    return sql, params


# 时间(全窗口/last30/last7/2026年10月)× 维度(不限/按类目/按渠道/按新客)的
# 9 种代表性组合(契约要求 ≥6):win 为手写侧窗口代号,label 为用例展示名。
COMBOS = {
    "full_all":        {"win": "full",   "label": "全窗口×不限",   "time": {"range": ["2026-08-09", "2026-11-06"]}},
    "last30_all":      {"win": "last30", "label": "last30×不限",   "time": {"preset": "last_30d"}},
    "last7_all":       {"win": "last7",  "label": "last7×不限",    "time": {"preset": "last_7d"}},
    "oct_all":         {"win": "oct",    "label": "2026年10月×不限", "time": {"range": ["2026-10-01", "2026-10-31"]}},
    "last30_category": {"win": "last30", "label": "last30×按类目",  "time": {"preset": "last_30d"}, "dims": ["category"]},
    "full_channel":    {"win": "full",   "label": "全窗口×按渠道",  "time": {"range": ["2026-08-09", "2026-11-06"]}, "dims": ["channel"]},
    "oct_usertype":    {"win": "oct",    "label": "2026年10月×按新老客", "time": {"range": ["2026-10-01", "2026-10-31"]}, "dims": ["user_type"]},
    "last30_newuser":  {"win": "last30", "label": "last30×按新客",  "time": {"preset": "last_30d"}, "filters": {"user_type": ["新客"]}},
    "last7_cat_chan":  {"win": "last7",  "label": "last7×类目+直播渠道", "time": {"preset": "last_7d"}, "dims": ["category"], "filters": {"channel": ["直播"]}},
}

# --------------------------------------------------------- 编译器管道(被测路径)

def pipeline_run(metric, time_spec, dims=None, filters=None):
    """编译器管道:构造 IR → normalize → validate → compile → 执行。

    返回 (rows, sql_text)。比率指标取 IR 原始返回(小数),不做 ×100。
    """
    from app.semantic import compiler
    from app.semantic.loader import get_data_as_of, get_filter_options, get_semantic

    sem = get_semantic()
    ir = {
        "metric": metric,
        "time": time_spec,
        "dims": list(dims or []),
        "filters": {k: list((filters or {}).get(k) or [])
                    for k in ("category", "channel", "user_type")},
        "granularity": "day",
        "compare": "none",
        "order": "desc",
        "limit": 50,
    }
    ir = compiler.normalize_ir(ir)
    ok, err = compiler.validate_ir(ir, sem, get_filter_options())
    if not ok:
        raise AssertionError(f"IR 校验失败({metric}): {err}")
    start, end = compiler.resolve_time(ir["time"], get_data_as_of())
    built = compiler.compile_metric_query(ir, sem, start, end)
    rows = exec_sql(built["sql"], built["params"])
    return rows, built["sql"]


# ------------------------------------------------------------- 结果比对工具

def fnum(v):
    return None if v is None else float(v)


def rows_to_map(rows):
    return {r.get("dim_name"): fnum(r.get("val")) for r in rows}


def assert_value_equal(tc, got, want, label, tol=TOL):
    tc.assertIsNotNone(got, f"{label}: 管道返回 None")
    tc.assertIsNotNone(want, f"{label}: 期望 SQL 返回 None")
    tc.assertAlmostEqual(fnum(got), fnum(want), delta=tol, msg=label)


def assert_rows_equal(tc, pipeline_rows, expected_rows, label):
    pm, em = rows_to_map(pipeline_rows), rows_to_map(expected_rows)
    tc.assertEqual(sorted(em), sorted(pm),
                   f"{label}: 分组键不一致 管道={sorted(pm)} 期望={sorted(em)}")
    for k in sorted(em, key=lambda x: (x is None, x)):
        assert_value_equal(tc, pm.get(k), em.get(k), f"{label}[{k}]")


# ----------------------------------------------- semantic/ 副本与篡改工具(依赖图)

def _squash(s):
    """去除全部空白后比较(避免 YAML 排版差异造成误报)。"""
    return re.sub(r"\s+", "", str(s or ""))


def copy_semantic_to_tmp(prefix="biz_semcopy_"):
    """把 backend/semantic/ 整目录复制到临时目录(布局 <tmp>/backend/semantic)。

    返回 (tmp_base, tmp_backend, tmp_semantic)。调用方负责 finally 清理 tmp_base。
    """
    tmp_base = Path(tempfile.mkdtemp(prefix=prefix))
    tmp_backend = tmp_base / "backend"
    tmp_sem = tmp_backend / "semantic"
    shutil.copytree(SEMANTIC_DIR, tmp_sem)
    return tmp_base, tmp_backend, tmp_sem


def _find_metrics_mapping(doc):
    """兼容 metrics/base.yml 的两种顶层形态:{metrics: {...}} 或直接 {gmv: {...}}。"""
    if isinstance(doc, dict):
        if isinstance(doc.get("metrics"), dict):
            return doc["metrics"]
        if isinstance(doc.get("gmv"), dict):
            return doc
    return None


def tamper_gmv_agg(sem_dir, wrapper="2 * ({agg})"):
    """在副本中把 gmv 的 agg 篡改为 wrapper 包裹形式,返回篡改后的 agg 文本。

    只改副本文件,真实 semantic/ 不受影响。
    """
    import yaml

    root = Path(sem_dir)
    for yml in sorted(list(root.rglob("*.yml")) + list(root.rglob("*.yaml"))):
        try:
            doc = yaml.safe_load(yml.read_text(encoding="utf-8"))
        except Exception:
            continue
        metrics = _find_metrics_mapping(doc)
        if metrics and isinstance(metrics.get("gmv"), dict) and metrics["gmv"].get("agg"):
            orig = metrics["gmv"]["agg"]
            metrics["gmv"]["agg"] = wrapper.format(agg=orig)
            yml.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
            return metrics["gmv"]["agg"]
    raise AssertionError(f"副本中未找到 gmv 指标定义: {root}")


def _clear_loader_cache(L):
    clear = getattr(getattr(L, "get_semantic", None), "cache_clear", None)
    if callable(clear):
        clear()


def _patch_backend_root(tmp_backend):
    """monkeypatch 兜底:把 loader/config 模块里指向 backend 的 Path 常量改指副本。

    在 finally 中恢复原值并清空 get_semantic 缓存(避免污染其他测试)。
    """
    import app.core.config as cfgmod
    import app.semantic.loader as L

    real, newroot = str(BACKEND_DIR), str(tmp_backend)
    saved = []
    for mod in (L, cfgmod):
        for name, val in list(vars(mod).items()):
            if isinstance(val, Path):
                s = str(val)
                if s == real:
                    saved.append((mod, name, val))
                    setattr(mod, name, Path(newroot))
                elif s.startswith(real + os.sep):
                    saved.append((mod, name, val))
                    setattr(mod, name, Path(newroot + s[len(real):]))
    _clear_loader_cache(L)
    try:
        sem = None
        if callable(getattr(L, "get_semantic", None)):
            try:
                sem = L.get_semantic()
            except Exception:
                sem = None
        if (sem is None or not getattr(sem, "metrics", None)) and hasattr(L, "Semantic"):
            sem = L.Semantic()
        return sem
    finally:
        for mod, name, val in saved:
            setattr(mod, name, val)
        _clear_loader_cache(L)


def load_semantic_from_dir(d, verify=None):
    """从指定目录构造 loader 实例(B2 对 B1 的唯一接口要求:支持自定义目录)。

    依次尝试:构造参数(常见命名)→ 模块级 load 函数 → 环境变量 → monkeypatch
    backend 根路径。verify(loaded) 为真才接受(用于确认加载的是副本而非真实目录)。
    """
    import app.semantic.loader as L

    d = Path(d).resolve()

    def accept(obj):
        if obj is None or not getattr(obj, "metrics", None):
            return False
        return True if verify is None else bool(verify(obj))

    if hasattr(L, "Semantic"):
        cls = L.Semantic
        candidates = [
            lambda: cls(path=d), lambda: cls(dir=d), lambda: cls(directory=d),
            lambda: cls(semantic_dir=d), lambda: cls(base_dir=d), lambda: cls(root=d),
            lambda: cls(root_dir=d), lambda: cls(base=d), lambda: cls(d),
        ]
        for fn in candidates:
            try:
                obj = fn()
            except Exception:
                continue
            if accept(obj):
                return obj

    for fname in ("load_semantic", "load", "load_semantic_from_dir",
                  "load_from_dir", "semantic_from_dir"):
        fn = getattr(L, fname, None)
        if callable(fn):
            try:
                obj = fn(d)
            except Exception:
                continue
            if accept(obj):
                return obj

    for env in ("BIZ_SEMANTIC_DIR", "SEMANTIC_DIR", "BIZ_SEMANTIC_PATH"):
        old = os.environ.get(env)
        os.environ[env] = str(d)
        try:
            _clear_loader_cache(L)
            obj = None
            if callable(getattr(L, "get_semantic", None)):
                try:
                    obj = L.get_semantic()
                except Exception:
                    obj = None
            if not accept(obj) and hasattr(L, "Semantic"):
                try:
                    obj = L.Semantic()
                except Exception:
                    obj = None
            if accept(obj):
                return obj
        finally:
            if old is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = old
            _clear_loader_cache(L)

    obj = _patch_backend_root(d.parent.parent)   # d = <tmp>/backend/semantic
    if accept(obj):
        return obj
    raise RuntimeError(
        f"无法从目录构造 loader 实例(需要 loader 支持自定义目录参数): {d}")
