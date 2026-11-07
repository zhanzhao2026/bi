"""规则兜底意图解析(契约 §9,无 LLM 时必须可用)。

关键词命中表 + 口语中文解析,输出结构化 intent:
  domain / metric / card / dim / time_preset / time_range / filters / vague 等。
覆盖:退款(走势/归因/默认)、GMV·销售(走势/排名/渠道/默认)、客单价、支付人数、
订单数、退款金额、支付件数、新老客对比、时间词(昨天/最近7天/最近30天/上月/本月/
具体月份"2026年10月"、"今年11月")、类目词、渠道词。
"""
import datetime as dt
import re

# ---------- 时间词(顺序敏感:具体优先) ----------
TIME_PATTERNS: list[tuple[str, str]] = [
    (r"上个月|上月", "last_month"),
    (r"本月|这个月|当月", "this_month"),
    (r"昨天|昨日", "yesterday"),
    (r"最近7天|近7天|过去7天|最近一周|近一周|过去一周|上周|近1周", "last_7d"),
    (r"最近30天|近30天|过去30天|最近一个月|近一个月|过去一个月|近1个月|近30日|近一个月内", "last_30d"),
]

MONTH_FULL_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
MONTH_BARE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月(?!\d)")

CHANNEL_ALIASES = {"付费": "付费推广", "推广": "付费推广", "投流": "付费推广"}

# ---------- 指标词(顺序敏感:先具体后宽泛) ----------
# (pattern, metric_key, flags)
METRIC_PATTERNS: list[tuple[str, str, int]] = [
    (r"订单退款率", "refund_order_rate", 0),
    (r"退款率", "refund_rate", 0),
    (r"退款金额|退款额|退了多少|退款规模|退款了?多少钱|退款情况", "refund_amt", 0),
    (r"客单价|单均|每单金额", "arppu", 0),
    (r"支付人数|付款人数|购买人数|下单人数|消费人数|多少人来买|多少人买"
     r"|多少[个来人].{0,8}用户|支付用户|下单用户|用户数|买家人数", "pay_user_cnt", 0),
    (r"新客订单占比|新客占比|新客比例", "new_order_ratio", 0),
    (r"订单数|订单量|单量|多少单|几单|多少笔|笔订单|多少个订单", "order_cnt", 0),
    (r"件数|销量|卖了多少件", "item_cnt", 0),
    (r"成交额|成交金额|交易额|流水|成交规模|营业额|销售情况|销售", "gmv", 0),
    (r"gmv", "gmv", re.IGNORECASE),
    (r"arppu", "arppu", re.IGNORECASE),
]

VAGUE_METRIC_RE = re.compile(r"销售额|卖得怎么样|卖得如何|业绩|生意怎么样|做得怎么样")
COMPARE_RE = re.compile(r"新老客|新客和老客|老客和新客|新老对比|新客老客")
REFUND_RE = re.compile(r"退款|退货|退了")
TREND_RE = re.compile(r"走势|趋势|按天|每天|每日|曲线|波动")
RANK_RE = re.compile(r"排名|排行|top|贡献|结构|分布|占比|哪些|哪一个|哪个|各个|各类|每类|各渠道")
ATTRIBUTION_RE = re.compile(
    r"为什么|原因|归因|驱动|上升|上涨|升高|提高|下滑|下降|下跌|恶化|变差|变高|涨了|跌了"
    r"|高了多少|低了多少|比之前|比上个|较之前|相比之前|变化")
COMPARE_REQ_RE = re.compile(r"对比|比较|较上|相比|同期|上一期|上期|环比|同比")
# 追问式问句(无指标无筛选词,需挂靠上一轮继承指标)
FOLLOWUP_RE = re.compile(r"较上一期|上一期|变化多少|对比|比较|环比|同比|那.*呢|表现呢|情况呢")
# 经营概况类口语问法 → overview 卡(四 KPI:GMV/退款率/客单价/新客订单占比)
OVERVIEW_RE = re.compile(
    r"整体经营|经营情况|经营概况|经营怎么样|大盘|整体情况|整体概况|整体怎么样"
    r"|总体情况|总体概况|最近怎么样|最近表现|整体表现")

# compare 卡允许承载的指标(新老客对比口径下有业务含义的)
COMPARE_METRICS = {"order_cnt", "gmv", "arppu", "pay_user_cnt", "item_cnt",
                   "new_order_ratio", "refund_rate", "refund_amt"}


def month_range(year: int, month: int) -> list[str]:
    start = dt.date(year, month, 1)
    end = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return [start.isoformat(), end.isoformat()]


def parse_time(question: str) -> str | None:
    q = question or ""
    for pat, preset in TIME_PATTERNS:
        if re.search(pat, q):
            return preset
    return None


def parse_time_range(question: str, asof=None) -> list[str] | None:
    """解析具体月份:"2026年10月" / "10月" / "今年11月" → [月初, 月末]。"""
    q = question or ""
    m = MONTH_FULL_RE.search(q)
    if m and 1 <= int(m.group(2)) <= 12:
        return month_range(int(m.group(1)), int(m.group(2)))
    m = MONTH_BARE_RE.search(q)
    if m and 1 <= int(m.group(1)) <= 12:
        year = asof.year if asof is not None else 2026
        return month_range(year, int(m.group(1)))
    return None


def parse_filters(question: str, options: dict) -> dict:
    """解析类目/渠道/用户类型筛选词(基于 dim 表真实取值 + 渠道别名)。"""
    q = question or ""
    filters = {"category": [], "channel": [], "user_type": []}
    for name in options.get("category", []):
        if name in q:
            filters["category"].append(name)
    for name in options.get("channel", []):
        if name in q:
            filters["channel"].append(name)
    for alias, name in CHANNEL_ALIASES.items():
        if alias in q and name not in filters["channel"]:
            filters["channel"].append(name)
    if not COMPARE_RE.search(q):
        if "新客" in q and "老客" not in q:
            filters["user_type"].append("新客")
        elif "老客" in q and "新客" not in q:
            filters["user_type"].append("老客")
    return filters


def has_filter_word(question: str, options: dict) -> bool:
    f = parse_filters(question, options)
    return any(f.values())


def parse_metric(question: str):
    q = question or ""
    ql = q.lower()
    if "arppu" in ql:
        return "arppu"
    if VAGUE_METRIC_RE.search(q):  # 模糊指标词优先(守卫/assume 逻辑依赖)
        return "__vague__"
    for pat, key, flags in METRIC_PATTERNS:
        if re.search(pat, ql if flags & re.IGNORECASE else q):
            return key
    if REFUND_RE.search(q):
        return "__refund__"  # 退款域,指标未指明
    return None


def _rank_dim(q: str, filters: dict) -> str:
    if filters.get("channel") or "渠道" in q:
        return "channel"
    return "category"


def parse(question: str, options: dict, asof=None) -> dict:
    """解析问题 → intent;未命中任何指标/筛选时返回 {"ok": False}。"""
    q = question or ""
    metric = parse_metric(q)
    time_preset = parse_time(q)
    time_range = None if time_preset else parse_time_range(q, asof)
    filters = parse_filters(q, options)
    m = VAGUE_METRIC_RE.search(q)
    vague_word = m.group(0) if m else None
    time_explicit = time_preset is not None or time_range is not None

    if metric is None and OVERVIEW_RE.search(q) and not COMPARE_RE.search(q):
        # 经营概况/大盘/整体情况类:overview 卡(四 KPI 概述),时间词照常解析
        # (新老客对比问句让位于 compare 分支)
        return _mk("overview", "summary", q, time_preset, time_range, filters,
                   metric=None, compare=True, vague=vague_word,
                   time_explicit=time_explicit)

    if metric is None and not any(filters.values()) and not COMPARE_RE.search(q):
        if FOLLOWUP_RE.search(q):
            # 追问式("较上一期变化多少"/"那...呢"):无指标词,由编排层继承上一轮指标
            card = "trend" if TREND_RE.search(q) else "summary"
            return _mk(None, card, q, time_preset, time_range, filters, metric=None,
                       compare=True, vague=vague_word, time_explicit=time_explicit)
        return {"ok": False}

    is_trend = bool(TREND_RE.search(q))
    is_rank = bool(RANK_RE.search(q))
    is_attr = bool(ATTRIBUTION_RE.search(q))
    is_compare_q = bool(COMPARE_RE.search(q))
    want_cmp = bool(COMPARE_REQ_RE.search(q))

    # 新老客对比 → compare 卡(新老客口径下有意义的指标都进 compare)
    if is_compare_q and (metric in (None, "__refund__") or metric in COMPARE_METRICS):
        return _mk("compare", "compare", q, time_preset, time_range, filters,
                   metric="new_order_ratio", compare=False, vague=vague_word,
                   time_explicit=time_explicit)

    if metric == "__vague__":
        # 模糊指标词:按 GMV 理解(无时间词时已被守卫拦截为 clarify)
        card = "trend" if is_trend else ("rank" if is_rank else "summary")
        return _mk("gmv", card, q, time_preset, time_range, filters, metric="gmv",
                   compare=card == "summary" or want_cmp, vague=vague_word,
                   time_explicit=time_explicit)

    if metric == "__refund__":
        if is_attr:
            card = "attribution"
        elif is_trend:
            card = "trend"
        elif is_rank:
            card = "rank"
        else:
            card = "summary"
        if card == "trend":
            mkey = "refund_rate" if "退款率" in q else "refund_amt"
        elif card == "rank":
            mkey = "refund_amt" if re.search(r"退款金额|退得多|退款多|退款额", q) else "refund_rate"
        else:
            mkey = None  # summary:退款率 + 退款金额双 KPI
        return _mk("refund", card, q, time_preset, time_range, filters, metric=mkey,
                   compare=card in ("summary", "attribution") or want_cmp,
                   vague=vague_word, time_explicit=time_explicit)

    # 指标明确
    if metric in ("refund_rate", "refund_amt") and is_attr:
        return _mk("refund", "attribution", q, time_preset, time_range, filters,
                   metric="refund_rate", compare=True, vague=vague_word,
                   time_explicit=time_explicit)
    if is_trend:
        card = "trend"
    elif is_rank and metric in ("gmv", "refund_rate", "refund_amt", "item_cnt",
                                "order_cnt", "pay_user_cnt"):
        card = "rank"
    else:
        card = "summary"
    if metric is None:
        # 只有类目/渠道/新老客词、未指明指标 → 默认按 GMV 汇总
        metric = "gmv"
    return _mk(metric, card, q, time_preset, time_range, filters, metric=metric,
               compare=card == "summary" or want_cmp, vague=vague_word,
               time_explicit=time_explicit)


def _mk(domain, card, q, time_preset, time_range, filters, metric=None, compare=False,
        vague=None, time_explicit=False) -> dict:
    return {
        "ok": True,
        "domain": domain,
        "metric": metric,
        "card": card,
        "dim": _rank_dim(q, filters) if card == "rank" else None,
        "time_preset": time_preset,
        "time_range": time_range,
        "time_explicit": time_explicit,
        "filters": filters,
        "filter_explicit": any(filters.values()),
        "want_compare": compare,
        "vague": vague,
        "question": q,
    }
