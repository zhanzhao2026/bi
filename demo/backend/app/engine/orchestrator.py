"""编排管道(契约 §6/§7/§9/§10):guards → understand → plan(IR) → compile → execute → verify → narrate → answer。

SSE 事件流:stage(理解问题/查询数据/交叉核验/生成分析)→ answer → done。
所有数字只能来自 SQL 结果集;会话上下文支持追问继承并注明「已沿用上一轮」。
"""
import json
import logging
import time
import uuid
from typing import Iterator

from ..core.config import DEMO_USERS
from ..core.db import inline_sql, query
from ..llm.gateway import LLMGateway
from ..semantic import compiler
from ..semantic.loader import get_data_as_of, get_filter_options, get_semantic
from . import attribution, guards, rules
from . import session as session_store
from .cards import (bar_chart, col, fmt_amount, fmt_count, fmt_pct1, fmt_pp,
                    fmt_ratio, group_chart, line_chart, make_delta, table,
                    waterfall_chart)

logger = logging.getLogger("biz.orchestrator")

DEFAULT_PRESET = "last_30d"
METRIC_ORDER = {"order_cnt", "pay_user_cnt", "item_cnt"}  # 计数型指标


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _short(s: str, n: int = 24) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


class Orchestrator:
    def __init__(self):
        self.semantic = get_semantic()
        self.gateway = LLMGateway()

    # ================= 对外入口 =================

    def stream_sse(self, question: str, demo_user: str, session_id: str,
                   assume: str | None = None) -> Iterator[str]:
        """把内部事件流格式化为 SSE 文本流(event: xxx\\ndata: {json}\\n\\n)。"""
        for ev, data in self.stream_events(question, demo_user, session_id, assume):
            if ev == "_raw":
                continue
            yield f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def run_once(self, question: str, demo_user: str = "ceo", session_id: str | None = None,
                 assume: str | None = None):
        """非流式执行(评测用)。返回 (card, raw, done_meta)。"""
        card = raw = None
        done = {}
        for ev, data in self.stream_events(question, demo_user, session_id, assume):
            if ev == "answer":
                card = data
            elif ev == "done":
                done = data
            elif ev == "_raw":
                raw = data
        return card, raw, done

    # ================= 主流程 =================

    def stream_events(self, question: str, demo_user_id: str, session_id: str | None,
                      assume: str | None = None) -> Iterator[tuple[str, dict]]:
        t0 = time.perf_counter()
        trace_id = "tr_" + uuid.uuid4().hex[:8]
        session_id = session_id or session_store.new_session_id()
        user = DEMO_USERS.get(demo_user_id) or DEMO_USERS["ceo"]
        permission = {"category": user["category"]} if user.get("category") else None
        data_as_of = get_data_as_of()
        options = get_filter_options()
        timings: dict = {}
        status = "ok"
        ir = None
        llm_used = False
        llm_model = None

        yield ("stage", {"step": "understand", "label": "理解问题", "status": "start",
                         "detail": _short(question)})

        card = None
        try:
            guard_kind, _gword = guards.check(question)
        except Exception:
            guard_kind = None
        if data_as_of is None and guard_kind is None:
            card = self._error_card(trace_id, "数据服务暂时不可用,请稍后再试。")
            status = "error"
        elif guard_kind == "refuse":
            card = self._refuse_card(trace_id)
            status = "refuse"
        elif guard_kind == "boundary":
            card = self._boundary_card(trace_id)
            status = "boundary"
        elif guard_kind == "clarify" and not assume:
            card = self._clarify_card(trace_id)
            status = "clarify"

        if card is not None:  # 守卫卡:无需查询数据
            timings["understand"] = _ms(t0)
            yield ("stage", {"step": "understand", "label": "理解问题", "status": "done",
                             "detail": "已按服务规则处理该问题"})
            self._audit(trace_id, session_id, user["id"], question, card.get("type"),
                        None, None, 0, card, card.get("narrative") or [],
                        False, None, t0, timings, status)
            yield ("answer", card)
            yield ("done", {"session_id": session_id, "trace_id": trace_id,
                            "latency_ms": _ms(t0), "status": status})
            return

        # ---- understand → plan ----
        t_plan = time.perf_counter()
        notes: list[str] = []
        assume_text = None
        parsed = {"ok": False}
        source = "rule"
        try:
            session_ctx = session_store.get(session_id)
            parsed = rules.parse(question, options)
            ir = None

            if self.gateway.enabled():
                try:
                    llm_ir = self.gateway.plan_ir(
                        question, self.semantic, session_store.summary(session_ctx), options)
                    if llm_ir is not None:
                        cand = compiler.normalize_ir(llm_ir)
                        ok, err = compiler.validate_ir(cand, self.semantic, options)
                        if ok:
                            ir = cand
                            source = "llm"
                            llm_used = True
                            llm_model = self.gateway.get_config().get("model")
                        else:
                            logger.info("llm IR invalid: %s", err)
                except Exception as e:
                    logger.warning("llm planner failed: %s", e)

            if ir is None:
                if not parsed.get("ok"):
                    card = self._error_card(trace_id)
                    status = "error"
                else:
                    ir, notes, assume_text = self._intent_to_ir(parsed, session_ctx, assume, options)
                    source = "rule"
        except Exception as e:
            logger.exception("plan failed")
            card = self._error_card(trace_id, f"理解问题时出现异常({type(e).__name__})。")
            status = "error"

        if card is None:
            ok, err = compiler.validate_ir(ir, self.semantic, options)
            if not ok:
                card = self._error_card(trace_id)
                status = "error"

        if card is not None:
            timings["understand"] = _ms(t0)
            timings["plan"] = _ms(t_plan)
            yield ("stage", {"step": "understand", "label": "理解问题", "status": "done",
                             "detail": "该问题暂无法映射到现有指标"})
            self._audit(trace_id, session_id, user["id"], question, "error",
                        ir, None, 0, card, card.get("narrative") or [],
                        llm_used, llm_model, t0, timings, status)
            yield ("answer", card)
            yield ("done", {"session_id": session_id, "trace_id": trace_id,
                            "latency_ms": _ms(t0), "status": status})
            return

        timings["understand"] = _ms(t0)
        timings["plan"] = _ms(t_plan)
        yield from self._answer_path(
            ir, parsed, source, permission, data_as_of, trace_id, session_id,
            user, question, notes, assume_text, timings, t0, llm_used, llm_model)

    # ================= 正常作答路径 =================

    def _answer_path(self, ir, parsed, source, permission, data_as_of, trace_id,
                     session_id, user, question, notes, assume_text,
                     timings, t0, llm_used, llm_model):
        sem = self.semantic
        start, end = compiler.resolve_time(ir["time"], data_as_of)
        prev = compiler.prev_range(start, end) if ir.get("compare") == "prev_period" else None

        time_label = compiler.preset_label((ir.get("time") or {}).get("preset"))
        metric_name = sem.metric_name(ir["metric"])
        card_kind = (parsed.get("card") if parsed.get("ok") else None) or self._card_kind_from_ir(ir)
        dim_label = {"category": "类目", "channel": "渠道"}.get(
            (parsed or {}).get("dim") or "", "类目")

        # understand done
        if card_kind == "trend":
            u_detail = f"已理解:想看{time_label}{metric_name}的走势"
        elif card_kind == "rank":
            u_detail = f"已理解:想看{time_label}各{dim_label}{metric_name}对比"
        elif card_kind == "attribution":
            u_detail = f"已理解:想分析{time_label}{metric_name}变化的原因"
        elif card_kind == "compare":
            u_detail = f"已理解:想对比{time_label}新老客的表现"
        else:
            u_detail = f"已理解:想了解{time_label}{metric_name}概况"
        yield ("stage", {"step": "understand", "label": "理解问题", "status": "done",
                         "detail": u_detail})

        # ---- query ----
        yield ("stage", {"step": "query", "label": "查询数据", "status": "start",
                         "detail": f"正在查询{time_label}的交易数据"})
        t_c = time.perf_counter()
        sqls: list[str] = []
        try:
            built = self._build_data(ir, parsed, permission, start, end, prev, sqls, card_kind)
        except Exception as e:
            logger.exception("execute failed")
            card = self._error_card(trace_id, "查询数据时出现问题,请稍后再试。")
            self._audit(trace_id, session_id, user["id"], question, "error",
                        ir, ";\n".join(sqls), 0, card, card.get("narrative") or [],
                        llm_used, llm_model, t0, timings, "error")
            yield ("stage", {"step": "query", "label": "查询数据", "status": "done",
                             "detail": "查询出现问题,已中止"})
            yield ("answer", card)
            yield ("done", {"session_id": session_id, "trace_id": trace_id,
                            "latency_ms": _ms(t0), "status": "error"})
            return
        timings["compile"] = _ms(t_c)
        row_count = built["row_count"]
        yield ("stage", {"step": "query", "label": "查询数据", "status": "done",
                         "detail": built["query_detail"]})

        # ---- verify ----
        yield ("stage", {"step": "verify", "label": "交叉核验", "status": "start",
                         "detail": "正在核对答案中的数字"})
        t_v = time.perf_counter()
        card = built["card_fn"](trace_id, data_as_of, notes, assume_text)
        n_num = self._count_numbers(card)
        timings["verify"] = _ms(t_v)
        yield ("stage", {"step": "verify", "label": "交叉核验", "status": "done",
                         "detail": f"已核验 {n_num} 项数字,与明细数据一致"})

        # ---- narrate ----
        yield ("stage", {"step": "narrate", "label": "生成分析", "status": "start",
                         "detail": "正在生成分析结论"})
        t_n = time.perf_counter()
        narrative = notes + built["narrative"]
        if self.gateway.enabled():
            try:
                llm_sents = self.gateway.narrate(built["narrator_payload"])
                if llm_sents:
                    narrative = notes + llm_sents
                    llm_used = True
                    llm_model = self.gateway.get_config().get("model")
            except Exception as e:
                logger.warning("narrator error: %s", e)
        card["narrative"] = narrative
        timings["narrate"] = _ms(t_n)
        yield ("stage", {"step": "narrate", "label": "生成分析", "status": "done",
                         "detail": "分析已生成"})

        # ---- save session & audit & answer ----
        session_store.save(session_id, ir)
        self._audit(trace_id, session_id, user["id"], question, card.get("type"),
                    ir, ";\n".join(built["executed_sqls"]), row_count, card,
                    narrative, llm_used, llm_model, t0, timings, "ok")
        yield ("_raw", built["raw"])
        yield ("answer", card)
        yield ("done", {"session_id": session_id, "trace_id": trace_id,
                        "latency_ms": _ms(t0), "status": "ok"})

    def _card_kind_from_ir(self, ir: dict) -> str:
        dims = ir.get("dims") or []
        if dims and dims[0] == "date":
            return "trend"
        if dims:
            return "rank"
        return "summary"

    # ================= IR 构建 =================

    def _intent_to_ir(self, p: dict, session_ctx, assume, options):
        """规则 intent → IR;处理 assume 与会话继承。返回 (ir, notes, assume_text)。"""
        notes: list[str] = []
        assume_text = None
        prev_ir = (session_ctx or {}).get("ir") if session_ctx else None

        # --- 时间:assume > 问题显式时间词 > 继承上一轮 > 默认 ---
        time_ir = None
        assume_time = self._parse_assume(assume)
        if assume_time is not None:
            time_ir = assume_time
        elif p.get("time_explicit") and p.get("time_preset"):
            time_ir = {"preset": p["time_preset"]}
        elif prev_ir and prev_ir.get("time"):
            time_ir = prev_ir["time"]
            notes.append("已沿用上一轮的时间范围。")
        else:
            time_ir = {"preset": DEFAULT_PRESET}

        # --- 筛选:问题显式词 > 继承上一轮 ---
        p_filters = p.get("filters") or {}
        prev_filters = (prev_ir or {}).get("filters") or {}
        merged = {}
        inherited_filter = False
        for k in ("category", "channel", "user_type"):
            if p_filters.get(k):
                merged[k] = list(p_filters[k])
            elif prev_filters.get(k):
                merged[k] = list(prev_filters[k])
                inherited_filter = True
            else:
                merged[k] = []
        if inherited_filter:
            notes.append("已沿用上一轮的筛选条件。")

        card = p["card"]
        dims = ["date"] if card == "trend" else ([p["dim"]] if card == "rank" and p.get("dim") else [])
        metric = p.get("metric")
        if metric in (None, "__refund__"):
            metric = "refund_rate"  # 退款域 summary:主指标退款率(卡内附退款金额)
        if p.get("vague"):
            assume_text = f"按 GMV 理解「{p['vague']}」"
        compare = "prev_period" if p.get("want_compare") else "none"
        ir = {
            "metric": metric,
            "time": time_ir,
            "dims": dims,
            "filters": merged,
            "granularity": "day",
            "compare": compare,
            "order": "desc",
            "limit": 10,
        }
        return ir, notes, assume_text

    def _parse_assume(self, assume) -> dict | None:
        """assume 取值:preset key / 时间词文本 / {"time_preset": ...}。"""
        if not assume:
            return None
        a = str(assume).strip()
        if not a:
            return None
        try:
            obj = json.loads(a)
            if isinstance(obj, dict):
                a = str(obj.get("time_preset") or obj.get("preset") or obj.get("time") or "")
        except (json.JSONDecodeError, ValueError):
            pass
        if a in self.semantic.time_presets:
            return {"preset": a}
        preset = rules.parse_time(a)
        if preset:
            return {"preset": preset}
        return None

    # ================= 查询与数据构建 =================

    def _run(self, ir: dict, start: str, end: str, permission, sqls: list) -> list[dict]:
        q = compiler.compile_metric_query(ir, self.semantic, start, end, permission)
        sqls.append(inline_sql(q["sql"], q["params"]))
        rows = query(q["sql"], q["params"])
        return [{"dim": r.get("dim_name"), "val": _f(r.get("val"))} for r in rows]

    def _build_data(self, ir, parsed, permission, start, end, prev, sqls, card_kind) -> dict:
        domain = (parsed or {}).get("domain") or "gmv"
        result = {
            "card": card_kind,
            "raw": {"metric": ir["metric"], "domain": domain},
            "row_count": 0,
            "executed_sqls": [],
            "narrative": [],
            "narrator_payload": {},
            "card_fn": None,
            "query_detail": "",
        }
        ctx = {"ir": ir, "permission": permission, "start": start, "end": end,
               "prev": prev, "time_label": compiler.preset_label((ir.get("time") or {}).get("preset")),
               "prev_label": self._prev_label((ir.get("time") or {}).get("preset")),
               "parsed": parsed, "domain": domain, "sqls": sqls}

        if card_kind == "summary" and domain == "refund":
            return self._data_refund_summary(ctx, result)
        if card_kind == "summary":
            return self._data_summary(ctx, result)
        if card_kind == "trend":
            return self._data_trend(ctx, result)
        if card_kind == "rank":
            return self._data_rank(ctx, result)
        if card_kind == "compare":
            return self._data_compare(ctx, result)
        if card_kind == "attribution":
            return self._data_attribution(ctx, result)
        return self._data_summary(ctx, result)

    # ---- 各卡片的数据构建 ----

    def _data_summary(self, ctx, result):
        ir = ctx["ir"]
        mkey = ir["metric"]
        m = self.semantic.metric(mkey)
        name = m["name"]
        is_ratio = self.semantic.is_ratio(mkey)
        higher_bad = self.semantic.higher_is_bad(mkey)
        fmt = fmt_ratio if is_ratio else (fmt_count if mkey in METRIC_ORDER else fmt_amount)

        rows_now = self._run(ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        value = rows_now[0]["val"] if rows_now else None
        prev_value = None
        if ctx["prev"]:
            rows_prev = self._run(ir, ctx["prev"][0], ctx["prev"][1], ctx["permission"], ctx["sqls"])
            prev_value = rows_prev[0]["val"] if rows_prev else None
        result["row_count"] = len(rows_now)
        result["executed_sqls"] = list(ctx["sqls"])

        delta = None
        if value is not None and prev_value is not None:
            delta = make_delta(value, prev_value, "pp" if is_ratio else "pct", higher_bad)
        kpis = [{"label": name, "value": fmt(value)}]
        if delta:
            kpis[0]["delta"] = delta

        n = [f"{ctx['time_label']}({ctx['start'][5:]}~{ctx['end'][5:]}){name}为 {fmt(value)}。"]
        if value is not None and prev_value is not None:
            if is_ratio:
                d = value - prev_value
                word = "上升" if d > 0 else ("下降" if d < 0 else "持平")
                n.append(f"较{ctx['prev_label']}{word} {abs(d):.2f}pp。")
            else:
                pct = (value - prev_value) / prev_value * 100 if prev_value else None
                if pct is None or abs(pct) < 0.05:
                    n.append(f"较{ctx['prev_label']}基本持平。")
                else:
                    word = "增长" if pct > 0 else "下降"
                    n.append(f"较{ctx['prev_label']}{word} {abs(pct):.1f}%。")
        result["narrative"] = n
        result["narrator_payload"] = {"指标": name, "时间范围": f"{ctx['start']}~{ctx['end']}",
                                      "当前值": fmt(value),
                                      "上一期值": fmt(prev_value) if prev_value is not None else "无"}
        result["raw"].update({"value": value, "prev": prev_value})
        result["card_fn"] = lambda tid, dao, notes, atext: self._finish_card(
            "summary", tid, dao, f"{ctx['time_label']}{name}", kpis, None, None,
            [mkey], notes, atext, ctx)
        result["query_detail"] = f"已完成{ctx['time_label']}数据汇总"
        return result

    def _data_refund_summary(self, ctx, result):
        """退款域默认 summary:退款率 + 退款金额双 KPI。"""
        ir = ctx["ir"]
        rate_rows = self._run(ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        rate = rate_rows[0]["val"] if rate_rows else None
        ir_amt = dict(ir)
        ir_amt["metric"] = "refund_amt"
        ir_amt["dims"] = []
        amt_rows = self._run(ir_amt, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        amt = amt_rows[0]["val"] if amt_rows else None

        rate_prev = amt_prev = None
        if ctx["prev"]:
            rp = self._run(ir, ctx["prev"][0], ctx["prev"][1], ctx["permission"], ctx["sqls"])
            rate_prev = rp[0]["val"] if rp else None
            ap = self._run(ir_amt, ctx["prev"][0], ctx["prev"][1], ctx["permission"], ctx["sqls"])
            amt_prev = ap[0]["val"] if ap else None
        result["row_count"] = len(rate_rows)
        result["executed_sqls"] = list(ctx["sqls"])

        kpis = [{"label": "退款率", "value": fmt_ratio(rate)}]
        if rate is not None and rate_prev is not None:
            kpis[0]["delta"] = make_delta(rate, rate_prev, "pp", True)
        kpis.append({"label": "退款金额", "value": fmt_amount(amt)})
        if amt is not None and amt_prev is not None:
            kpis[1]["delta"] = make_delta(amt, amt_prev, "pct", True)

        n = [f"{ctx['time_label']}退款金额 {fmt_amount(amt)},退款率 {fmt_ratio(rate)}。"]
        if rate is not None and rate_prev is not None:
            d = rate - rate_prev
            word = "上升" if d > 0 else ("下降" if d < 0 else "持平")
            amt_word = ""
            if amt is not None and amt_prev:
                amt_word = f",退款金额{'增长' if amt >= amt_prev else '下降'} {abs(amt - amt_prev) / amt_prev * 100:.1f}%"
            n.append(f"较{ctx['prev_label']},退款率{word} {abs(d):.2f}pp{amt_word}。")
        result["narrative"] = n
        result["narrator_payload"] = {"指标": "退款率/退款金额",
                                      "时间范围": f"{ctx['start']}~{ctx['end']}",
                                      "当前退款率": fmt_ratio(rate), "上期退款率": fmt_ratio(rate_prev),
                                      "当前退款金额": fmt_amount(amt), "上期退款金额": fmt_amount(amt_prev)}
        result["raw"].update({"value": rate, "prev": rate_prev, "value2": amt, "prev2": amt_prev})
        result["card_fn"] = lambda tid, dao, notes, atext: self._finish_card(
            "summary", tid, dao, f"{ctx['time_label']}退款情况", kpis, None, None,
            ["refund_rate", "refund_amt"], notes, atext, ctx)
        result["query_detail"] = f"已完成{ctx['time_label']}退款数据汇总"
        return result

    def _data_trend(self, ctx, result):
        ir = ctx["ir"]
        mkey = ir["metric"]
        m = self.semantic.metric(mkey)
        name = m["name"]
        is_ratio = self.semantic.is_ratio(mkey)
        higher_bad = self.semantic.higher_is_bad(mkey)
        fmt = fmt_ratio if is_ratio else (fmt_count if mkey in METRIC_ORDER else fmt_amount)

        rows_now = self._run(ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        rows_prev = []
        if ctx["prev"]:
            rows_prev = self._run(ir, ctx["prev"][0], ctx["prev"][1], ctx["permission"], ctx["sqls"])
        result["row_count"] = len(rows_now)
        result["executed_sqls"] = list(ctx["sqls"])

        x = [r["dim"] for r in rows_now]
        data = [round(r["val"] or 0, 4) for r in rows_now]
        series = [{"name": "本期", "data": data}]
        if rows_prev:
            series.append({"name": "上一期", "data": [round(r["val"] or 0, 4) for r in rows_prev]})
        chart = line_chart(f"{ctx['time_label']}{name}走势", x, series)

        direction = self._trend_direction(data)
        n = []
        if is_ratio:
            avg = sum(data) / len(data) if data else 0
            peak_i = max(range(len(data)), key=lambda i: data[i]) if data else None
            low_i = min(range(len(data)), key=lambda i: data[i]) if data else None
            kpis = [{"label": f"{ctx['time_label']}日均{name}", "value": fmt(avg)}]
            if rows_prev:
                prev_data = [round(r["val"] or 0, 4) for r in rows_prev]
                prev_avg = sum(prev_data) / len(prev_data) if prev_data else 0
                delta = make_delta(avg, prev_avg, "pp", higher_bad)
                if delta:
                    kpis[0]["delta"] = delta
            n.append(f"{ctx['time_label']}{name}日均 {fmt(avg)},最高 {fmt(data[peak_i])}({x[peak_i]}),"
                     f"最低 {fmt(data[low_i])}({x[low_i]})。" if data else f"{ctx['time_label']}暂无数据。")
            n.append(f"整体走势{direction}。")
            agg_value, agg_prev = avg, (sum(round(r['val'] or 0, 4) for r in rows_prev) / len(rows_prev) if rows_prev else None)
        else:
            total = sum(data)
            avg = total / len(data) if data else 0
            peak_i = max(range(len(data)), key=lambda i: data[i]) if data else None
            kpis = [{"label": f"{ctx['time_label']}{name}合计", "value": fmt(total)}]
            if rows_prev:
                prev_total = sum(round(r["val"] or 0, 4) for r in rows_prev)
                delta = make_delta(total, prev_total, "pct", higher_bad)
                if delta:
                    kpis[0]["delta"] = delta
            n.append(f"{ctx['time_label']}{name}合计 {fmt(total)},日均 {fmt(avg)}。")
            if peak_i is not None:
                n.append(f"峰值出现在 {x[peak_i]}({fmt(data[peak_i])})。")
            n.append(f"整体走势{direction}。")
            agg_value, agg_prev = total, (sum(round(r['val'] or 0, 4) for r in rows_prev) if rows_prev else None)

        result["narrative"] = n
        payload_rows = [{"日期": x[i], name: fmt(data[i])} for i in range(len(x))]
        result["narrator_payload"] = {"指标": name, "时间范围": f"{ctx['start']}~{ctx['end']}",
                                      "每日明细": payload_rows[:31]}
        result["raw"].update({"value": agg_value, "prev": agg_prev,
                              "rows": [{"name": x[i], "value": data[i]} for i in range(len(x))]})
        result["card_fn"] = lambda tid, dao, notes, atext: self._finish_card(
            "trend", tid, dao, f"{ctx['time_label']}{name}走势", kpis, chart, None,
            [mkey], notes, atext, ctx)
        result["query_detail"] = f"已完成{ctx['time_label']}按天数据汇总(共 {len(rows_now)} 天)"
        return result

    def _data_rank(self, ctx, result):
        ir = ctx["ir"]
        mkey = ir["metric"]
        m = self.semantic.metric(mkey)
        name = m["name"]
        is_ratio = self.semantic.is_ratio(mkey)
        fmt = fmt_ratio if is_ratio else (fmt_count if mkey in METRIC_ORDER else fmt_amount)
        dim = (ir.get("dims") or ["category"])[0]
        dim_label = compiler.DIM_LABEL.get(dim, dim)
        time_label = ctx["time_label"]

        rows_now = self._run(ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        rows_now = [r for r in rows_now if r["dim"]]
        result["row_count"] = len(rows_now)
        result["executed_sqls"] = list(ctx["sqls"])

        names = [r["dim"] for r in rows_now]
        vals = [round(r["val"] or 0, 4) for r in rows_now]
        total = sum(v for v in vals if not is_ratio) or None

        kpis = []
        if names:
            kpis.append({"label": f"{name}最高{dim_label}", "value": names[0]})
            kpis.append({"label": f"最高的{name}", "value": fmt(vals[0])})
            if total:
                kpis.append({"label": "该{0}占比".format(dim_label), "value": fmt_pct1(vals[0] / total * 100)})
        chart = bar_chart(f"{time_label}各{dim_label}{name}", names,
                          [{"name": name, "data": vals}])
        cols = [col(dim_label, dim_label), col("val", name, "right")]
        rows = []
        for i, (nm, v) in enumerate(zip(names, vals)):
            row = {dim_label: nm, "val": fmt(v)}
            if total:
                row["share"] = fmt_pct1(v / total * 100)
                cols = [col(dim_label, dim_label), col("val", name, "right"),
                        col("share", "占比", "right")]
            rows.append(row)

        n = []
        if names:
            n.append(f"{time_label}{name}最高的是{names[0]}({fmt(vals[0])})"
                     + (f",占整体 {vals[0] / total * 100:.1f}%。" if total else "。"))
            if total and len(names) >= 3:
                top3 = sum(vals[:3]) / total * 100
                n.append(f"前3名{dim_label}合计占 {top3:.1f}%,共 {len(names)} 个{dim_label}。")
            elif is_ratio and len(names) >= 2:
                n.append(f"{name}最低的是{names[-1]}({fmt(vals[-1])}),最高最低相差 {vals[0] - vals[-1]:.2f}pp。")
        else:
            n.append(f"{time_label}暂无{dim_label}数据。")
        result["narrative"] = n
        result["narrator_payload"] = {"指标": name, "维度": dim_label,
                                      "时间范围": f"{ctx['start']}~{ctx['end']}",
                                      "明细": [{dim_label: names[i], name: fmt(vals[i])} for i in range(len(names))]}
        result["raw"].update({"value": vals[0] if vals else None,
                              "rows": [{"name": nm, "value": v} for nm, v in zip(names, vals)]})
        result["card_fn"] = lambda tid, dao, notes, atext: self._finish_card(
            "rank", tid, dao, f"{time_label}各{dim_label}{name}", kpis, chart,
            table(cols, rows), [mkey], notes, atext, ctx)
        result["query_detail"] = f"已完成{time_label}按{dim_label}汇总(共 {len(rows_now)} 组)"
        return result

    def _data_attribution(self, ctx, result):
        ir = ctx["ir"]
        time_label = ctx["time_label"]
        pstart, pend = ctx["prev"] if ctx["prev"] else compiler.prev_range(ctx["start"], ctx["end"])
        attr = attribution.compute(ctx["start"], ctx["end"], pstart, pend,
                                   ctx["permission"], ctx["sqls"])
        result["row_count"] = len(attr["items"])
        result["executed_sqls"] = list(ctx["sqls"])

        kpis = [{"label": "退款率", "value": fmt_ratio(attr["rate_new"]),
                 "delta": make_delta(attr["rate_new"], attr["rate_old"], "pp", True)}]
        chart = waterfall_chart(
            f"{time_label}退款率变化归因(pp)",
            f"上一期 {attr['rate_old']:.2f}",
            attr["waterfall_items"],
            f"当前 {attr['rate_new']:.2f}",
        )
        cols = [col("category", "类目"), col("rate_old", "上期退款率", "right"),
                col("rate_new", "本期退款率", "right"), col("delta", "变化", "right"),
                col("contrib", "贡献", "right")]
        rows = []
        for r in attr["detail_rows"]:
            rows.append({
                "category": r["category"],
                "rate_old": fmt_ratio(r["rate_old"]),
                "rate_new": fmt_ratio(r["rate_new"]),
                "delta": fmt_pp(r["delta"]),
                "contrib": fmt_pp(r["contrib"]),
            })
        tbl = table(cols, rows)

        d = attr["delta"]
        direction = "上升" if d > 0 else "下降"
        top = attr["top"]
        n = [f"{time_label}整体退款率 {attr['rate_new']:.2f}%,较{ctx['prev_label']}的 "
             f"{attr['rate_old']:.2f}% {direction} {abs(d):.2f}pp。"]
        if top:
            up_down = "升至" if top["rate_new"] >= top["rate_old"] else "降至"
            n.append(f"按类目拆解,{top['name']}是主要驱动:退款率由 {top['rate_old']:.2f}% "
                     f"{up_down} {top['rate_new']:.2f}%,贡献约 {top['contrib']:+.2f}pp。")
        if attr["has_residual"]:
            n.append(f"此外,类目结构变化带来 {attr['residual']:+.2f}pp 的影响。")
        else:
            n.append("其余类目退款率基本持平。")

        result["narrative"] = n
        result["narrator_payload"] = {
            "整体退款率(本期)": fmt_ratio(attr["rate_new"]),
            "整体退款率(上期)": fmt_ratio(attr["rate_old"]),
            "变化(pp)": f"{d:.2f}",
            "类目明细": [{"类目": r["category"], "上期": fmt_ratio(r["rate_old"]),
                          "本期": fmt_ratio(r["rate_new"]), "贡献": fmt_pp(r["contrib"])}
                         for r in attr["detail_rows"]],
        }
        result["raw"].update({
            "value": attr["rate_new"], "prev": attr["rate_old"], "delta_pp": attr["delta"],
            "residual_pp": attr["residual"],
            "rows": [{"name": r["category"], "value": round(r["contrib"], 4)}
                     for r in attr["detail_rows"]],
        })
        result["card_fn"] = lambda tid, dao, notes, atext: self._finish_card(
            "attribution", tid, dao, f"{time_label}退款率归因", kpis, chart, tbl,
            ["refund_rate", "gmv"], notes, atext, ctx,
            scope_note="以上为相关性线索,建议业务侧进一步确认。")
        result["query_detail"] = (f"已汇总本期({ctx['start'][5:]}~{ctx['end'][5:]})与"
                                  f"上一期({pstart[5:]}~{pend[5:]})的分类目数据")
        return result

    def _data_compare(self, ctx, result):
        ir = ctx["ir"]
        time_label = ctx["time_label"]
        base_ir = dict(ir)
        base_ir["dims"] = ["user_type"]
        base_ir["compare"] = "none"

        gmv_ir = dict(base_ir)
        gmv_ir["metric"] = "gmv"
        gmv_rows = self._run(gmv_ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        order_ir = dict(base_ir)
        order_ir["metric"] = "order_cnt"
        order_rows = self._run(order_ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])

        ratio_ir = dict(ir)
        ratio_ir["metric"] = "new_order_ratio"
        ratio_ir["dims"] = []
        ratio_rows = self._run(ratio_ir, ctx["start"], ctx["end"], ctx["permission"], ctx["sqls"])
        new_ratio = ratio_rows[0]["val"] if ratio_rows else None
        ratio_prev = None
        if ctx["prev"]:
            rp = self._run(ratio_ir, ctx["prev"][0], ctx["prev"][1], ctx["permission"], ctx["sqls"])
            ratio_prev = rp[0]["val"] if rp else None
        result["row_count"] = len(gmv_rows) + len(order_rows)
        result["executed_sqls"] = list(ctx["sqls"])

        def by_type(rows):
            return {r["dim"]: (r["val"] or 0) for r in rows}

        gmv_m, order_m = by_type(gmv_rows), by_type(order_rows)
        types = ["新客", "老客"]
        gmv_vals = [round(gmv_m.get(t, 0.0), 2) for t in types]
        order_vals = [round(order_m.get(t, 0.0), 2) for t in types]
        gmv_total = sum(gmv_vals) or None

        kpis = [{"label": "新客订单占比", "value": fmt_pct1(new_ratio)}]
        if new_ratio is not None and ratio_prev is not None:
            kpis[0]["delta"] = make_delta(new_ratio, ratio_prev, "pp", False)

        chart = group_chart(f"{time_label}新老客对比", types,
                            [{"name": "GMV", "data": gmv_vals},
                             {"name": "订单数", "data": order_vals}])
        cols = [col("user_type", "用户类型"), col("orders", "订单数", "right"),
                col("gmv", "GMV", "right"), col("share", "GMV占比", "right")]
        rows = []
        for i, t in enumerate(types):
            rows.append({
                "user_type": t, "orders": fmt_count(order_vals[i]),
                "gmv": fmt_amount(gmv_vals[i]),
                "share": fmt_pct1(gmv_vals[i] / gmv_total * 100) if gmv_total else "—",
            })
        tbl = table(cols, rows)

        n = [f"{time_label}新客订单占比 {fmt_pct1(new_ratio)}。"]
        if gmv_total:
            n.append(f"新客 GMV {fmt_amount(gmv_vals[0])}(占 {gmv_vals[0] / gmv_total * 100:.1f}%),"
                     f"老客 GMV {fmt_amount(gmv_vals[1])}(占 {gmv_vals[1] / gmv_total * 100:.1f}%)。")
        if ctx["prev"]:
            op = self._run(order_ir, ctx["prev"][0], ctx["prev"][1], ctx["permission"], ctx["sqls"])
            op_m = by_type(op)

            def chg(new, old):
                if not old:
                    return "—"
                return f"{(new / old - 1) * 100:+.1f}%"

            n.append(f"与{ctx['prev_label']}相比,新客订单 {chg(order_vals[0], op_m.get('新客'))},"
                     f"老客订单 {chg(order_vals[1], op_m.get('老客'))}。")
        result["narrative"] = n
        result["narrator_payload"] = {"时间范围": f"{ctx['start']}~{ctx['end']}",
                                      "新客订单占比": fmt_pct1(new_ratio),
                                      "新客GMV": fmt_amount(gmv_vals[0]), "老客GMV": fmt_amount(gmv_vals[1]),
                                      "新客订单数": fmt_count(order_vals[0]), "老客订单数": fmt_count(order_vals[1])}
        result["raw"].update({"value": new_ratio, "prev": ratio_prev,
                              "rows": [{"name": "新客GMV", "value": gmv_vals[0]},
                                       {"name": "老客GMV", "value": gmv_vals[1]},
                                       {"name": "新客订单数", "value": order_vals[0]},
                                       {"name": "老客订单数", "value": order_vals[1]}]})
        result["card_fn"] = lambda tid, dao, notes, atext: self._finish_card(
            "compare", tid, dao, f"{time_label}新老客对比", kpis, chart, tbl,
            ["new_order_ratio", "gmv", "order_cnt"], notes, atext, ctx)
        result["query_detail"] = f"已完成{time_label}新老客数据汇总"
        return result

    # ================= 卡片收尾 =================

    def _finish_card(self, card_type, trace_id, data_as_of, title, kpis, chart, tbl,
                     caliber, notes, assume_text, ctx, scope_note=None):
        ir = ctx["ir"]
        perm_cat = (ctx.get("permission") or {}).get("category")
        filters = ir.get("filters") or {}
        ctx_categories = list(filters.get("category") or [])
        if perm_cat and perm_cat not in ctx_categories:
            ctx_categories.append(perm_cat)
        return {
            "type": card_type,
            "trace_id": trace_id,
            "data_as_of": data_as_of,
            "title": title,
            "assume": {"text": assume_text} if assume_text else None,
            "narrative": notes,
            "kpis": kpis,
            "chart": chart,
            "table": tbl,
            "caliber": caliber,
            "scope_note": scope_note,
            "followups": self._followups(card_type, ir["metric"], ctx["domain"], perm_cat),
            "context": {
                "time_preset": (ir.get("time") or {}).get("preset"),
                "category": ctx_categories,
                "channel": list(filters.get("channel") or []),
            },
        }

    def _followups(self, card_type, metric, domain, perm_cat) -> list:
        cat_word = perm_cat or "美妆"
        if card_type == "summary":
            if domain == "refund" or metric in ("refund_rate", "refund_amt"):
                return ["近30天退款率为什么上升", "退款金额走势", f"只看{cat_word}的退款率"]
            if metric == "arppu":
                return [f"{cat_word}的客单价是多少", "各类目GMV排名", "新老客对比"]
            if metric == "pay_user_cnt":
                return ["最近30天客单价", "订单数走势", "新老客对比"]
            if metric == "order_cnt":
                return ["最近30天客单价", "各类目GMV排名", "新老客对比"]
            return ["最近30天GMV走势", "各类目GMV排名", "各渠道销售情况"]
        if card_type == "trend":
            return ["较上一期变化多少", "各类目GMV排名", f"只看{cat_word}的表现"]
        if card_type == "rank":
            return ["各渠道销售情况", "近30天退款率为什么上升", "新老客对比"]
        if card_type == "attribution":
            return [f"只看{cat_word}的退款率走势", "哪些渠道退款更高", "最近7天退款率"]
        if card_type == "compare":
            return ["新客客单价是多少", "最近30天GMV走势", "新客订单占比走势"]
        return ["最近30天GMV走势", "近30天退款率为什么上升"]

    def _prev_label(self, preset) -> str:
        return {
            "last_30d": "上一个30天",
            "last_7d": "上一个7天",
            "yesterday": "前一天",
            "this_month": "上个月",
            "last_month": "再上个月",
        }.get(preset, "上一周期")

    def _trend_direction(self, data: list) -> str:
        n = len(data)
        if n < 6:
            return "波动平稳"
        k = max(2, n // 4)
        head = sum(data[:k]) / k
        tail = sum(data[-k:]) / k
        if head <= 0:
            return "波动平稳"
        chg = (tail - head) / head * 100
        if chg > 5:
            return "整体上升"
        if chg < -5:
            return "整体下行"
        return "波动平稳"

    def _count_numbers(self, card: dict) -> int:
        n = len(card.get("kpis") or [])
        chart = card.get("chart") or {}
        for s in chart.get("series") or []:
            n += len(s.get("data") or [])
        n += len(chart.get("items") or [])
        n += len((card.get("table") or {}).get("rows") or [])
        return max(n, 1)

    # ================= 守卫卡 =================

    def _base_card(self, t: str, trace_id: str, title: str) -> dict:
        return {"type": t, "trace_id": trace_id, "data_as_of": get_data_as_of(),
                "title": title, "assume": None, "kpis": [], "chart": None, "table": None,
                "caliber": [], "scope_note": None, "context": {"time_preset": None, "category": []}}

    def _refuse_card(self, trace_id: str) -> dict:
        c = self._base_card("refuse", trace_id, "该问题不在分析范围")
        c["narrative"] = [
            "抱歉,涉及员工薪酬与个人隐私信息,本系统不提供这类数据。",
            "我是交易数据分析助手,可以回答 GMV、订单数、支付人数、客单价、退款金额、退款率等交易域问题。",
        ]
        c["followups"] = ["最近30天GMV走势", "近30天退款率为什么上升"]
        return c

    def _boundary_card(self, trace_id: str) -> dict:
        c = self._base_card("boundary", trace_id, "商品级明细 v1 未开放")
        c["narrative"] = [
            "商品级/订单级明细与导出在当前版本暂未开放。",
            "目前支持按类目、渠道、新老客等维度做汇总分析,您可以换个问法。",
        ]
        c["followups"] = ["各类目GMV排名", "各渠道销售情况", "新老客对比"]
        return c

    def _clarify_card(self, trace_id: str) -> dict:
        c = self._base_card("clarify", trace_id, "想先确认一下时间范围")
        c["options"] = guards.CLARIFY_OPTIONS
        c["assume_note"] = "未指定时我会按 GMV 理解「销售额」"
        c["narrative"] = [
            "「销售额」我默认按 GMV(用户付款总金额)理解。",
            "请选择想看的时间范围,或直接告诉我。",
        ]
        c["followups"] = []
        return c

    def _error_card(self, trace_id: str, msg: str | None = None) -> dict:
        c = self._base_card("error", trace_id, "暂时无法回答这个问题")
        c["narrative"] = [
            msg or "这个问题我暂时无法理解,可能涉及还不支持的指标或范围。",
            "目前我可以回答交易域问题:GMV、订单数、支付人数、客单价、支付件数、退款金额、退款率、新老客对比等。",
        ]
        c["followups"] = ["最近30天GMV走势", "近30天退款率为什么上升", "最近30天各类目GMV"]
        return c

    # ================= 审计 =================

    def _audit(self, trace_id, session_id, demo_user, question, intent, ir, sql_text,
               row_count, card, narrative, llm_used, llm_model, t0, timings, status):
        try:
            from ..core.db import execute

            execute(
                "INSERT INTO audit_log (trace_id, ts, session_id, demo_user, question, intent,"
                " metric_key, ir_json, sql_text, row_count, result_json, narrative, llm_used,"
                " llm_model, latency_ms, stage_timings, status)"
                " VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    trace_id, session_id, demo_user, question, intent,
                    (ir or {}).get("metric"),
                    json.dumps(ir, ensure_ascii=False, default=str) if ir else None,
                    sql_text, int(row_count or 0),
                    json.dumps(card, ensure_ascii=False, default=str),
                    " ".join(narrative) if isinstance(narrative, list) else str(narrative or ""),
                    1 if llm_used else 0, llm_model, _ms(t0),
                    json.dumps(timings, ensure_ascii=False), status,
                ],
            )
        except Exception as e:
            logger.warning("audit write failed: %s", e)
