"""守卫规则(契约 §7,顺序执行):薪酬/PII → refuse;明细/具体商品 → boundary;模糊指标词且无时间词 → clarify。"""
import re

REFUSE_WORDS = ["薪酬", "工资", "薪资", "工资条", "身份证", "手机号", "银行卡", "社保"]
BOUNDARY_WORDS = ["明细", "订单列表", "订单详情", "具体商品", "哪款商品", "哪款产品", "哪个商品卖", "导出"]
VAGUE_WORDS = ["销售额", "卖得怎么样", "卖得如何", "业绩", "生意怎么样", "做得怎么样"]

CLARIFY_OPTIONS = [
    {"label": "最近30天", "value": "last_30d"},
    {"label": "最近7天", "value": "last_7d"},
    {"label": "昨天", "value": "yesterday"},
    {"label": "上个月", "value": "last_month"},
]

_TIME_RE = re.compile(
    r"上个月|上月|本月|这个月|当月|昨天|昨日|最近7天|近7天|过去7天|最近一周|近一周|过去一周|上周"
    r"|最近30天|近30天|过去30天|最近一个月|近一个月|过去一个月|30天|近1个月|今天|当日"
)


def has_time_word(question: str) -> bool:
    return bool(_TIME_RE.search(question or ""))


def hit_any(question: str, words: list) -> str | None:
    q = question or ""
    for w in words:
        if w in q:
            return w
    return None


def check(question: str) -> tuple[str | None, str | None]:
    """返回 (guard_kind, hit_word)。kind ∈ refuse / boundary / clarify / None。"""
    q = question or ""
    w = hit_any(q, REFUSE_WORDS)
    if w:
        return "refuse", w
    w = hit_any(q, BOUNDARY_WORDS)
    if w:
        return "boundary", w
    w = hit_any(q, VAGUE_WORDS)
    if w and not has_time_word(q):
        return "clarify", w
    return None, None
