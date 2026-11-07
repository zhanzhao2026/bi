"""公开 API:health / me / quickview / caliber / feedback。"""
import logging

from fastapi import APIRouter, Header, HTTPException

from ..core.config import DEMO_USERS
from ..core.db import execute, query
from ..engine import attribution
from ..engine.cards import fmt_amount, fmt_pct1, make_delta
from ..schemas import FeedbackRequest
from ..semantic import compiler
from ..semantic.loader import (get_data_as_of, get_filter_options,
                               get_semantic)
from .chat import resolve_demo_user

logger = logging.getLogger("biz.api.public")
router = APIRouter()


@router.get("/health")
def health():
    from ..core.db import db_ok

    ok = db_ok()
    return {"status": "ok", "db": "ok" if ok else "down",
            "data_as_of": get_data_as_of() if ok else None}


@router.get("/me")
def me(x_demo_user: str | None = Header(default=None)):
    current = resolve_demo_user(x_demo_user)
    return {
        "current": current,
        "users": [
            {"id": u["id"], "name": u["name"], "scope": u["scope"]}
            for u in DEMO_USERS.values()
        ],
    }


def _period_metrics(start: str, end: str, permission) -> dict:
    perm_sql = ""
    params: list = [start, end]
    if permission and permission.get("category"):
        perm_sql = " AND f.category_id IN (SELECT category_id FROM dim_category WHERE category_name = %s)"
        params.append(permission["category"])
    row = query(
        "SELECT SUM(f.pay_amount) AS gmv,"
        " SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0)*100 AS refund_rate,"
        " SUM(f.pay_amount)/NULLIF(COUNT(DISTINCT f.user_id),0) AS arppu,"
        " SUM(f.user_type='新客')/NULLIF(COUNT(DISTINCT f.order_id),0)*100 AS new_order_ratio"
        f" FROM fact_order f WHERE f.pay_date BETWEEN %s AND %s{perm_sql}",
        params,
    )
    r = (row or [{}])[0]
    return {k: (float(v) if v is not None else None) for k, v in r.items()}


@router.get("/quickview")
def quickview(x_demo_user: str | None = Header(default=None)):
    sem = get_semantic()
    data_as_of = get_data_as_of()
    if not data_as_of:
        raise HTTPException(status_code=503, detail="数据库暂不可用,请稍后再试")
    user = DEMO_USERS.get(resolve_demo_user(x_demo_user)) or DEMO_USERS["ceo"]
    permission = {"category": user["category"]} if user.get("category") else None

    import datetime as dt

    asof = dt.date.fromisoformat(data_as_of)
    end_n, start_n = asof, asof - dt.timedelta(days=29)
    end_o, start_o = start_n - dt.timedelta(days=1), start_n - dt.timedelta(days=30)
    cur = _period_metrics(start_n.isoformat(), end_n.isoformat(), permission)
    prev = _period_metrics(start_o.isoformat(), end_o.isoformat(), permission)

    def kpi(label, key, mode, higher_bad, fmt):
        d = make_delta(cur.get(key), prev.get(key), mode, higher_bad)
        item = {"label": label, "value": fmt(cur.get(key))}
        if d:
            item["delta"] = d
        return item

    kpis = [
        kpi("GMV", "gmv", "pct", False, fmt_amount),
        kpi("退款率", "refund_rate", "pp", True, fmt_pct1),
        kpi("客单价", "arppu", "pct", False, fmt_amount),
        kpi("新客订单占比", "new_order_ratio", "pp", False, fmt_pct1),
    ]

    alert = None
    rr_cur, rr_prev = cur.get("refund_rate"), prev.get("refund_rate")
    if rr_cur is not None and rr_prev is not None and rr_cur - rr_prev > 0.05:
        try:
            attr = attribution.compute(start_n.isoformat(), end_n.isoformat(),
                                       start_o.isoformat(), end_o.isoformat(), permission)
            driver = attr["top"]["name"] if attr.get("top") else "部分类目"
        except Exception:
            driver = "部分类目"
        alert = {
            "text": f"近30天退款率 {rr_cur:.2f}%,较上一期上升 {rr_cur - rr_prev:.2f}pp,主要由{driver}类目驱动",
            "question": "近30天退款率为什么上升",
        }
    return {"data_as_of": data_as_of, "kpis": kpis, "alert": alert}


@router.get("/caliber/{key}")
def caliber(key: str, mode: str = "user", x_demo_user: str | None = Header(default=None)):
    sem = get_semantic()
    m = sem.metric(key)
    if not m:
        raise HTTPException(status_code=400, detail=f"未知指标: {key}")
    if mode == "admin":
        # 编译 SQL 示例(最近30天,无筛选,ceo 权限)
        try:
            data_as_of = get_data_as_of() or "2026-11-06"
            import datetime as dt

            asof = dt.date.fromisoformat(data_as_of)
            start = (asof - dt.timedelta(days=29)).isoformat()
            ir = compiler.normalize_ir({"metric": key, "time": {"preset": "last_30d"}})
            q = compiler.compile_metric_query(ir, sem, start, data_as_of, None)
            from ..core.db import inline_sql

            sql = inline_sql(q["sql"], q["params"])
        except Exception:
            sql = m.get("agg", "")
        updated = None
        try:
            row = query("SELECT MAX(released) AS d FROM semantic_version")
            updated = str(row[0]["d"]) if row and row[0].get("d") else None
        except Exception:
            pass
        return {"key": key, "name": m.get("name"), "plain": m.get("plain"), "sql": sql,
                "version": sem.version, "updated": updated}
    # 用户模式:只回 plain
    return {"key": key, "plain": m.get("plain")}


@router.post("/feedback")
def feedback(req: FeedbackRequest, x_demo_user: str | None = Header(default=None)):
    try:
        execute(
            "INSERT INTO feedback_log (ts, trace_id, session_id, demo_user, rating, tags, comment)"
            " VALUES (NOW(), %s, %s, %s, %s, %s, %s)",
            [
                req.trace_id,
                req.session_id,
                resolve_demo_user(x_demo_user),
                req.rating,
                ",".join(req.tags) if req.tags else None,
                req.comment,
            ],
        )
    except Exception as e:
        logger.warning("feedback insert failed: %s", e)
        raise HTTPException(status_code=500, detail="反馈写入失败")
    return {"ok": True}
