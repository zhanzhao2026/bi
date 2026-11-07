# -*- coding: utf-8 -*-
"""层级维度下钻测试(契约 §16.2,代理 P2-A)。

1. 层级结构:dims.yml category.children=[product]、product 维声明(column/
   name_table/name_col)、loader 层级访问器(dim_children/dim_parent)与
   编译器 DIM_LABEL["product"]=「商品」。
2. loader/IR 校验:validate_ir 接受 ["product"] 与 ["category","product"],
   拒绝 ["product","channel"];商品组合守卫(_needs_dim_clarify)防笛卡尔爆炸。
3. 端到端对拍(≥3 条,容差 1e-6):手写期望 SQL(测试内独立编写,不经过
   loader/compiler)vs 编译器管道(IR → normalize/validate/compile → 执行):
   - 美妆 last30 退款金额 TOP1 商品及金额
   - 全店 last30 GMV TOP3 商品
   - 美妆 last30 客单价最高商品(支付人数 ≥1)
4. 答案卡:商品 rank 卡标题含「商品」、scope_note 固定为「商品聚合视角,
   不含订单明细」、表格带父类目名列;组合问法出澄清卡。
"""
import unittest

from tests._support import (
    EXPECTED_AGG,
    SEMANTIC_DIR,
    data_as_of_handwritten,
    expected_window,
    exec_sql,
    fnum,
    pipeline_run,
)

TOL = 1e-6


def _sem():
    from app.semantic.loader import get_semantic
    return get_semantic()


def _rules_parse(question):
    from app.engine import rules
    from app.semantic.loader import get_filter_options
    from tests._support import ASOF
    return rules.parse(question, get_filter_options(), ASOF)


# ------------------------------------------------ 层级结构 / loader 校验 / 编译器

class TestHierarchyStructure(unittest.TestCase):
    """dims.yml 层级声明与 loader 访问器(§16.2)。"""

    def test_category_declares_children_product(self):
        import yaml
        doc = yaml.safe_load((SEMANTIC_DIR / "dims.yml").read_text(encoding="utf-8"))
        cat = (doc.get("dims") or {}).get("category") or {}
        self.assertEqual(["product"], list(cat.get("children") or []),
                         "category 应声明 children: [product]")

    def test_product_dim_declared(self):
        import yaml
        doc = yaml.safe_load((SEMANTIC_DIR / "dims.yml").read_text(encoding="utf-8"))
        prod = (doc.get("dims") or {}).get("product") or {}
        self.assertEqual("f.product_id", prod.get("column"))
        self.assertEqual("dim_product", prod.get("name_table"))
        self.assertEqual("product_name", prod.get("name_col"))

    def test_time_presets_unchanged(self):
        """层级升级不得动时间口径:5 个 preset 逐字保持。"""
        self.assertEqual(["yesterday", "last_7d", "last_30d", "this_month", "last_month"],
                         _sem().time_presets)

    def test_loader_hierarchy_accessors(self):
        sem = _sem()
        self.assertEqual(["product"], sem.dim_children("category"))
        self.assertEqual("category", sem.dim_parent("product"))
        self.assertIsNone(sem.dim_parent("category"))
        self.assertEqual([], sem.dim_children("product"))

    def test_loader_rejects_unknown_child(self):
        """children 引用未声明维度 → 启动即抛错(fail fast,副本目录不受污染)。"""
        import shutil
        import tempfile
        from pathlib import Path
        from app.semantic.loader import Semantic, SemanticError
        tmp = tempfile.mkdtemp(prefix="biz_dims_bad_")
        try:
            shutil.copytree(SEMANTIC_DIR, tmp + "/semantic")
            p = tmp + "/semantic/dims.yml"
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
            with open(p, "w", encoding="utf-8") as f:
                f.write(text.replace("children: [product]", "children: [sku]"))
            with self.assertRaises(SemanticError):
                Semantic(root=Path(tmp) / "semantic")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_compiler_product_label_and_join(self):
        from app.semantic import compiler
        self.assertEqual("商品", compiler.DIM_LABEL.get("product"))
        self.assertIn("dim_product", compiler.DIM_JOIN["product"])
        self.assertIn("dim_category", compiler.DIM_JOIN["product"])
        self.assertEqual("p.product_name", compiler.DIM_SELECT["product"])


class TestIRValidation(unittest.TestCase):
    """IR 校验:商品维组合白名单与拒绝路径。"""

    def _validate(self, dims):
        from app.semantic import compiler
        from app.semantic.loader import get_filter_options, get_semantic
        ir = compiler.normalize_ir({
            "metric": "gmv", "time": {"preset": "last_30d"},
            "dims": list(dims), "filters": {},
        })
        return compiler.validate_ir(ir, get_semantic(), get_filter_options())

    def test_accepts_product_only(self):
        ok, err = self._validate(["product"])
        self.assertTrue(ok, err)

    def test_accepts_category_product(self):
        ok, err = self._validate(["category", "product"])
        self.assertTrue(ok, err)

    def test_rejects_product_channel(self):
        ok, err = self._validate(["product", "channel"])
        self.assertFalse(ok, "商品×渠道组合应被拒绝(防笛卡尔爆炸)")

    def test_rejects_product_user_type(self):
        ok, _err = self._validate(["product", "user_type"])
        self.assertFalse(ok)

    def test_compiled_product_sql_has_parent_and_group_by(self):
        from app.semantic import compiler
        from app.semantic.loader import get_filter_options, get_semantic, get_data_as_of
        sem = get_semantic()
        ir = compiler.normalize_ir({
            "metric": "refund_amt", "time": {"preset": "last_30d"},
            "dims": ["product"], "filters": {"category": ["美妆"]},
        })
        start, end = compiler.resolve_time(ir["time"], get_data_as_of())
        built = compiler.compile_metric_query(ir, sem, start, end)
        self.assertIn("dim_parent", built["sql"])
        self.assertIn("JOIN dim_product", built["sql"])
        self.assertIn("GROUP BY p.product_id", built["sql"])
        rows = exec_sql(built["sql"], built["params"])
        self.assertTrue(rows, "美妆 last30 商品退款金额应有聚合行")
        self.assertIn("dim_parent", rows[0])


class TestDrilldownClarify(unittest.TestCase):
    """聚合守卫:product 与除 category/date 外的维度组合 → 澄清(防笛卡尔爆炸)。"""

    def test_guard_flags_channel_combo(self):
        from app.engine.orchestrator import Orchestrator
        o = Orchestrator()
        self.assertTrue(o._needs_dim_clarify(
            {"dims": ["product", "channel"]}, {}))
        self.assertTrue(o._needs_dim_clarify(
            {"dims": ["product"]}, {"dim_conflict": True}))

    def test_guard_allows_product_category_date(self):
        from app.engine.orchestrator import Orchestrator
        o = Orchestrator()
        self.assertFalse(o._needs_dim_clarify({"dims": ["product"]}, {}))
        self.assertFalse(o._needs_dim_clarify({"dims": ["product", "category"]}, {}))
        self.assertFalse(o._needs_dim_clarify({"dims": ["category"]}, {}))
        self.assertFalse(o._needs_dim_clarify({"dims": []}, {}))

    def test_rules_channel_word_conflict(self):
        """「各渠道哪些商品退款最多」→ dim_conflict,编排层出澄清卡。"""
        p = _rules_parse("各渠道哪些商品退款最多")
        self.assertTrue(p.get("ok"))
        self.assertTrue(p.get("dim_conflict"))

    def test_channel_name_filter_not_conflict(self):
        """「直播里哪些商品退款最多」:直播是渠道筛值词,不是「渠道」泛指词,不澄清。"""
        p = _rules_parse("直播里哪些商品退款最多")
        self.assertTrue(p.get("ok"))
        self.assertEqual("product", p.get("dim"))
        self.assertFalse(p.get("dim_conflict"))
        self.assertEqual(["直播"], (p.get("filters") or {}).get("channel"))


class TestDrilldownRules(unittest.TestCase):
    """商品下钻问法 → product rank intent。"""

    def test_beauty_refund_top(self):
        p = _rules_parse("美妆里哪些商品退款最多")
        self.assertTrue(p.get("ok"))
        self.assertEqual("rank", p.get("card"))
        self.assertEqual("product", p.get("dim"))
        self.assertEqual("refund_amt", p.get("metric"))
        self.assertEqual(["美妆"], (p.get("filters") or {}).get("category"))

    def test_topn_gmv_limit(self):
        p = _rules_parse("最近30天卖得最好的10个商品")
        self.assertTrue(p.get("ok"))
        self.assertEqual("rank", p.get("card"))
        self.assertEqual("product", p.get("dim"))
        self.assertEqual("gmv", p.get("metric"))
        self.assertEqual(10, p.get("limit"))
        self.assertEqual("last_30d", p.get("time_preset"))

    def test_arppu_best_product(self):
        p = _rules_parse("美妆哪个商品客单价最高")
        self.assertTrue(p.get("ok"))
        self.assertEqual("rank", p.get("card"))
        self.assertEqual("product", p.get("dim"))
        self.assertEqual("arppu", p.get("metric"))
        self.assertEqual(["美妆"], (p.get("filters") or {}).get("category"))

    def test_existing_category_rank_unchanged(self):
        """不含商品词的排名问法仍走类目/渠道(行为不变)。"""
        p = _rules_parse("最近30天哪个类目GMV最高")
        self.assertEqual("category", p.get("dim"))
        p2 = _rules_parse("各渠道GMV排名")
        self.assertEqual("channel", p2.get("dim"))


# ------------------------------------------------ 端到端对拍(手写 SQL vs 管道)

def _expected_product_sql(metric, start, end, categories=None, limit=5,
                          min_pay_users=None):
    """手写期望 SQL:商品维聚合(测试内独立编写,与 app/semantic/compiler.py 无代码复用)。"""
    agg = EXPECTED_AGG[metric]
    conds = ["f.pay_date BETWEEN %s AND %s"]
    params = [start, end]
    if categories:
        marks = ", ".join(["%s"] * len(categories))
        conds.append("f.category_id IN (SELECT category_id FROM dim_category "
                     f"WHERE category_name IN ({marks}))")
        params.extend(categories)
    where = " AND ".join(conds)
    having = ""
    if min_pay_users is not None:
        having = " HAVING COUNT(DISTINCT f.user_id) >= %s"
        params.append(min_pay_users)
    sql = (
        "SELECT p.product_name AS dim_name, pc.category_name AS dim_parent, "
        f"{agg} AS val FROM fact_order f "
        "JOIN dim_product p ON p.product_id = f.product_id "
        "LEFT JOIN dim_category pc ON pc.category_id = p.category_id "
        f"WHERE {where} GROUP BY p.product_id, p.product_name, pc.category_name"
        f"{having} ORDER BY val DESC, dim_name LIMIT {int(limit)}"
    )
    return sql, params


def _assert_topn_equal(tc, metric, categories, limit, label, min_pay_users=None):
    start, end = expected_window("last30")
    esql, eparams = _expected_product_sql(metric, start, end, categories, limit,
                                          min_pay_users)
    erows = exec_sql(esql, eparams)[:limit]
    prows, _psql = pipeline_run(metric, {"preset": "last_30d"}, ["product"],
                                {"category": categories} if categories else None)
    tc.assertGreaterEqual(len(prows), len(erows),
                          f"{label}: 管道返回行数不足(管道={len(prows)} 期望={len(erows)})")
    for i, erow in enumerate(erows):
        prow = prows[i]
        tc.assertEqual(erow["dim_name"], prow.get("dim_name"),
                       f"{label} 第{i + 1}名商品名不一致 管道={prow.get('dim_name')} "
                       f"期望={erow['dim_name']}")
        tc.assertEqual(erow.get("dim_parent"), prow.get("dim_parent"),
                       f"{label} 第{i + 1}名商品父类目不一致")
        tc.assertAlmostEqual(fnum(prow.get("val")), fnum(erow["val"]), delta=TOL,
                             msg=f"{label} 第{i + 1}名({erow['dim_name']})指标值超出容差 1e-6")


class TestDrilldownE2E(unittest.TestCase):
    """端到端对拍:手写期望 SQL == 编译器管道(容差 1e-6,契约 §16.4)。"""

    def test_beauty_refund_amt_top1(self):
        """美妆 last30 退款金额 TOP1 商品及金额。"""
        _assert_topn_equal(self, "refund_amt", ["美妆"], 1, "美妆last30退款金额TOP1")

    def test_store_gmv_top3(self):
        """全店 last30 GMV TOP3 商品。"""
        _assert_topn_equal(self, "gmv", None, 3, "全店last30GMV TOP3")

    def test_beauty_arppu_top1(self):
        """美妆 last30 客单价最高商品(支付人数 ≥1,手写侧 HAVING 守卫)。"""
        _assert_topn_equal(self, "arppu", ["美妆"], 1, "美妆last30客单价TOP1",
                           min_pay_users=1)

    def test_beauty_refund_amt_top5_order_stable(self):
        """补充对拍:TOP5 全序列一致(排序键 val DESC, dim_name 两侧行为一致)。"""
        _assert_topn_equal(self, "refund_amt", ["美妆"], 5, "美妆last30退款金额TOP5")


class TestDrilldownCardE2E(unittest.TestCase):
    """答案卡:商品 rank 卡标题/scope_note/父类目列;组合问法澄清。"""

    @classmethod
    def setUpClass(cls):
        from app.engine.orchestrator import Orchestrator
        cls.orch = Orchestrator()

    def test_beauty_refund_rank_card(self):
        card, _raw, done = self.orch.run_once("美妆里哪些商品退款最多", "ceo")
        self.assertEqual("rank", card.get("type"))
        self.assertIn("商品", card.get("title") or "")
        self.assertEqual("商品聚合视角,不含订单明细", card.get("scope_note"))
        tbl = card.get("table") or {}
        keys = [c["key"] for c in tbl.get("columns") or []]
        self.assertIn("parent", keys, "商品 rank 卡表格应含父类目列")
        self.assertIn("product", keys)
        self.assertTrue(tbl.get("rows"), "应有商品聚合行")
        self.assertEqual("美妆", tbl["rows"][0].get("parent"))
        self.assertEqual("ok", done.get("status"))
        # rank 卡标题含「商品」且叙事首句含 TOP1 商品名(数字全部来自结果集)
        self.assertIn(tbl["rows"][0]["product"], " ".join(card.get("narrative") or []))

    def test_topn_gmv_card_limit10(self):
        card, _raw, done = self.orch.run_once("最近30天卖得最好的10个商品", "ceo")
        self.assertEqual("rank", card.get("type"))
        self.assertIn("商品", card.get("title") or "")
        rows = (card.get("table") or {}).get("rows") or []
        self.assertEqual(10, len(rows), "「10个商品」应输出 10 行")
        self.assertEqual("ok", done.get("status"))

    def test_channel_combo_clarify(self):
        card, _raw, done = self.orch.run_once("各渠道哪些商品退款最多", "ceo")
        self.assertEqual("clarify", card.get("type"))
        self.assertEqual("clarify", done.get("status"))
        self.assertTrue(card.get("narrative"))


if __name__ == "__main__":
    unittest.main()
