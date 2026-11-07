#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""biz_demo 造数脚本(契约 §2.3~§2.6)。random.seed(42) 固定种子,可复现;仅标准库 + PyMySQL。

故事线(verify.py 逐一校验):
  1) 类目 GMV 变化(last30 vs prior30):美妆+26.0 家居+23.6 食品+18.0 母婴-3.5 运动-6.1 女装-9.2 数码-18.4,整体≈+5%
  2) 整体退款率 2.4%→2.8% 由美妆驱动(美妆 2.0%→4.0%,其他类目两窗口持平)
  3) 渠道结构:直播35% / 自然流量30% / 私域20% / 付费推广15%
  4) last30 新客订单占比 ≈25%(目标区间 24%~28%)

实现要点:
  - 按(日×类目)精确设定 GMV 目标,组内订单金额缩放到"分",类目/整体 delta 精确;
    last30 增长乘子按两窗口实际季节性和反解,整体 delta = Σ(权重×delta) ≈ +4.97%。
  - 退款按(类目×窗口)用"水位法"把退款总额精确调到 目标费率×GMV,单笔退款金额始终在
    [0.7×付款, 1.0×付款] 内(契约规则)。
  - 用户 5.5 万;首日(2026-08-09)无历史用户故全部为新客,其后新客订单占比≈25%;
    新客判定 = 该用户在表内首次付款日 == 本单付款日。
"""
import json
import math
import random
import time
from datetime import date, timedelta

import pymysql

DB = dict(host="127.0.0.1", port=3306, user="appuser", password="apppass",
          database="biz_demo", charset="utf8mb4")

SEED = 42
START = date(2026, 8, 9)
END = date(2026, 11, 6)                      # 数据 T+1:data_as_of = 2026-11-06
LAST30 = (date(2026, 10, 8), date(2026, 11, 6))
PRIOR30 = (date(2026, 9, 8), date(2026, 10, 7))
TOTAL_ORDERS = 207_000                       # 90 天 × 日均约 2300
NUM_USERS = 55_000
ORDER_AOV = 120.0                            # 笔均付款(元),客单价=GMV/支付人数 约 190~200
NEW_RATIO = 0.25                             # 第 2 天起新客订单占比(首日全部为新客)
BATCH = 5000

# (category_id, 名称, prior30 GMV 权重, delta, 退款率prior, 退款率last, 基准笔均aov)
CATS = [
    (1, "美妆", 0.15,  0.260, 0.0200, 0.0400, 110.0),
    (2, "女装", 0.21, -0.092, 0.0220, 0.0220, 125.0),
    (3, "数码", 0.14, -0.184, 0.0200, 0.0200, 950.0),
    (4, "家居", 0.16,  0.236, 0.0300, 0.0300, 175.0),
    (5, "食品", 0.15,  0.180, 0.0315, 0.0315, 62.0),
    (6, "母婴", 0.10, -0.035, 0.0220, 0.0220, 95.0),
    (7, "运动", 0.09, -0.061, 0.0210, 0.0210, 145.0),
]

CHANNELS = [(1, "直播", 0.35), (2, "自然流量", 0.30), (3, "私域", 0.20), (4, "付费推广", 0.15)]

# 商品:(名称, 挂牌价, 权重, 是否问题商品)。美妆 3 款问题商品仅影响 last30 退款概率。
PRODUCTS = {
    1: [("水光焕颜精华液 30ml", 259, 0.5, 0),
        ("氨基酸温和洁面乳 120g", 89, 2.0, 0),
        ("雪域补水保湿面膜 25ml×10片", 99, 1.6, 0),
        ("轻雾持妆粉底液 30ml", 219, 0.6, 0),
        ("丝绒哑光口红 3.5g", 129, 1.4, 0),
        ("多肽紧致眼霜 20g", 269, 0.4, 0),
        ("温和舒缓卸妆水 500ml", 89, 1.8, 0),
        ("烟酰胺美白身体乳 250ml", 119, 1.2, 0),
        ("轻盈清爽防晒乳 SPF50+ 50ml", 149, 1.0, 0),
        ("山茶花滋养护发精油 80ml", 139, 0.9, 0),
        ("焕肤果酸焕新面膜 30ml", 168, 0.0, 1),   # 问题商品1
        ("水杨酸净颜精华液 30ml", 189, 0.0, 1),   # 问题商品2
        ("深海胶原睡眠面膜 100g", 158, 0.0, 1)],  # 问题商品3
    2: [("法式泡泡袖连衣裙", 299, 1.0, 0),
        ("高腰直筒牛仔裤", 259, 1.1, 0),
        ("真丝飘带衬衫", 399, 0.5, 0),
        ("针织开衫外套", 199, 1.2, 0),
        ("阔腿垂感西裤", 239, 0.9, 0),
        ("轻便白鸭绒羽绒服", 399, 0.5, 0),
        ("复古格纹半身裙", 189, 0.8, 0),
        ("圆领羊绒混纺毛衣", 329, 0.6, 0),
        ("撞色条纹棉质T恤", 129, 1.4, 0),
        ("荷叶边雪纺上衣", 159, 1.1, 0)],
    3: [("星耀Pro真无线耳机", 999, 0.9, 0),
        ("智能运动手表 S6", 1499, 0.5, 0),
        ("4K高清运动相机", 1899, 0.3, 0),
        ("便携蓝牙音箱", 599, 1.1, 0),
        ("机械键盘 87键", 699, 1.0, 0),
        ("27英寸4K显示器", 2299, 0.3, 0),
        ("移动固态硬盘 1TB", 799, 1.0, 0),
        ("全画幅微单相机", 2999, 0.2, 0),
        ("智能家居中控屏", 1299, 0.4, 0),
        ("电竞游戏鼠标", 549, 1.3, 0)],
    4: [("实木简约床头柜", 499, 0.8, 0),
        ("记忆棉弹簧床垫 1.5m", 799, 0.5, 0),
        ("北欧简约落地灯", 329, 1.0, 0),
        ("陶瓷餐具16件套", 299, 1.0, 0),
        ("静音破壁机", 559, 0.8, 0),
        ("全棉贡缎四件套", 259, 1.2, 0),
        ("智能扫拖机器人", 799, 0.6, 0),
        ("天然乳胶护颈枕", 199, 1.3, 0),
        ("折叠布艺收纳凳", 159, 1.1, 0),
        ("香薰蜡烛礼盒装", 179, 0.9, 0)],
    5: [("每日坚果混合装 30包", 79, 1.2, 0),
        ("手工黑糖话梅糖", 35, 1.0, 0),
        ("冷萃冻干咖啡 40条", 69, 1.3, 0),
        ("海盐苏打饼干", 32, 1.1, 0),
        ("麻辣小龙虾尾 250g×3", 78, 0.9, 0),
        ("有机纯牛奶 250ml×12", 62, 1.4, 0),
        ("蛋黄酥礼盒 6枚", 49, 1.0, 0),
        ("草莓冻干酸奶块", 39, 1.1, 0),
        ("五谷杂粮代餐粉", 59, 0.9, 0),
        ("柠檬蜂蜜茶 500g", 45, 0.8, 0)],
    6: [("婴儿纸尿裤 L码64片", 139, 1.3, 0),
        ("幼儿益生菌固体饮料", 128, 0.9, 0),
        ("婴儿有机棉连体衣", 99, 1.0, 0),
        ("儿童乳钙凝胶糖果", 89, 0.9, 0),
        ("婴幼儿配方奶粉 400g", 219, 0.8, 0),
        ("婴儿硅胶辅食碗勺套装", 79, 1.0, 0),
        ("儿童保温杯 450ml", 119, 0.9, 0),
        ("宝宝洗发沐浴二合一", 69, 0.8, 0),
        ("儿童涂鸦蜡笔 24色", 65, 0.7, 0),
        ("婴儿推车凉席垫", 89, 0.7, 0)],
    7: [("轻弹缓震跑鞋 男款", 399, 1.0, 0),
        ("速干训练T恤", 149, 1.2, 0),
        ("可调节哑铃 20kg", 459, 0.7, 0),
        ("加厚防滑瑜伽垫", 139, 1.1, 0),
        ("运动水壶 750ml", 129, 1.0, 0),
        ("弹力阻力带套装", 125, 0.9, 0),
        ("羽毛球拍对装", 299, 0.8, 0),
        ("智能计数跳绳", 159, 0.9, 0),
        ("户外三合一冲锋衣", 499, 0.6, 0),
        ("室内7号篮球", 179, 0.8, 0)],
}

# 每类目件数分布 (件数, 权重)
ITEM_CNT = {
    1: [(1, 72), (2, 18), (3, 8), (4, 2)],
    2: [(1, 75), (2, 18), (3, 6), (4, 1)],
    3: [(1, 93), (2, 6), (3, 1)],
    4: [(1, 80), (2, 14), (3, 5), (4, 1)],
    5: [(1, 45), (2, 30), (3, 17), (4, 8)],
    6: [(1, 68), (2, 22), (3, 8), (4, 2)],
    7: [(1, 78), (2, 15), (3, 5), (4, 2)],
}

PROBLEM_SHARE = 0.15      # 美妆 3 款问题商品合计订单份额(对应约 20% GMV 份额)
PROBLEM_P = 0.16          # 问题商品退款概率(契约 12%~20% 区间中段)
RATIO_LO, RATIO_HI = 0.70, 1.00   # 退款金额 = 付款金额 × 0.7~1.0


def largest_remainder(total, weights):
    """按最大余数法把 total 拆成整数份,和恰为 total。"""
    s = sum(weights)
    raw = [total * w / s for w in weights]
    base = [int(math.floor(x)) for x in raw]
    rest = total - sum(base)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in range(rest):
        base[order[i]] += 1
    return base


def waterfill(inits, los, his, target):
    """退款总额精确调到 target:refund_i = clamp(init_i×f, lo_i, hi_i),二分解 f,再分摊取整残差(单位:分)。"""
    n = len(inits)
    if n == 0:
        return []
    lo_sum, hi_sum = sum(los), sum(his)
    tgt = int(min(max(target, lo_sum), hi_sum))

    def total_at(f):
        return sum(min(max(x * f, lo), hi) for x, lo, hi in zip(inits, los, his))

    f_lo, f_hi = 0.0, 10.0
    for _ in range(80):
        mid = (f_lo + f_hi) / 2
        if total_at(mid) < tgt:
            f_lo = mid
        else:
            f_hi = mid
    f = (f_lo + f_hi) / 2
    out = [int(round(min(max(x * f, lo), hi))) for x, lo, hi in zip(inits, los, his)]
    resid = tgt - sum(out)
    i = 0
    while resid != 0 and i <= 10 * n + 10:
        idx = i % n
        if resid > 0 and out[idx] < his[idx]:
            step = min(resid, his[idx] - out[idx])
            out[idx] += step
            resid -= step
        elif resid < 0 and out[idx] > los[idx]:
            step = min(-resid, out[idx] - los[idx])
            out[idx] -= step
            resid += step
        i += 1
    return out


def cents_str(c):
    return f"{c // 100}.{c % 100:02d}"


def insert_batches(cur, sql, rows, bs=BATCH):
    for i in range(0, len(rows), bs):
        cur.executemany(sql, rows[i:i + bs])


def main():
    t0 = time.time()
    random.seed(SEED)
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    # ---------- 1. 日期与季节性 ----------
    days = [START + timedelta(days=i) for i in range((END - START).days + 1)]  # 90 天
    assert len(days) == 90
    s = []
    for d in days:
        base = 1.2 if d.isoweekday() >= 6 else 1.0     # 周末 GMV ×1.2
        s.append(base * random.uniform(0.9, 1.1))      # 日常 ±10% 噪声
    n_day = largest_remainder(TOTAL_ORDERS, s)         # 每日订单数,总和恰为 207000
    t_day = [ORDER_AOV * n for n in n_day]             # 每日 GMV 目标(元)

    def window_of(d):
        if LAST30[0] <= d <= LAST30[1]:
            return "last"
        if PRIOR30[0] <= d <= PRIOR30[1]:
            return "prior"
        return "early"

    win_idx = [window_of(d) for d in days]
    g_prior = sum(t for t, w in zip(t_day, win_idx) if w == "prior")
    g_last = sum(t for t, w in zip(t_day, win_idx) if w == "last")

    # ---------- 2. (日×类目) GMV 目标(精确命中 delta) ----------
    t_cd = [[0.0] * 8 for _ in days]     # [day_idx][cat_id]
    for cid, name, w, delta, rp, rl, aov in CATS:
        m_last = (1.0 + delta) * g_prior / g_last      # 反解乘子 → 窗口比值恰为 1+delta
        for di, d in enumerate(days):
            m = m_last if win_idx[di] == "last" else 1.0
            t_cd[di][cid] = t_day[di] * w * m
    g_cat = [0.0] * 8
    for di in range(len(days)):
        for cid in range(1, 8):
            g_cat[cid] += t_cd[di][cid]

    # ---------- 3. 每组订单数(笔均 aov 标定,使总单数≈207000) ----------
    base_aov = {c[0]: c[6] for c in CATS}
    lam = sum(g_cat[cid] / base_aov[cid] for cid in range(1, 8)) / TOTAL_ORDERS
    n_cd = [[0] * 8 for _ in days]
    for cid in range(1, 8):
        aov_f = base_aov[cid] * lam
        for di in range(len(days)):
            n_cd[di][cid] = max(1, int(round(t_cd[di][cid] / aov_f)))

    # ---------- 4. 商品/渠道/件数准备 ----------
    prod_info, prob_by_pid = {}, {}
    pid_base = 0
    for cid in range(1, 8):
        items = PRODUCTS[cid]
        w_norm = sum(x[2] for x in items if not x[3])
        n_prob = sum(1 for x in items if x[3])
        w_prob_each = (PROBLEM_SHARE / (1 - PROBLEM_SHARE)) * w_norm / max(1, n_prob)
        pids, prices, weights, probs = [], [], [], []
        for name, price, w, flag in items:
            pid = pid_base + len(pids) + 1
            pids.append(pid)
            prices.append(float(price))
            probs.append(bool(flag))
            prob_by_pid[pid] = bool(flag)
            weights.append(w_prob_each if flag else w)
        prod_info[cid] = (pids, prices, weights, probs)
        pid_base += len(pids)
    n_products = pid_base
    chan_ids = [c[0] for c in CHANNELS]
    chan_w = [c[2] for c in CHANNELS]

    # ---------- 5. 逐日逐类目生成订单(组内金额缩放到分) ----------
    orders = []   # [order_id, date, product_id, cat_id, chan_id, user_id, user_type, item_cnt, pay_cents]
    user_pool = list(range(1, NUM_USERS + 1))
    random.shuffle(user_pool)
    ptr = 0
    activated_before = []    # 严格早于当日的已激活用户(仅这些用户可复购)
    first_pay = {}           # user_id -> 表内首次付款日
    oid = 1
    for di, d in enumerate(days):
        new_today = []
        for cid in range(1, 8):
            n = n_cd[di][cid]
            k_new = n if di == 0 else int(round(n * NEW_RATIO))
            k_rep = n - k_new
            assert ptr + k_new <= NUM_USERS, "用户池不足,请调低 NEW_RATIO"
            new_users = user_pool[ptr:ptr + k_new]
            ptr += k_new
            for uid in new_users:
                first_pay[uid] = d
            new_today.extend(new_users)
            reps = [random.choice(activated_before) for _ in range(k_rep)] if activated_before else []
            pids, prices, pweights, probs = prod_info[cid]
            idxs = random.choices(range(len(pids)), weights=pweights, k=n)
            cnts = random.choices([c for c, _ in ITEM_CNT[cid]],
                                  weights=[w for _, w in ITEM_CNT[cid]], k=n)
            chans = random.choices(chan_ids, weights=chan_w, k=n)
            users_g = new_users + reps
            raws = [prices[i] * c for i, c in zip(idxs, cnts)]
            target_cents = int(round(t_cd[di][cid] * 100))
            factor = target_cents / (sum(raws) * 100.0)
            pay_cents = [int(round(r * factor * 100)) for r in raws]
            drift = target_cents - sum(pay_cents)
            pay_cents[-1] = max(1, pay_cents[-1] + drift)
            for j in range(n):
                orders.append([oid, d, pids[idxs[j]], cid, chans[j], users_g[j],
                               "新客" if j < k_new else "老客", cnts[j], pay_cents[j]])
                oid += 1
        activated_before.extend(new_today)

    print(f"[seed] orders generated: {len(orders)} rows, users activated: {ptr}, "
          f"elapsed {time.time() - t0:.1f}s")

    # ---------- 6. 退款:按(类目×窗口)配额精确控制费率 ----------
    # 每组(类目×窗口)按 目标退款额=费率×GMV 反推选中订单数(随机配额+修补,保证
    # 选中付款总额落在 [1.02,1.40]×目标,waterfill 必有解且单笔退款/付款 ∈ [0.7,1.0]);
    # 美妆 last30 的退款增量由 3 款问题商品承担(每款退款概率≈PROBLEM_P,契约 12%~20%)。
    pools = {}
    for idx, o in enumerate(orders):
        pools.setdefault((o[3], window_of(o[1])), []).append(idx)

    rate_map = {c[0]: {"prior": c[4], "last": c[5]} for c in CATS}
    n_refund = 0

    def pick_quota(idxs, target_cents):
        """从 idxs 随机抽 q 笔并修补,使选中付款和 ∈ [1.02,1.40]×target_cents。"""
        pays = {i: orders[i][8] for i in idxs}
        mean_pay = sum(pays.values()) / len(idxs)
        q = int(round(target_cents / (0.85 * mean_pay)))
        q = max(1, min(q, len(idxs)))
        sel = set(random.sample(list(idxs), q))
        s = sum(pays[i] for i in sel)
        rest = sorted((i for i in idxs if i not in sel), key=lambda i: pays[i], reverse=True)
        ri = 0
        while s < int(1.02 * target_cents) and ri < len(rest):      # 太小 → 补最大
            sel.add(rest[ri]); s += pays[rest[ri]]; ri += 1
        sel_asc = sorted(sel, key=lambda i: pays[i])
        si = 0
        while s > int(1.40 * target_cents) and si < len(sel_asc) and len(sel) > 1:  # 太大 → 剔最小
            sel.discard(sel_asc[si]); s -= pays[sel_asc[si]]; si += 1
        return sorted(sel)

    for (cid, w), idxs in pools.items():
        rate = rate_map[cid][w] if w in ("prior", "last") else rate_map[cid]["prior"]
        pool_gmv = sum(orders[i][8] for i in idxs)
        target = int(round(rate * pool_gmv))
        beauty_last = (cid == 1 and w == "last")
        if beauty_last:
            by_pid = {}
            for i in idxs:
                by_pid.setdefault(orders[i][2], []).append(i)
            prob_pids = [pid for pid in by_pid if prob_by_pid[pid]]
            prob_pay_total = sum(orders[i][8] for pid in prob_pids for i in by_pid[pid])
            r_prob_total = min(int(round(PROBLEM_P * 0.85 * prob_pay_total)), int(0.9 * target))
            groups = []
            for pid in prob_pids:   # 每款问题商品按付款额占比分摊 → 每款退款概率都≈PROBLEM_P
                g_idxs = by_pid[pid]
                g_pay = sum(orders[i][8] for i in g_idxs)
                g_target = int(round(r_prob_total * g_pay / prob_pay_total)) if prob_pay_total else 0
                groups.append((g_idxs, g_target))
            rest_idxs = [i for i in idxs if not prob_by_pid[orders[i][2]]]
            groups.append((rest_idxs, target - r_prob_total))
        else:
            groups = [(idxs, target)]
        for g_idxs, g_target in groups:
            if g_target <= 0 or not g_idxs:
                continue
            sel = pick_quota(g_idxs, g_target)
            inits = [orders[i][8] for i in sel]
            los = [(7 * p + 9) // 10 for p in inits]   # ceil(0.7×pay),单位分,整数精确
            his = list(inits)
            refunds = waterfill(inits, los, his, g_target)
            for i, r in zip(sel, refunds):
                if r > 0:
                    orders[i].extend([r, 1])   # refund_cents, is_refund
                    n_refund += 1
    for o in orders:
        if len(o) == 9:
            o.extend([0, 0])
    print(f"[seed] refunds: {n_refund} orders, elapsed {time.time() - t0:.1f}s")

    # ---------- 7. 注册日期 ----------
    reg_min = date(2026, 3, 1)
    dim_user_rows = []
    for uid in range(1, NUM_USERS + 1):
        if uid in first_pay:
            reg = max(reg_min, first_pay[uid] - timedelta(days=random.randint(0, 14)))
        else:
            reg = reg_min + timedelta(days=random.randint(0, (END - reg_min).days))
        dim_user_rows.append((uid, reg.isoformat()))

    # ---------- 8. 入库 ----------
    t1 = time.time()
    cur.execute("SET SESSION sql_mode=''")
    insert_batches(cur, "INSERT INTO dim_date (d, dow, is_weekend, month) VALUES (%s,%s,%s,%s)",
                   [(d.isoformat(), d.isoweekday(), 1 if d.isoweekday() >= 6 else 0,
                     f"{d.year}-{d.month:02d}") for d in days])
    insert_batches(cur, "INSERT INTO dim_category (category_id, category_name, sort) VALUES (%s,%s,%s)",
                   [(c[0], c[1], i + 1) for i, c in enumerate(CATS)])
    insert_batches(cur, "INSERT INTO dim_channel (channel_id, channel_name) VALUES (%s,%s)",
                   [(c[0], c[1]) for c in CHANNELS])
    prod_rows = []
    pid = 0
    for cid in range(1, 8):
        for name, price, _w, _f in PRODUCTS[cid]:
            pid += 1
            prod_rows.append((pid, name, cid, f"{price:.2f}"))
    insert_batches(cur, "INSERT INTO dim_product (product_id, product_name, category_id, price) VALUES (%s,%s,%s,%s)",
                   prod_rows)
    insert_batches(cur, "INSERT INTO dim_user (user_id, reg_date) VALUES (%s,%s)", dim_user_rows)
    fact_rows = [(o[0], o[1].isoformat(), o[2], o[3], o[4], o[5], o[6], o[7],
                  cents_str(o[8]), cents_str(o[9]), o[10]) for o in orders]
    insert_batches(cur,
                   "INSERT INTO fact_order (order_id, pay_date, product_id, category_id, channel_id, "
                   "user_id, user_type, item_cnt, pay_amount, refund_amount, is_refund) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", fact_rows)
    conn.commit()
    print(f"[seed] fact rows inserted: {len(fact_rows)}, elapsed {time.time() - t1:.1f}s")

    # ---------- 9. semantic_version / sys_settings ----------
    versions = [
        (10, "2026-09-12", "数据小组", "初版语义层:定义 GMV、支付订单数、支付人数、客单价、支付件数、退款金额等核心指标口径"),
        (11, "2026-10-18", "数据小组", "退款率口径说明补充:分子含『仅退款』与『退货退款』两部分,分母为同范围付款金额(GMV)"),
        (12, "2026-11-06", "数据小组", "当前版本:比率类指标分母统一为同范围 GMV;新增订单退款率、新客订单占比指标"),
    ]
    snapshots = {
        10: "# semantic metrics snapshot(占位快照)\nversion: 10\nmetrics:\n  gmv: {agg: 'SUM(f.pay_amount)'}\n  order_cnt: {agg: 'COUNT(DISTINCT f.order_id)'}\n  refund_amt: {agg: 'SUM(f.refund_amount)'}\n",
        11: "# semantic metrics snapshot(占位快照)\nversion: 11\nnotes: 退款率分子含仅退款与退货退款,分母为同范围 GMV\nmetrics:\n  refund_rate: {agg: 'SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0)'}\n",
        12: "# semantic metrics snapshot(占位快照)\nversion: 12\nnotes: 比率类指标分母统一为同范围 GMV\nmetrics:\n  refund_rate: {agg: 'SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0)'}\n  new_order_ratio: {agg: \"SUM(f.user_type='新客')/NULLIF(COUNT(DISTINCT f.order_id),0)\"}\n",
    }
    insert_batches(cur, "INSERT INTO semantic_version (version, released, author, change_desc, yaml_snapshot) VALUES (%s,%s,%s,%s,%s)",
                   [(v, r, a, d, snapshots[v]) for v, r, a, d in versions])
    settings = [
        ("llm_base_url", "https://open.bigmodel.cn/api/paas/v4"),
        ("llm_model", "glm-4-flash"),
        ("llm_api_key", ""),
    ]
    insert_batches(cur, "INSERT INTO sys_settings (skey, svalue) VALUES (%s,%s)", settings)

    # ---------- 10. golden_qa(ref_sql 实时计算 ref_value,保证与数据自洽) ----------
    L0, L1 = "2026-10-08", "2026-11-06"     # last30
    P0, P1 = "2026-09-08", "2026-10-07"     # prior30
    Y0 = "2026-11-06"                        # yesterday
    W0, W1 = "2026-10-31", "2026-11-06"      # last_7d
    M0, M1 = "2026-10-01", "2026-10-31"      # last_month(2026-10)

    def scalar(sql):
        cur.execute(sql)
        row = cur.fetchone()
        return None if row is None or row[0] is None else float(row[0])

    golden = [
        ("最近30天GMV是多少?", "value", "gmv", None,
         f"SELECT ROUND(SUM(f.pay_amount),2) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'", 2.0, "核心"),
        ("昨天的GMV是多少?", "value", "gmv", None,
         f"SELECT ROUND(SUM(f.pay_amount),2) FROM fact_order f WHERE f.pay_date BETWEEN '{Y0}' AND '{Y0}'", 2.0, "核心"),
        ("2026年10月美妆类目的GMV是多少?", "value", "gmv", '["category=美妆"]',
         f"SELECT ROUND(SUM(f.pay_amount),2) FROM fact_order f JOIN dim_category dc ON dc.category_id=f.category_id "
         f"WHERE dc.category_name='美妆' AND f.pay_date BETWEEN '{M0}' AND '{M1}'", 2.0, "核心"),
        ("上个月总共有多少笔支付订单?", "value", "order_cnt", None,
         f"SELECT COUNT(DISTINCT f.order_id) FROM fact_order f WHERE f.pay_date BETWEEN '{M0}' AND '{M1}'", 2.0, "核心"),
        ("最近30天有多少个下单支付的用户?", "value", "pay_user_cnt", None,
         f"SELECT COUNT(DISTINCT f.user_id) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'", 2.0, "核心"),
        ("最近30天的客单价是多少?", "value", "arppu", None,
         f"SELECT ROUND(SUM(f.pay_amount)/NULLIF(COUNT(DISTINCT f.user_id),0),2) FROM fact_order f "
         f"WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'", 2.0, "核心"),
        ("最近30天退款了多少钱?", "value", "refund_amt", None,
         f"SELECT ROUND(SUM(f.refund_amount),2) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'", 2.0, "核心"),
        ("最近30天整体退款率是多少?", "value", "refund_rate", None,
         f"SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f "
         f"WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'", 2.0, "核心"),
        ("昨天直播渠道卖了多少?", "value", "gmv", '["channel=直播"]',
         f"SELECT ROUND(SUM(f.pay_amount),2) FROM fact_order f JOIN dim_channel ch ON ch.channel_id=f.channel_id "
         f"WHERE ch.channel_name='直播' AND f.pay_date BETWEEN '{Y0}' AND '{Y0}'", 2.0, "核心"),
        ("最近7天自然流量渠道的GMV是多少?", "value", "gmv", '["channel=自然流量"]',
         f"SELECT ROUND(SUM(f.pay_amount),2) FROM fact_order f JOIN dim_channel ch ON ch.channel_id=f.channel_id "
         f"WHERE ch.channel_name='自然流量' AND f.pay_date BETWEEN '{W0}' AND '{W1}'", 2.0, "核心"),
        ("最近30天GMV走势怎么样?", "trend", "gmv", '["date"]',
         f"SELECT COUNT(*) FROM (SELECT f.pay_date FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' "
         f"GROUP BY f.pay_date) t", 0.0, "核心"),
        ("最近7天退款金额的走势?", "trend", "refund_amt", '["date"]',
         f"SELECT COUNT(*) FROM (SELECT f.pay_date FROM fact_order f WHERE f.pay_date BETWEEN '{W0}' AND '{W1}' "
         f"GROUP BY f.pay_date) t", 0.0, "核心"),
        ("最近30天哪个类目GMV最高?", "rank", "gmv", '["category"]',
         f"SELECT ROUND(SUM(f.pay_amount),2) AS v FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' "
         f"GROUP BY f.category_id ORDER BY v DESC LIMIT 1", 2.0, "核心"),
        ("最近30天哪个类目退款率最高?", "rank", "refund_rate", '["category"]',
         f"SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) AS v FROM fact_order f "
         f"WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' GROUP BY f.category_id "
         f"ORDER BY SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) DESC LIMIT 1", 2.0, "核心"),
        ("对比一下最近30天新客和老客的客单价", "compare", "arppu", '["user_type=老客"]',
         f"SELECT ROUND(SUM(f.pay_amount)/NULLIF(COUNT(DISTINCT f.user_id),0),2) FROM fact_order f "
         f"WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' AND f.user_type='老客'", 2.0, "核心"),
        ("最近30天新客和老客分别下了多少单?", "compare", "order_cnt", '["user_type=新客"]',
         f"SELECT COUNT(DISTINCT f.order_id) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' "
         f"AND f.user_type='新客'", 2.0, "核心"),
        ("最近30天退款率为什么上升?", "attribution", "refund_rate", None,
         f"SELECT (SELECT SUM(fa.refund_amount) FROM fact_order fa JOIN dim_category dc ON dc.category_id=fa.category_id "
         f"WHERE fa.pay_date BETWEEN '{L0}' AND '{L1}' AND dc.category_name='美妆')/NULLIF((SELECT SUM(fa.pay_amount) "
         f"FROM fact_order fa WHERE fa.pay_date BETWEEN '{L0}' AND '{L1}'),0) - "
         f"(SELECT SUM(fb.refund_amount) FROM fact_order fb JOIN dim_category db ON db.category_id=fb.category_id "
         f"WHERE fb.pay_date BETWEEN '{P0}' AND '{P1}' AND db.category_name='美妆')/NULLIF((SELECT SUM(fb.pay_amount) "
         f"FROM fact_order fb WHERE fb.pay_date BETWEEN '{P0}' AND '{P1}'),0)", 5.0, "核心"),
        ("美妆最近30天的退款率比之前30天高了多少?", "attribution", "refund_rate", '["category=美妆"]',
         f"SELECT (SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f JOIN dim_category dc "
         f"ON dc.category_id=f.category_id WHERE dc.category_name='美妆' AND f.pay_date BETWEEN '{L0}' AND '{L1}') - "
         f"(SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f JOIN dim_category dc "
         f"ON dc.category_id=f.category_id WHERE dc.category_name='美妆' AND f.pay_date BETWEEN '{P0}' AND '{P1}')",
         5.0, "核心"),
        ("销售额怎么样", "clarify", None, None, None, 0.0, "兜底"),
        ("员工平均工资是多少", "refuse", None, None, None, 0.0, "兜底"),
        ("帮我看下具体订单明细", "boundary", None, None, None, 0.0, "边界"),
    ]
    golden_rows = []
    for q, kind, metric, dims, sql, tol, tag in golden:
        rv = scalar(sql) if sql else None
        golden_rows.append((q, kind, metric, dims, sql, rv, tol, tag))
    insert_batches(cur,
                   "INSERT INTO golden_qa (question, kind, expect_metric, expect_dims, ref_sql, ref_value, tolerance_pct, tag) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", golden_rows)

    # ---------- 11. audit_log(7 条演示记录,数字取自真实查询) ----------
    g_last_a = scalar(f"SELECT SUM(f.pay_amount) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'")
    g_prior_a = scalar(f"SELECT SUM(f.pay_amount) FROM fact_order f WHERE f.pay_date BETWEEN '{P0}' AND '{P1}'")
    r_last_a = scalar(f"SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'")
    r_prior_a = scalar(f"SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f WHERE f.pay_date BETWEEN '{P0}' AND '{P1}'")
    contrib_b = scalar(golden[16][4])
    delta_g = (g_last_a - g_prior_a) / g_prior_a

    def wan(x):
        return f"{x / 10000:,.1f}万元"

    ir_summary = {"metric": "gmv", "time": {"preset": "last_30d"}, "dims": [],
                  "filters": {"category": [], "channel": [], "user_type": []},
                  "granularity": "none", "compare": "prev_period", "order": "desc", "limit": 10}
    ir_attr = {"metric": "refund_rate", "time": {"preset": "last_30d"}, "dims": ["category"],
               "filters": {"category": [], "channel": [], "user_type": []},
               "granularity": "none", "compare": "prev_period", "order": "desc", "limit": 10}
    ir_trend = {"metric": "gmv", "time": {"preset": "last_30d"}, "dims": ["date"],
                "filters": {"category": ["美妆"], "channel": [], "user_type": []},
                "granularity": "day", "compare": "none", "order": "asc", "limit": 30}
    ir_rank = {"metric": "gmv", "time": {"preset": "last_30d"}, "dims": ["category"],
               "filters": {"category": [], "channel": [], "user_type": []},
               "granularity": "none", "compare": "none", "order": "desc", "limit": 10}

    cur.execute(f"SELECT dc.category_name, ROUND(SUM(f.pay_amount),2) v FROM fact_order f "
                f"JOIN dim_category dc ON dc.category_id=f.category_id WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' "
                f"GROUP BY dc.category_name ORDER BY v DESC LIMIT 3")
    top3 = cur.fetchall()
    cur.execute(f"SELECT f.pay_date, ROUND(SUM(f.pay_amount),2) FROM fact_order f JOIN dim_category dc "
                f"ON dc.category_id=f.category_id WHERE dc.category_name='美妆' AND f.pay_date BETWEEN '{W0}' AND '{W1}' "
                f"GROUP BY f.pay_date ORDER BY f.pay_date")
    beauty_series = [[r[0].isoformat(), float(r[1])] for r in cur.fetchall()]
    cat_rates = {}
    for c in CATS:
        cat_rates[c[1]] = scalar(
            f"SELECT SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f "
            f"WHERE f.category_id={c[0]} AND f.pay_date BETWEEN '{L0}' AND '{L1}'")

    def stages(u=140, p=40, c=15, e=90, v=25, n=60):
        d = {"understand": u, "plan": p, "compile": c, "execute": e, "verify": v, "narrate": n}
        return json.dumps(d), sum(d.values())

    sess1 = "a3f8c2e4-9d17-4b6e-8f52-3c1a90d4e7b2"
    sess2 = "5f20b8c1-2e64-4a93-b7d0-9c8e12f6a5d4"
    sess3 = "c94d1a7e-83b5-4f20-a6e1-7d2b9c4f8e30"

    audits = [
        # trace_id, ts, session, user, question, intent, metric, ir, sql, row_count, result, narrative, status
        ("tr_3f8a21c9", "2026-11-06 10:12:31", sess1, "ceo", "最近30天的GMV是多少?比上一个30天呢",
         "metric_query", "gmv", json.dumps(ir_summary, ensure_ascii=False),
         f"SELECT SUM(f.pay_amount) FROM fact_order f WHERE f.pay_date BETWEEN '{L0}' AND '{L1}'",
         1,
         json.dumps({"gmv": g_last_a, "gmv_prev": g_prior_a, "delta_pct": round(delta_g * 100, 2)},
                    ensure_ascii=False),
         json.dumps([f"近30天({L0}~{L1})GMV {wan(g_last_a)}。",
                     f"较上一个30天({wan(g_prior_a)}){'增长' if delta_g >= 0 else '下降'} {abs(delta_g) * 100:.1f}%。"],
                    ensure_ascii=False),
         "ok"),
        ("tr_7c1d94ef", "2026-11-06 11:05:12", sess1, "ceo", "退款率为什么上升了?",
         "attribution", "refund_rate", json.dumps(ir_attr, ensure_ascii=False),
         f"SELECT f.category_id, SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0) FROM fact_order f "
         f"WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' GROUP BY f.category_id",
         7,
         json.dumps({"rate_last": round(r_last_a * 100, 2), "rate_prev": round(r_prior_a * 100, 2),
                     "contrib_beauty_pp": round(contrib_b * 100, 2),
                     "beauty_rate_last": round(cat_rates["美妆"] * 100, 2)}, ensure_ascii=False),
         json.dumps([f"近30天整体退款率 {r_last_a * 100:.2f}%,较上一个30天({r_prior_a * 100:.2f}%)上升 "
                     f"{(r_last_a - r_prior_a) * 100:.2f}pp。",
                     f"上升主要由美妆类目贡献 +{contrib_b * 100:.2f}pp:美妆近30天退款率 "
                     f"{cat_rates['美妆'] * 100:.2f}%,明显高于其他类目,建议业务侧进一步确认。"],
                    ensure_ascii=False),
         "ok"),
        ("tr_52e6b8a1", "2026-11-06 14:22:40", sess2, "beauty", "最近30天美妆的GMV走势",
         "metric_query", "gmv", json.dumps(ir_trend, ensure_ascii=False),
         f"SELECT f.pay_date, SUM(f.pay_amount) FROM fact_order f JOIN dim_category dc ON dc.category_id=f.category_id "
         f"WHERE dc.category_name='美妆' AND f.pay_date BETWEEN '{L0}' AND '{L1}' GROUP BY f.pay_date ORDER BY f.pay_date",
         30,
         json.dumps({"series_recent": beauty_series}, ensure_ascii=False),
         json.dumps([f"已按行级权限仅统计美妆类目。近7天美妆日GMV在 {wan(min(x[1] for x in beauty_series))} ~ "
                     f"{wan(max(x[1] for x in beauty_series))} 之间波动。"], ensure_ascii=False),
         "ok"),
        ("tr_9b4d06f3", "2026-11-05 16:40:18", sess1, "ceo", "哪个类目卖得最好?",
         "rank", "gmv", json.dumps(ir_rank, ensure_ascii=False),
         f"SELECT dc.category_name, SUM(f.pay_amount) v FROM fact_order f JOIN dim_category dc "
         f"ON dc.category_id=f.category_id WHERE f.pay_date BETWEEN '{L0}' AND '{L1}' GROUP BY dc.category_name "
         f"ORDER BY v DESC",
         7,
         json.dumps({"top3": [[r[0], float(r[1])] for r in top3]}, ensure_ascii=False),
         json.dumps([f"近30天GMV最高的是{top3[0][0]}({wan(float(top3[0][1]))}),其次是{top3[1][0]}"
                     f"({wan(float(top3[1][1]))})、{top3[2][0]}({wan(float(top3[2][1]))})。"],
                    ensure_ascii=False),
         "ok"),
        ("tr_1a2b3c4d", "2026-11-05 09:31:55", sess2, "ceo", "销售额怎么样",
         "clarify", None, None, None, 0, None,
         json.dumps(["「销售额」可以按不同口径理解:GMV(用户付款总额,含后续退款部分)或实收金额。",
                     "未指定时间时,我会按最近30天、GMV 口径理解,也可在选项中切换。"], ensure_ascii=False),
         "clarify"),
        ("tr_6e0f85d2", "2026-11-05 15:02:47", sess1, "ceo", "查一下员工平均工资",
         "refuse", None, None, None, 0, None,
         json.dumps(["抱歉,员工薪酬属于敏感个人信息,不在本系统的查询范围内。",
                     "您可以试试问我 GMV、退款率、客单价、新老客对比等经营指标。"], ensure_ascii=False),
         "refuse"),
        ("tr_4d93e7b8", "2026-11-06 17:26:03", sess3, "ceo", "导出一下最近的订单明细",
         "boundary", None, None, None, 0, None,
         json.dumps(["商品级/订单级明细在当前版本(v1)暂未开放。",
                     "您可以试试「最近30天各渠道GMV」「美妆类目退款率走势」等汇总类问法。"], ensure_ascii=False),
         "boundary"),
    ]
    audit_rows = []
    for tid, ts, sess, user, q, intent, metric, ir, sql, rc, result, narr, status in audits:
        st, lat = stages()
        audit_rows.append((tid, ts, sess, user, q, intent, metric, ir, sql, rc, result, narr,
                           0, "rule-fallback", lat, st, status))
    insert_batches(cur,
                   "INSERT INTO audit_log (trace_id, ts, session_id, demo_user, question, intent, metric_key, "
                   "ir_json, sql_text, row_count, result_json, narrative, llm_used, llm_model, latency_ms, "
                   "stage_timings, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   audit_rows)

    # ---------- 12. feedback_log(3 条演示) ----------
    feedback = [
        ("2026-11-06 10:13:02", "tr_3f8a21c9", sess1, "ceo", "up", '["数字可信"]', None),
        ("2026-11-06 11:07:45", "tr_7c1d94ef", sess1, "ceo", "up", '["归因清晰","口径透明"]', None),
        ("2026-11-05 15:04:11", "tr_6e0f85d2", sess1, "ceo", "down", '["答非所问"]', "其实我是想看人力成本口径,不过拒答提示还算清楚"),
    ]
    insert_batches(cur, "INSERT INTO feedback_log (ts, trace_id, session_id, demo_user, rating, tags, comment) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)", feedback)

    conn.commit()

    # ---------- 13. 摘要 ----------
    def one(sql):
        cur.execute(sql)
        return cur.fetchone()[0]

    print("[seed] row counts:",
          "dim_date", one("SELECT COUNT(*) FROM dim_date"),
          "| dim_category", one("SELECT COUNT(*) FROM dim_category"),
          "| dim_channel", one("SELECT COUNT(*) FROM dim_channel"),
          "| dim_product", one(f"SELECT COUNT(*) FROM dim_product"),
          "| dim_user", one("SELECT COUNT(*) FROM dim_user"),
          "| fact_order", one("SELECT COUNT(*) FROM fact_order"),
          "| golden_qa", one("SELECT COUNT(*) FROM golden_qa"),
          "| audit_log", one("SELECT COUNT(*) FROM audit_log"))
    cur.close()
    conn.close()
    print(f"[seed] done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
