# -*- coding: utf-8 -*-
"""指标一致性测试(契约 §15.2 核心)。

11 个指标 × 9 种代表性(时间 × 维度)组合:手写期望 SQL(直接 cursor.execute,
逐指标独立编写,不经过 loader/compiler)的结果 == 编译器管道(构造 IR →
normalize/validate → compile → 执行)的结果,容差 1e-6。

比率类(gmv 之外的 refund_rate/refund_order_rate/new_order_ratio 等)按原始小数
口径比较(如 0.0281),不做百分数换算。
"""
import unittest

from tests._support import (
    COMBOS,
    METRIC_KEYS,
    assert_rows_equal,
    data_as_of_handwritten,
    expected_sql,
    expected_window,
    exec_sql,
    pipeline_run,
)


class TestMetricConsistency(unittest.TestCase):
    def test_data_as_of_pipeline_and_handwritten_agree(self):
        """管道的 data_as_of(loader 缓存)与手写 MAX(pay_date) 一致。"""
        from app.semantic.loader import get_data_as_of

        hand = data_as_of_handwritten()
        pipe = get_data_as_of()
        self.assertIsNotNone(hand)
        self.assertIsNotNone(pipe)
        self.assertEqual(str(pipe), hand.isoformat())


def _make_case(ckey, combo, mkey):
    def test(self):
        start, end = expected_window(combo["win"])
        dim = (combo.get("dims") or [None])[0]
        filters = combo.get("filters") or {}
        esql, eparams = expected_sql(mkey, start, end, dim, filters)
        erows = exec_sql(esql, eparams)
        prows, _psql = pipeline_run(mkey, combo["time"], combo.get("dims"), filters)
        assert_rows_equal(self, prows, erows, f"{combo['label']} × {mkey}")
    test.__doc__ = f"一致性[{combo['label']} × {mkey}]:手写期望 SQL == 编译器管道(容差 1e-6)"
    return test


def _bind_generated_cases():
    for ckey, combo in COMBOS.items():
        for mkey in METRIC_KEYS:
            setattr(TestMetricConsistency, f"test_{ckey}__{mkey}",
                    _make_case(ckey, combo, mkey))


_bind_generated_cases()


if __name__ == "__main__":
    unittest.main()
