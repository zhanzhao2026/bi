"""规则兜底意图解析(契约 §9/§16,无 LLM 时必须可用)。

词表全部来自 semantic/terms.yml(词是数据,经 app/semantic/loader 加载);
意图构造逻辑(正则编译、解析顺序、分支判断)留在本模块。输出结构化 intent:
  domain / metric / card / dim / time_preset / time_range / time_pair /
  filters / vague / limit / dim_conflict 等。

时间解析(§16.1):统一走 app/semantic/timelex(纯函数),preset 词表仍由
terms.yml time_words 提供,既有时间问法解析结果逐字不变;新增 旬/周/最近N天/
今年以来/从X到Y 等表达式与「X比Y」相邻比较(time_pair)。

商品下钻(§16.2):terms.yml synonyms.dimension 词 + 排名/销量句式 →
product 维 rank 卡;与「渠道」泛指词同现 → dim_conflict(编排层出澄清卡)。
"""
import re

from ..semantic import timelex
from ..semantic.loader import get_semantic

# ---------- 词表加载(terms.yml,导入时编译一次;缺词表即抛错,fail fast) ----------
_T = get_semantic().terms


def _req(section: str, key: str) -> str:
    v = (_T.get(section) or {}).get(key)
    if not v:
        raise RuntimeError(f"terms.yml 缺少 {section}.{key} 词表")
    return str(v)


# ---------- 时间词(顺序敏感:具体优先;pattern 数据在 terms.yml,timelex 按此命中) ----------
TIME_PATTERNS: list[tuple[str, str]] = [
    (str(t["pattern"]), str(t["preset"])) for t in (_T.get("time_words") or [])
]
if not TIME_PATTERNS:
    raise RuntimeError("terms.yml 缺少 time_words 词表")

# ---------- 指标词(顺序敏感:先具体后宽泛;pattern 数据在 terms.yml) ----------
# (pattern, metric_key, flags)
METRIC_PATTERNS: list[tuple[str, str, int]] = [
    (str(m["pattern"]), str(m["metric"]),
     re.IGNORECASE if str(m.get("flags", "")).lower() == "i" else 0)
    for m in ((_T.get("synonyms") or {}).get("metric_patterns") or [])
]
if not METRIC_PATTERNS:
    raise RuntimeError("terms.yml 缺少 synonyms.metric_patterns 词表")

VAGUE_METRIC_RE = re.compile(_req("synonyms", "vague"))
COMPARE_RE = re.compile(_req("synonyms", "compare"))
REFUND_RE = re.compile(_req("synonyms", "refund_domain"))
CHANNEL_WORD_RE = re.compile(_req("synonyms", "channel_word"))
USER_TYPE_WORDS: list[str] = [str(w) for w in ((_T.get("synonyms") or {}).get("user_type") or [])]
if not USER_TYPE_WORDS:
    raise RuntimeError("terms.yml 缺少 synonyms.user_type 词表")

# ---------- 商品维度下钻词(§16.2) ----------
PRODUCT_WORDS: list[str] = [str(w) for w in ((_T.get("synonyms") or {}).get("dimension") or [])]
if not PRODUCT_WORDS:
    raise RuntimeError("terms.yml 缺少 synonyms.dimension 词表")
PRODUCT_RANK_RE = re.compile(_req("synonyms", "product_rank"))
PRODUCT_LIMIT_RE = re.compile(_req("synonyms", "product_limit"), re.IGNORECASE)

TREND_RE = re.compile(_req("intent", "trend"))
RANK_RE = re.compile(_req("intent", "rank"))
ATTRIBUTION_RE = re.compile(_req("intent", "attribution"))
COMPARE_REQ_RE = re.compile(_req("intent", "compare_request"))
FOLLOWUP_RE = re.compile(_req("intent", "followup"))
OVERVIEW_RE = re.compile(_req("intent", "overview"))
# 退款域细分:走势按"退款率"词判定;排名按金额词判定(词在 terms.yml)
REFUND_TREND_RATE_RE = re.compile(_req("intent", "refund_trend_rate"))
REFUND_RANK_AMT_RE = re.compile(_req("intent", "refund_rank_amt"))

CHANNEL_ALIASES: dict[str, str] = {
    str(k): str(v) for k, v in ((_T.get("synonyms") or {}).get("channel_aliases") or {}).items()
}

# compare 卡允许承载的指标(新老客对比口径下有业务含义的;指标集合属意图逻辑,留代码)
COMPARE_METRICS = {"order_cnt", "gmv", "arppu", "pay_user_cnt", "item_cnt",
                   "new_order_ratio", "refund_rate", "refund_amt"}


def month_range(year: int, month: int) -> list[str]:
    """自然月闭区间(委托 timelex,保持既有函数可用)。"""
    return timelex.month_range(year, month)


def parse_time(question: str) -> str | None:
    """时间词 → preset(委托 timelex;既有 preset 问法结果逐字不变)。"""
    return timelex.parse(question, None, TIME_PATTERNS)["preset"]


def parse_time_range(question: str, asof=None) -> list[str] | None:
    """具体时间 → 闭区间(委托 timelex:月份/旬/从X到Y/最近N天/N周/年以来等)。"""
    return timelex.parse(question, asof, TIME_PATTERNS)["range"]


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
    # 用户类型词:命中恰好一个筛值词才筛(同现=新老客对比,互斥不筛)
    if not COMPARE_RE.search(q):
        hits = [w for w in USER_TYPE_WORDS if w in q]
        if len(hits) == 1:
            filters["user_type"].append(hits[0])
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
    if filters.get("channel") or CHANNEL_WORD_RE.search(q):
        return "channel"
    return "category"


# ---------- 商品维度下钻(§16.2) ----------

def _parse_limit(q: str) -> int | None:
    """「前10名 / TOP10 / 10个商品」→ 条数(1~50);未命中返回 None(默认 10)。"""
    m = PRODUCT_LIMIT_RE.search(q)
    if not m:
        return None
    for g in m.groups():
        if g:
            return max(1, min(50, int(g)))
    return None


def _product_detect(q: str) -> dict:
    """商品下钻探测:dimension 词 + (rank 词或销量句式) 才算命中。

    返回 {hit, conflict, limit}:conflict=与「渠道」泛指词同现(编排层澄清)。
    """
    if not any(w in q for w in PRODUCT_WORDS):
        return {"hit": False, "conflict": False, "limit": None}
    if not (RANK_RE.search(q) or PRODUCT_RANK_RE.search(q)):
        return {"hit": False, "conflict": False, "limit": None}
    return {"hit": True, "conflict": bool(CHANNEL_WORD_RE.search(q)),
            "limit": _parse_limit(q)}


def parse(question: str, options: dict, asof=None) -> dict:
    """解析问题 → intent;未命中任何指标/筛选时返回 {"ok": False}。"""
    q = question or ""
    metric = parse_metric(q)
    # 时间解析统一走 timelex(§16.1):preset / range / 「X比Y」相邻比较三者互斥
    tx = timelex.parse(q, asof, TIME_PATTERNS)
    time_preset = tx["preset"]
    time_range = tx["range"]
    time_pair = tx["pair"]
    filters = parse_filters(q, options)
    m = VAGUE_METRIC_RE.search(q)
    vague_word = m.group(0) if m else None
    time_explicit = bool(time_preset or time_range or time_pair)
    prod = _product_detect(q)
    is_trend = bool(TREND_RE.search(q))
    is_rank = bool(RANK_RE.search(q))
    is_attr = bool(ATTRIBUTION_RE.search(q))
    is_compare_q = bool(COMPARE_RE.search(q))
    want_cmp = bool(COMPARE_REQ_RE.search(q))

    if metric is None and OVERVIEW_RE.search(q) and not COMPARE_RE.search(q):
        # 经营概况/大盘/整体情况类:overview 卡(四 KPI 概述),时间词照常解析
        # (新老客对比问句让位于 compare 分支)
        return _mk("overview", "summary", q, time_preset, time_range, filters,
                   metric=None, compare=True, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair)

    # 商品维度下钻(§16.2):商品词 + 排名/销量句式 → product rank 卡;
    # 与「渠道」泛指词同现 → dim_conflict(编排层出澄清卡,防笛卡尔组合)
    if prod["hit"] and not is_trend and not is_compare_q:
        if prod["conflict"]:
            return _mk("product", "clarify_dims", q, time_preset, time_range, filters,
                       metric=None, compare=False, vague=vague_word,
                       time_explicit=time_explicit, time_pair=time_pair,
                       dim=None, dim_conflict=True)
        if metric == "__refund__":
            # 商品退款排名:问「退款率」→ 退款率,其余(退款最多等)→ 退款金额
            mkey = "refund_rate" if REFUND_TREND_RATE_RE.search(q) else "refund_amt"
        elif metric in (None, "__vague__"):
            mkey = "gmv"
        else:
            mkey = metric
        return _mk("product", "rank", q, time_preset, time_range, filters,
                   metric=mkey, compare=want_cmp, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair,
                   dim="product", limit=prod["limit"])

    if metric is None and not any(filters.values()) and not COMPARE_RE.search(q):
        if FOLLOWUP_RE.search(q):
            # 追问式("较上一期变化多少"/"那...呢"):无指标词,由编排层继承上一轮指标
            card = "trend" if TREND_RE.search(q) else "summary"
            return _mk(None, card, q, time_preset, time_range, filters, metric=None,
                       compare=True, vague=vague_word, time_explicit=time_explicit,
                       time_pair=time_pair)
        return {"ok": False}

    # 新老客对比 → compare 卡(新老客口径下有意义的指标都进 compare)
    if is_compare_q and (metric in (None, "__refund__") or metric in COMPARE_METRICS):
        return _mk("compare", "compare", q, time_preset, time_range, filters,
                   metric="new_order_ratio", compare=False, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair)

    if metric == "__vague__":
        # 模糊指标词:按 GMV 理解(无时间词时已被守卫拦截为 clarify)
        card = "trend" if is_trend else ("rank" if is_rank else "summary")
        return _mk("gmv", card, q, time_preset, time_range, filters, metric="gmv",
                   compare=card == "summary" or want_cmp, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair)

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
            mkey = "refund_rate" if REFUND_TREND_RATE_RE.search(q) else "refund_amt"
        elif card == "rank":
            mkey = "refund_amt" if REFUND_RANK_AMT_RE.search(q) else "refund_rate"
        else:
            mkey = None  # summary:退款率 + 退款金额双 KPI
        return _mk("refund", card, q, time_preset, time_range, filters, metric=mkey,
                   compare=card in ("summary", "attribution") or want_cmp,
                   vague=vague_word, time_explicit=time_explicit, time_pair=time_pair)

    # 指标明确
    if metric in ("refund_rate", "refund_amt") and is_attr:
        return _mk("refund", "attribution", q, time_preset, time_range, filters,
                   metric="refund_rate", compare=True, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair)
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
               time_explicit=time_explicit, time_pair=time_pair)


def _mk(domain, card, q, time_preset, time_range, filters, metric=None, compare=False,
        vague=None, time_explicit=False, time_pair=None, dim=None, limit=None,
        dim_conflict=False) -> dict:
    if dim is None and card == "rank":
        dim = _rank_dim(q, filters)
    return {
        "ok": True,
        "domain": domain,
        "metric": metric,
        "card": card,
        "dim": dim,
        "time_preset": time_preset,
        "time_range": time_range,
        "time_pair": time_pair,
        "time_explicit": time_explicit,
        "filters": filters,
        "filter_explicit": any(filters.values()),
        "want_compare": compare,
        "vague": vague,
        "limit": limit,
        "dim_conflict": dim_conflict,
        "question": q,
    }
