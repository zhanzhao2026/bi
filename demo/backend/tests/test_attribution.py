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


if __name__ == "__main__":
    unittest.main()
