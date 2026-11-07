"""退款率归因(契约 §10 归因卡)。

分解口径(与数据层 verify.py 一致,精确可加):
  贡献_i = rate_i,new × share_i,new − rate_i,old × share_i,old   (单位 pp)
         = share_i,new × (rate_i,new − rate_i,old)  +  (share_i,new − share_i,old) × rate_i,old
  即"类目内退款率变化"+"类目结构(GMV占比)变化"的合计,Σ贡献 ≡ 整体 Δr。
基期 = 整体 prior30 退款率,终值 = 整体 last30 退款率;
若混合效应残差 |Δ − Σ贡献| > 0.05pp 则增加「结构变化」瀑布项(理论上仅浮点误差)。
所有数字仅来自 SQL 结果集。
"""
from ..core.db import inline_sql, query


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


def _overall(start: str, end: str, extra: str, params: list, sqls: list | None = None) -> tuple:
    sql = (
        "SELECT SUM(f.pay_amount) AS gmv, SUM(f.refund_amount) AS ref "
        f"FROM fact_order f WHERE f.pay_date BETWEEN %s AND %s{extra}"
    )
    if sqls is not None:
        sqls.append(inline_sql(sql, [start, end] + params))
    row = query(sql, [start, end] + params) or [{}]
    gmv = float(row[0].get("gmv") or 0)
    ref = float(row[0].get("ref") or 0)
    return gmv, ref


def _by_category(start: str, end: str, extra: str, params: list, sqls: list | None = None) -> dict:
    sql = (
        "SELECT c.category_name AS name, SUM(f.pay_amount) AS gmv, SUM(f.refund_amount) AS ref "
        "FROM fact_order f JOIN dim_category c ON c.category_id = f.category_id "
        f"WHERE f.pay_date BETWEEN %s AND %s{extra} GROUP BY c.category_name"
    )
    if sqls is not None:
        sqls.append(inline_sql(sql, [start, end] + params))
    rows = query(sql, [start, end] + params)
    return {r["name"]: (float(r["gmv"] or 0), float(r["ref"] or 0)) for r in rows}


def compute(start_new: str, end_new: str, start_old: str, end_old: str,
            permission: dict | None = None, sqls: list | None = None,
            filters: dict | None = None) -> dict:
    """执行 4 组查询并计算归因。返回结构含 waterfall/table 与原始数值。"""
    base_params: list = []
    extra = _filter_clause(filters, permission, base_params)

    gmv_n, ref_n = _overall(start_new, end_new, extra, base_params, sqls)
    gmv_o, ref_o = _overall(start_old, end_old, extra, base_params, sqls)
    rate_new = ref_n / gmv_n * 100 if gmv_n else 0.0
    rate_old = ref_o / gmv_o * 100 if gmv_o else 0.0

    cats_n = _by_category(start_new, end_new, extra, base_params, sqls)
    cats_o = _by_category(start_old, end_old, extra, base_params, sqls)
    names = sorted(set(cats_n) | set(cats_o), key=lambda n: -cats_n.get(n, (0, 0))[0])

    items = []
    total_contrib = 0.0
    for name in names:
        gmv_i_n, ref_i_n = cats_n.get(name, (0.0, 0.0))
        gmv_i_o, ref_i_o = cats_o.get(name, (0.0, 0.0))
        rate_i_n = ref_i_n / gmv_i_n * 100 if gmv_i_n else 0.0
        rate_i_o = ref_i_o / gmv_i_o * 100 if gmv_i_o else 0.0
        share_n = gmv_i_n / gmv_n if gmv_n else 0.0
        share_o = gmv_i_o / gmv_o if gmv_o else 0.0
        # 精确可加分解:rate_i×share_i 的两期变化(类目内 + 结构,单位 pp)
        contrib = rate_i_n * share_n - rate_i_o * share_o
        within = share_n * (rate_i_n - rate_i_o)          # 其中:类目内退款率变化
        mix = contrib - within                             # 其中:GMV 占比结构变化
        total_contrib += contrib
        items.append({
            "name": name,
            "gmv_new": gmv_i_n, "ref_new": ref_i_n, "rate_new": rate_i_n,
            "gmv_old": gmv_i_o, "ref_old": ref_i_o, "rate_old": rate_i_o,
            "share": share_n, "contrib": contrib, "within": within, "mix": mix,
        })
    items.sort(key=lambda x: -abs(x["contrib"]))

    delta = rate_new - rate_old
    residual = delta - total_contrib  # 混合效应残差(精确分解下≈0,机制按契约保留)
    has_residual = abs(residual) > 0.05

    waterfall_items = [
        {"name": it["name"], "value": round(it["contrib"], 2)}
        for it in items if abs(it["contrib"]) >= 0.005
    ]
    if has_residual:
        waterfall_items.append({"name": "结构变化", "value": round(residual, 2)})

    detail_rows = [{
        "category": it["name"],
        "rate_old": it["rate_old"], "rate_new": it["rate_new"],
        "delta": it["rate_new"] - it["rate_old"],
        "share": it["share"] * 100,
        "contrib": it["contrib"],
    } for it in items]

    return {
        "rate_new": rate_new, "rate_old": rate_old, "delta": delta,
        "gmv_new": gmv_n, "gmv_old": gmv_o,
        "items": items, "detail_rows": detail_rows,
        "waterfall_items": waterfall_items, "residual": residual,
        "has_residual": has_residual,
        "top": items[0] if items and abs(items[0]["contrib"]) > 0.001 else None,
        "scoped": [n for n in (filters or {}).get("category", [])],
    }
