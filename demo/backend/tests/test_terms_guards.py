# -*- coding: utf-8 -*-
"""术语表与守卫回归测试(契约 §7/§9/§15.2)。

词表已从代码迁入 semantic/terms.yml(forbidden/boundary/clarify/synonyms/
time_words),本文件逐条走守卫与规则入口(guards.check / rules.parse),
断言意图与状态与现状一致(行为回归:现有问法命中不变)。

- forbidden(拒答):薪酬/PII 词 → refuse 守卫
- boundary(明细边界):明细/导出类词 → boundary 守卫
- clarify(澄清):模糊指标词且无时间词 → clarify;带时间词则不澄清、按 GMV 解析
- synonyms(同义词):口语词 → 指标/维度/筛值映射
- time_words:时间词 → preset / 具体月份 range
"""
import datetime as dt
import unittest

from tests._support import ASOF

OCT_RANGE = ["2026-10-01", "2026-10-31"]

# forbidden(拒答):问题 → 命中词(≥3 条,契约要求)
REFUSE_CASES = [
    ("员工平均工资是多少", "工资"),
    ("帮我查一下薪资构成", "薪资"),
    ("我的工资条在哪里", "工资"),   # 词表按序扫描:「工资」先于「工资条」命中
    ("发我一下员工身份证号", "身份证"),
    ("客户手机号是多少", "手机号"),
    ("从银行卡查一下流水", "银行卡"),   # 含 gmv 同义词「流水」,refuse 必须优先
    ("社保缴费金额是多少", "社保"),
    ("本月薪酬支出多少", "薪酬"),       # 带时间词也必须拒答
]

# boundary(明细边界):问题 → 命中词(≥3 条)
BOUNDARY_CASES = [
    ("看下具体订单明细", "明细"),
    ("帮我把数据导出", "导出"),
    ("给我看看订单列表", "订单列表"),
    ("查一下订单详情", "订单详情"),
    ("具体商品卖得怎么样", "具体商品"),  # 含 clarify 词「卖得怎么样」,boundary 必须优先
    ("哪款商品最好卖", "哪款商品"),
    ("哪个商品卖得最多", "哪个商品卖"),
    ("母婴哪款产品卖得好", "哪款产品"),
]

# clarify(澄清):模糊指标词 + 无时间词 → clarify 守卫
CLARIFY_CASES = [
    ("销售额怎么样", "销售额"),
    ("业绩如何", "业绩"),
    ("生意怎么样", "生意怎么样"),
    ("卖得怎么样", "卖得怎么样"),
    ("卖得如何", "卖得如何"),
    ("最近做得怎么样", "做得怎么样"),
]

# 带时间词的模糊问法:不澄清,直接按 GMV + 对应时间解析
CLARIFY_WITH_TIME_CASES = [
    ("近30天销售额", {"metric": "gmv", "time_preset": "last_30d"}),
    ("上月业绩", {"metric": "gmv", "time_preset": "last_month"}),
    ("昨天卖得怎么样", {"metric": "gmv", "time_preset": "yesterday"}),
    ("10月销售额", {"metric": "gmv", "time_range": OCT_RANGE}),
]

# synonyms:指标同义词(口语问法 → metric)
SYNONYM_METRIC_CASES = [
    ("营业额多少", "gmv"),
    ("成交额是多少", "gmv"),
    ("最近流水多少", "gmv"),
    ("美妆卖了多少", "gmv"),
    ("销量怎么样", "item_cnt"),
    ("支付件数是多少", "item_cnt"),
    ("卖了多少件", "item_cnt"),
    ("多少人来买", "pay_user_cnt"),
    ("最近7天下单人数", "pay_user_cnt"),
    ("退了多少", "refund_amt"),
    ("退款率怎么样", "refund_rate"),
    ("订单退款率是多少", "refund_order_rate"),
    ("新客占比", "new_order_ratio"),
    ("客单价是多少", "arppu"),
    ("最近30天单均多少", "arppu"),
    ("最近30天有多少单", "order_cnt"),
]

# 兜底不变式:模糊口语「卖了…」永远不得映射到 gmv 以外的指标
SALES_VAGUE_QUESTIONS = ["卖了多少", "卖了多少钱", "整体卖了多少"]

# synonyms:维度/筛值同义词(问题 → filters 中应命中的键值)
SYNONYM_FILTER_CASES = [
    ("直播的订单数", {"channel": ["直播"]}),
    ("私域GMV是多少", {"channel": ["私域"]}),
    ("自然流量渠道的GMV", {"channel": ["自然流量"]}),
    ("付费推广的成交额", {"channel": ["付费推广"]}),
    ("付费渠道卖了多少件", {"channel": ["付费推广"]}),   # 「付费」别名 → 付费推广
    ("只看美妆的GMV", {"category": ["美妆"]}),
    ("女装退款金额是多少", {"category": ["女装"]}),
    ("新客下了多少单", {"user_type": ["新客"]}),
    ("老客的客单价", {"user_type": ["老客"]}),
]

# time_words:时间词 → preset / 具体月份 range
TIME_CASES = [
    ("昨天GMV是多少", {"time_preset": "yesterday"}),
    ("最近7天订单数", {"time_preset": "last_7d"}),
    ("近30天退款金额", {"time_preset": "last_30d"}),
    ("上个月支付人数", {"time_preset": "last_month"}),
    ("本月GMV", {"time_preset": "this_month"}),
    ("10月销售额", {"time_range": OCT_RANGE}),
    ("2026年10月GMV", {"time_range": OCT_RANGE}),
]

# 意图构造(card)抽查:问法 → 卡类型/维度
CARD_CASES = [
    ("各渠道GMV排名", {"card": "rank", "dim": "channel"}),
    ("最近30天哪个类目GMV最高", {"card": "rank", "dim": "category"}),
    ("最近30天退款率为什么上升", {"card": "attribution", "metric": "refund_rate"}),
    # ---- v15 分析类扩容(§16.5):M4 变化排名 / M5 归因泛化 / 为什么追问 ----
    ("哪些类目GMV下降最明显", {"card": "rank", "metric": "gmv", "dim": "category",
                            "delta_dir": "down", "want_compare": True}),
    ("哪些渠道GMV上升最多", {"card": "rank", "metric": "gmv", "dim": "channel",
                          "delta_dir": "up", "want_compare": True}),
    ("最近30天哪些类目退款率变化最大", {"card": "rank", "metric": "refund_rate",
                                   "delta_dir": "abs", "want_compare": True}),
    ("最近30天退款金额为什么上升", {"card": "attribution", "metric": "refund_amt",
                                "why_attr": True}),
    ("最近30天新客占比为什么变化", {"card": "attribution", "metric": "new_order_ratio",
                                "why_attr": True}),
    ("最近30天订单退款率为什么上升", {"card": "attribution", "metric": "refund_order_rate"}),
    # 「新客」词仍会挂在规则层 filters;占比口径剥离由编排层 _answer_path
    # 统一执行并注明(test_attribution E2E 锁定)
    ("那为什么下降呢", {"card": "summary", "why_attr": True, "metric": None}),
    # 边界:去重口径指标不可归因(ATTR_SPECS 边界),回落 summary
    ("最近30天客单价为什么变化", {"card": "summary", "metric": "arppu"}),
    ("最近30天支付人数为什么减少", {"card": "summary", "metric": "pay_user_cnt"}),
    # compare 指标感知:指定指标保留为 compare_focus,卡型不变
    ("最近30天新老客的退款率对比", {"card": "compare", "compare_focus": "refund_rate"}),
    ("对比一下最近30天新客和老客的客单价", {"card": "compare"}),
    # 不可表达回归锁:跨类目对比仍由规则判 summary(LLM 模式由逃生口拒答,
    # 场景矩阵 U01);规则侧不得给出 attribution/rank,防止误答
    ("女装今年以来退款金额比数码多多少", {"card": "summary", "metric": "refund_amt"}),
    ("女装客单价比数码高多少", {"card": "summary", "metric": "arppu"}),
]


def _guards_check(question):
    from app.engine import guards
    return guards.check(question)


def _rules_parse(question):
    from app.engine import rules
    from app.semantic.loader import get_filter_options
    return rules.parse(question, get_filter_options(), ASOF)


def _bind_guard_cases(cls, cases, kind):
    for i, (question, word) in enumerate(cases, 1):
        def make(q=question, w=word):
            def test(self):
                got_kind, got_word = _guards_check(q)
                self.assertEqual(kind, got_kind,
                                 f"「{q}」守卫结果应为 {kind},实际 {got_kind}")
                self.assertEqual(w, got_word, f"「{q}」命中词应为 {w},实际 {got_word}")
            test.__doc__ = f"「{q}」→ {kind}({w})"
            return test
        setattr(cls, f"test_{kind}_{i:02d}", make())


def _bind_parse_cases(cls, cases, checker, tag):
    for i, (question, expect) in enumerate(cases, 1):
        def make(q=question, e=expect):
            def test(self):
                p = _rules_parse(q)
                checker(self, q, p, e)
            test.__doc__ = f"「{q}」→ {tag}"
            return test
        setattr(cls, f"test_{tag}_{i:02d}", make())


def _check_metric(self, q, p, expect_metric):
    self.assertTrue(p.get("ok"), f"「{q}」应命中意图,实际 {p}")
    self.assertEqual(expect_metric, p.get("metric"),
                     f"「{q}」应解析为指标 {expect_metric},实际 {p.get('metric')}")


def _check_sales_invariant(self, q, p, _e=None):
    # 兜底不变式:「卖了…」若命中意图必须是 gmv;
    # 当前现状是未命中(ok=False → error 兜底,绝不猜指标),两种状态都合法。
    if p.get("ok"):
        self.assertEqual("gmv", p.get("metric"),
                         f"「{q}」若命中意图必须是 gmv,实际 {p.get('metric')}")


def _check_filter(self, q, p, expect):
    self.assertTrue(p.get("ok"), f"「{q}」应命中意图,实际 {p}")
    for key, vals in expect.items():
        self.assertEqual(list(vals), list((p.get("filters") or {}).get(key) or []),
                         f"「{q}」的 {key} 筛选应为 {vals}")


def _check_time(self, q, p, expect):
    self.assertTrue(p.get("ok"), f"「{q}」应命中意图,实际 {p}")
    if "time_preset" in expect:
        self.assertEqual(expect["time_preset"], p.get("time_preset"),
                         f"「{q}」时间词应解析为 {expect['time_preset']}")
        self.assertIsNone(p.get("time_range"), f"「{q}」不应产生 range")
    if "time_range" in expect:
        self.assertEqual(expect["time_range"], p.get("time_range"),
                         f"「{q}」应解析为月份范围 {expect['time_range']}")
        self.assertIsNone(p.get("time_preset"), f"「{q}」不应产生 preset")


def _check_card(self, q, p, expect):
    self.assertTrue(p.get("ok"), f"「{q}」应命中意图,实际 {p}")
    for key, val in expect.items():
        self.assertEqual(val, p.get(key), f"「{q}」的 {key} 应为 {val},实际 {p.get(key)}")


def _check_clarify_with_time(self, q, p, expect):
    kind, _word = _guards_check(q)
    self.assertIsNone(kind, f"「{q}」带时间词,不应触发 clarify,实际 {kind}")
    self.assertTrue(p.get("ok"), f"「{q}」应命中意图,实际 {p}")
    self.assertEqual(expect.get("metric"), p.get("metric"))
    if "time_preset" in expect:
        self.assertEqual(expect["time_preset"], p.get("time_preset"))
    if "time_range" in expect:
        self.assertEqual(expect["time_range"], p.get("time_range"))


class TestForbiddenRefuse(unittest.TestCase):
    """forbidden 词表:拒答守卫(顺序第一,时间词与指标词不影响)。"""


class TestBoundary(unittest.TestCase):
    """boundary 词表:明细/导出边界守卫(优先于 clarify)。"""


class TestClarify(unittest.TestCase):
    """clarify 词表:模糊指标词且无时间词 → 澄清卡。"""


class TestClarifyWithTime(unittest.TestCase):
    """模糊指标词 + 时间词:不澄清,按 GMV + 时间解析。"""


class TestSynonymMetrics(unittest.TestCase):
    """synonyms 词表:指标同义词映射。"""


class TestSalesVagueInvariant(unittest.TestCase):
    def test_sales_vague_never_maps_to_non_gmv(self):
        """「卖了多少」类口语:若命中意图必须是 gmv(当前行为:未命中,走 error 兜底)。"""
        for q in SALES_VAGUE_QUESTIONS:
            with self.subTest(q=q):
                _check_sales_invariant(self, q, _rules_parse(q))


class TestSynonymFilters(unittest.TestCase):
    """synonyms 词表:维度/筛值同义词映射(含渠道别名)。"""


class TestTimeWords(unittest.TestCase):
    """time_words 词表:时间词 → preset / 月份 range。"""


class TestIntentCards(unittest.TestCase):
    """意图构造抽查:rank/attribution 卡与维度归属。"""


_bind_guard_cases(TestForbiddenRefuse, REFUSE_CASES, "refuse")
_bind_guard_cases(TestBoundary, BOUNDARY_CASES, "boundary")
_bind_guard_cases(TestClarify, CLARIFY_CASES, "clarify")
_bind_parse_cases(TestClarifyWithTime, CLARIFY_WITH_TIME_CASES, _check_clarify_with_time, "clarify_time")
_bind_parse_cases(TestSynonymMetrics, SYNONYM_METRIC_CASES, _check_metric, "syn_metric")
_bind_parse_cases(TestSynonymFilters, SYNONYM_FILTER_CASES, _check_filter, "syn_filter")
_bind_parse_cases(TestTimeWords, TIME_CASES, _check_time, "time_word")
_bind_parse_cases(TestIntentCards, CARD_CASES, _check_card, "card")


class TestCrossGroup(unittest.TestCase):
    """v17 跨筛选组对比:同一筛选组 ≥2 个取值 + 比较句式 → parsed.cross_group
    (编排层确定性拒答,规则/LLM 双模式一致);纯并列/单筛值/时间对比不受影响。"""

    def _cg(self, q):
        return bool(_rules_parse(q).get("cross_group"))

    def test_u01_cross_group(self):
        self.assertTrue(self._cg("女装今年以来退款金额比数码多多少?"))
        self.assertTrue(self._cg("女装客单价比数码高多少?"))

    def test_respectively_and_which(self):
        self.assertTrue(self._cg("女装和数码的退款金额分别是多少"))
        self.assertTrue(self._cg("女装和数码哪个GMV高"))

    def test_plain_conjunction_not_flagged(self):
        """纯并列(无比较词)→ 不标记(「A和B合计」类合并意图仍可答)。"""
        self.assertFalse(self._cg("女装和数码合计的退款金额是多少"))

    def test_single_value_or_time_compare_not_flagged(self):
        """单筛值、无筛值(旬/周时间对比)、新老客对比卡不受影响。"""
        self.assertFalse(self._cg("女装今年以来退款金额是多少"))
        self.assertFalse(self._cg("10月下旬比上旬客单价怎么样?"))
        self.assertFalse(self._cg("本周GMV比上周变化了多少?"))
        p = _rules_parse("对比一下最近30天新客和老客的客单价")
        self.assertFalse(p.get("cross_group"), "新老客对比走内置 compare 卡,不标记")
        self.assertEqual("compare", p.get("card"))


if __name__ == "__main__":
    unittest.main()
