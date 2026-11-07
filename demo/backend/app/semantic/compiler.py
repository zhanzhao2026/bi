"""语义编译器(契约 §5):IR → SQL。

- 基础模板 FROM {事实表} {别名},时间列取自 models.yml(default_table/alias/date_col),
  不再硬编码表名 fact_order
- category/channel 维 JOIN 维表取中文名;date 维格式化 MM-DD
- 行级权限谓词(§12)以 AND 注入 WHERE
- 比率指标分母 NULLIF 防除零(来自语义层指标定义,派生指标已由 loader 依赖图展开)
"""
import datetime as dt
import re

from .loader import Semantic

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CompileError(Exception):
    pass


# ---------------- 时间窗口 ----------------

def _as_date(s) -> dt.date:
    if isinstance(s, dt.date):
        return s
    if not DATE_RE.match(str(s)):
        raise CompileError(f"日期格式不合法: {s}(应为 YYYY-MM-DD)")
    return dt.date.fromisoformat(str(s))


def resolve_time(ir_time: dict, data_as_of: str) -> tuple[str, str]:
    """把 IR time(preset 或 range)解析为 (start, end) 日期字符串,闭区间。"""
    if not isinstance(ir_time, dict):
        raise CompileError("time 缺失")
    if ir_time.get("range"):
        rng = ir_time["range"]
        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
            raise CompileError("time.range 应为 [起,止] 两个日期")
        start, end = _as_date(rng[0]), _as_date(rng[1])
        if start > end:
            raise CompileError("time.range 起止颠倒")
        return start.isoformat(), end.isoformat()
    preset = ir_time.get("preset")
    asof = _as_date(data_as_of)
    if preset == "yesterday":
        return asof.isoformat(), asof.isoformat()
    if preset == "last_7d":
        return (asof - dt.timedelta(days=6)).isoformat(), asof.isoformat()
    if preset == "last_30d":
        return (asof - dt.timedelta(days=29)).isoformat(), asof.isoformat()
    if preset == "this_month":
        return asof.replace(day=1).isoformat(), asof.isoformat()
    if preset == "last_month":
        first_this = asof.replace(day=1)
        last_prev = first_this - dt.timedelta(days=1)
        return last_prev.replace(day=1).isoformat(), last_prev.isoformat()
    raise CompileError(f"未知时间口径: {preset}")


def prev_range(start: str, end: str) -> tuple[str, str]:
    """同长度窗口向前平移(compare=prev_period 用,Python 侧合并)。"""
    s, e = _as_date(start), _as_date(end)
    shift = (e - s).days + 1
    return (s - dt.timedelta(days=shift)).isoformat(), (e - dt.timedelta(days=shift)).isoformat()


def preset_label(preset: str) -> str:
    return {
        "yesterday": "昨天",
        "last_7d": "最近7天",
        "last_30d": "最近30天",
        "this_month": "本月",
        "last_month": "上个月",
    }.get(preset, preset or "自定义时间")


def time_label(ir_time: dict | None) -> str:
    """IR time 的人话标签:preset → 固定词;range → 整月"2026年10月"或"起~止"。"""
    if isinstance(ir_time, dict) and ir_time.get("range"):
        rng = ir_time["range"]
        try:
            s, e = _as_date(rng[0]), _as_date(rng[1])
            nxt = (e.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            last_day = (nxt - dt.timedelta(days=1)).day
            if s.day == 1 and s.year == e.year and s.month == e.month and e.day == last_day:
                return f"{s.year}年{s.month}月"
            return f"{s.isoformat()}~{e.isoformat()}"
        except Exception:
            return "/".join(str(x) for x in rng)
    return preset_label((ir_time or {}).get("preset"))


# ---------------- IR 校验与规范化 ----------------

VALID_FILTER_KEYS = {"category", "channel", "user_type"}


def normalize_ir(ir: dict) -> dict:
    ir = dict(ir or {})
    ir["dims"] = [d for d in (ir.get("dims") or []) if d]
    filters = ir.get("filters") or {}
    ir["filters"] = {
        "category": list(filters.get("category") or []),
        "channel": list(filters.get("channel") or []),
        "user_type": list(filters.get("user_type") or []),
    }
    ir.setdefault("granularity", "day")
    ir.setdefault("compare", "none")
    ir.setdefault("order", "desc")
    ir.setdefault("limit", 10)
    return ir


def validate_ir(ir: dict, semantic: Semantic, options: dict) -> tuple[bool, str]:
    """严格校验:未知 metric/dim/filter 值一律拒绝,绝不猜。"""
    metric = ir.get("metric")
    if not metric or metric not in semantic.metrics:
        return False, f"未知指标: {metric}"
    t = ir.get("time")
    if not isinstance(t, dict):
        return False, "缺少时间条件"
    if not t.get("range") and t.get("preset") not in semantic.time_presets:
        return False, f"未知时间口径: {t.get('preset')}"
    dims = ir.get("dims") or []
    if len(dims) > 1:
        return False, "v1 暂不支持多维度同时拆分"
    for d in dims:
        if d not in semantic.dims:
            return False, f"未知维度: {d}"
    filters = ir.get("filters") or {}
    for k, vals in (filters or {}).items():
        if k not in VALID_FILTER_KEYS:
            return False, f"未知筛选维度: {k}"
        if not isinstance(vals, list):
            return False, f"筛选值应为列表: {k}"
        for v in vals:
            if v not in (options.get(k) or []):
                return False, f"未知的{k}筛选值: {v}"
    if ir.get("granularity") not in (None, "day", "none"):
        return False, f"未知粒度: {ir.get('granularity')}"
    if ir.get("compare") not in ("none", "prev_period"):
        return False, f"未知对比方式: {ir.get('compare')}"
    try:
        limit = int(ir.get("limit", 10))
        if not 1 <= limit <= 50:
            return False, "limit 应在 1~50"
    except (TypeError, ValueError):
        return False, "limit 应为整数"
    return True, ""


# ---------------- WHERE 构建 ----------------

def build_where(params: list, semantic: Semantic, start: str, end: str,
                filters: dict | None = None, permission: dict | None = None) -> str:
    f = semantic.fact_alias
    conds = [f"{f}.{semantic.fact_date_col} BETWEEN %s AND %s"]
    params.extend([start, end])
    filters = filters or {}
    if filters.get("category"):
        ph = ", ".join(["%s"] * len(filters["category"]))
        conds.append(f"{f}.category_id IN (SELECT category_id FROM dim_category WHERE category_name IN ({ph}))")
        params.extend(filters["category"])
    if filters.get("channel"):
        ph = ", ".join(["%s"] * len(filters["channel"]))
        conds.append(f"{f}.channel_id IN (SELECT channel_id FROM dim_channel WHERE channel_name IN ({ph}))")
        params.extend(filters["channel"])
    if filters.get("user_type"):
        ph = ", ".join(["%s"] * len(filters["user_type"]))
        conds.append(f"{f}.user_type IN ({ph})")
        params.extend(filters["user_type"])
    # 行级权限谓词(§12):beauty → 仅美妆,以 AND 注入
    if permission and permission.get("category"):
        conds.append(f"{f}.category_id IN (SELECT category_id FROM dim_category WHERE category_name = %s)")
        params.append(permission["category"])
    return " AND ".join(conds)


# ---------------- 编译 ----------------
# 维度模板:{f} = 事实表别名,{d} = 时间列(models.yml date_col),编译时展开
DIM_JOIN = {
    "category": "JOIN dim_category c ON c.category_id = {f}.category_id",
    "channel": "JOIN dim_channel ch ON ch.channel_id = {f}.channel_id",
    "date": "",
    "user_type": "",
}
DIM_SELECT = {
    "category": "c.category_name",
    "channel": "ch.channel_name",
    "date": "DATE_FORMAT({f}.{d}, '%%m-%%d')",
    "user_type": "{f}.user_type",
}
DIM_GROUP = {
    "category": "c.category_name",
    "channel": "ch.channel_name",
    "date": "{f}.{d}",
    "user_type": "{f}.user_type",
}
DIM_LABEL = {"category": "类目", "channel": "渠道", "date": "日期", "user_type": "用户类型"}


def compile_metric_query(ir: dict, semantic: Semantic, start: str, end: str,
                         permission: dict | None = None) -> dict:
    """编译为单条聚合 SQL。返回 {sql, params, dim, dim_label}。"""
    metric = semantic.metric(ir["metric"])
    if not metric:
        raise CompileError(f"未知指标: {ir['metric']}")
    agg = metric["agg"]
    table = semantic.fact_table          # 来自 models.yml(default_table)
    f = semantic.fact_alias              # 来自 models.yml(tables.<table>.alias)
    d = semantic.fact_date_col           # 来自 models.yml(tables.<table>.date_col)
    dims = ir.get("dims") or []
    dim = dims[0] if dims else None
    params: list = []
    where = build_where(params, semantic, start, end, ir.get("filters"), permission)

    if dim:
        select_dim = f"{DIM_SELECT[dim].format(f=f, d=d)} AS dim_name"
        join = DIM_JOIN[dim].format(f=f)
        if dim == "date":
            # date 维直接 GROUP BY 事实表日期列,升序输出
            sql = (
                f"SELECT {select_dim}, {agg} AS val FROM {table} {f} {join} "
                f"WHERE {where} GROUP BY {f}.{d} ORDER BY {f}.{d}"
            )
        else:
            order = "ASC" if str(ir.get("order")) == "asc" else "DESC"
            limit = int(ir.get("limit", 10))
            sql = (
                f"SELECT {select_dim}, {agg} AS val FROM {table} {f} {join} "
                f"WHERE {where} GROUP BY {DIM_GROUP[dim].format(f=f, d=d)} ORDER BY val {order}, dim_name LIMIT {limit}"
            )
    else:
        sql = f"SELECT {agg} AS val FROM {table} {f} WHERE {where}"

    return {"sql": sql, "params": params, "dim": dim, "dim_label": DIM_LABEL.get(dim)}
