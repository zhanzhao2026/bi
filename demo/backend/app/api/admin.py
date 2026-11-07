"""管理后台 API(契约 §11):overview/metrics/audit/eval/settings/versions。"""
import datetime as dt
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Query

from ..core.db import execute, query
from ..llm.gateway import LLMGateway
from ..schemas import SettingsUpdate
from ..semantic import compiler
from ..semantic.loader import get_data_as_of, get_filter_options, get_semantic
from .chat import get_orchestrator

logger = logging.getLogger("biz.api.admin")
router = APIRouter()
_gateway = LLMGateway()


def _db_or_503():
    from ..core.db import db_ok

    if not db_ok():
        raise HTTPException(status_code=503, detail="数据库暂不可用,请稍后再试")


# ================= 运营总览 =================

@router.get("/admin/overview")
def overview():
    _db_or_503()
    data_as_of = get_data_as_of()

    today_cnt = (query("SELECT COUNT(*) AS c FROM audit_log WHERE DATE(ts) = CURDATE()") or [{}])[0].get("c", 0)

    trend_rows = query(
        "SELECT DATE(ts) AS d, COUNT(*) AS c FROM audit_log"
        " WHERE ts >= CURDATE() - INTERVAL 6 DAY GROUP BY DATE(ts) ORDER BY d"
    )
    by_day = {str(r["d"]): int(r["c"]) for r in trend_rows}
    trend = []
    base = dt.date.today()
    for i in range(6, -1, -1):
        d = (base - dt.timedelta(days=i)).isoformat()
        trend.append({"date": d[5:], "count": by_day.get(d, 0)})

    active = (query("SELECT COUNT(DISTINCT session_id) AS c FROM audit_log"
                    " WHERE ts >= NOW() - INTERVAL 7 DAY") or [{}])[0].get("c", 0)
    avg_row = (query("SELECT AVG(latency_ms) AS v, AVG(llm_used)*100 AS llm_pct FROM audit_log"
                     " WHERE ts >= NOW() - INTERVAL 7 DAY") or [{}])[0]
    fact_row = (query("SELECT COUNT(*) AS c, MAX(pay_date) AS d FROM fact_order") or [{}])[0]

    # DQC:近30天退款率 > 3% 的类目
    asof = data_as_of or dt.date.today().isoformat()
    start30 = (dt.date.fromisoformat(asof) - dt.timedelta(days=29)).isoformat()
    dqc_rows = query(
        "SELECT c.category_name AS name,"
        " SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0)*100 AS rate"
        " FROM fact_order f JOIN dim_category c ON c.category_id = f.category_id"
        " WHERE f.pay_date BETWEEN %s AND %s GROUP BY c.category_name HAVING rate > 3"
        " ORDER BY rate DESC",
        [start30, asof],
    )
    dqc = [{"category": r["name"], "rate": round(float(r["rate"]), 2)} for r in dqc_rows]

    errors = query(
        "SELECT id, trace_id, ts, question, intent, status, demo_user"
        " FROM audit_log WHERE status != 'ok' ORDER BY ts DESC LIMIT 5"
    )
    for e in errors:
        e["ts"] = str(e["ts"])

    return {
        "data_as_of": data_as_of,
        "today_questions": int(today_cnt or 0),
        "trend_7d": trend,
        "active_sessions": int(active or 0),
        "avg_latency_ms": round(float(avg_row.get("v") or 0)),
        "llm_usage_pct": round(float(avg_row.get("llm_pct") or 0), 1),
        "data_water": {"fact_order_rows": int(fact_row.get("c") or 0),
                       "latest_date": str(fact_row.get("d") or "")},
        "dqc_alerts": dqc,
        "recent_errors": errors,
        "mode": "llm" if _gateway.enabled() else "rule",
    }


# ================= 语义管理 =================

@router.get("/admin/metrics")
def metrics_list():
    sem = get_semantic()
    return [
        {"key": k, "name": m.get("name"), "plain": m.get("plain"), "unit": m.get("unit"),
         "type": sem.metric_type(k), "version": sem.version}
        for k, m in sem.metrics.items()
    ]


@router.get("/admin/metrics/{key}")
def metrics_detail(key: str):
    sem = get_semantic()
    m = sem.metric(key)
    if not m:
        raise HTTPException(status_code=400, detail=f"未知指标: {key}")
    try:
        data_as_of = get_data_as_of() or dt.date.today().isoformat()
        start = (dt.date.fromisoformat(data_as_of) - dt.timedelta(days=29)).isoformat()
        ir = compiler.normalize_ir({"metric": key, "time": {"preset": "last_30d"}})
        q = compiler.compile_metric_query(ir, sem, start, data_as_of, None)
        from ..core.db import inline_sql

        sql_example = inline_sql(q["sql"], q["params"])
    except Exception:
        sql_example = m.get("agg", "")
    history = query(
        "SELECT id, version, released, author, change_desc FROM semantic_version ORDER BY version DESC"
    ) or []
    for h in history:
        h["released"] = str(h["released"])
    return {"key": key, "name": m.get("name"), "plain": m.get("plain"), "unit": m.get("unit"),
            "type": sem.metric_type(key), "agg": m.get("agg"), "sql_example": sql_example,
            "version": sem.version, "version_history": history}


@router.get("/admin/versions")
def versions():
    _db_or_503()
    rows = query(
        "SELECT id, version, released, author, change_desc, LENGTH(yaml_snapshot) AS snapshot_len"
        " FROM semantic_version ORDER BY id DESC"
    ) or []
    for r in rows:
        r["released"] = str(r["released"])
    return rows


# ================= 审计日志 =================

@router.get("/admin/audit")
def audit_list(keyword: str | None = None, page: int = 1, size: int = Query(default=20, ge=1, le=100)):
    _db_or_503()
    page = max(page, 1)
    where = "1=1"
    params: list = []
    if keyword:
        kw = f"%{keyword}%"
        where = ("question LIKE %s OR trace_id LIKE %s OR status LIKE %s"
                 " OR intent LIKE %s OR metric_key LIKE %s OR demo_user LIKE %s")
        params = [kw] * 6
    total = (query(f"SELECT COUNT(*) AS c FROM audit_log WHERE {where}", params) or [{}])[0].get("c", 0)
    rows = query(
        "SELECT id, trace_id, ts, session_id, demo_user, question, intent, metric_key,"
        " llm_used, llm_model, latency_ms, status, row_count, LEFT(narrative, 160) AS narrative"
        f" FROM audit_log WHERE {where} ORDER BY id DESC LIMIT %s OFFSET %s",
        params + [size, (page - 1) * size],
    )
    for r in rows:
        r["ts"] = str(r["ts"])
    return {"total": int(total or 0), "page": page, "size": size, "items": rows}


@router.get("/admin/audit/{trace_id}")
def audit_detail(trace_id: str):
    _db_or_503()
    rows = query("SELECT * FROM audit_log WHERE trace_id = %s ORDER BY id DESC LIMIT 1", [trace_id])
    if not rows:
        raise HTTPException(status_code=404, detail="未找到该审计记录")
    r = rows[0]
    r["ts"] = str(r["ts"])
    for f in ("ir_json", "stage_timings", "result_json"):
        try:
            r[f] = json.loads(r[f]) if r.get(f) else None
        except (json.JSONDecodeError, TypeError):
            pass
    return r


# ================= 评测 =================

def _candidates(raw: dict) -> list[float]:
    if not raw:
        return []
    vals = []
    for k in ("value", "prev", "value2", "prev2", "delta_pp", "residual_pp"):
        v = raw.get(k)
        if isinstance(v, (int, float)):
            vals.append(float(v))
    for r in raw.get("rows") or []:
        v = r.get("value")
        if isinstance(v, (int, float)):
            vals.append(float(v))
    return vals


def _value_match(raw: dict, ref_value, tolerance_pct: float) -> tuple[bool, float | None]:
    """容忍单位换算(百分数 2.80 vs 小数 0.028)。"""
    cands = _candidates(raw)
    tol = float(tolerance_pct or 2.0)
    ref = float(ref_value)
    for scale in (1.0, 100.0, 0.01):
        target = ref * scale
        if abs(target) < 1e-12:
            continue
        for c in cands:
            if abs(c - target) <= abs(target) * tol / 100.0:
                return True, c
    return False, (cands[0] if cands else None)


def _metrics_of(raw: dict) -> set:
    """答案卡涉及的指标集合(raw.metrics 优先,兼容单 metric 字段)。"""
    if not raw:
        return set()
    ms = raw.get("metrics")
    if isinstance(ms, list) and ms:
        return {m for m in ms if isinstance(m, str)}
    if raw.get("metric"):
        return {raw["metric"]}
    return set()


@router.get("/admin/eval")
def eval_list():
    _db_or_503()
    golden = query(
        "SELECT id, question, kind, expect_metric, expect_dims, ref_value, tolerance_pct, tag"
        " FROM golden_qa ORDER BY id"
    ) or []
    for g in golden:
        g["ref_value"] = float(g["ref_value"]) if g["ref_value"] is not None else None
    history = query(
        "SELECT id, ts, mode, pass_rate, total, passed FROM eval_history ORDER BY id DESC LIMIT 5"
    ) or []
    for h in history:
        h["ts"] = str(h["ts"])
    return {"golden": golden, "history": history, "mode": "llm" if _gateway.enabled() else "rule"}


@router.post("/admin/eval/run")
def eval_run():
    _db_or_503()
    orch = get_orchestrator()
    sem = get_semantic()
    mode = "llm" if _gateway.enabled() else "rule"
    golden = query(
        "SELECT id, question, kind, expect_metric, ref_value, tolerance_pct FROM golden_qa ORDER BY id"
    ) or []
    items = []
    passed = 0
    t0 = time.perf_counter()
    for g in golden:
        ref_value = float(g["ref_value"]) if g["ref_value"] is not None else None
        item = {"id": g["id"], "question": g["question"], "kind": g["kind"],
                "expect_metric": g["expect_metric"], "ref_value": ref_value,
                "got_type": None, "got_value": None, "passed": False, "note": ""}
        try:
            card, raw, done = orch.run_once(g["question"], demo_user="ceo",
                                            session_id=f"eval-{uuid.uuid4().hex[:8]}")
            item["got_type"] = (card or {}).get("type")
            if card is None:
                item["note"] = "无答案卡"
            elif g["kind"] in ("clarify", "refuse", "boundary"):
                item["passed"] = card.get("type") == g["kind"]
                if not item["passed"]:
                    item["note"] = f"期望 {g['kind']},实际 {card.get('type')}"
            else:
                type_ok = card.get("type") in ("summary", "trend", "rank", "attribution", "compare")
                metric_ok = True
                if g.get("expect_metric") and g["expect_metric"] in sem.metrics:
                    metric_ok = g["expect_metric"] in _metrics_of(raw or {})
                    if not metric_ok:
                        item["note"] = (f"指标不符: 期望 {g['expect_metric']},"
                                        f"实际 {sorted(_metrics_of(raw or {}))}")
                value_ok = True
                if ref_value is not None:
                    if g["kind"] == "trend":
                        # trend 参考值 = 走势点数(天数),tolerance_pct=0 要求精确
                        pts = len((raw or {}).get("rows") or [])
                        item["got_value"] = pts
                        tol = float(g.get("tolerance_pct") or 0)
                        ref = float(ref_value)
                        value_ok = abs(pts - ref) <= abs(ref) * tol / 100.0
                        if not value_ok:
                            item["note"] = (item["note"] + " " if item["note"] else "") + \
                                           f"走势点数不符: 期望 {int(ref)} 天,实际 {pts} 天"
                    else:
                        value_ok, got = _value_match(raw or {}, ref_value, g.get("tolerance_pct") or 2.0)
                        item["got_value"] = got
                        if not value_ok:
                            item["note"] = (item["note"] + " " if item["note"] else "") + \
                                           f"数值不符: 参考值 {ref_value},容差 {g.get('tolerance_pct')}%"
                item["passed"] = bool(type_ok and metric_ok and value_ok)
                if not type_ok:
                    item["note"] = f"卡片类型不符: {card.get('type')}"
        except Exception as e:
            item["note"] = f"执行异常: {type(e).__name__}: {e}"[:200]
        passed += 1 if item["passed"] else 0
        items.append(item)

    total = len(items)
    pass_rate = round(passed / total * 100, 2) if total else 0.0
    report = {"mode": mode, "total": total, "passed": passed, "pass_rate": pass_rate,
              "duration_ms": int((time.perf_counter() - t0) * 1000),
              "ran_at": dt.datetime.now().isoformat(timespec="seconds"), "items": items}
    try:
        execute(
            "INSERT INTO eval_history (ts, mode, pass_rate, total, passed, report_json)"
            " VALUES (NOW(), %s, %s, %s, %s, %s)",
            [mode, pass_rate, total, passed, json.dumps(report, ensure_ascii=False)],
        )
    except Exception as e:
        logger.warning("eval_history write failed: %s", e)
    return report


# ================= 系统设置 =================

@router.get("/admin/settings")
def settings_get():
    return _gateway.public_settings()


@router.put("/admin/settings")
def settings_put(req: SettingsUpdate):
    _db_or_503()
    updates = []
    if req.llm_base_url is not None:
        updates.append(("llm_base_url", req.llm_base_url.strip()))
    if req.llm_model is not None:
        updates.append(("llm_model", req.llm_model.strip()))
    if req.llm_api_key is not None:
        # 空串 = 清除;掩码值原样回传时视为未修改
        v = req.llm_api_key.strip()
        if not v.startswith("sk-***"):
            updates.append(("llm_api_key", v))
    for k, v in updates:
        if v:
            execute(
                "INSERT INTO sys_settings (skey, svalue) VALUES (%s, %s)"
                " ON DUPLICATE KEY UPDATE svalue = VALUES(svalue)",
                [k, v],
            )
        else:
            execute("DELETE FROM sys_settings WHERE skey = %s", [k])
    _gateway.invalidate()
    return {"ok": True, "settings": _gateway.public_settings()}


@router.post("/admin/settings/test")
def settings_test():
    return _gateway.test_connection()
