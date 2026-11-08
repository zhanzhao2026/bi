# -*- coding: utf-8 -*-
"""时间表达式解析层测试(契约 §16.1,代理 P2-A)。

timelex:中文时间短语 → IR time 结构(preset / range / 「X比Y」pair 三者互斥)。
- 解析表 ≥20 例:5 个既有 preset、前天/本周/最近N天/N周、上/中/下旬(显式月、
  相对月、无月份=asof 所在月)、今年以来/年初至今、从X到Y、具体月份、
  相邻比较(本周比上周/下旬比上旬/最近7天比上一个7天/10月下旬比上旬 等);
- 回归铁律:既有时间问法(terms.yml time_words + 月份)解析结果逐字不变,
  经 rules.parse 断言与 test_terms_guards.TIME_CASES 同源期望一致;
- compiler 联动:time_label 旬标签、resolve_compare 显式对比窗口。
锚点:ASOF = 2026-11-06(tests._support,契约 §0)。
"""
import datetime as dt
import unittest

from tests._support import ASOF

OCT_RANGE = ["2026-10-01", "2026-10-31"]


def _tx(question, asof=ASOF):
    from app.engine import rules
    from app.semantic import timelex
    return timelex.parse(question, asof, rules.TIME_PATTERNS)


def _rules_parse(question, asof=ASOF):
    from app.engine import rules
    from app.semantic.loader import get_filter_options
    return rules.parse(question, get_filter_options(), asof)


def _pair(question, asof=ASOF):
    return _tx(question, asof)["pair"]


# -------------------------------------------------------------
# 解析表(≥20 例):问题 → 期望({"preset":...} / {"range": [...]} / pair 键)
# -------------------------------------------------------------
PARSE_TABLE = [
    # ---- 既有 5 preset(逐字沿用,顺序敏感)----
    ("昨天GMV是多少", {"preset": "yesterday"}),
    ("最近7天订单数", {"preset": "last_7d"}),
    ("近30天退款金额", {"preset": "last_30d"}),
    ("上个月支付人数", {"preset": "last_month"}),
    ("本月GMV", {"preset": "this_month"}),
    # 「上周」独立问法沿用 last_7d(行为不变),不用日历上周
    ("上周GMV", {"preset": "last_7d"}),
    # ---- 新增:相对日 / 最近N天 / N周 / 本周 ----
    ("前天GMV", {"range": ["2026-11-04", "2026-11-04"]}),
    ("最近3天GMV", {"range": ["2026-11-04", "2026-11-06"]}),
    ("近14天退款金额", {"range": ["2026-10-24", "2026-11-06"]}),
    ("最近2周GMV", {"range": ["2026-10-24", "2026-11-06"]}),
    ("本周GMV", {"range": ["2026-11-02", "2026-11-06"]}),   # 周一~data_as_of
    # ---- 新增:旬(1-10 / 11-20 / 21-月末)----
    ("10月上旬GMV", {"range": ["2026-10-01", "2026-10-10"]}),
    ("10月中旬退款金额", {"range": ["2026-10-11", "2026-10-20"]}),
    ("10月下旬GMV", {"range": ["2026-10-21", "2026-10-31"]}),
    ("2026年10月下旬GMV", {"range": ["2026-10-21", "2026-10-31"]}),
    ("下旬GMV", {"range": ["2026-11-21", "2026-11-30"]}),   # 无月份 → asof 所在月
    ("上旬销售额", {"range": ["2026-11-01", "2026-11-10"]}),
    ("中旬订单数", {"range": ["2026-11-11", "2026-11-20"]}),
    ("上月下旬退款率", {"range": ["2026-10-21", "2026-10-31"]}),  # 相对月用其月
    ("最近一期下旬GMV", {"range": ["2026-11-21", "2026-11-30"]}),
    # 「最近30天下旬」无显式月份 → asof 所在月(2026-11),不按 last_30d 平移
    ("最近30天下旬GMV", {"range": ["2026-11-21", "2026-11-30"]}),
    # ---- 新增:年累计 / 显式区间 / 具体月份 ----
    ("今年以来GMV", {"range": ["2026-01-01", "2026-11-06"]}),
    ("年初至今退款金额", {"range": ["2026-01-01", "2026-11-06"]}),
    ("从2026-10-01到2026-10-15的GMV", {"range": ["2026-10-01", "2026-10-15"]}),
    ("从10月1日到10月15日GMV", {"range": ["2026-10-01", "2026-10-15"]}),
    ("10月销售额", {"range": OCT_RANGE}),                    # 既有行为逐字不变
    ("2026年10月GMV", {"range": OCT_RANGE}),
    # ---- v16:裸 ISO 日期 / 「M月D日」单日闭区间 ----
    ("2026-11-05的GMV", {"range": ["2026-11-05", "2026-11-05"]}),
    ("2026/11/5的GMV", {"range": ["2026-11-05", "2026-11-05"]}),
    ("11月5日GMV", {"range": ["2026-11-05", "2026-11-05"]}),
    ("11月04日的退款金额", {"range": ["2026-11-04", "2026-11-04"]}),
]

# -------------------------------------------------------------
# 相邻比较(「X比Y」):主窗口 + 对比窗口
# -------------------------------------------------------------
PAIR_TABLE = [
    ("本周比上周GMV",
     {"main": ["2026-11-02", "2026-11-06"], "main_label": "本周",
      "compare": ["2026-10-26", "2026-11-01"], "compare_label": "上周"}),
    ("下旬比上旬退款率",
     {"main": ["2026-11-21", "2026-11-30"], "main_label": "下旬",
      "compare": ["2026-11-01", "2026-11-10"], "compare_label": "上旬"}),
    ("最近7天比上一个7天GMV",
     {"main": ["2026-10-31", "2026-11-06"], "main_label": "最近7天",
      "compare": ["2026-10-24", "2026-10-30"], "compare_label": "上一个7天"}),
    ("最近30天比上一个30天退款金额",
     {"main": ["2026-10-08", "2026-11-06"], "main_label": "最近30天",
      "compare": ["2026-09-08", "2026-10-07"], "compare_label": "上一个30天"}),
    ("本月比上月GMV",
     {"main": ["2026-11-01", "2026-11-06"], "main_label": "本月",
      "compare": OCT_RANGE, "compare_label": "上月"}),
    # 对比侧裸旬继承主窗口月份(10月)
    ("10月下旬比上旬的客单价",
     {"main": ["2026-10-21", "2026-10-31"], "main_label": "10月下旬",
      "compare": ["2026-10-01", "2026-10-10"], "compare_label": "上旬"}),
    # ---- v16:ISO 日期对比(紧邻比 / 「对比」连接 / 中隔词语)----
    ("2026-11-05比2026-11-04的GMV",
     {"main": ["2026-11-05", "2026-11-05"], "main_label": "2026-11-05",
      "compare": ["2026-11-04", "2026-11-04"], "compare_label": "2026-11-04"}),
    ("2026-11-05的gmv对比2026-11-04",
     {"main": ["2026-11-05", "2026-11-05"], "main_label": "2026-11-05",
      "compare": ["2026-11-04", "2026-11-04"], "compare_label": "2026-11-04"}),
    ("11月5日对比11月4日的订单数",
     {"main": ["2026-11-05", "2026-11-05"], "main_label": "11月5日",
      "compare": ["2026-11-04", "2026-11-04"], "compare_label": "11月4日"}),
]

# 不构成对比对的问法(右侧/左侧非时间短语 → 回退常规解析)
NO_PAIR_CASES = [
    "美妆最近30天的退款率比之前30天高了多少",   # golden Q18:右侧「之前30天」非时间短语
    "对比一下最近30天新客和老客的客单价",       # 「对比」不算相邻比较
    "整体比之前好多了",
]

# 既有问法回归:与 test_terms_guards.TIME_CASES 同源(逐字不变铁律)
LEGACY_RULES_CASES = [
    ("昨天GMV是多少", {"time_preset": "yesterday"}),
    ("最近7天订单数", {"time_preset": "last_7d"}),
    ("近30天退款金额", {"time_preset": "last_30d"}),
    ("上个月支付人数", {"time_preset": "last_month"}),
    ("本月GMV", {"time_preset": "this_month"}),
    ("10月销售额", {"time_range": OCT_RANGE}),
    ("2026年10月GMV", {"time_range": OCT_RANGE}),
]


class TestTimelexParseTable(unittest.TestCase):
    """解析表:preset / 相对日 / N天N周 / 本周 / 旬 / 年累计 / 区间 / 月份。"""

    def test_parse_table_at_least_20(self):
        self.assertGreaterEqual(len(PARSE_TABLE), 20, "契约 §16.4:解析表 ≥20 例")

    def test_no_time_word_returns_empty(self):
        self.assertEqual({"preset": None, "range": None, "pair": None}, _tx("美妆退款率怎么样"))
        self.assertEqual({"preset": None, "range": None, "pair": None}, _tx(""))


def _bind_parse_table():
    for i, (question, expect) in enumerate(PARSE_TABLE, 1):
        def make(q=question, e=expect):
            def test(self):
                got = _tx(q)
                if "preset" in e:
                    self.assertEqual(e["preset"], got["preset"], f"「{q}」preset 不符: {got}")
                    self.assertIsNone(got["range"], f"「{q}」不应产生 range")
                    self.assertIsNone(got["pair"], f"「{q}」不应产生 pair")
                else:
                    self.assertIsNone(got["preset"], f"「{q}」不应产生 preset")
                    self.assertIsNone(got["pair"], f"「{q}」不应产生 pair")
                    self.assertEqual(e["range"], got["range"], f"「{q}」range 不符: {got}")
            test.__doc__ = f"解析表「{q}」→ {e}"
            return test
        setattr(TestTimelexParseTable, f"test_case_{i:02d}", make())


_bind_parse_table()


class TestTimelexPairs(unittest.TestCase):
    """「X比Y」相邻比较:主窗口 + 对比窗口(等长平移或显式)。"""

    def test_pair_table_at_least_5(self):
        self.assertGreaterEqual(len(PAIR_TABLE), 5)

    def test_no_pair_fallback(self):
        for q in NO_PAIR_CASES:
            with self.subTest(q=q):
                self.assertIsNone(_tx(q)["pair"], f"「{q}」不应解析出对比对")


def _bind_pairs():
    for i, (question, expect) in enumerate(PAIR_TABLE, 1):
        def make(q=question, e=expect):
            def test(self):
                got = _pair(q)
                self.assertIsNotNone(got, f"「{q}」应解析出对比对")
                self.assertEqual(e["main"], got["main"], f"「{q}」主窗口不符: {got}")
                self.assertEqual(e["compare"], got["compare"], f"「{q}」对比窗口不符: {got}")
                self.assertEqual(e["main_label"], got["main_label"])
                self.assertEqual(e["compare_label"], got["compare_label"])
            test.__doc__ = f"对比对「{q}」→ {e['main_label']}({e['main'][0]}~{e['main'][1]}) vs {e['compare_label']}"
            return test
        setattr(TestTimelexPairs, f"test_pair_{i:02d}", make())


_bind_pairs()


class TestLegacyTimeBehavior(unittest.TestCase):
    """既有时间问法经 rules.parse 结果逐字不变(回归铁律,§16.1)。"""

    def test_golden_q18_not_a_pair(self):
        """golden Q18「退款率比之前30天」:不构成时间对比对,仍走 attribution。"""
        p = _rules_parse("美妆最近30天的退款率比之前30天高了多少")
        self.assertTrue(p.get("ok"))
        self.assertIsNone(p.get("time_pair"))
        self.assertEqual("last_30d", p.get("time_preset"))
        self.assertEqual("attribution", p.get("card"))

    def test_month_with_xun_priority(self):
        """「10月下旬」按旬解析(新能力);「10月销售额」仍整月(既有行为不变)。"""
        self.assertEqual(["2026-10-21", "2026-10-31"], _tx("10月下旬GMV")["range"])
        p = _rules_parse("10月销售额")
        self.assertEqual(OCT_RANGE, p.get("time_range"))
        self.assertIsNone(p.get("time_preset"))


def _bind_legacy():
    for i, (question, expect) in enumerate(LEGACY_RULES_CASES, 1):
        def make(q=question, e=expect):
            def test(self):
                p = _rules_parse(q)
                self.assertTrue(p.get("ok"), f"「{q}」应命中意图")
                if "time_preset" in e:
                    self.assertEqual(e["time_preset"], p.get("time_preset"))
                    self.assertIsNone(p.get("time_range"))
                else:
                    self.assertEqual(e["time_range"], p.get("time_range"))
                    self.assertIsNone(p.get("time_preset"))
            test.__doc__ = f"回归「{q}」→ {e}"
            return test
        setattr(TestLegacyTimeBehavior, f"test_legacy_{i:02d}", make())


_bind_legacy()


class TestRulesPairIntegration(unittest.TestCase):
    """「X比Y」经 rules.parse → time_range(主窗口)+ time_pair(对比窗口)。"""

    def test_xun_compare_question(self):
        """curl 回归问法:主指标照常解析,compare 用两个 range,叙事注明对比区间。"""
        p = _rules_parse("10月下旬数码的客单价比上旬低了多少")
        self.assertTrue(p.get("ok"))
        self.assertEqual("arppu", p.get("metric"))
        # 对比问法:主窗口在 time_pair.main(time_range 留空,pair 优先)
        self.assertIsNone(p.get("time_preset"))
        pair = p.get("time_pair") or {}
        self.assertEqual(["2026-10-21", "2026-10-31"], pair.get("main"))
        self.assertEqual(["2026-10-01", "2026-10-10"], pair.get("compare"))
        self.assertEqual("上旬", pair.get("compare_label"))
        self.assertEqual(["数码"], (p.get("filters") or {}).get("category"))
        self.assertTrue(p.get("time_explicit"))


class TestCompilerTimeGlue(unittest.TestCase):
    """compiler 联动:time_label 旬标签、resolve_compare 显式对比窗口。"""

    def test_time_label_xun(self):
        from app.semantic import compiler
        self.assertEqual("2026年10月下旬",
                         compiler.time_label({"range": ["2026-10-21", "2026-10-31"]}))
        self.assertEqual("2026年10月上旬",
                         compiler.time_label({"range": ["2026-10-01", "2026-10-10"]}))
        self.assertEqual("2026年10月中旬",
                         compiler.time_label({"range": ["2026-10-11", "2026-10-20"]}))
        # 整月标签不受影响
        self.assertEqual("2026年10月", compiler.time_label({"range": OCT_RANGE}))

    def test_resolve_compare_range(self):
        from app.semantic import compiler
        ir = {"compare": {"type": "range", "range": ["2026-10-01", "2026-10-10"],
                          "label": "上旬"}}
        self.assertEqual(("2026-10-01", "2026-10-10"),
                         compiler.resolve_compare(ir, "2026-10-21", "2026-10-31"))
        # prev_period 平移不变(31 天窗口向前平移 31 天)
        self.assertEqual(("2026-08-31", "2026-09-30"),
                         compiler.resolve_compare({"compare": "prev_period"},
                                                  "2026-10-01", "2026-10-31"))
        self.assertIsNone(compiler.resolve_compare({"compare": "none"}, "2026-10-01", "2026-10-31"))

    def test_validate_ir_accepts_range_compare(self):
        from app.semantic import compiler
        from app.semantic.loader import get_filter_options, get_semantic
        sem = get_semantic()
        ir = compiler.normalize_ir({
            "metric": "arppu", "time": {"range": ["2026-10-21", "2026-10-31"]},
            "filters": {"category": ["数码"]},
            "compare": {"type": "range", "range": ["2026-10-01", "2026-10-10"], "label": "上旬"},
        })
        ok, err = compiler.validate_ir(ir, sem, get_filter_options())
        self.assertTrue(ok, f"显式对比窗口 IR 应通过校验: {err}")
        bad = dict(ir)
        bad["compare"] = {"type": "range", "range": ["2026-10-10", "2026-10-01"]}
        ok, err = compiler.validate_ir(bad, sem, get_filter_options())
        self.assertFalse(ok, "compare.range 起止颠倒应拒绝")

    def test_validate_ir_rejects_bad_time_range(self):
        """LLM 防线:time.range 起止颠倒/非法日期/长度不对 → 拒绝(回退规则 IR),
        而不是到 resolve_time/编译期才抛异常(v15 eval LLM 模式实测暴露)。"""
        from app.semantic import compiler
        from app.semantic.loader import get_filter_options, get_semantic
        sem = get_semantic()

        def check(time_spec):
            ir = compiler.normalize_ir({"metric": "gmv", "time": time_spec})
            ok, err = compiler.validate_ir(ir, sem, get_filter_options())
            self.assertFalse(ok, f"{time_spec} 应被拒绝")
            return err

        self.assertIn("起止颠倒", check({"range": ["2026-11-06", "2026-10-08"]}))
        self.assertIn("日期不合法", check({"range": ["2026-13-01", "2026-12-31"]}))
        self.assertIn("应为 [起,止]", check({"range": ["2026-10-01"]}))
        # 合法 range 不受影响
        ir = compiler.normalize_ir({"metric": "gmv",
                                    "time": {"range": ["2026-10-08", "2026-11-06"]}})
        ok, err = compiler.validate_ir(ir, sem, get_filter_options())
        self.assertTrue(ok, f"正常 range 应通过: {err}")


class TestCoverageGuard(unittest.TestCase):
    """§17 覆盖窗口防线(v17):窗口起止两侧对称——整体越界 → 确定性拒答
    (理由引用真实覆盖窗口,不假手 LLM 措辞),部分越界 → 截断+注明(数字不变);
    compare 显式窗口同理(整体越界丢弃/部分越界截断)。"""

    def _g(self, ir, asof=ASOF, start="2026-08-08"):
        from app.engine.orchestrator import Orchestrator
        return Orchestrator._guard_time_window(ir, asof, start)

    def test_future_entire_after_refuses(self):
        """v16 行为回归锁:整个窗口晚于水位 → 拒答,理由含水位事实。"""
        ir = {"metric": "gmv", "time": {"range": ["2026-11-10", "2026-11-15"]}}
        ok, _ir2, reason, notes = self._g(ir)
        self.assertFalse(ok)
        self.assertIn("数据还没有发生", reason or "")
        self.assertIn("2026-11-06", reason or "")
        self.assertEqual([], notes)

    def test_past_entire_before_refuses(self):
        """新增:整个窗口早于数据起始 → 拒答,理由引用真实覆盖窗口
        (此前 LLM 自己写原因,误表述为「超出数据截至日」)。"""
        ir = {"metric": "gmv", "time": {"range": ["2025-01-01", "2025-12-31"]}}
        ok, _ir2, reason, notes = self._g(ir)
        self.assertFalse(ok)
        self.assertIn("数据仅覆盖 2026-08-08~2026-11-06", reason or "")
        self.assertIn("早于数据起始日", reason or "")
        self.assertEqual([], notes)

    def test_ytd_partial_past_clipped_with_note(self):
        """「今年以来」(2026-01-01 起)→ 截断到数据起始 2026-08-08 + 注明,
        不再无声拿 90 天数据冒充全年口径。"""
        ir = {"metric": "gmv", "time": {"range": ["2026-01-01", "2026-11-06"]}}
        ok, ir2, reason, notes = self._g(ir)
        self.assertTrue(ok, reason)
        self.assertEqual(["2026-08-08", "2026-11-06"], ir2["time"]["range"])
        self.assertEqual(1, len(notes))
        self.assertIn("已按 2026-08-08~2026-11-06 统计", notes[0])

    def test_partial_future_clipped_with_note(self):
        ir = {"metric": "gmv", "time": {"range": ["2026-11-01", "2026-11-11"]}}
        ok, ir2, _reason, notes = self._g(ir)
        self.assertTrue(ok)
        self.assertEqual(["2026-11-01", "2026-11-06"], ir2["time"]["range"])
        self.assertIn("已按 2026-11-01~2026-11-06 统计", notes[0])

    def test_both_sides_clipped(self):
        ir = {"metric": "gmv", "time": {"range": ["2025-01-01", "2027-01-01"]}}
        ok, ir2, _reason, notes = self._g(ir)
        self.assertTrue(ok)
        self.assertEqual(["2026-08-08", "2026-11-06"], ir2["time"]["range"])
        self.assertEqual(1, len(notes))

    def test_compare_entire_before_dropped_with_note(self):
        ir = {"metric": "gmv", "time": {"preset": "last_30d"},
              "compare": {"type": "range", "range": ["2025-11-01", "2025-12-01"],
                          "label": "去年同期"}}
        ok, ir2, _reason, notes = self._g(ir)
        self.assertTrue(ok)
        self.assertEqual("none", ir2["compare"])
        self.assertEqual(1, len(notes))
        self.assertIn("省略对比", notes[0])

    def test_compare_partial_clipped_with_note(self):
        ir = {"metric": "gmv", "time": {"preset": "last_30d"},
              "compare": {"type": "range", "range": ["2026-07-01", "2026-09-01"]}}
        ok, ir2, _reason, notes = self._g(ir)
        self.assertTrue(ok)
        self.assertEqual(["2026-08-08", "2026-09-01"], ir2["compare"]["range"])
        self.assertIn("对比窗口已按 2026-08-08~2026-09-01 截断统计", notes[0])

    def test_preset_and_in_window_untouched(self):
        """preset 窗口与覆盖窗口内的显式 range 零变化、零 note(既有行为锁)。"""
        for t in ({"preset": "last_30d"}, {"range": ["2026-09-01", "2026-10-01"]}):
            ir = {"metric": "gmv", "time": dict(t)}
            ok, ir2, _reason, notes = self._g(ir)
            self.assertTrue(ok)
            self.assertEqual(ir["time"], ir2["time"])
            self.assertEqual([], notes)

    def test_no_data_start_skips_past_side(self):
        """data_start 不可用(DB 异常回退)→ 起始侧检查跳过,未来侧照常。"""
        ir = {"metric": "gmv", "time": {"range": ["2025-01-01", "2025-12-31"]}}
        ok, ir2, _reason, notes = self._g(ir, start=None)
        self.assertTrue(ok)
        self.assertEqual(ir["time"], ir2["time"])
        self.assertEqual([], notes)
        ir_f = {"metric": "gmv", "time": {"range": ["2026-11-10", "2026-11-15"]}}
        ok, _ir2, reason, _notes = self._g(ir_f, start=None)
        self.assertFalse(ok)
        self.assertIn("数据还没有发生", reason or "")


if __name__ == "__main__":
    unittest.main()
