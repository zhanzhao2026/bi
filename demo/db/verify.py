#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据层校验(契约 §2.4 故事线 + §2.7 输出)。全部 PASS 数据层才算完成。

容差:品类 GMV delta ±1.5pp;整体退款率与美妆贡献 ±0.1pp;渠道占比 ±3pp;
新客订单占比 24%~28%;其余为结构校验。AOV 契约要求"只报告,不强制"。
"""
import sys
import time

import pymysql

DB = dict(host="127.0.0.1", port=3306, user="appuser", password="apppass",
          database="biz_demo", charset="utf8mb4")

L0, L1 = "2026-10-08", "2026-11-06"    # last30
P0, P1 = "2026-09-08", "2026-10-07"    # prior30

CAT_TARGET = {1: 26.0, 2: -9.2, 3: -18.4, 4: 23.6, 5: 18.0, 6: -3.5, 7: -6.1}
CHAN_TARGET = {1: 35.0, 2: 30.0, 3: 20.0, 4: 15.0}

T0 = time.time()
PASS: list = []
FAIL: list = []


def check(name, ok, actual, expect):
    mark = "PASS" if ok else "FAIL"
    (PASS if ok else FAIL).append(name)
    print(f"[{mark}] {name}: 实测 {actual} | 期望 {expect}")


def info(name, actual):
    print(f"[INFO] {name}: {actual}")


def scalar(cur, sql, args=None):
    cur.execute(sql, args or ())
    row = cur.fetchone()
    return None if row is None or row[0] is None else float(row[0])


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    print("=" * 88)
    print("biz_demo 数据层校验(窗口: last30 = %s~%s, prior30 = %s~%s)" % (L0, L1, P0, P1))
    print("=" * 88)

    # ---------- 0. 数据水位 ----------
    cur.execute("SELECT MAX(pay_date) FROM fact_order")
    as_of = cur.fetchone()[0]
    as_of = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
    check("数据截至 data_as_of", as_of == "2026-11-06", as_of, "2026-11-06")

    counts = {}
    for t in ("dim_date", "dim_category", "dim_channel", "dim_product",
              "dim_user", "fact_order", "golden_qa", "audit_log",
              "semantic_version", "sys_settings", "feedback_log", "eval_history"):
        counts[t] = int(scalar(cur, f"SELECT COUNT(*) FROM {t}"))
    check("dim_date 行数", counts["dim_date"] == 90, counts["dim_date"], "= 90(90天)")
    check("dim_category 行数", counts["dim_category"] == 7, counts["dim_category"], "= 7")
    check("dim_channel 行数", counts["dim_channel"] == 4, counts["dim_channel"], "= 4")
    check("dim_product 行数(每类目≥8)", counts["dim_product"] >= 56, counts["dim_product"], ">= 56")
    check("dim_user 行数(约5.5万)", 50000 <= counts["dim_user"] <= 60000,
          counts["dim_user"], "∈ [50000, 60000]")
    check("fact_order 行数(约20.7万)", 190000 <= counts["fact_order"] <= 225000,
          counts["fact_order"], "∈ [190000, 225000]")
    info("日均订单数", f"{counts['fact_order'] / 90:.0f} 单/天(目标约2300)")

    # ---------- 1. 类目 GMV 与退款(一次查全) ----------
    cur.execute(
        """
        SELECT category_id,
               SUM(CASE WHEN pay_date BETWEEN %s AND %s THEN pay_amount ELSE 0 END),
               SUM(CASE WHEN pay_date BETWEEN %s AND %s THEN pay_amount ELSE 0 END),
               SUM(CASE WHEN pay_date BETWEEN %s AND %s THEN refund_amount ELSE 0 END),
               SUM(CASE WHEN pay_date BETWEEN %s AND %s THEN refund_amount ELSE 0 END)
        FROM fact_order GROUP BY category_id
        """,
        (L0, L1, P0, P1, L0, L1, P0, P1),
    )
    cat = {int(r[0]): {"gl": float(r[1]), "gp": float(r[2]),
                       "rl": float(r[3]), "rp": float(r[4])} for r in cur.fetchall()}
    g_last = sum(c["gl"] for c in cat.values())
    g_prior = sum(c["gp"] for c in cat.values())
    r_last_sum = sum(c["rl"] for c in cat.values())
    r_prior_sum = sum(c["rp"] for c in cat.values())

    cur.execute("SELECT category_id, category_name FROM dim_category ORDER BY sort")
    cat_name = {int(a): b for a, b in cur.fetchall()}

    print("-" * 88)
    print("故事线 1 · 类目 GMV 变化(last30 vs prior30,容差 ±1.5pp)")
    for cid in sorted(cat):
        d = (cat[cid]["gl"] - cat[cid]["gp"]) / cat[cid]["gp"] * 100
        tgt = CAT_TARGET[cid]
        check(f"  {cat_name[cid]} GMV delta", abs(d - tgt) <= 1.5, f"{d:+.2f}%", f"{tgt:+.1f}%")
    d_all = (g_last - g_prior) / g_prior * 100
    check("  整体 GMV delta", abs(d_all - 5.0) <= 1.5, f"{d_all:+.2f}%", "约 +5.0%")

    # ---------- 2. 退款率故事线 ----------
    print("-" * 88)
    print("故事线 2 · 退款率上升(整体 2.4%→2.8%,美妆驱动;容差 ±0.1pp)")
    rate_last = r_last_sum / g_last * 100
    rate_prior = r_prior_sum / g_prior * 100
    check("  整体退款率 prior30", abs(rate_prior - 2.4) <= 0.1, f"{rate_prior:.3f}%", "2.4%")
    check("  整体退款率 last30", abs(rate_last - 2.8) <= 0.1, f"{rate_last:.3f}%", "2.8%")
    check("  整体退款率变化", abs((rate_last - rate_prior) - 0.4) <= 0.1,
          f"{rate_last - rate_prior:+.3f}pp", "+0.4pp")
    b = cat[1]
    b_rate_l = b["rl"] / b["gl"] * 100
    b_rate_p = b["rp"] / b["gp"] * 100
    check("  美妆退款率 prior30", abs(b_rate_p - 2.0) <= 0.1, f"{b_rate_p:.3f}%", "2.0%")
    check("  美妆退款率 last30", abs(b_rate_l - 4.0) <= 0.1, f"{b_rate_l:.3f}%", "4.0%")
    contrib = (b["rl"] / g_last - b["rp"] / g_prior) * 100
    check("  美妆对整体退款率变化的贡献", abs(contrib - 0.4) <= 0.1, f"{contrib:+.3f}pp", "约 +0.4pp")
    for cid in sorted(cat):
        if cid == 1:
            continue
        rr_l = cat[cid]["rl"] / cat[cid]["gl"] * 100
        rr_p = cat[cid]["rp"] / cat[cid]["gp"] * 100
        check(f"  {cat_name[cid]} 退款率两窗口持平", abs(rr_l - rr_p) <= 0.05,
              f"{rr_p:.3f}% → {rr_l:.3f}%(|Δ|={abs(rr_l - rr_p):.3f}pp)", "|Δ| ≤ 0.05pp")

    # ---------- 3. 渠道结构 ----------
    print("-" * 88)
    print("故事线 3 · 渠道结构(容差 ±3pp)")
    cur.execute(
        """
        SELECT ch.channel_id, SUM(f.pay_amount), COUNT(*)
        FROM fact_order f JOIN dim_channel ch ON ch.channel_id = f.channel_id
        WHERE f.pay_date BETWEEN %s AND %s GROUP BY ch.channel_id
        """,
        (L0, L1),
    )
    chan = {int(r[0]): (float(r[1]), int(r[2])) for r in cur.fetchall()}
    c_gmv = sum(v[0] for v in chan.values())
    c_cnt = sum(v[1] for v in chan.values())
    chan_name = {}
    cur.execute("SELECT channel_id, channel_name FROM dim_channel")
    for a, b_ in cur.fetchall():
        chan_name[int(a)] = b_
    for cid in sorted(CHAN_TARGET):
        share = chan[cid][0] / c_gmv * 100
        oshare = chan[cid][1] / c_cnt * 100
        check(f"  {chan_name[cid]} GMV 占比(last30)", abs(share - CHAN_TARGET[cid]) <= 3.0,
              f"{share:.2f}%(订单占比 {oshare:.2f}%)", f"{CHAN_TARGET[cid]:.0f}% ±3pp")

    # ---------- 4. 新客订单占比 ----------
    print("-" * 88)
    print("故事线 4 · 新客订单占比(last30 目标 24%~28%)")
    n_ord = scalar(cur, "SELECT COUNT(*) FROM fact_order WHERE pay_date BETWEEN %s AND %s", (L0, L1))
    n_new = scalar(cur, "SELECT SUM(user_type='新客') FROM fact_order WHERE pay_date BETWEEN %s AND %s",
                   (L0, L1))
    new_ratio = n_new / n_ord * 100
    check("  新客订单占比 last30", 24.0 <= new_ratio <= 28.0, f"{new_ratio:.2f}%", "∈ [24%, 28%]")

    # ---------- 5. 问题商品与退款规则 ----------
    print("-" * 88)
    print("规则校验 · 美妆问题商品(退款概率 12%~20%)与退款金额比例(0.7~1.0×付款)")
    cur.execute(
        """
        SELECT p.product_name, COUNT(*) AS n, SUM(f.is_refund) AS r
        FROM fact_order f JOIN dim_product p ON p.product_id = f.product_id
        WHERE f.category_id = 1 AND f.pay_date BETWEEN %s AND %s
        GROUP BY p.product_id, p.product_name HAVING n >= 50
        ORDER BY r / n DESC LIMIT 3
        """,
        (L0, L1),
    )
    prob = cur.fetchall()
    ok_prob = len(prob) == 3
    desc = []
    for name, n, r in prob:
        pr = float(r) / float(n) * 100
        desc.append(f"{name} {pr:.1f}%")
        ok_prob = ok_prob and (12.0 <= pr <= 20.0)
    check("  美妆 last30 退款率最高3款商品(问题商品)退款订单率",
          ok_prob, "; ".join(desc), "每款 ∈ [12%, 20%]")
    lo = scalar(cur, "SELECT MIN(refund_amount / pay_amount) FROM fact_order WHERE is_refund = 1")
    hi = scalar(cur, "SELECT MAX(refund_amount / pay_amount) FROM fact_order WHERE is_refund = 1")
    check("  单笔退款金额/付款金额范围", lo is not None and 0.7 <= lo and hi <= 1.0,
          f"[{lo:.4f}, {hi:.4f}]", "⊂ [0.7, 1.0]")

    # ---------- 6. 新客判定一致性 ----------
    print("-" * 88)
    bad_new = scalar(
        cur,
        """
        SELECT COUNT(*) FROM fact_order f WHERE f.user_type = '新客' AND EXISTS (
          SELECT 1 FROM fact_order g WHERE g.user_id = f.user_id AND g.pay_date < f.pay_date)
        """,
    )
    check("  新客判定一致性(新客订单前不存在同用户更早订单)", bad_new == 0,
          f"违例 {int(bad_new)} 条", "= 0")

    # ---------- 7. 其他种子数据 ----------
    print("-" * 88)
    check("golden_qa 条数(≥16)", counts["golden_qa"] >= 16, counts["golden_qa"], ">= 16")
    cur.execute("SELECT kind, COUNT(*) FROM golden_qa GROUP BY kind")
    kinds = {k: int(n) for k, n in cur.fetchall()}
    need_kinds = {"value", "trend", "rank", "compare", "attribution", "clarify", "refuse", "boundary"}
    check("golden_qa kind 覆盖", need_kinds.issubset(kinds),
          ",".join(f"{k}×{v}" for k, v in sorted(kinds.items())),
          "需含 " + ",".join(sorted(need_kinds)))
    cur.execute("SELECT COUNT(*) FROM golden_qa WHERE kind IN ('value','trend','rank','compare','attribution') "
                "AND ref_value IS NULL")
    null_ref = int(cur.fetchone()[0])
    check("golden_qa ref_value 实时写入(非兜底类 ref_value 非空)", null_ref == 0,
          f"缺失 {null_ref} 条", "= 0")
    check("audit_log 演示记录 6~8 条", 6 <= counts["audit_log"] <= 8, counts["audit_log"], "∈ [6, 8]")
    cur.execute("SELECT status, COUNT(*) FROM audit_log GROUP BY status")
    st = {k: int(n) for k, n in cur.fetchall()}
    check("audit_log 含 clarify/refuse 状态", st.get("clarify", 0) >= 1 and st.get("refuse", 0) >= 1,
          ",".join(f"{k}×{v}" for k, v in sorted(st.items())), "clarify≥1 且 refuse≥1")
    cur.execute("SELECT version, released, change_desc, yaml_snapshot IS NOT NULL AND yaml_snapshot != '' "
                "FROM semantic_version ORDER BY version")
    vers = cur.fetchall()
    check("semantic_version 三条(v10/v11/v12)",
          [int(v[0]) for v in vers] == [10, 11, 12] and all(v[3] for v in vers),
          "; ".join(f"v{v[0]}({v[1]})" for v in vers), "v10/v11/v12,含 yaml_snapshot")
    cur.execute("SELECT skey, svalue FROM sys_settings ORDER BY skey")
    settings = {k: (v or "") for k, v in cur.fetchall()}
    ok_set = (settings.get("llm_base_url") == "https://open.bigmodel.cn/api/paas/v4"
              and settings.get("llm_model") == "glm-4-flash"
              and settings.get("llm_api_key") == "")
    check("sys_settings 三项(llm_base_url/llm_model/llm_api_key=空)", ok_set,
          f"base_url={settings.get('llm_base_url')}, model={settings.get('llm_model')}, "
          f"api_key='{settings.get('llm_api_key')}'",
          "https://open.bigmodel.cn/api/paas/v4 / glm-4-flash / ''")

    # ---------- 8. 参考指标(只报告) ----------
    print("-" * 88)
    users_l = scalar(cur, "SELECT COUNT(DISTINCT user_id) FROM fact_order WHERE pay_date BETWEEN %s AND %s",
                     (L0, L1))
    aov = g_last / users_l
    aov_order = g_last / n_ord
    info(f"客单价(.last30,GMV/支付人数,支付人数 {users_l:,.0f})", f"{aov:.1f} 元/人(预期 140~220)")
    info("笔均付款(.last30)", f"{aov_order:.1f} 元/单")
    check("  客单价参考区间", 140.0 <= aov <= 220.0, f"{aov:.1f}", "∈ [140, 220](契约:只报告,不强制)")
    info("整体 GMV(.last30 / .prior30)",
         f"{g_last:,.0f} 元 / {g_prior:,.0f} 元({g_last / 10000:,.1f} 万 / {g_prior / 10000:,.1f} 万)")
    info("整体退款金额(.last30 / .prior30)",
         f"{r_last_sum:,.0f} 元 / {r_prior_sum:,.0f} 元")
    info("golden_qa kind 分布", str(kinds))

    # ---------- 汇总 ----------
    print("=" * 88)
    total = len(PASS) + len(FAIL)
    print(f"结果:{len(PASS)}/{total} PASS,{len(FAIL)} FAIL,耗时 {time.time() - T0:.1f}s")
    if FAIL:
        print("未通过项:" + "; ".join(FAIL))
    print("=" * 88)
    cur.close()
    conn.close()
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
