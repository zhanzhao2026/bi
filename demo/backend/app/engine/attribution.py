"""指标归因(契约 §10 归因卡;docs/04 分析模式库 M5 泛化)。

两类支持(docs/04 §3.1):

1. 比率型 rate = Σnum/Σden,且 **den 按维度可加**(各维度 den_i 之和 = 整体 den):
     rate = Σ(rate_i × share_i),share_i = den_i / Σden
     贡献_i = rate_i,new × share_i,new − rate_i,old × share_i,old   (精确可加)
            = share_i,new × (rate_i,new − rate_i,old)               (维度内变化)
            + (share_i,new − share_i,old) × rate_i,old              (结构占比变化)
   Σ贡献 ≡ 整体 Δrate。支持 refund_rate(分母 GMV)/ refund_order_rate、
   new_order_ratio(分母订单数,行级 PK 可加)。

2. 可加型(绝对量):贡献_i = val_i,new − val_i,old,Σ贡献 ≡ 整体差。
   支持 gmv / refund_amt / order_cnt / item_cnt / refund_order_cnt / new_order_cnt。

**边界(诚实性)**:分母为去重计数的指标(arppu ÷ 去重人数、pay_user_cnt)不纳入
归因——去重数跨维度不可加,Σden_i ≠ den,分解残差失真;此类问法由 summary+环比
承接(见 rules/orchestrator)。

基期 = 上一等长窗口;若混合效应残差 |Δ − Σ贡献| > 阈值则追加「结构变化」瀑布项
(比率型理论上仅浮点误差;可加型恒为 0)。所有数字仅来自 SQL 结果集。
v1 行为保持:refund_rate 归因数值与 v1 逐位一致(单测锁定)。
"""
from ..core.db import inline_sql, query

# ---------------------------------------------------------------- 归因指标规格
# kind="ratio":num/den 为可加聚合表达式,den 必须按维度可加(见模块 docstring);
#   pct=True 时输出百分数口径(×100),差值单位 pp。
# kind="add":expr 为可加聚合表达式,贡献 = 两期绝对差,单位即指标单位。
# 与 semantic/metrics/*.yml 的 agg 表达式一致性由 test_attribution.py 锁定。
ATTR_SPECS: dict[str, dict] = {
    # ---- 比率型(分母可加) ----
    "refund_rate": {
        "kind": "ratio", "num": "SUM(f.refund_amount)", "den": "SUM(f.pay_amount)",
        "label": "退款率", "pct": True, "fmt": "ratio", "higher_bad": True,
    },
    "refund_order_rate": {
        "kind": "ratio", "num": "SUM(f.is_refund)", "den": "COUNT(DISTINCT f.order_id)",
        "label": "订单退款率", "pct": True, "fmt": "ratio", "higher_bad": True,
    },
    "new_order_ratio": {
        "kind": "ratio", "num": "SUM(f.user_type='新客')", "den": "COUNT(DISTINCT f.order_id)",
        "label": "新客订单占比", "pct": True, "fmt": "ratio", "higher_bad": False,
    },
    # ---- 可加型 ----
    "gmv": {"kind": "add", "expr": "SUM(f.pay_amount)", "label": "GMV",
            "fmt": "amount", "higher_bad": False},
    "refund_amt": {"kind": "add", "expr": "SUM(f.refund_amount)", "label": "退款金额",
                   "fmt": "amount", "higher_bad": True},
    "order_cnt": {"kind": "add", "expr": "COUNT(DISTINCT f.order_id)", "label": "支付订单数",
                  "fmt": "count", "higher_bad": False},
    "item_cnt": {"kind": "add", "expr": "SUM(f.item_cnt)", "label": "支付件数",
                 "fmt": "count", "higher_bad": False},
    "refund_order_cnt": {"kind": "add", "expr": "SUM(f.is_refund)", "label": "退款订单数",
                         "fmt": "count", "higher_bad": True},
    "new_order_cnt": {"kind": "add", "expr": "SUM(f.user_type='新客')", "label": "新客订单数",
                      "fmt": "count", "higher_bad": False},
}
ATTR_KEYS: tuple[str, ...] = tuple(ATTR_SPECS)

# 阈值(口径与 v1 一致):比率型数值为百分数口径,瀑布最小展示 0.005pp、
# 残差阈值 0.05pp;可加型按"值 ≥1 个单位"展示,残差恒为 0(精确可加)。
RATIO_WMIN = 0.005
RATIO_RESID = 0.05
ADD_WMIN = 1.0
ADD_RESID = 0.5


def attr_spec(metric_key: str) -> dict | None:
    return ATTR_SPECS.get(metric_key)


def _filter_clause(filters: dict | None, permission: dict | None,
                   params: list) -> str:
    """把 IR filters + 行级权限拼成以 AND 开头的 WHERE 附加段。"""
    frag = ""
    filters = filters or {}
    if filters.get("category"):
        ph = ", ".join(["%s"] * len(filters["category"]))
        frag += (f" AND f.category_id IN"
                 f" (SELECT category_id FROM dim_category WHERE category_name IN ({ph}))")
        params.extend(filters["category"])
    if filters.get("channel"):
        ph = ", ".join(["%s"] * len(filters["channel"]))
        frag += (f" AND f.channel_id IN"
                 f" (SELECT channel_id FROM dim_channel WHERE channel_name IN ({ph}))")
        params.extend(filters["channel"])
    if filters.get("user_type"):
        ph = ", ".join(["%s"] * len(filters["user_type"]))
        frag += f" AND f.user_type IN ({ph})"
        params.extend(filters["user_type"])
    if permission and permission.get("category"):
        frag += (" AND f.category_id IN"
                 " (SELECT category_id FROM dim_category WHERE category_name = %s)")
        params.append(permission["category"])
    return frag


def _select(spec: dict) -> str:
    """整体/分组共用的聚合选择段(别名固定,便于统一取数)。"""
    if spec["kind"] == "ratio":
        return f"{spec['num']} AS num, {spec['den']} AS den"
    return f"{spec['expr']} AS val"


def _overall(spec: dict, start: str, end: str, extra: str, params: list,
             sqls: list | None = None) -> tuple:
    sql = (f"SELECT {_select(spec)} FROM fact_order f "
           f"WHERE f.pay_date BETWEEN %s AND %s{extra}")
    if sqls is not None:
        sqls.append(inline_sql(sql, [start, end] + params))
    row = query(sql, [start, end] + params) or [{}]
    if spec["kind"] == "ratio":
        return float(row[0].get("num") or 0), float(row[0].get("den") or 0)
    return float(row[0].get("val") or 0), 0.0


def _by_dim(spec: dict, start: str, end: str, extra: str, params: list,
            sqls: list | None = None) -> dict:
    sql = (
        "SELECT c.category_name AS name, " + _select(spec) + " "
        "FROM fact_order f JOIN dim_category c ON c.category_id = f.category_id "
        f"WHERE f.pay_date BETWEEN %s AND %s{extra} GROUP BY c.category_name"
    )
    if sqls is not None:
        sqls.append(inline_sql(sql, [start, end] + params))
    rows = query(sql, [start, end] + params)
    out: dict[str, tuple] = {}
    for r in rows:
        if spec["kind"] == "ratio":
            out[r["name"]] = (float(r.get("num") or 0), float(r.get("den") or 0))
        else:
            out[r["name"]] = (float(r.get("val") or 0), 0.0)
    return out


def compute(start_new: str, end_new: str, start_old: str, end_old: str,
            permission: dict | None = None, sqls: list | None = None,
            filters: dict | None = None, metric_key: str = "refund_rate") -> dict:
    """执行 4 组查询并计算指定指标的归因。返回结构含 waterfall/table 与原始数值。"""
    spec = ATTR_SPECS.get(metric_key)
    if not spec:
        raise ValueError(f"指标不可归因: {metric_key}(见 ATTR_SPECS 边界说明)")
    base_params: list = []
    extra = _filter_clause(filters, permission, base_params)

    is_ratio = spec["kind"] == "ratio"
    k = 100.0 if (is_ratio and spec.get("pct")) else 1.0
    wmin = RATIO_WMIN if is_ratio else ADD_WMIN
    resid_tol = RATIO_RESID if is_ratio else ADD_RESID

    a_new, d_new = _overall(spec, start_new, end_new, extra, base_params, sqls)
    a_old, d_old = _overall(spec, start_old, end_old, extra, base_params, sqls)
    if is_ratio:
        rate_new = a_new / d_new * k if d_new else 0.0
        rate_old = a_old / d_old * k if d_old else 0.0
    else:
        rate_new, rate_old = a_new, a_old

    by_n = _by_dim(spec, start_new, end_new, extra, base_params, sqls)
    by_o = _by_dim(spec, start_old, end_old, extra, base_params, sqls)
    tot_n = d_new if is_ratio else a_new
    tot_o = d_old if is_ratio else a_old
    names = sorted(set(by_n) | set(by_o),
                   key=lambda n: -(by_n.get(n, (0.0, 0.0))[0]))

    items = []
    total_contrib = 0.0
    for name in names:
        num_n, den_n = by_n.get(name, (0.0, 0.0))
        num_o, den_o = by_o.get(name, (0.0, 0.0))
        if is_ratio:
            rate_i_n = num_n / den_n * k if den_n else 0.0
            rate_i_o = num_o / den_o * k if den_o else 0.0
            share_n = den_n / tot_n if tot_n else 0.0
            share_o = den_o / tot_o if tot_o else 0.0
            contrib = rate_i_n * share_n - rate_i_o * share_o
            within = share_n * (rate_i_n - rate_i_o)      # 其中:维度内变化
            mix = contrib - within                        # 其中:结构占比变化
            items.append({
                "name": name,
                "num_new": num_n, "den_new": den_n, "rate_new": rate_i_n,
                "num_old": num_o, "den_old": den_o, "rate_old": rate_i_o,
                "share": share_n, "contrib": contrib, "within": within, "mix": mix,
            })
        else:
            contrib = num_n - num_o
            items.append({
                "name": name,
                "num_new": num_n, "den_new": 0.0, "rate_new": num_n,
                "num_old": num_o, "den_old": 0.0, "rate_old": num_o,
                "share": None, "contrib": contrib, "within": None, "mix": None,
            })
        total_contrib += contrib
    items.sort(key=lambda x: -abs(x["contrib"]))

    delta = rate_new - rate_old
    residual = delta - total_contrib  # 混合效应残差(精确分解下≈0,机制按契约保留)
    has_residual = abs(residual) > resid_tol

    waterfall_items = [
        {"name": it["name"], "value": round(it["contrib"], 2)}
        for it in items if abs(it["contrib"]) >= wmin
    ]
    if has_residual:
        waterfall_items.append({"name": "结构变化", "value": round(residual, 2)})

    if is_ratio:
        detail_rows = [{
            "category": it["name"],
            "rate_old": it["rate_old"], "rate_new": it["rate_new"],
            "delta": it["rate_new"] - it["rate_old"],
            "share": it["share"] * 100,
            "contrib": it["contrib"],
        } for it in items]
    else:
        detail_rows = [{
            "category": it["name"],
            "rate_old": it["rate_old"], "rate_new": it["rate_new"],
            "delta": it["contrib"], "share": None, "contrib": it["contrib"],
        } for it in items]

    return {
        "kind": spec["kind"], "metric_key": metric_key, "label": spec["label"],
        "fmt": spec["fmt"],
        "pct": bool(is_ratio and spec.get("pct")), "higher_bad": spec["higher_bad"],
        "rate_new": rate_new, "rate_old": rate_old, "delta": delta,
        "gmv_new": tot_n, "gmv_old": tot_o,
        "items": items, "detail_rows": detail_rows,
        "waterfall_items": waterfall_items, "residual": residual,
        "has_residual": has_residual,
        "top": items[0] if items and abs(items[0]["contrib"]) > (RATIO_WMIN if is_ratio else ADD_WMIN) / 5 else None,
        "scoped": [n for n in (filters or {}).get("category", [])],
    }
