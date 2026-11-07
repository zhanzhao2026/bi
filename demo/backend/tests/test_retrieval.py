# -*- coding: utf-8 -*-
"""BM25 语义检索测试(契约 §16.3,代理 P2-B)。

覆盖三层:
- 检索质量(≥10 例):口语问法 → 期望指标/维度/词表条目进入 top-k;
- 边界(≥3 例):空 q / 纯符号 / 超长 q 不抛错,返回空列表或合理结果;
- 集成:索引单例与 rebuild()、planner 指标清单检索注入(§16.3)与 ≤12 回退全量。

索引构造不依赖数据库:直接以 loader.Semantic() + 注入维度取值构建
(维度取值与 db/seed_data.py 的 dim_category/dim_channel 一致),
因此本文件可独立于 DB 运行。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.semantic.loader import Semantic
from app.semantic.retrieval import build_index, get_index, rebuild
from app.llm.gateway import LLMGateway
from app.llm.prompts import render_planner_system

# 与 db/seed_data.py 的维度取值一致(测试注入,不依赖 DB)
DIM_VALUES = {
    "category": ["美妆", "女装", "数码", "母婴", "食品", "运动", "家居"],
    "channel": ["自然流量", "付费推广", "直播", "私域"],
    "user_type": ["新客", "老客"],
}


def _top_keys(hits):
    return [(h["kind"], h["key"]) for h in hits]


class RetrievalQualityTest(unittest.TestCase):
    """检索质量用例(契约 §16.3:≥10 例)。"""

    @classmethod
    def setUpClass(cls):
        cls.sem = Semantic()
        cls.idx = build_index(cls.sem, DIM_VALUES)

    def hits(self, q, k=5):
        return self.idx.search(q, k=k)

    def keys(self, q, k=5):
        return _top_keys(self.hits(q, k=k))

    def metric_keys(self, q, k=5):
        return [key for kind, key in self.keys(q, k=k) if kind == "metric"]

    # 1) 「退款率为什么高」top1 = refund_rate
    def test_refund_rate_why_high_top1(self):
        keys = self.metric_keys("退款率为什么高")
        self.assertTrue(keys, "应至少命中一个指标")
        self.assertEqual("refund_rate", keys[0])

    # 2) 「客单价」top1 = arppu
    def test_arppu_top1(self):
        self.assertEqual("arppu", self.metric_keys("客单价")[0])

    # 3) 「新客」→ new_order_ratio / new_order_cnt 至少一个进 top3
    def test_new_customer_in_top3(self):
        top3 = set(self.metric_keys("新客", k=3))
        self.assertTrue(
            {"new_order_ratio", "new_order_cnt"} & top3,
            f"新客相关指标应进 top3,实际 {top3}")

    # 4) 「美妆退款」→ refund_rate top1,且 category 维进 top3
    def test_beauty_refund(self):
        hits = self.hits("美妆退款", k=3)
        self.assertEqual("metric", hits[0]["kind"])
        self.assertEqual("refund_rate", hits[0]["key"])
        kinds = {(h["kind"], h["key"]) for h in hits}
        self.assertIn(("dim", "category"), kinds,
                      f"category 维应进 top3,实际 {kinds}")

    # 5) 「哪个类目卖得好」→ gmv + category 同时命中(默认 k=5)
    def test_category_selling_well(self):
        found = set(self.keys("哪个类目卖得好", k=5))
        self.assertIn(("dim", "category"), found)
        self.assertIn(("metric", "gmv"), found, f"gmv 应进 top5,实际 {found}")

    # 6) 「退款金额」→ refund_amt(top1)
    def test_refund_amt_top1(self):
        self.assertEqual("refund_amt", self.metric_keys("退款金额")[0])

    # 7) 「支付人数」→ pay_user_cnt(top1)
    def test_pay_user_cnt_top1(self):
        self.assertEqual("pay_user_cnt", self.metric_keys("支付人数")[0])

    # 8) 「订单退款率」→ refund_order_rate 进 top3
    def test_refund_order_rate_top3(self):
        self.assertIn("refund_order_rate", self.metric_keys("订单退款率", k=3))

    # 9) 「销售额」→ 模糊词官方映射(销售额 → gmv)进 top3
    def test_vague_sales_to_gmv(self):
        keys = self.metric_keys("销售额", k=3)
        self.assertIn("gmv", keys, f"销售额应映射到 gmv,实际 {keys}")
        self.assertIn("同义词", next(h["reason"] for h in self.hits("销售额") if h["key"] == "gmv"))

    # 10) 「直播渠道」→ channel 维命中
    def test_channel_dim_hit(self):
        top3 = set(self.keys("直播渠道", k=3))
        self.assertIn(("dim", "channel"), top3)

    # 11) 「退了多少钱」→ 同义词命中 refund_amt 进 top3
    def test_refund_colloquial_synonym(self):
        keys = self.metric_keys("退了多少钱", k=3)
        self.assertTrue(keys and keys[0] == "refund_amt",
                        f"退款金额同义词应命中 refund_amt,实际 {keys}")
        self.assertIn("同义词", self.hits("退了多少钱")[0]["reason"])

    # 12) 「GMV 是多少」→ 英文整词命中 gmv top1
    def test_english_gmv_top1(self):
        self.assertEqual("gmv", self.metric_keys("GMV是多少")[0])

    # 13) 「工资条怎么查」→ 拒答词表条目(kind=term)top1
    def test_forbidden_term_hit(self):
        hit = self.hits("工资条怎么查")[0]
        self.assertEqual("term", hit["kind"])
        self.assertTrue(hit["key"].startswith("forbidden:"),
                        f"应命中拒答词表,实际 {hit['key']}")

    # 14) 「新客占比」→ 同义词精确命中 new_order_ratio top1
    def test_new_order_ratio_top1(self):
        self.assertEqual("new_order_ratio", self.metric_keys("新客占比")[0])

    # 15) 「类目有哪些」→ 用户说法命中 category 维 top1
    def test_category_user_term_top1(self):
        hit = self.hits("类目有哪些")[0]
        self.assertEqual(("dim", "category"), (hit["kind"], hit["key"]))

    # 16) 结果结构:kind/key/name/score/reason 齐全且类型正确
    def test_result_schema(self):
        for q in ("退款率为什么高", "美妆", "新客", "工资"):
            for h in self.idx.search(q, k=8):
                self.assertIn(h["kind"], ("metric", "dim", "term"))
                self.assertIsInstance(h["key"], str)
                self.assertTrue(h["key"])
                self.assertTrue(h["name"])
                self.assertIsInstance(h["score"], float)
                self.assertGreaterEqual(h["score"], 0.0)
                self.assertIn("『", h["reason"], "reason 应含命中来源说明")

    # 17) reason 命中来源可读:plain/同义词/取值等来源词出现
    def test_reason_sources(self):
        reasons = {h["key"]: h["reason"] for h in self.idx.search("美妆退款", k=5)}
        self.assertIn("取值『美妆』", reasons.get("category", ""))
        self.assertIn("名称", reasons.get("refund_rate", ""))


class RetrievalBoundaryTest(unittest.TestCase):
    """边界用例(契约 §16.3:空 q / 纯符号 / 超长 q)。"""

    @classmethod
    def setUpClass(cls):
        cls.idx = build_index(Semantic(), DIM_VALUES)

    def test_empty_query(self):
        self.assertEqual([], self.idx.search(""))
        self.assertEqual([], self.idx.search("   "))
        self.assertEqual([], self.idx.search(None))

    def test_symbol_only_query(self):
        for q in ("!!!@#$%^&*()", "???。;;,", "——+++===   ", "!!!!"):
            self.assertEqual([], self.idx.search(q), f"纯符号应返回空列表: {q!r}")

    def test_overlong_query_no_raise(self):
        long_q = "退款率为什么高" * 300          # 2100 字,超 _MAX_QUERY_LEN 截断
        hits = self.idx.search(long_q)          # 不应抛错
        self.assertIsInstance(hits, list)
        if hits:
            self.assertEqual("refund_rate", hits[0]["key"])
        garbage = "啊" * 3000                    # 无命中也不抛错
        self.assertEqual([], self.idx.search(garbage))

    def test_k_clamped(self):
        self.assertGreaterEqual(len(self.idx.search("退款率", k=0)), 1)   # k<1 → 至少 1 条
        self.assertLessEqual(len(self.idx.search("退款", k=999)), 20)    # k 上限 20
        self.assertEqual(len(self.idx.search("退款率", k=3)), 3)


class RetrievalIndexLifecycleTest(unittest.TestCase):
    """索引单例:随 loader 加载构建、缓存复用、rebuild() 强制重建。"""

    def test_get_index_cached(self):
        idx1 = get_index()
        idx2 = get_index()
        self.assertIs(idx1, idx2)
        hits = idx1.search("退款率")
        self.assertTrue(hits and hits[0]["key"] == "refund_rate")

    def test_rebuild_returns_fresh_index(self):
        old = get_index()
        fresh = rebuild()
        self.assertIsNot(old, fresh)
        self.assertTrue(fresh.search("客单价")[0]["key"] == "arppu")


class PlannerRetrievalInjectionTest(unittest.TestCase):
    """planner 指标清单检索注入(§16.3):>12 指标时 top-k 注入,否则回退全量。"""

    @classmethod
    def setUpClass(cls):
        cls.sem = Semantic()

    def test_full_prompt_contains_all_metrics(self):
        text = render_planner_system(self.sem, {})
        for key in self.sem.metrics:
            self.assertIn(key, text)
        self.assertIn("可用指标(格式:", text)          # 全量注入的表头

    def test_retrieved_prompt_injects_subset(self):
        text = render_planner_system(self.sem, {}, metric_keys=["gmv", "refund_rate"])
        self.assertIn("gmv", text)
        self.assertIn("refund_rate", text)
        self.assertNotIn("pay_user_cnt", text)        # 未入选指标不出现
        self.assertIn("检索", text)                    # 表头注明为检索注入

    def test_fallback_when_metrics_le_12(self):
        # 当前 11 指标 ≤ 12 → 回退全量(None),行为不变,且不触发检索
        self.assertIsNone(LLMGateway._retrieved_metric_keys("退款率", self.sem))

    def test_injection_when_metrics_gt_12(self):
        # 构造 >12 指标的语义对象(复用真实指标 + 补充假指标),验证检索注入路径
        fake = dict(self.sem.metrics)
        for i in range(4):
            fake[f"fake_metric_{i}"] = {"name": f"虚拟指标{i}", "plain": "占位口径", "unit": "个"}
        fake_sem = SimpleNamespace(metrics=fake, version=self.sem.version)
        keys = LLMGateway._retrieved_metric_keys("退款率为什么高", fake_sem)
        self.assertIsInstance(keys, list)
        self.assertTrue(keys, "检索非空时应返回指标 key 列表")
        self.assertLessEqual(len(keys), 8)             # top-k = 8
        self.assertEqual("refund_rate", keys[0])
        for k in keys:
            self.assertIn(k, fake)
        # 检索不到的问法 → 回退全量(None)
        self.assertIsNone(
            LLMGateway._retrieved_metric_keys("啊", fake_sem))


if __name__ == "__main__":
    unittest.main()
