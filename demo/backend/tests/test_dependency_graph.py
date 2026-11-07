# -*- coding: utf-8 -*-
"""依赖图测试与 semantic/ 结构测试(契约 §15.1/§15.2)。

1. 结构:semantic/ 新结构文件齐全,11 个指标经 loader 加载后暴露契约字段。
2. 依赖展开:每个派生指标展开后的 agg 必须包含其 depends_on 各基础指标的 agg
   片段(防止绕开 depends_on 重抄表达式)。
3. 篡改证明:把 semantic/ 整目录复制到 /tmp 副本,篡改副本中 gmv 的 agg(×2),
   用副本构造 loader 实例,断言 arppu/refund_rate 的展开结果随 gmv 变化(字符串
   包含 + 实际执行 SQL 的数值关系),并在 finally 清理副本 —— 证明"改基础口径,
   派生指标跟着变"。
"""
import shutil
import unittest

from tests._support import (
    EXPECTED_AGG,
    SEMANTIC_DIR,
    _squash,
    copy_semantic_to_tmp,
    expected_window,
    fnum,
    load_semantic_from_dir,
    scalar,
    tamper_gmv_agg,
)

# 契约 §15.1:派生指标 → 依赖的基础指标
CONTRACT_DERIVED_DEPS = {
    "arppu": ["gmv", "pay_user_cnt"],
    "refund_rate": ["refund_amt", "gmv"],
    "refund_order_rate": ["refund_order_cnt", "order_cnt"],
    "new_order_ratio": ["new_order_cnt", "order_cnt"],
}
# 契约 §15.1:基础指标 7 个
CONTRACT_BASE_KEYS = ["gmv", "order_cnt", "pay_user_cnt", "item_cnt",
                      "refund_amt", "refund_order_cnt", "new_order_cnt"]
CONTRACT_DERIVED_KEYS = list(CONTRACT_DERIVED_DEPS.keys())
ALL_METRIC_KEYS = CONTRACT_BASE_KEYS + CONTRACT_DERIVED_KEYS

REQUIRED_FILES = [
    "models.yml",
    "metrics/base.yml",
    "metrics/derived.yml",
    "dims.yml",
    "terms.yml",
    "version.yml",
]


def _sem():
    from app.semantic.loader import get_semantic
    return get_semantic()


class TestSemanticLayout(unittest.TestCase):
    """§15.1 新结构落地检查。"""

    def test_semantic_dir_structure(self):
        """semantic/ 下 models/metrics(base+derived)/dims/terms/version 齐全。"""
        for rel in REQUIRED_FILES:
            p = SEMANTIC_DIR / rel
            self.assertTrue(p.is_file(), f"缺少文件: backend/semantic/{rel}")

    def test_version_yml_at_least_13(self):
        """version.yml 版本号 ≥ 13(§15.1: version: 13)。"""
        import yaml
        doc = yaml.safe_load((SEMANTIC_DIR / "version.yml").read_text(encoding="utf-8"))
        ver = int(doc.get("version") or doc.get("data", {}).get("version") or 0)
        self.assertGreaterEqual(ver, 13, f"version.yml 版本应 ≥13,实际 {ver}")

    def test_all_11_metrics_loaded_with_contract_fields(self):
        """loader 暴露 11 个指标,且字段与旧结构完全一致(name/plain/unit/agg/ratio/derived)。"""
        sem = _sem()
        for key in ALL_METRIC_KEYS:
            m = sem.metrics.get(key)
            self.assertTrue(m, f"指标缺失: {key}")
            for field in ("name", "plain", "unit", "agg"):
                self.assertTrue(m.get(field), f"{key} 缺字段 {field}")
            self.assertIn("agg", m, f"{key} 缺展开后的 agg")

    def test_metric_type_flags(self):
        """7 基础 / 4 派生(derived: true)/ 3 比率(ratio: true)与契约一致。"""
        sem = _sem()
        for key in CONTRACT_BASE_KEYS:
            self.assertFalse(sem.metrics[key].get("derived"), f"{key} 应为基础指标")
        for key in CONTRACT_DERIVED_KEYS:
            self.assertTrue(sem.metrics[key].get("derived"), f"{key} 应为派生指标")
        for key in ("refund_rate", "refund_order_rate", "new_order_ratio"):
            self.assertTrue(sem.metrics[key].get("ratio"), f"{key} 应为 ratio")
        self.assertTrue(sem.metrics["refund_rate"].get("higher_is_bad"))
        self.assertTrue(sem.metrics["refund_order_rate"].get("higher_is_bad"))

    def test_base_agg_matches_contract(self):
        """基础指标 agg 与契约 §3 定义逐字一致(空白差异忽略),口径零变化。"""
        sem = _sem()
        for key, want in EXPECTED_AGG.items():
            if key in CONTRACT_DERIVED_KEYS:
                continue
            self.assertEqual(_squash(sem.metrics[key]["agg"]), _squash(want),
                             f"{key} 的 agg 与契约 §3 不一致")


class TestDependencyExpansion(unittest.TestCase):
    """展开后的 agg 必须包含所依赖基础指标的 agg 片段。"""

    def _assert_contains(self, derived_key):
        sem = _sem()
        dagg = _squash(sem.metrics[derived_key]["agg"])
        for dep in CONTRACT_DERIVED_DEPS[derived_key]:
            frag = _squash(sem.metrics[dep]["agg"])
            self.assertTrue(frag and frag in dagg,
                            f"派生指标 {derived_key} 的 agg 未包含基础指标 {dep} 的 agg 片段:"
                            f" 片段={frag!r} 展开={dagg!r}")
        # 若 loader 保留原始定义(如 v13 的 metric_defs),交叉核对 depends_on 声明
        defs = getattr(sem, "metric_defs", None) or {}
        raw = (defs.get("derived") or {}).get(derived_key) or {}
        declared = raw.get("depends_on")
        if declared:
            self.assertEqual(sorted(CONTRACT_DERIVED_DEPS[derived_key]), sorted(declared),
                             f"{derived_key} 的 depends_on 声明与契约 §15.1 不一致")

    def test_expand_arppu(self):
        """arppu 展开包含 gmv 与 pay_user_cnt 的 agg 片段。"""
        self._assert_contains("arppu")

    def test_expand_refund_rate(self):
        """refund_rate 展开包含 refund_amt 与 gmv 的 agg 片段。"""
        self._assert_contains("refund_rate")

    def test_expand_refund_order_rate(self):
        """refund_order_rate 展开包含 refund_order_cnt 与 order_cnt 的 agg 片段。"""
        self._assert_contains("refund_order_rate")

    def test_expand_new_order_ratio(self):
        """new_order_ratio 展开包含 new_order_cnt 与 order_cnt 的 agg 片段。"""
        self._assert_contains("new_order_ratio")

    def test_no_metric_copies_gmv_expr_verbatim_without_dep(self):
        """除派生指标外,任何指标不得原样内嵌 gmv 的 agg(防止重抄口径)。"""
        sem = _sem()
        gmv_frag = _squash(sem.metrics["gmv"]["agg"])
        for key, m in sem.metrics.items():
            if key == "gmv" or key in CONTRACT_DERIVED_DEPS:
                continue
            self.assertNotIn(gmv_frag, _squash(m.get("agg")),
                             f"{key} 疑似绕开 depends_on 重抄了 gmv 表达式")


class TestTamperedBase(unittest.TestCase):
    """篡改副本基础口径 → 派生指标跟着变(/tmp 副本,测试后清理)。"""

    def test_tamper_gmv_propagates_to_arppu_and_refund_rate(self):
        """副本中 gmv agg ×2:arppu 展开跟随(值×2)、refund_rate 跟随(值÷2)。"""
        import yaml  # noqa: F401  (tamper 内部使用,提前导入确认可用)
        from app.semantic.loader import get_semantic

        real = get_semantic()
        real_gmv_agg = real.metrics["gmv"]["agg"]

        tmp_base, tmp_backend, tmp_sem = copy_semantic_to_tmp()
        try:
            marker = tamper_gmv_agg(tmp_sem)  # 形如 "2 * (SUM(f.pay_amount))"
            self.assertIn("2 * (", marker)

            sem2 = load_semantic_from_dir(
                tmp_sem,
                verify=lambda s: _squash(marker) in _squash(
                    (s.metrics.get("gmv") or {}).get("agg", "")))

            # 1) 副本中基础口径确实变了
            t_gmv_agg = sem2.metrics["gmv"]["agg"]
            self.assertNotEqual(_squash(t_gmv_agg), _squash(real_gmv_agg))

            # 2) 派生指标展开结果跟随基础口径变化(包含篡改后的 gmv agg 片段)
            for dk in ("arppu", "refund_rate"):
                self.assertIn(_squash(marker), _squash(sem2.metrics[dk]["agg"]),
                              f"篡改 gmv 后,{dk} 的展开未跟随基础口径")

            # 3) 数值证明:同一窗口实际执行两侧展开的 agg(手写 SQL 挂载,不经编译器)
            start, end = expected_window("last30")

            def one(agg):
                return fnum(scalar(
                    f"SELECT {agg} AS val FROM fact_order f "
                    f"WHERE f.pay_date BETWEEN %s AND %s", [start, end]))

            g0, g1 = one(real_gmv_agg), one(t_gmv_agg)
            self.assertAlmostEqual(g1, 2.0 * g0, delta=1e-6,
                                   msg="副本 gmv 应为原值的 2 倍")
            a0, a1 = one(real.metrics["arppu"]["agg"]), one(sem2.metrics["arppu"]["agg"])
            self.assertAlmostEqual(a1 / a0, 2.0, delta=1e-3,
                                   msg="gmv 口径×2 后,arppu 应跟随变为 2 倍")
            r0, r1 = one(real.metrics["refund_rate"]["agg"]), one(sem2.metrics["refund_rate"]["agg"])
            self.assertAlmostEqual(r1 / r0, 0.5, delta=1e-3,
                                   msg="gmv 口径×2 后,refund_rate 应跟随变为 1/2")
            print(f"[依赖图篡改证明] 篡改后 gmv agg = {t_gmv_agg!r}")
            print(f"[依赖图篡改证明] arppu: 原值={a0:.6f} 篡改后={a1:.6f} 倍数={a1 / a0:.6f}(期望 2)")
            print(f"[依赖图篡改证明] refund_rate: 原值={r0:.6f} 篡改后={r1:.6f} 倍数={r1 / r0:.6f}(期望 0.5)")

            # 4) 真实 semantic/ 未被污染
            self.assertEqual(_squash(get_semantic().metrics["gmv"]["agg"]),
                             _squash(real_gmv_agg))
            self.assertEqual(_squash(real.metrics["arppu"]["agg"]),
                             _squash(get_semantic().metrics["arppu"]["agg"]))
        finally:
            shutil.rmtree(tmp_base, ignore_errors=True)
        # 5) 副本已清理
        self.assertFalse(tmp_sem.exists(), "/tmp 临时副本应已删除,不得残留")


if __name__ == "__main__":
    unittest.main()
