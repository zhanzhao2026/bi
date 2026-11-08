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
from .attribution import ATTR_KEYS

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
# 分析意图(v15,§16.5):why=追问词(升级归因/LLM 逃生口守卫用);
# delta_rank=变化贡献排名句式(M4),方向由命中词推断
WHY_RE = re.compile(_req("intent", "why"))
DELTA_RANK_RE = re.compile(_req("intent", "delta_rank"))
# 跨筛选组对比句式(v17):命中 + 同一筛选组 ≥2 取值 → 确定性拒答
CROSS_GROUP_RE = re.compile(_req("intent", "cross_group"))
# 退款域细分:走势按"退款率"词判定;排名按金额词判定(词在 terms.yml)
REFUND_TREND_RATE_RE = re.compile(_req("intent", "refund_trend_rate"))
REFUND_RANK_AMT_RE = re.compile(_req("intent", "refund_rank_amt"))

CHANNEL_ALIASES: dict[str, str] = {
    str(k): str(v) for k, v in ((_T.get("synonyms") or {}).get("channel_aliases") or {}).items()
}

# compare 卡允许承载的指标(新老客对比口径下有业务含义的;指标集合属意图逻辑,留代码)
COMPARE_METRICS = {"order_cnt", "gmv", "arppu", "pay_user_cnt", "item_cnt",
                   "new_order_ratio", "refund_rate", "refund_amt", "refund_order_rate"}


def has_why_word(question: str) -> bool:
    """问题是否含「为什么」类追问词(含裸追问「为什么」)。

    供编排层把追问升级为归因卡使用——不要求规则 parse 成功(裸「为什么」
    无指标词,parse 会失败,但结合会话继承仍可升级归因)。
    """
    return bool(WHY_RE.search(question or ""))


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
    # 「新客占比」的占比口径剥离不在此处:user_type 筛选统一由编排层
    # _answer_path 剥离并向用户注明(同时拦 LLM IR,单一剥离点)。
    filters = parse_filters(q, options)
    m = VAGUE_METRIC_RE.search(q)
    vague_word = m.group(0) if m else None
    time_explicit = bool(time_preset or time_range or time_pair)
    prod = _product_detect(q)
    is_trend = bool(TREND_RE.search(q))
    is_rank = bool(RANK_RE.search(q))
    is_attr = bool(ATTRIBUTION_RE.search(q))
    why_attr = bool(WHY_RE.search(q))
    # M4 方向:下降/下滑/降幅/跌 → down;上升/上涨/涨幅/增长/升 → up;其余 → abs
    m_dr = DELTA_RANK_RE.search(q)
    if m_dr:
        w = m_dr.group(0)
        if any(x in w for x in ("下降", "下滑", "降幅", "跌")):
            delta_dir = "down"
        elif any(x in w for x in ("上升", "上涨", "涨幅", "增长", "升")):
            delta_dir = "up"
        else:
            delta_dir = "abs"
    else:
        delta_dir = None
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
            # 追问式("较上一期变化多少"/"那...呢"):无指标词,由编排层继承上一轮指标;
            # why_attr 供编排层把「为什么…呢」追问升级为归因卡(doc 04 §5)
            card = "trend" if TREND_RE.search(q) else "summary"
            return _mk(None, card, q, time_preset, time_range, filters, metric=None,
                       compare=True, vague=vague_word, time_explicit=time_explicit,
                       time_pair=time_pair, why_attr=why_attr)
        return {"ok": False}

    # 新老客对比 → compare 卡(新老客口径下有意义的指标都进 compare;
    # compare_focus 保留问句指定的指标,编排层为该指标追加分组序列)
    if is_compare_q and (metric in (None, "__refund__") or metric in COMPARE_METRICS):
        return _mk("compare", "compare", q, time_preset, time_range, filters,
                   metric="new_order_ratio", compare=False, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair,
                   compare_focus=(metric if metric not in (None, "__refund__") else None))

    if metric == "__vague__":
        # 模糊指标词:按 GMV 理解(无时间词时已被守卫拦截为 clarify)
        card = "trend" if is_trend else ("rank" if is_rank else "summary")
        return _mk("gmv", card, q, time_preset, time_range, filters, metric="gmv",
                   compare=card == "summary" or want_cmp, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair)

    # M4 变化贡献排名(docs/04):「下降/上升最明显|变化最大」+ 排名词 →
    # rank + prev_period 两期对比(类目/渠道维;商品维已走上方 product 分支)。
    # 注意须在退款域归因之前:「哪些类目退款金额下降最明显」是排名而非归因。
    if is_rank and delta_dir and isinstance(metric, str) and not metric.startswith("__"):
        return _mk(metric, "rank", q, time_preset, time_range, filters,
                   metric=metric, compare=True, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair, delta_dir=delta_dir)
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
                   vague=vague_word, time_explicit=time_explicit, time_pair=time_pair,
                   why_attr=why_attr)

    # 指标明确
    if metric in ("refund_rate", "refund_amt") and is_attr:
        # v15:refund_amt+为什么 → 可加归因(瀑布单位元),不再强制退款率
        return _mk("refund", "attribution", q, time_preset, time_range, filters,
                   metric=metric, compare=True, vague=vague_word,
                   time_explicit=time_explicit, time_pair=time_pair, why_attr=why_attr)
    if is_trend:
        card = "trend"
    elif is_rank and metric in ("gmv", "refund_rate", "refund_amt", "item_cnt",
                                "order_cnt", "pay_user_cnt"):
        card = "rank"
    elif is_attr and metric in ATTR_KEYS:
        # M5 归因泛化:比率(退款率/订单退款率/新客占比)与可加指标(GMV 等);
        # 「新客占比为什么变化」的「占比」会命中 rank 词表,由本分支兜回归因
        card = "attribution"
    else:
        card = "summary"
    if metric is None:
        # 只有类目/渠道/新老客词、未指明指标 → 默认按 GMV 汇总
        metric = "gmv"
    return _mk(metric, card, q, time_preset, time_range, filters, metric=metric,
               compare=card in ("summary", "attribution") or want_cmp, vague=vague_word,
               time_explicit=time_explicit, time_pair=time_pair, why_attr=why_attr)


def _mk(domain, card, q, time_preset, time_range, filters, metric=None, compare=False,
        vague=None, time_explicit=False, time_pair=None, dim=None, limit=None,
        dim_conflict=False, why_attr=False, delta_dir=None, compare_focus=None) -> dict:
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
        # 分析意图(v15):why=为什么类追问;delta_dir=M4 排名方向;compare_focus=对比卡指定指标
        "why_attr": why_attr,
        "delta_dir": delta_dir,
        "compare_focus": compare_focus,
        # 跨筛选组对比(v17):同一筛选组命中 ≥2 个取值且含比较句式 → 编排层
        # 确定性拒答(一次查询只能聚合一个筛选范围,合并求和是答非所问)
        "cross_group": bool(CROSS_GROUP_RE.search(q)
                            and any(isinstance(v, list) and len(v) >= 2
                                    for v in (filters or {}).values())),
        "question": q,
    }
