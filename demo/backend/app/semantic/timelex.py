"""中文时间表达式解析层(契约 §16.1):中文时间短语 → IR time 结构。

纯函数、无副作用:不读文件、不连数据库、不留全局状态。
- preset 词表(terms.yml time_words 的 (pattern, preset) 列表)由调用方传入,
  5 个既有 preset 的命中顺序与结果逐字沿用(现有时间问法解析结果不变);
- 时间锚点 asof(data_as_of)由调用方传入,未传时用契约 §0 的 2026-11-06。

parse() 返回统一结构(三键互斥,pair 优先):
  {"preset": "last_30d" | None,                       # 既有 5 preset
   "range": ["YYYY-MM-DD", "YYYY-MM-DD"] | None,      # 新表达式 → 闭区间
   "pair": {"main": [...], "main_label": str,         # 「X比Y」相邻比较
            "compare": [...], "compare_label": str} | None}

支持清单:
  preset:    昨天/最近7天/最近30天/本月/上月(词表顺序敏感,逐字沿用)
  相对日:    前天
  最近N天:   最近3天/近14天/过去90天(N≥1,等价 [asof-(N-1), asof])
  最近N周:   最近2周/近4周(N*7 天窗口)
  周:        本周(周一 ~ data_as_of;「上周」独立问法沿用 last_7d,对比语境用日历上周)
  旬:        上旬/中旬/下旬(1-10 / 11-20 / 21-月末);显式月份用该月(「10月下旬」),
             相对月份用其月(「上月下旬」),无月份用 asof 所在月(「下旬」→ 2026-11)
  年累计:    今年以来 / 年初至今(当年 01-01 ~ data_as_of)
  显式区间:  从2026-10-01到2026-10-15 / 从10月1日到10月15日
  月份:      2026年10月 / 10月(逐字沿用既有 month_range 行为)
  相邻比较:  「本周比上周」「下旬比上旬」「最近7天比上一个7天」「10月下旬比上旬」
             「本月比上月」「最近30天比上一个30天」→ 主窗口 + 对比窗口;
             「上一个N天」按主窗口等长平移;对比侧裸「旬」继承主窗口月份。
"""
import datetime as dt
import re

# 契约 §0:data_as_of = 2026-11-06(未显式传入 asof 时使用,保证纯函数确定性)
DEFAULT_ASOF = dt.date(2026, 11, 6)

MONTH_FULL_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
MONTH_BARE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月(?!\d)")
XUN_RE = re.compile(r"(上旬|中旬|下旬)")

# 旬的归属月限定词(在旬词之前的窗口内搜索;显式月 > 上月 > 本月/最近一期)
XUN_MONTH_RE = re.compile(
    r"(?P<y>\d{4})\s*年\s*(?P<ym>\d{1,2})\s*月"
    r"|(?<!\d)(?P<m>\d{1,2})\s*月(?!\d)"
    r"|上个月|上月|本月|这个月|当月|最近一期|最新一期"
)

# 「从X到Y」显式区间
_DATE_TOKEN = (
    r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}日?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日"
)
FROM_TO_RE = re.compile(rf"从\s*({_DATE_TOKEN})\s*(?:到|至|—|~)\s*({_DATE_TOKEN})")
SINGLE_DATE_RE = re.compile(rf"(?<!\d)({_DATE_TOKEN})(?!\d)")

# 新增简单表达式
QIANTIAN_RE = re.compile(r"前天")
BENZHOU_RE = re.compile(r"本周|这一周")
YTD_RE = re.compile(r"今年以来|年初至今|今年")
RECENT_N_DAY_RE = re.compile(r"(?:最近|近|过去)\s*(\d{1,3})\s*天")
RECENT_N_WEEK_RE = re.compile(r"(?:最近|近|过去)\s*(\d{1,2})\s*周")

# 「X比Y」相邻比较:排除 对比/比较/环比/同比/相比(比 前一字为 对/较/环/同/相)
PAIR_CMP_RE = re.compile(r"(?<![对较环同相])比")
# 「A对比B」显式对比词(v16):两侧均须解析出具体时间短语才算对比问法
# (在 _parse_pair 中与 PAIR_CMP_RE 的命中位合并,防止既有短语误配)
PAIR_DUIBI_RE = re.compile(r"对比")
PREV_N_FULL_RE = re.compile(r"上一个?\s*\d{1,3}\s*天|前一个\s*\d{1,3}\s*天")

# 单个时间短语(供「比」两侧提取;长形式在前)
_T_REL = r"(?:上个月|上月|本月|这个月|当月|最近一期|最新一期)"
TIME_EXPR_RE = re.compile(
    r"上一个?\s*\d{1,3}\s*天"
    r"|(?:最近|近|过去)\s*\d{1,3}\s*天"
    r"|(?:最近|近|过去)\s*\d{1,2}\s*周"
    r"|(?<!\d)\d{4}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}日?(?!\d)"
    r"|\d{4}\s*年\s*\d{1,2}\s*月(?:上旬|中旬|下旬)?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日?"
    r"|\d{1,2}\s*月(?:上旬|中旬|下旬)?"
    r"|" + _T_REL + r"(?:上旬|中旬|下旬)?"
    r"|上旬|中旬|下旬"
    r"|本周|上周|这一周|昨天|昨日|前天|今年以来|年初至今"
)

_EMPTY = {"preset": None, "range": None, "pair": None}


# ---------------- 基础日期工具 ----------------

def _norm_asof(asof) -> dt.date:
    if asof is None:
        return DEFAULT_ASOF
    if isinstance(asof, dt.datetime):
        return asof.date()
    if isinstance(asof, dt.date):
        return asof
    try:
        return dt.date.fromisoformat(str(asof)[:10])
    except ValueError:
        return DEFAULT_ASOF


def month_last_day(year: int, month: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def month_range(year: int, month: int) -> list[str]:
    """自然月闭区间(与既有 rules.month_range 逐字等价)。"""
    start = dt.date(year, month, 1)
    end = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return [start.isoformat(), end.isoformat()]


def xun_range(year: int, month: int, which: str) -> list[str]:
    """旬区间:上旬 1-10 / 中旬 11-20 / 下旬 21-月末。"""
    if which == "上旬":
        return [dt.date(year, month, 1).isoformat(), dt.date(year, month, 10).isoformat()]
    if which == "中旬":
        return [dt.date(year, month, 11).isoformat(), dt.date(year, month, 20).isoformat()]
    return [dt.date(year, month, 21).isoformat(), month_last_day(year, month).isoformat()]


def _prev_month(asof: dt.date) -> tuple[int, int]:
    first = dt.date(asof.year, asof.month, 1)
    prev_last = first - dt.timedelta(days=1)
    return prev_last.year, prev_last.month


def _shift_days(rng: list[str], days: int) -> list[str]:
    s = dt.date.fromisoformat(rng[0]) - dt.timedelta(days=days)
    e = dt.date.fromisoformat(rng[1]) - dt.timedelta(days=days)
    return [s.isoformat(), e.isoformat()]


def _qualifier_month(text: str, asof: dt.date):
    """从短语中解析归属月 → (year, month) | None(显式月 > 上月 > 本月/最近一期)。"""
    m = XUN_MONTH_RE.search(text)
    if not m:
        return None
    if m.group("y"):
        y, mo = int(m.group("y")), int(m.group("ym"))
        return (y, mo) if 1 <= mo <= 12 else None
    if m.group("m"):
        mo = int(m.group("m"))
        return (asof.year, mo) if 1 <= mo <= 12 else None
    if m.group(0) in ("上个月", "上月"):
        return _prev_month(asof)
    return asof.year, asof.month


# ---------------- 各类表达式解析 ----------------

def _parse_xun(q: str, asof: dt.date):
    """旬:归属月取旬词前窗口内的月份限定词;无月份 → asof 所在月(契约 §16.1)。"""
    m = XUN_RE.search(q)
    if not m:
        return None
    window = q[: m.start()][-14:]
    ym = _qualifier_month(window, asof) or (asof.year, asof.month)
    return xun_range(ym[0], ym[1], m.group(1))


def _parse_date_token(tok: str, asof: dt.date):
    t = tok.strip().rstrip("日").replace("年", "-").replace("月", "-").replace("/", "-")
    parts = [p for p in t.split("-") if p]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        y, mo, dd = nums
    elif len(nums) == 2:
        y, mo, dd = asof.year, nums[0], nums[1]
    else:
        return None
    if not (1 <= mo <= 12 and 1 <= dd <= 31):
        return None
    try:
        return dt.date(y, mo, dd)
    except ValueError:
        return None


def _parse_from_to(q: str, asof: dt.date):
    """显式「从X到Y」区间(支持 YYYY-MM-DD / YYYY年MM月DD日 / MM月DD日)。"""
    m = FROM_TO_RE.search(q)
    if not m:
        return None
    d1 = _parse_date_token(m.group(1), asof)
    d2 = _parse_date_token(m.group(2), asof)
    if not d1 or not d2 or d1 > d2:
        return None
    return [d1.isoformat(), d2.isoformat()]


def _parse_month(q: str, asof: dt.date):
    """具体月份(与既有 parse_time_range 逐字等价):2026年10月 / 10月 → [月初, 月末]。"""
    m = MONTH_FULL_RE.search(q)
    if m and 1 <= int(m.group(2)) <= 12:
        return month_range(int(m.group(1)), int(m.group(2)))
    m = MONTH_BARE_RE.search(q)
    if m and 1 <= int(m.group(1)) <= 12:
        return month_range(asof.year, int(m.group(1)))
    return None


def _parse_simple(q: str, asof: dt.date):
    if QIANTIAN_RE.search(q):
        d = asof - dt.timedelta(days=2)
        return [d.isoformat(), d.isoformat()]
    if BENZHOU_RE.search(q):
        monday = asof - dt.timedelta(days=asof.weekday())
        return [monday.isoformat(), asof.isoformat()]
    if YTD_RE.search(q):
        return [dt.date(asof.year, 1, 1).isoformat(), asof.isoformat()]
    m = RECENT_N_DAY_RE.search(q)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 365:
            return [(asof - dt.timedelta(days=n - 1)).isoformat(), asof.isoformat()]
    m = RECENT_N_WEEK_RE.search(q)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 52:
            return [(asof - dt.timedelta(days=7 * n - 1)).isoformat(), asof.isoformat()]
    return None


def _match_preset(q: str, preset_patterns) -> str | None:
    """既有 preset 词表命中(顺序敏感,逐字沿用 time_words 行为)。"""
    for pat, preset in (preset_patterns or []):
        try:
            if re.search(pat, q):
                return preset
        except re.error:
            continue
    return None


# ---------------- 「比」两侧短语解析(相邻比较) ----------------

def _resolve_expr(text: str, asof: dt.date, month_ctx=None):
    """解析单个时间短语(对比语境)→ [d1, d2] | None。

    对比语境下「本周/上周」按日历周(周一~周日);独立问法中「上周」仍沿用
    last_7d(由 preset 词表处理,行为不变)。裸旬继承 month_ctx(主窗口月份)。
    """
    t = (text or "").strip().strip(" \u3000的").strip()
    if not t:
        return None
    if t in ("本周", "这一周"):
        monday = asof - dt.timedelta(days=asof.weekday())
        return [monday.isoformat(), asof.isoformat()]
    if t == "上周":
        monday = asof - dt.timedelta(days=asof.weekday())
        return [(monday - dt.timedelta(days=7)).isoformat(),
                (monday - dt.timedelta(days=1)).isoformat()]
    m = XUN_RE.search(t)
    if m:
        ym = _qualifier_month(t, asof) or month_ctx or (asof.year, asof.month)
        return xun_range(ym[0], ym[1], m.group(1))
    if re.fullmatch(r"昨天|昨日", t):
        return [asof.isoformat(), asof.isoformat()]
    if re.fullmatch(r"前天", t):
        d = asof - dt.timedelta(days=2)
        return [d.isoformat(), d.isoformat()]
    if re.search(r"上个月|上月", t):
        return month_range(*_prev_month(asof))
    if re.search(r"本月|这个月|当月|最近一期|最新一期", t):
        return [dt.date(asof.year, asof.month, 1).isoformat(), asof.isoformat()]
    if re.fullmatch(r"今年以来|年初至今|今年", t):
        return [dt.date(asof.year, 1, 1).isoformat(), asof.isoformat()]
    # ISO 日期(2026-11-05 / 2026/11/5)与「11月5日」:单日闭区间(v16)
    m = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})日?", t)
    if m:
        try:
            d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return [d.isoformat(), d.isoformat()]
    m = re.fullmatch(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", t)
    if m and 1 <= int(m.group(1)) <= 12:
        try:
            d = dt.date(asof.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        return [d.isoformat(), d.isoformat()]
    m = re.fullmatch(r"(?:最近|近|过去)\s*(\d{1,3})\s*天", t)
    if m and 1 <= int(m.group(1)) <= 365:
        n = int(m.group(1))
        return [(asof - dt.timedelta(days=n - 1)).isoformat(), asof.isoformat()]
    m = re.fullmatch(r"(?:最近|近|过去)\s*(\d{1,2})\s*周", t)
    if m and 1 <= int(m.group(1)) <= 52:
        n = int(m.group(1))
        return [(asof - dt.timedelta(days=7 * n - 1)).isoformat(), asof.isoformat()]
    m = MONTH_FULL_RE.fullmatch(t)
    if m and 1 <= int(m.group(2)) <= 12:
        return month_range(int(m.group(1)), int(m.group(2)))
    m = MONTH_BARE_RE.fullmatch(t)
    if m and 1 <= int(m.group(1)) <= 12:
        return month_range(asof.year, int(m.group(1)))
    return None


def _month_ctx_of(rng: list[str]):
    """主窗口所在月(起止同月才继承,供对比侧裸旬使用)。"""
    try:
        s = dt.date.fromisoformat(rng[0])
        e = dt.date.fromisoformat(rng[1])
    except ValueError:
        return None
    return (s.year, s.month) if (s.year, s.month) == (e.year, e.month) else None


def _parse_pair(q: str, asof: dt.date):
    """「X比Y」相邻比较 → {main, main_label, compare, compare_label} | None。

    - 连接词:「比」(PAIR_CMP_RE,排除 对比/比较/环比/同比/相比)与「对比」
      (PAIR_DUIBI_RE,v16:两侧均须解析出具体时间短语,防泛指误配);
    - 左侧取「比」之前最后一个时间短语;右侧取「比」之后紧邻的时间短语;
    - 两侧均解析成功才算对比问法,否则回退常规解析;
    - 右侧「上一个N天」按主窗口等长平移;右侧裸旬继承主窗口月份。
    """
    hits = list(PAIR_CMP_RE.finditer(q)) + list(PAIR_DUIBI_RE.finditer(q))
    for cm in sorted(hits, key=lambda m: m.start()):
        pos = cm.end() - 1                       # 「比」字符位置(两种连接词均以比收尾)
        rm = TIME_EXPR_RE.match(q[pos + 1:].lstrip())
        if not rm:
            continue
        right_txt = rm.group(0)
        left = None
        for lm in TIME_EXPR_RE.finditer(q, 0, pos):
            left = lm
        if left is None:
            continue
        left_txt = left.group(0)
        main = _resolve_expr(left_txt, asof)
        if main is None:
            continue
        if PREV_N_FULL_RE.fullmatch(right_txt):
            # 「上一个N天」:主窗口等长向前平移(与 prev_period 语义一致)
            s = dt.date.fromisoformat(main[0])
            e = dt.date.fromisoformat(main[1])
            shift = (e - s).days + 1
            comp = _shift_days(main, shift)
        else:
            comp = _resolve_expr(right_txt, asof, month_ctx=_month_ctx_of(main))
            if comp is None:
                continue
        return {"main": main, "main_label": left_txt,
                "compare": comp, "compare_label": right_txt}
    return None


def _parse_single_date(q: str, asof: dt.date):
    """单个具体日期(2026-11-05 / 2026/11/5 / 11月5日)→ 单日闭区间(v16)。"""
    m = SINGLE_DATE_RE.search(q)
    if not m:
        return None
    return _resolve_expr(m.group(1), asof)


# ---------------- 对外入口 ----------------

def parse(question, asof=None, preset_patterns=None) -> dict:
    """中文时间短语 → IR time 结构。优先级:对比对 > 旬 > preset > 从X到Y > 单日 > 月份 > 简单式。"""
    q = str(question or "")
    asof = _norm_asof(asof)
    result = dict(_EMPTY)
    if not q.strip():
        return result
    pair = _parse_pair(q, asof)
    if pair:
        result["pair"] = pair
        return result
    rng = _parse_xun(q, asof)
    if rng:
        result["range"] = rng
        return result
    preset = _match_preset(q, preset_patterns)
    if preset:
        result["preset"] = preset
        return result
    rng = _parse_from_to(q, asof)
    if rng:
        result["range"] = rng
        return result
    rng = _parse_single_date(q, asof)
    if rng:
        result["range"] = rng
        return result
    rng = _parse_month(q, asof)
    if rng:
        result["range"] = rng
        return result
    rng = _parse_simple(q, asof)
    if rng:
        result["range"] = rng
    return result
