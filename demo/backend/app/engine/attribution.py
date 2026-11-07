"""退款率归因(契约:归因卡)。

贡献_i = share_i(last30 GMV 占比) × (rate_i,last30 − rate_i,prior30)
基期 = 整体 prior30 退款率,终值 = 整体 last30 退款率;
混合效应残差 |Δ| > 0.05pp 时增加「结构变化」项;附类目明细表。
所有数字仅来自 SQL 结果集。
"""
from ..core.db import inline_sql, query


def _overall(start: str, end: str, perm_sql: str, perm_params: list, sqls: list | None = None) -> tuple:
    sql = (
        "SELECT SUM(f.pay_amount) AS gmv, SUM(f.refund_amount) AS ref "
        f"FROM fact_order f WHERE f.pay_date BETWEEN %s AND %s{perm_sql}"
    )
    if sqls is not None:
        sqls.append(inline_sql(sql, [start, end] + perm_params))
    row = query(sql, [start, end] + perm_params) or [{}]
    gmv = float(row[0].get("gmv") or 0)
    ref = float(row[0].get("ref") or 0)
    return gmv, ref


def _by_category(start: str, end: str, perm_sql: str, perm_params: list, sqls: list | None = None) -> dict:
    sql = (
        "SELECT c.category_name AS name, SUM(f.pay_amount) AS gmv, SUM(f.refund_amount) AS ref "
        "FROM fact_order f JOIN dim_category c ON c.category_id = f.category_id "
        f"WHERE f.pay_date BETWEEN %s AND %s{perm_sql} GROUP BY c.category_name"
    )
    if sqls is not None:
        sqls.append(inline_sql(sql, [start, end] + perm_params))
    rows = query(sql, [start, end] + perm_params)
    return {r["name"]: (float(r["gmv"] or 0), float(r["ref"] or 0)) for r in rows}


def compute(start_new: str, end_new: str, start_old: str, end_old: str,
            permission: dict | None = None, sqls: list | None = None) -> dict:
    """执行 4 组查询并计算归因。返回结构含 waterfall/table 与原始数值。"""
    perm_sql = ""
    perm_params: list = []
    if permission and permission.get("category"):
        perm_sql = " AND f.category_id IN (SELECT category_id FROM dim_category WHERE category_name = %s)"
        perm_params = [permission["category"]]

    gmv_n, ref_n = _overall(start_new, end_new, perm_sql, perm_params, sqls)
    gmv_o, ref_o = _overall(start_old, end_old, perm_sql, perm_params, sqls)
    rate_new = ref_n / gmv_n * 100 if gmv_n else 0.0
    rate_old = ref_o / gmv_o * 100 if gmv_o else 0.0

    cats_n = _by_category(start_new, end_new, perm_sql, perm_params, sqls)
    cats_o = _by_category(start_old, end_old, perm_sql, perm_params, sqls)
    names = sorted(set(cats_n) | set(cats_o), key=lambda n: -cats_n.get(n, (0, 0))[0])

    items = []
    detail_rows = []
    total_contrib = 0.0
    for name in names:
        gmv_i_n, ref_i_n = cats_n.get(name, (0.0, 0.0))
        gmv_i_o, ref_i_o = cats_o.get(name, (0.0, 0.0))
        rate_i_n = ref_i_n / gmv_i_n * 100 if gmv_i_n else 0.0
        rate_i_o = ref_i_o / gmv_i_o * 100 if gmv_i_o else 0.0
        share = gmv_i_n / gmv_n if gmv_n else 0.0
        contrib = share * (rate_i_n - rate_i_o)  # 单位:pp
        total_contrib += contrib
        items.append({
            "name": name,
            "gmv_new": gmv_i_n, "ref_new": ref_i_n, "rate_new": rate_i_n,
            "gmv_old": gmv_i_o, "ref_old": ref_i_o, "rate_old": rate_i_o,
            "share": share, "contrib": contrib,
        })
    items.sort(key=lambda x: -abs(x["contrib"]))

    delta = rate_new - rate_old
    residual = delta - total_contrib  # 混合效应(结构变化)
    has_residual = abs(residual) > 0.05

    waterfall_items = [
        {"name": it["name"], "value": round(it["contrib"], 2)}
        for it in items if abs(it["contrib"]) >= 0.005
    ]
    if has_residual:
        waterfall_items.append({"name": "结构变化", "value": round(residual, 2)})

    for it in items:
        detail_rows.append({
            "category": it["name"],
            "rate_old": it["rate_old"], "rate_new": it["rate_new"],
            "delta": it["rate_new"] - it["rate_old"],
            "share": it["share"] * 100,
            "contrib": it["contrib"],
        })

    return {
        "rate_new": rate_new, "rate_old": rate_old, "delta": delta,
        "gmv_new": gmv_n, "gmv_old": gmv_o,
        "items": items, "detail_rows": detail_rows,
        "waterfall_items": waterfall_items, "residual": residual,
        "has_residual": has_residual,
        "top": items[0] if items and abs(items[0]["contrib"]) > 0.001 else None,
    }
