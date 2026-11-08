# -*- coding: utf-8 -*-
"""归因测试(契约 §10 归因卡 + docs/04 M5 泛化,语义层 v15)。

1. 规格一致性:ATTR_SPECS 的 num/den/expr 与 semantic/metrics/*.yml 展开后的
   agg 逐字对应(归因口径 = 语义层口径,防止两处漂移);
   去重口径指标(arppu/pay_user_cnt)不纳入归因(分母跨维度不可加,边界锁定)。
2. 数学性质(对拍真实库):
   - 比率型精确可加:Σ贡献 ≡ 整体 Δ(容差 1e-9);
   - refund_rate 与 v1 手写公式(rate_i×share_i)逐位一致(向后兼容铁律);
   - 可加型:Σ(类目差) ≡ 整体差,数值 = 手写 SUM 聚合;
   - 瀑布项阈值:|value| ≥ wmin;筛选传递:美妆 scoped 下整体变化即该类目变化。
3. 答案卡 E2E:可加归因卡(退款金额,元瀑布)/新客占比归因(忽略单客群筛选)/
   M4 变化排名卡(变化额=手写 MIN)/compare 指标感知/「为什么」追问升级归因。
"""
import datetime as dt
import unittest
import uuid

from tests._support import (
    EXPECTED_AGG,
    data_as_of_handwritten,
    exec_sql,
    scalar,
)

TOL = 1e-6
IDENTITY_TOL = 1e-9

RATIO_KEYS = ("refund_rate", "refund_order_rate", "new_order_ratio")
ADD_KEYS = ("gmv", "refund_amt", "order_cnt", "item_cnt", "refund_order_cnt", "new_order_cnt")


def _attr():
    from app.engine import attribution
    return attribution


def _sem():
    from app.semantic.loader import get_semantic
    return get_semantic()


def _windows():
    """(last30 起,止 / prior30 起,止),由手写水位独立推导。"""
    asof = data_as_of_handwritten()
    if isinstance(asof, str):
        asof = dt.date.fromisoformat(asof)
    l1 = asof.isoformat()
    l0 = (asof - dt.timedelta(days=29)).isoformat()
    p1 = (asof - dt.timedelta(days=30)).isoformat()
    p0 = (asof - dt.timedelta(days=59)).isoformat()
    return l0, l1, p0, p1


def _sq(s):
    import re
    return re.sub(r"\s+", "", str(s or ""))


# ------------------------------------------------- 规格一致性 / 边界锁定

class TestAttrSpecs(unittest.TestCase):
    """ATTR_SPECS 与语义层 agg 同源;去重口径指标不可归因。"""

    def test_ratio_spec_matches_semantic_agg(self):
        a = _attr()
        sem = _sem()
        for key in RATIO_KEYS:
            spec = a.ATTR_SPECS[key]
            agg = _sq(sem.metric(key)["agg"])
            self.assertIn(_sq(spec["num"]), agg, f"{key}.num 应出现在语义层 agg 中")
            self.assertIn(_sq(spec["den"]), agg, f"{key}.den 应出现在语义层 agg 中")
            self.assertTrue(sem.is_ratio(key), f"{key} 应为语义层比率指标")

    def test_add_spec_matches_semantic_agg(self):
        a = _attr()
        sem = _sem()
        for key in ADD_KEYS:
            spec = a.ATTR_SPECS[key]
            self.assertEqual(_sq(EXPECTED_AGG[key]), _sq(spec["expr"]),
                             f"{key}.expr 应与语义层 agg 一致")

    def test_dedup_metrics_excluded(self):
        """arppu/pay_user_cnt 分母为去重人数(跨维度不可加),不得进入归因。"""
        a = _attr()
        self.assertNotIn("arppu", a.ATTR_KEYS)
        self.assertNotIn("pay_user_cnt", a.ATTR_KEYS)
        with self.assertRaises(ValueError):
            a.compute("2026-10-08", "2026-11-06", "2026-09-08", "2026-10-07",
                      metric_key="arppu")


# ------------------------------------------------- 数学性质(真实库对拍)

class TestRatioDecomposition(unittest.TestCase):
    """比率型:Σ贡献 ≡ 整体 Δ;refund_rate 与 v1 手写公式逐位一致。"""

    def test_ratio_identity_three_metrics(self):
        a = _attr()
        l0, l1, p0, p1 = _windows()
        for key in RATIO_KEYS:
            attr = a.compute(l0, l1, p0, p1, metric_key=key)
            total = sum(it["contrib"] for it in attr["items"])
            self.assertAlmostEqual(total, attr["delta"], delta=IDENTITY_TOL,
                                   msg=f"{key}: Σ贡献应精确等于整体变化")
            # 瀑布项阈值与排序:|value| ≥ wmin 且按 |贡献| 降序
            for it in attr["waterfall_items"]:
                self.assertGreaterEqual(abs(it["value"]), a.RATIO_WMIN, f"{key}: 瀑布阈值")
            vals = [abs(it["value"]) for it in attr["waterfall_items"]]
            self.assertEqual(vals, sorted(vals, reverse=True), f"{key}: 瀑布按|贡献|降序")

    def test_refund_rate_backward_compat_v1(self):
        """refund_rate 归因数值与 v1 公式逐位一致(v1:_overall/_by_category 手写)。"""
        a = _attr()
        l0, l1, p0, p1 = _windows()
        attr = a.compute(l0, l1, p0, p1, metric_key="refund_rate")

        def win(start, end):
            row = exec_sql(
                "SELECT SUM(f.pay_amount) AS gmv, SUM(f.refund_amount) AS ref "
                "FROM fact_order f WHERE f.pay_date BETWEEN %s AND %s", [start, end])[0]
            return float(row["gmv"] or 0), float(row["ref"] or 0)

        def by_cat(start, end):
            rows = exec_sql(
                "SELECT c.category_name AS name, SUM(f.pay_amount) AS gmv, "
                "SUM(f.refund_amount) AS ref FROM fact_order f "
                "JOIN dim_category c ON c.category_id=f.category_id "
                "WHERE f.pay_date BETWEEN %s AND %s GROUP BY c.category_name",
                [start, end])
            return {r["name"]: (float(r["gmv"] or 0), float(r["ref"] or 0)) for r in rows}

        gmv_n, ref_n = win(l0, l1)
        gmv_o, ref_o = win(p0, p1)
        rate_new, rate_old = ref_n / gmv_n * 100, ref_o / gmv_o * 100
        self.assertAlmostEqual(rate_new, attr["rate_new"], delta=TOL)
        self.assertAlmostEqual(rate_old, attr["rate_old"], delta=TOL)
        cats_n, cats_o = by_cat(l0, l1), by_cat(p0, p1)
        v1 = {}
        for name in set(cats_n) | set(cats_o):
            g_i_n, r_i_n = cats_n.get(name, (0.0, 0.0))
            g_i_o, r_i_o = cats_o.get(name, (0.0, 0.0))
            ri_n = r_i_n / g_i_n * 100 if g_i_n else 0.0
            ri_o = r_i_o / g_i_o * 100 if g_i_o else 0.0
            v1[name] = ri_n * (g_i_n / gmv_n) - ri_o * (g_i_o / gmv_o)
        got = {it["name"]: it["contrib"] for it in attr["items"]}
        self.assertEqual(sorted(v1), sorted(got))
        for name, c in v1.items():
            self.assertAlmostEqual(c, got[name], delta=IDENTITY_TOL,
                                   msg=f"refund_rate 贡献[{name}] 与 v1 公式不一致")

    def test_scoped_attribution_single_category(self):
        """美妆筛选:scoped 命中、整体变化即该类目两期率差。"""
        a = _attr()
        l0, l1, p0, p1 = _windows()
        attr = a.compute(l0, l1, p0, p1, filters={"category": ["美妆"]},
                         metric_key="refund_rate")
        self.assertEqual(["美妆"], attr["scoped"])
        self.assertEqual(1, len(attr["items"]))
        row = exec_sql(
            "SELECT SUM(CASE WHEN f.pay_date BETWEEN %s AND %s THEN f.refund_amount ELSE 0 END) "
            "/NULLIF(SUM(CASE WHEN f.pay_date BETWEEN %s AND %s THEN f.pay_amount ELSE 0 END),0) "
            "*100 - SUM(CASE WHEN f.pay_date BETWEEN %s AND %s THEN f.refund_amount ELSE 0 END) "
            "/NULLIF(SUM(CASE WHEN f.pay_date BETWEEN %s AND %s THEN f.pay_amount ELSE 0 END),0)*100 "
            "AS d FROM fact_order f JOIN dim_category c ON c.category_id=f.category_id "
            "WHERE c.category_name='美妆' AND (f.pay_date BETWEEN %s AND %s "
            "OR f.pay_date BETWEEN %s AND %s)",
            [l0, l1, l0, l1, p0, p1, p0, p1, l0, l1, p0, p1])[0]
        self.assertAlmostEqual(float(row["d"]), attr["delta"], delta=1e-4)


class TestAdditiveDecomposition(unittest.TestCase):
    """可加型:Σ(类目差) ≡ 整体差;数值 = 手写 SUM 聚合。"""

    def test_add_identity_and_values(self):
        a = _attr()
        l0, l1, p0, p1 = _windows()
        for key in ("gmv", "refund_amt", "order_cnt", "item_cnt"):
            attr = a.compute(l0, l1, p0, p1, metric_key=key)
            total = sum(it["contrib"] for it in attr["items"])
            self.assertAlmostEqual(total, attr["delta"], delta=1e-6,
                                   msg=f"{key}: Σ类目差应等于整体差")
            self.assertFalse(attr["has_residual"], f"{key}: 精确可加,残差应为 0")
            sql, params = EXPECTED_AGG[key], [l0, l1]
            want = scalar(f"SELECT {sql} AS v FROM fact_order f "
                          f"WHERE f.pay_date BETWEEN %s AND %s", params)
            self.assertAlmostEqual(float(want), attr["rate_new"], delta=TOL,
                                   msg=f"{key}: 本期值应等于手写 SUM 聚合")


# ------------------------------------------------- 答案卡 E2E(v15 新卡)

class TestAnalysisCardsE2E(unittest.TestCase):
    """可加归因卡 / 新客占比归因 / M4 变化排名 / compare 指标感知 / 追问升级。"""

    @classmethod
    def setUpClass(cls):
        from app.engine.orchestrator import Orchestrator
        cls.orch = Orchestrator()
        # 本类断言的是规则语义(LLM 模式的端到端行为由场景矩阵守门);
        # 禁用网关保证测试确定性,不随 sys_settings 是否配置 token 波动。
        cls.orch.gateway.enabled = lambda: False
        cls.l0, cls.l1, cls.p0, cls.p1 = _windows()

    def test_refund_amt_attribution_card(self):
        """退款金额+为什么 → 可加归因卡:元瀑布、KPI 标签、本期值=手写 SUM。"""
        card, raw, done = self.orch.run_once("最近30天退款金额为什么上升", "ceo")
        self.assertEqual("attribution", card.get("type"))
        self.assertIn("退款金额", card.get("title") or "")
        self.assertEqual("退款金额", (card.get("kpis") or [{}])[0].get("label"))
        chart = card.get("chart") or {}
        self.assertEqual("waterfall", chart.get("kind"))
        self.assertIn("(元)", chart.get("title") or "")
        self.assertEqual("ok", done.get("status"))
        want = scalar(f"SELECT {EXPECTED_AGG['refund_amt']} AS v FROM fact_order f "
                      f"WHERE f.pay_date BETWEEN '{self.l0}' AND '{self.l1}'")
        self.assertAlmostEqual(float(want), float(raw.get("value")), delta=1e-4)

    def test_new_ratio_attribution_card(self):
        """新客占比+为什么 → 归因卡;「新客」筛选被剥离,数值=整体新客占比。"""
        card, raw, done = self.orch.run_once("最近30天新客占比为什么变化", "ceo")
        self.assertEqual("attribution", card.get("type"))
        self.assertIn("新客订单占比", card.get("title") or "")
        self.assertTrue(any("已忽略单独的客群筛选" in s
                            for s in (card.get("narrative") or []) or [""]),
                        "叙事应注明忽略单客群筛选")
        want = scalar(f"SELECT {EXPECTED_AGG['new_order_ratio']}*100 AS v FROM fact_order f "
                      f"WHERE f.pay_date BETWEEN '{self.l0}' AND '{self.l1}'")
        self.assertAlmostEqual(float(want), float(raw.get("value")), delta=1e-4)
        self.assertNotAlmostEqual(100.0, float(raw.get("value")), delta=1.0,
                                  msg="若被「新客」筛选污染,占比会恒为 100%")
        self.assertEqual("ok", done.get("status"))

    def test_gmv_delta_rank_card(self):
        """「哪些类目GMV下降最明显」→ M4 变化排名卡:变化额=手写 MIN(类目差)。"""
        card, raw, done = self.orch.run_once("最近30天哪些类目GMV下降最明显", "ceo")
        self.assertEqual("rank", card.get("type"))
        self.assertIn("变化", card.get("title") or "")
        tbl = card.get("table") or {}
        keys = [c["key"] for c in tbl.get("columns") or []]
        for k in ("val_old", "val_new", "delta", "share"):
            self.assertIn(k, keys, f"变化排名卡表格应含 {k} 列")
        want = scalar(
            "SELECT MIN(d) AS v FROM ("
            " SELECT SUM(CASE WHEN f.pay_date BETWEEN %s AND %s THEN f.pay_amount ELSE 0 END)"
            " - SUM(CASE WHEN f.pay_date BETWEEN %s AND %s THEN f.pay_amount ELSE 0 END) AS d"
            " FROM fact_order f GROUP BY f.category_id) t",
            [self.l0, self.l1, self.p0, self.p1])
        self.assertAlmostEqual(float(want), float(raw.get("value")), delta=1e-4,
                               msg="KPI 变化额应为各类目两期差的最小值(降幅最大)")
        self.assertEqual("ok", done.get("status"))

    def test_compare_with_refund_rate_focus(self):
        """「新老客的退款率对比」→ compare 卡追加退款率序列,数值=手写分组比率。"""
        card, raw, done = self.orch.run_once("最近30天新老客的退款率对比", "ceo")
        self.assertEqual("compare", card.get("type"))
        chart = card.get("chart") or {}
        names = [s.get("name") for s in chart.get("series") or []]
        self.assertIn("退款率", names, "对比卡应追加退款率分组序列")
        tbl = card.get("table") or {}
        labels = [c["label"] for c in tbl.get("columns") or []]
        self.assertIn("退款率", labels)
        self.assertIn("refund_rate", raw.get("metrics") or [])
        got = {r["name"]: float(r["value"]) for r in (raw.get("rows") or [])
               if isinstance(r.get("value"), (int, float))}
        want_new = scalar(f"SELECT {EXPECTED_AGG['refund_rate']}*100 AS v FROM fact_order f "
                          f"WHERE f.pay_date BETWEEN '{self.l0}' AND '{self.l1}' "
                          f"AND f.user_type='新客'")
        self.assertAlmostEqual(float(want_new), got.get("新客退款率"), delta=1e-4)
        self.assertEqual("ok", done.get("status"))

    def test_why_followup_upgrades_to_attribution(self):
        """「那为什么变化了呢」继承上一轮指标/筛选并升级为归因卡(doc 04 §5)。"""
        sid = f"t-{uuid.uuid4().hex[:8]}"
        c1, _r1, d1 = self.orch.run_once("最近30天美妆的GMV是多少", "ceo", session_id=sid)
        self.assertEqual("summary", c1.get("type"))
        self.assertEqual("ok", d1.get("status"))
        c2, _r2, d2 = self.orch.run_once("那为什么变化了呢", "ceo", session_id=sid)
        self.assertEqual("attribution", c2.get("type"),
                         "为什么追问应升级为归因卡")
        self.assertIn("GMV", c2.get("title") or "")
        self.assertTrue(any("美妆" in s for s in (c2.get("narrative") or [])),
                        "归因叙事应沿用上一轮的美妆筛选")
        chart = c2.get("chart") or {}
        self.assertEqual("waterfall", chart.get("kind"))
        self.assertIn("(元)", chart.get("title") or "")
        self.assertEqual("ok", d2.get("status"))


class TestMetaEscapeE2E(unittest.TestCase):
    """逃生口3(§17):LLM 判定问候/身份类 → 确定性自我介绍卡(不依赖具体问法词表)。"""

    def test_meta_escape_card(self):
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {"meta": True}
        card, _raw, done = orch.run_once("你是谁", "ceo")
        self.assertEqual("summary", card.get("type"))
        self.assertIn("经营分析助手", card.get("title") or "")
        self.assertTrue(card.get("narrative"))
        self.assertEqual("ok", done.get("status"))
        # 自我介绍卡不含任何数据元素(kpis 空、无 chart/table),followups 为示例问法
        self.assertEqual([], card.get("kpis"))
        self.assertIsNone(card.get("chart"))
        self.assertIsNone(card.get("table"))
        self.assertEqual(3, len(card.get("followups") or []))
        # 审计 intent 落卡型 summary(曾写死 "error" → 审计里 状态ok/指标error 假不一致)
        row = exec_sql("SELECT intent, status FROM audit_log WHERE trace_id = %s",
                       [card.get("trace_id")])
        self.assertTrue(row, "审计应写入该 trace")
        self.assertEqual("summary", row[0]["intent"], "meta 卡审计 intent 应为卡型 summary")
        self.assertEqual("ok", row[0]["status"])

    def test_clarify_card_template_scoping(self):
        """「销售额→GMV」假设话术只属于守卫 vague 路径;LLM 澄清逃生为中性话术。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        neutral = orch._clarify_card("tr_test")
        self.assertNotIn("销售额", " ".join(neutral.get("narrative") or []),
                         "中性澄清卡不应出现「销售额」假设")
        self.assertIsNone(neutral.get("assume_note"))
        vague = orch._clarify_card("tr_test", vague_word="业绩")
        self.assertIn("业绩", " ".join(vague.get("narrative") or []))
        self.assertIn("业绩", vague.get("assume_note") or "")

    def test_bare_why_with_prev_ir_inherits(self):
        """裸「为什么」+ 有上一轮数据问题 → 确定性继承并升级归因
        (即使 LLM 对追问误判 clarify,也不放走去出时间选项)。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        sid = f"t-{uuid.uuid4().hex[:8]}"
        orch.gateway.enabled = lambda: True
        orch.gateway.narrate = lambda payload: None   # 叙述留规则模式,保持测试封闭
        calls = {"n": 0}

        def plan_ir(question, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"metric": "gmv", "time": {"preset": "last_30d"}, "dims": [],
                        "filters": {"category": ["美妆"], "channel": [], "user_type": []},
                        "granularity": "none", "compare": "none", "order": "desc",
                        "limit": 10}
            return {"clarify": True}   # 模拟 LLM 对裸追问误判 clarify

        orch.gateway.plan_ir = plan_ir
        c1, _r1, d1 = orch.run_once("最近30天美妆的GMV是多少", "ceo", session_id=sid)
        self.assertEqual("summary", c1.get("type"))
        self.assertEqual("ok", d1.get("status"))
        c2, _r2, d2 = orch.run_once("为什么", "ceo", session_id=sid)
        self.assertEqual("attribution", c2.get("type"), "裸「为什么」应继承上一轮升级归因")
        self.assertTrue(any("美妆" in s for s in (c2.get("narrative") or [])))
        self.assertEqual("ok", d2.get("status"))

    def test_bare_why_without_context_refuses(self):
        """裸「为什么」+ 无任何可继承上下文(LLM 误判 clarify)→ 诚实拒答,
        不出「选时间范围」的澄清卡——没有分析对象,选时间也答不了。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {"clarify": True}
        card, _raw, done = orch.run_once("为什么", "ceo")
        self.assertEqual("error", card.get("type"))
        self.assertIn("缺少分析对象", " ".join(card.get("narrative") or []))
        self.assertEqual("error", done.get("status"))


class TestCoverageWindowE2E(unittest.TestCase):
    """§17 覆盖窗口防线(v17)E2E:YTD 部分越界 → 截断+注明(规则路径);
    整体早于数据起始 → 确定性拒答(理由引用真实覆盖窗口);meta 介绍卡含
    数据覆盖行(「能查到什么范围」类能力询问的确定性应答)。"""

    @classmethod
    def setUpClass(cls):
        from app.engine.orchestrator import Orchestrator
        cls.orch = Orchestrator()
        cls.orch.gateway.enabled = lambda: False

    def test_ytd_clipped_and_noted(self):
        """「今年以来GMV」→ 数字不变(数据起始前无行),注明实际覆盖窗口;
        LLM 违规附加的 prev_period(上一期必然为空)被确定性省略,
        不再出「较上一期无数据」噪声。"""
        from app.semantic.loader import get_data_as_of, get_data_start
        s, a = get_data_start(), get_data_as_of()
        card, _raw, done = self.orch.run_once("今年以来GMV是多少", "ceo")
        self.assertEqual("summary", card.get("type"))
        self.assertTrue(any(f"已按 {s}" in n for n in (card.get("narrative") or [])),
                        f"应含覆盖截断注明: {card.get('narrative')}")
        self.assertNotIn("较上一期", " ".join(card.get("narrative") or []),
                         "上一期窗口早于数据起始 → 对比应被省略,不出「较上一期无数据」")
        self.assertEqual("ok", done.get("status"))

    def test_llm_prev_period_out_of_coverage_dropped(self):
        """LLM IR 附加 prev_period(主窗 YTD → 上一期整体早于数据起始)→
        对比确定性省略 + 注明,不出「较上一期无数据」(live 实测暴露)。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {
            "metric": "gmv", "time": {"range": ["2026-01-01", "2026-11-06"]},
            "dims": [], "filters": {"category": [], "channel": [], "user_type": []},
            "granularity": "none", "compare": "prev_period", "order": "desc", "limit": 10}
        orch.gateway.narrate = lambda payload: None   # 叙述留规则模式,保持测试封闭
        card, _raw, done = orch.run_once("今年以来GMV是多少", "ceo")
        self.assertEqual("summary", card.get("type"))
        self.assertNotIn("较上一期", " ".join(card.get("narrative") or []))
        self.assertTrue(any("已省略对比" in n for n in (card.get("narrative") or [])),
                        f"应注明省略对比: {card.get('narrative')}")
        self.assertEqual("ok", done.get("status"))

    def test_pre_start_range_refused_with_factual_reason(self):
        """「从2025-01-01到2025-06-30」整体早于数据起始 → 确定性拒答,
        理由是系统的覆盖窗口事实,不是 LLM 编的「超出数据截至日」。"""
        from app.semantic.loader import get_data_as_of, get_data_start
        s, a = get_data_start(), get_data_as_of()
        card, _raw, done = self.orch.run_once("从2025-01-01到2025-06-30的GMV是多少", "ceo")
        self.assertEqual("error", card.get("type"))
        self.assertTrue((card.get("narrative") or [""])[0]
                        .startswith(f"数据仅覆盖 {s}~{a}"),
                        f"拒答理由应引用真实覆盖窗口: {card.get('narrative')}")
        self.assertEqual("error", done.get("status"))

    def test_meta_card_has_coverage_line(self):
        from app.semantic.loader import get_data_as_of, get_data_start
        s, a = get_data_start(), get_data_as_of()
        card = self.orch._meta_card("tr_cov")
        self.assertTrue(any(n.startswith(f"数据覆盖 {s} ~ {a}")
                            for n in (card.get("narrative") or [])),
                        f"meta 卡应含数据覆盖行: {card.get('narrative')}")

    def test_prompt_renders_coverage_and_meta_scope(self):
        """planner prompt:数据覆盖窗口上下文 + 能力/数据范围询问属于 meta。"""
        from app.llm.prompts import render_planner_system
        from app.semantic.loader import get_data_as_of, get_data_start
        s, a = get_data_start(), get_data_as_of()
        text = render_planner_system(_sem(), {})
        self.assertIn(f"数据覆盖 = {s} ~ {a}", text)
        self.assertIn("能查到什么范围", text)
        self.assertIn("不得编造与事实不符的说法", text)

    def test_narrator_prompt_forbids_unsourced_compare(self):
        """narrator 硬性要求:输入没有对比数据时禁止「较上一期/环比」表述
        (live 实测:普通汇总问数被叙述成「较上一期无数据」——编造不存在的事实)。"""
        from app.llm.prompts import render_narrator_system
        text = render_narrator_system(_sem())
        self.assertIn("较上一期", text)
        self.assertIn("编造", text)


class TestEscapeGuardsE2E(unittest.TestCase):
    """v17 逃生口守卫推广 + 跨筛选组确定性拒答:
    - 规则已解析出可回答问题(parsed.ok)→ LLM 的 unsupportable/clarify/meta
      一律不得覆盖(同问不同答的方差根除);
    - 同一筛选组 ≥2 取值 + 比较句式 → 双模式都确定性拒答(此前规则模式
      给出两类目合并求和的错误答案)。"""

    def test_cross_group_refuses_in_rules_mode(self):
        """U01 规则模式:不再给出「女装/数码」合并求和的错误答案(17.6万),
        确定性拒答,理由给分开问的指引。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: False
        card, _raw, done = orch.run_once("女装今年以来退款金额比数码多多少", "ceo")
        self.assertEqual("error", card.get("type"))
        self.assertIn("跨筛选组对比", (card.get("narrative") or [""])[0])
        self.assertEqual("error", done.get("status"))

    def test_llm_unsupportable_ignored_when_rules_ok(self):
        """规则窗口超出覆盖(答案已被防线确定性裁决)→ LLM unsupportable 被守卫
        忽略,按规则 IR 正常作答(T11 实测同问不同答的根除)。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {"unsupportable": "不支持今年以来的数据"}
        card, _raw, done = orch.run_once("今年以来GMV是多少", "ceo")
        self.assertEqual("summary", card.get("type"))
        self.assertEqual("ok", done.get("status"))

    def test_llm_unsupportable_wins_on_semantic_drift(self):
        """竞品/预测类:规则层只 superficial 命中指标词(无显式窗口),
        LLM 的 unsupportable 必须生效——答错比拒答严重(B03/U03 矩阵)。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {"unsupportable": "竞品数据不在本系统数据范围内"}
        card, _raw, done = orch.run_once("竞品A的GMV是多少?", "ceo")
        self.assertEqual("error", card.get("type"))
        self.assertIn("竞品", " ".join(card.get("narrative") or []))
        self.assertEqual("error", done.get("status"))

    def test_attribution_main_window_rules_owned(self):
        """两期分析主窗口归规则所有:LLM IR 改写主窗口被纠正(eval Q18 实测:
        「最近30天…比之前30天高了多少」主窗被 LLM 改成上期窗口,口径错位)。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {
            "metric": "refund_rate", "time": {"range": ["2026-10-01", "2026-10-07"]},
            "dims": [], "filters": {"category": [], "channel": [], "user_type": []},
            "granularity": "none", "compare": "prev_period", "order": "desc", "limit": 10}
        orch.gateway.narrate = lambda payload: None
        card, raw, done = orch.run_once("美妆最近30天的退款率比之前30天高了多少", "ceo")
        self.assertEqual("attribution", card.get("type"))
        self.assertTrue((card.get("title") or "").startswith("最近30天"),
                        f"主窗口应来自规则解析的最近30天: {card.get('title')}")
        self.assertTrue(any("美妆" in n for n in (card.get("narrative") or [])))
        self.assertEqual("ok", done.get("status"))

    def test_llm_meta_ignored_when_rules_ok(self):
        """「问候+数据意图」且规则可解析 → 按规则作答,不出自我介绍卡。"""
        from app.engine.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.gateway.enabled = lambda: True
        orch.gateway.plan_ir = lambda *a, **k: {"meta": True}
        orch.gateway.narrate = lambda payload: None
        card, _raw, done = orch.run_once("最近30天GMV是多少", "ceo")
        self.assertEqual("summary", card.get("type"))
        self.assertIn("GMV", card.get("title") or "")
        self.assertEqual("ok", done.get("status"))


if __name__ == "__main__":
    unittest.main()
