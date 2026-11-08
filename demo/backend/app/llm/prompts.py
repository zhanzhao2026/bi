"""LLM system prompt 渲染(契约 §15.1):planner/narrator 的 system prompt 由语义层对象渲染。

指标清单(key/name/unit/plain/ratio)、维度与筛值、术语/守卫规则均取自 semantic/
(models/metrics/dims/terms),prompt 文本不再内联在 gateway.py;词是数据,渲染逻辑在此。

§17 planner 加固:
- 注入时间上下文(今天 / 数据截至 data_as_of),要求「先查后答」;
- 时间铁律(显式时间词必须完整映射进 IR.time,禁止降级为 this_month/last_30d)、
  排名铁律(dims+order+limit,禁止把候选值塞进 filters)、诚实铁律(不可表达 →
  {"unsupportable": 原因},模糊问法 → {"clarify": true},问候/身份类对话 → {"meta": true});
- 10 组 few-shot 覆盖:显式年月/排名/TOP N 下钻/筛值/相邻比较/跨类目对比拒答/
  无指标拒答/澄清/新老客对比/趋势,全部由语义层对象 + data_as_of 动态渲染。
"""
import datetime as dt
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅为类型标注,避免运行时循环依赖
    from ..semantic.loader import Semantic

# IR JSON Schema 约束(结构契约,与 CONTRACT §4 一致;compare 支持显式对比窗口 §16.1)
_IR_SCHEMA_TMPL = (
    '{{"metric": "指标key", "time": {{"preset": "{presets}"'
    ' 或 {{"range": ["YYYY-MM-DD","YYYY-MM-DD"]}}}}, "dims": [], "filters": {{"category": [],'
    ' "channel": [], "user_type": []}}, "granularity": "none|day",'
    ' "compare": "none|prev_period|{{\\"type\\": \\"range\\", \\"range\\": [\\"起\\",\\"止\\"]}}",'
    ' "order": "desc|asc", "limit": 整数}}'
)

# 文化/促销时间词 → (月, 起, 止);超出 data_as_of 的部分须截断(§17 时间铁律 3)
_CULTURE_RANGES = {
    "双十一": (11, 1, 11),
    "双11": (11, 1, 11),
    "双十二": (12, 1, 12),
    "双12": (12, 1, 12),
    "618": (6, 1, 18),
}


def _asof_date() -> dt.date:
    """数据截至日:优先语义层缓存(data_as_of),异常时回退 timelex 默认锚点。"""
    try:
        from ..semantic.loader import get_data_as_of

        v = get_data_as_of()
        if v:
            return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        pass
    from ..semantic import timelex

    return timelex.DEFAULT_ASOF


def _culture_example(asof: dt.date) -> tuple[str, str] | None:
    """双十一 few-shot 的区间:11-01 起,止日截断到 data_as_of(未到则原样)。"""
    cfg = _CULTURE_RANGES["双十一"]
    try:
        start = dt.date(asof.year, cfg[0], cfg[1])
    except ValueError:
        return None
    end_full = dt.date(asof.year, cfg[0], cfg[2])
    end = min(end_full, asof)
    if start > end:
        return None
    return start.isoformat(), end.isoformat()


def _few_shots(asof: dt.date) -> list[tuple[str, str, str]]:
    """few-shot 组(问题, IR JSON, 规则注释);相对时间一律按 data_as_of 动态计算。

    覆盖:显式时间词铁律 / 排名意图 / TOP N 商品下钻 / 筛值 / 相邻比较 /
    跨类目对比不可表达 / 无指标不可表达 / clarify 逃生 / 新老客对比 / 趋势。
    few-shot 中只使用 gmv/arppu/refund_rate 三个指标 key(避免与 §16.3 检索
    注入子集的断言冲突),其余指标靠清单本身。
    """
    oct_xia = f'["{asof.year}-10-21", "{asof.year}-10-31"]'
    oct_shang = f'["{asof.year}-10-01", "{asof.year}-10-10"]'
    culture = _culture_example(asof)
    culture_rng = f'["{culture[0]}", "{culture[1]}"]' if culture else '["11-01", "11-11"]'
    monday = (asof - dt.timedelta(days=asof.weekday())).isoformat()
    benzhou_rng = f'["{monday}", "{asof.isoformat()}"]'
    return [
        # a. 显式时间词铁律:年月必须完整映射,禁止降级 this_month
        ("2026年10月美妆类目的GMV是多少?",
         '{"metric": "gmv", "time": {"range": ["2026-10-01", "2026-10-31"]}, "dims": [],'
         ' "filters": {"category": ["美妆"], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         "显式年月 → 该月1日~月末完整映射进 time.range,禁止降级为 this_month/last_30d"),
        # b. 排名意图:dims + order + limit,绝不把候选值塞进 filters
        ("最近30天哪个类目GMV最高?",
         '{"metric": "gmv", "time": {"preset": "last_30d"}, "dims": ["category"],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 1}',
         "「哪个最高」= 排名:该维度进 dims、order=desc、limit=1;"
         "把类目名塞进 filters 会变成对筛选值求合计,是错误答案"),
        # c. TOP N 商品下钻(product 自动带父类目)
        ("最近30天卖得最好的10个商品",
         '{"metric": "gmv", "time": {"preset": "last_30d"}, "dims": ["product"],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         "TOP N → dims=[\"product\"] 且 limit=N;product 维自动带父类目名"),
        # d. 筛值进 filters(不进 dims)
        ("直播渠道最近7天退款率是多少?",
         '{"metric": "refund_rate", "time": {"preset": "last_7d"}, "dims": [],'
         ' "filters": {"category": [], "channel": ["直播"], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         "「直播」是筛选条件进 filters.channel,不是 dims;"
         "「最近7天」完整映射为 preset"),
        # e. 相邻比较:「X比Y」→ 主窗口 + compare 显式区间
        ("10月下旬比上旬客单价怎么样?",
         f'{{"metric": "arppu", "time": {{"range": {oct_xia}}}, "dims": [],'
         f' "filters": {{"category": [], "channel": [], "user_type": []}},'
         f' "granularity": "none", "compare": {{"type": "range", "range": {oct_shang}}},'
         ' "order": "desc", "limit": 10}',
         "「X比Y」→ time.range=被比较的主窗口,compare={type:range}=对比窗口;"
         "两个时间词都不许丢"),
        # f. 不可表达必须诚实:跨类目对比,禁止合并筛选或丢时间词
        ("女装今年以来退款金额比数码多多少?",
         '{"unsupportable": "跨类目对比需要分组对比,当前一次查询只能聚合一个筛选范围"}',
         "跨筛选组的「A比B多多少」不可表达:合并两类目求和、或丢掉「今年以来」"
         "都会得到数字对但答非所问的错误答案,必须诚实拒答"),
        # g. 无法定位指标 → 诚实拒答(预测类 + 未收录指标都走这里,不用 clarify)
        ("明年利润预测是多少?",
         '{"unsupportable": "语义层暂无利润指标"}',
         "预测类问题(未来数据不存在)→ unsupportable,绝不硬答,也不要 clarify"),
        # m. 未收录指标(非模糊)→ unsupportable,不走 clarify
        ("利润有多少?",
         '{"unsupportable": "语义层暂无利润指标"}',
         "「利润」不在可用指标清单里,这不是模糊而是不存在 → unsupportable;"
         "成本/流量/UV 等任何清单外指标同理,不要 clarify"),
        # h. clarify 逃生:既有词表无法定位的模糊问法
        ("上个月的情况怎么样?",
         '{"clarify": true}',
         "问法模糊、无法定位到任何指标与维度 → 请求澄清,而不是猜一个指标"),
        # i. 新老客对比:系统内置卡片,filters.user_type 必须留空
        ("对比一下最近30天新客和老客的客单价",
         '{"metric": "arppu", "time": {"preset": "last_30d"}, "dims": [],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         "「新客和老客」同现 = 新老客对比,由系统内置卡片处理:"
         "IR 只需给指标与时间,filters.user_type 留空(填了会漏掉其中一组)"),
        # j. 趋势:dims=["date"] + granularity=day
        ("最近30天GMV走势怎么样?",
         '{"metric": "gmv", "time": {"preset": "last_30d"}, "dims": ["date"],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "day", "compare": "none", "order": "desc", "limit": 10}',
         "走势/趋势/按天 → dims=[\"date\"] 且 granularity=day"),
        # k. 文化促销词:双十一,超出数据截至日的部分截断
        ("双十一那几天的GMV怎么样?",
         f'{{"metric": "gmv", "time": {{"range": {culture_rng}}}, "dims": [],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         f"文化促销词「双十一」=11-01~11-11;数据截至 {asof.isoformat()},"
         f"11-07 之后的日期还没发生,range 止日必须截断为 {asof.isoformat()}"),
        # l. 本周:周一~数据截至日,不得降级为 last_7d
        ("本周GMV是多少?",
         f'{{"metric": "gmv", "time": {{"range": {benzhou_rng}}}, "dims": [],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         f"「本周」= 本周一({monday})~数据截至日({asof.isoformat()}),"
         "必须用 range 表达,不许降级成 last_7d(那是最近7天,不是本周)"),
        # n. 最近N天:从数据截至日往前数 N 天(含截至日),天数一个都不能少
        ("最近3天GMV是多少?",
         f'{{"metric": "gmv", "time": {{"range": '
         f'["{(asof - dt.timedelta(days=2)).isoformat()}", "{asof.isoformat()}"]}}, "dims": [],'
         ' "filters": {"category": [], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         f"「最近3天」= 含数据截至日({asof.isoformat()})在内的 3 天,起点为 "
         f"{(asof - dt.timedelta(days=2)).isoformat()};N 天的起点=截至日-(N-1),"
         "数错天数会直接改变答案数字(任意指标同理)"),
        # o. 无拆分词的问数:dims 留空,不要自作主张加维度
        ("美妆最近30天的客单价是多少?",
         '{"metric": "arppu", "time": {"preset": "last_30d"}, "dims": [],'
         ' "filters": {"category": ["美妆"], "channel": [], "user_type": []},'
         ' "granularity": "none", "compare": "none", "order": "desc", "limit": 10}',
         "问「是多少/有多少」且无排名/走势词 → 汇总问数,dims 留空;"
         "「美妆」是筛选进 filters.category;擅自加 dims 会把问数变成排名,"
         "答案形态就错了(任意指标同理)"),
        # p. 闲聊逃生:问候/身份/能力询问 → meta(系统给固定自我介绍)
        ("你是谁?",
         '{"meta": true}',
         "与经营数据无关的身份询问 → meta,不要编 IR、不要 clarify;"
         "「你好」「你能做什么」等同理;带数据意图的问候(你好,最近GMV怎么样)是问数"),
    ]


def render_planner_system(sem: "Semantic", options: dict | None = None,
                          metric_keys: list[str] | None = None) -> str:
    """planner system prompt:语义层指标/维度/筛值/术语 + 时间上下文 + 铁律 + few-shot。

    metric_keys(契约 §16.3):按用户问题检索出的指标 key 子集(BM25 top-k 注入);
    None/空 = 全量注入(指标总数 ≤ 12 或检索为空时由 gateway 回退,行为不变)。
    dims/筛值/术语/few-shot 始终全量。
    """
    options = options or {}
    asof = _asof_date()
    today = dt.date.today()
    lines: list[str] = []
    lines.append("你是电商 BI 系统的语义解析器,把用户的中文数据问题转换为查询 IR(中间表示)JSON。")
    lines.append("")
    lines.append("【当前时间上下文】")
    lines.append(f"- 今天 = {today.isoformat()}")
    lines.append(f"- 数据截至 = {asof.isoformat()}(数据只更新到该日,更晚的日期查不到数据)")
    lines.append("- 先查后答:输出前先逐词自检——问题中的时间词是否都完整进了 IR.time、"
                 "range 里的每个日期是否都不晚于数据截至日、指标是否在下方清单里、"
                 "筛选值是否真实存在;自检不过就改 IR 或诚实拒答,绝不猜。")
    lines.append("")
    metric_items = list(sem.metrics.items())
    header = "可用指标(格式: key(名称, 单位[, 比率]) — 口径说明):"
    if metric_keys:
        selected = [k for k in metric_keys if k in sem.metrics]
        if selected and len(selected) < len(metric_items):
            metric_items = [(k, sem.metrics[k]) for k in selected]
            header = (f"可用指标(按问题检索出最相关的 {len(metric_items)}/{len(sem.metrics)} 个,"
                      "格式: key(名称, 单位[, 比率]) — 口径说明):")
    lines.append(header)
    # 指标同义词(词是数据:terms.yml synonyms.metric_patterns → 按指标归并展示,
    # 只展示纯词形条目,过滤正则元字符写法)
    alias_map: dict[str, list[str]] = {}
    for pat_item in ((sem.terms or {}).get("synonyms") or {}).get("metric_patterns") or []:
        mkey = str(pat_item.get("metric") or "")
        for tok in str(pat_item.get("pattern") or "").split("|"):
            tok = tok.strip()
            if tok and mkey and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9]+", tok):
                alias_map.setdefault(mkey, []).append(tok)
    for k, m in metric_items:
        ratio = ", 比率" if m.get("ratio") else ""
        aliases = alias_map.get(k) or []
        alias_txt = f"〔别名: {'、'.join(dict.fromkeys(aliases))}〕" if aliases else ""
        lines.append(f"- {k}({m.get('name', '')}, {m.get('unit', '')}{ratio}): "
                     f"{m.get('plain', '')}{alias_txt}")
    lines.append("")
    dim_parts = []
    for d, cfg in (sem.dims or {}).items():
        cfg = cfg or {}
        user_terms = "、".join(cfg.get("user_terms") or [])
        part = f"{d}({cfg.get('desc', '')}"
        if user_terms:
            part += f"; 用户说法: {user_terms}"
        part += ")"
        dim_parts.append(part)
    lines.append(f"可用维度(dims): {', '.join(dim_parts)}")
    lines.append(f"可用时间口径(preset): {', '.join(sem.time_presets)}")
    lines.append(
        f"可用筛选值 category: {options.get('category', [])};"
        f" channel: {options.get('channel', [])};"
        f" user_type: {options.get('user_type', ['新客', '老客'])}"
    )
    # 渠道口语别名(词是数据:terms.yml synonyms.channel_aliases)
    channel_aliases = {
        str(k): str(v)
        for k, v in (((sem.terms or {}).get("synonyms") or {}).get("channel_aliases") or {}).items()
    }
    if channel_aliases:
        alias_txt = "、".join(f"「{k}」→{v}" for k, v in channel_aliases.items())
        lines.append(f"渠道口语别名(写进 filters.channel 时须换成正式筛值): {alias_txt}")
    lines.append("")

    # ---- 铁律(§17)----
    lines.append("【时间铁律】")
    lines.append("1. 问题中的显式时间词(年月/旬/周/「从X到Y」区间/最近N天/今年以来等)必须"
                 "完整映射进 IR.time:能落到具体日期的用 {\"range\": [起, 止]},只有相对时段"
                 "才用 preset;禁止把显式时间词降级成 this_month/last_30d,也禁止丢弃任何一个时间词。")
    lines.append("2. 旬 = 上旬1~10日/中旬11~20日/下旬21~月末;「本周」= 本周一~数据截至日"
                 f"(例:数据截至 {asof.isoformat()} 时「本周GMV」的 range=["
                 f"{(asof - dt.timedelta(days=asof.weekday())).isoformat()}, {asof.isoformat()}],"
                 "不许写成 last_7d);「今年以来/年初至今」= 当年1月1日~数据截至日;"
                 "月份无年份时按数据截至日所在年份。")
    lines.append("3. 文化/促销时间词:双十一=11-01~11-11、双十二=12-01~12-12、618=06-01~06-18;"
                 "range 中的任何日期都不得晚于数据截至日 —— 超出部分必须截断"
                 f"(例:数据截至 {asof.isoformat()} 时,双十一的 range 止日必须写 {asof.isoformat()},"
                 "绝不能写 11-07 之后的日期);整个区间都晚于数据截至日时,该时间段查不到"
                 "数据,应输出 unsupportable 而不是硬查。")
    lines.append("4. 问题完全没有任何时间词时才用 last_30d。")
    qiantian = (asof - dt.timedelta(days=2)).isoformat()
    lines.append(f"5. 相对时间词(昨天/前天/最近N天/最近N周/本周/今年以来)一律以「数据截至日」"
                 f"当作『今天』来计算(数据世界只到 {asof.isoformat()}),不用自然今天"
                 f"(例:数据截至 {asof.isoformat()} 时,「前天」={qiantian},"
                 f"「最近3天」=[{qiantian}, {asof.isoformat()}])。")
    lines.append("")
    lines.append("【排名铁律】")
    lines.append("「哪个最高/最低/最好/最大」「TOP N」「前 N」是排名意图:把该维度放进 dims,"
                 "order 填 desc(最高/最好)或 asc(最低/最少),limit 填 N(问「哪个」时 limit=1);"
                 "绝对禁止把候选值(类目名/商品名/渠道名)塞进 filters——那会变成对这些值求合计,"
                 "完全改变问题含义。")
    lines.append("退款排名口径:「退款最多/退款排名」一律按退款金额 refund_amt 排序,"
                 "只有明确说「退款率」才用 refund_rate,不要用退款订单数。")
    lines.append("只筛选不拆分:某维度只是限定范围(如「母婴最近30天有多少下单用户」)时,"
                 "它只进 filters、dims 保持空;dims 只在用户要求按该维度拆分/排名/对比时才用,"
                 "同一个维度不要既进 filters 又进 dims。")
    lines.append("")
    lines.append("【诚实铁律(最重要)】")
    lines.append("以下情况必须输出 {\"unsupportable\": \"原因\"},禁止为了出答案而合并筛选、"
                 "丢弃时间词、更换指标或硬套范围:")
    lines.append("1. 跨筛选组对比(如「女装退款比数码多多少」「A和B分别是多少」):需要分组对比,"
                 "当前一次查询只能聚合一个筛选范围;")
    lines.append("2. 预测/预估类问题:未来数据不存在;")
    lines.append("3. 语义层没有的指标(利润/成本/流量等)或范围(竞品数据、非本系统数据);")
    lines.append("4. 需要逐单明细、用户名单的诉求。")
    lines.append("拒答不是失败,给错数字才是失败。")
    lines.append("")
    lines.append("【澄清逃生】")
    lines.append("clarify 只用于「用户补一句话就能回答」的场景(如指标词模糊但系统未拦截)。")
    lines.append("注意:指标词明确不在可用指标清单里(利润/成本/流量等)不属于模糊,"
                 "不要 clarify,直接输出 unsupportable(语义层暂无该指标)。")
    lines.append("**裸追问**(只有「为什么」「怎么样」等、没有指标词):若会话上下文里有"
                 "上一轮数据问题,按【多轮继承】输出继承的 IR(系统会升级为归因分析);"
                 "若没有任何可继承的上下文,输出 unsupportable(原因:缺少分析对象)——"
                 "绝不要对裸追问输出 clarify,选时间范围也答不了没有对象的问题。")
    lines.append("")
    lines.append("【闲聊逃生】")
    lines.append("与经营数据完全无关的对话——问候(你好)、询问你的身份/能力(你是谁/"
                 "你能做什么/介绍一下自己)、感谢与寒暄——输出 {\"meta\": true},"
                 "由系统给出固定的自我介绍,不要输出 IR,也不要 clarify/unsupportable。")
    lines.append("判定标准:问题里找不到任何可回答的数据意图时,先考虑 meta,再考虑 clarify;"
                 "只要包含任何指标/维度/时间意图就不是 meta(如「你好,最近GMV怎么样」是问数)。")
    lines.append("")

    # 术语/守卫规则(词表来自 terms.yml;守卫在 planner 之前执行,这里告知 LLM 不要为其生成 IR)
    terms = getattr(sem, "terms", {}) or {}
    forbidden = [str(w) for w in (terms.get("forbidden") or [])]
    boundary = [str(w) for w in (terms.get("boundary") or [])]
    clarify = terms.get("clarify") or {}
    vague = [str(w) for w in (clarify.get("vague_words") or [])]
    assume_metric = str(clarify.get("assume_metric") or "gmv")
    if forbidden or boundary or vague:
        lines.append("术语与守卫规则(以下问题由系统规则优先处理,不要为其编造 IR):")
        if forbidden:
            lines.append(f"- 涉及敏感/隐私信息(如 {'、'.join(forbidden)})会被拒答,不要输出 IR")
        if boundary:
            lines.append(f"- 明细/导出类需求(如 {'、'.join(boundary)})当前版本未开放,不要输出 IR")
        if vague:
            lines.append(
                f"- 模糊指标词(如 {'、'.join(vague)})在未指定时间时由系统先澄清;"
                f"用户确认时间后按 {assume_metric} 理解")
        lines.append("")

    # ---- few-shot(§17:≥7 组,动态渲染)----
    lines.append(f"【示例】(时间锚点: 今天={today.isoformat()},数据截至={asof.isoformat()})")
    for q, ir_json, note in _few_shots(asof):
        lines.append(f"问:「{q}」")
        lines.append(f"答:{ir_json}")
        lines.append(f"#{note}")
    lines.append("")

    # ---- 会话继承 ----
    lines.append("【多轮继承】用户消息里的「会话上下文」给出上一轮的 time/filters/metric;"
                 "「那…呢」「…呢」「为什么」这类追问沿用上一轮的 time 与 filters,"
                 "只把 metric 换成本次问到的指标(追问没提新指标时沿用上一轮 metric);"
                 "只有追问里出现了新的时间词/筛选词才覆盖对应字段。")
    lines.append("")

    schema = _IR_SCHEMA_TMPL.format(presets="|".join(sem.time_presets))
    lines.append(f"IR JSON Schema(只能输出该 JSON,不要输出任何解释或代码块): {schema}")
    lines.append(
        "规则: 1) metric 必须从可用指标中选; 2) 只有用户明确要求与上一期对比时 compare 才用"
        " prev_period; 3) 不确定的筛选值留空数组,绝不编造; 4) 趋势类问题 dims 为 [\"date\"]。"
    )
    lines.append("【输出约束(收尾)】")
    lines.append("- 只输出一个 JSON 对象:要么完整 IR,要么 {\"unsupportable\": \"原因\"},"
                 "要么 {\"clarify\": true},要么 {\"meta\": true};"
                 "IR 之外的一切输出(解释/思考过程/markdown 代码块)都禁止。")
    lines.append("- 数字禁止出现在 planner 输出里:除 time.range / compare.range 中的日期和 limit"
                 " 条数外,不得出现任何统计数值、金额或百分比。")
    return "\n".join(lines)


def render_narrator_system(sem: "Semantic | None" = None) -> str:
    """narrator system prompt:结论撰写约束 + 语义层指标口径速查。"""
    lines: list[str] = [
        "你是电商 BI 助手的结论撰写器。根据给定的统计数据,输出 2~3 句简短的中文结论。",
    ]
    if sem is not None:
        glossary = "; ".join(
            f"{k}={m.get('name', '')}({m.get('unit', '')})" for k, m in sem.metrics.items()
        )
        lines.append(f"指标口径速查: {glossary}")
    lines.append("硬性要求:1) 禁止编造数字,所有数字必须与输入完全一致(原样引用);")
    lines.append("2) 不出现 SQL/IR/接口等技术词;3) 每句一行,直接给句子,不要编号。")
    return "\n".join(lines)
