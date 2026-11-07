"""守卫规则(契约 §7,顺序执行):薪酬/PII → refuse;明细/具体商品 → boundary;模糊指标词且无时间词 → clarify。

词表(forbidden/boundary/clarify)全部来自 semantic/terms.yml(词是数据,
经 app/semantic/loader 加载);命中顺序与判断逻辑留在本模块。
"""
import re

from ..semantic.loader import get_semantic

_T = get_semantic().terms

REFUSE_WORDS = [str(w) for w in (_T.get("forbidden") or [])]
BOUNDARY_WORDS = [str(w) for w in (_T.get("boundary") or [])]
_CLARIFY = _T.get("clarify") or {}
VAGUE_WORDS = [str(w) for w in (_CLARIFY.get("vague_words") or [])]
CLARIFY_OPTIONS = [dict(o) for o in (_CLARIFY.get("options") or [])]
CLARIFY_ASSUME_METRIC = str(_CLARIFY.get("assume_metric") or "gmv")
if not REFUSE_WORDS or not BOUNDARY_WORDS or not VAGUE_WORDS or not CLARIFY_OPTIONS:
    raise RuntimeError("terms.yml 缺少 forbidden/boundary/clarify 词表(守卫拒答词不能为空)")

_TIME_RE = re.compile(str(_CLARIFY.get("time_detect") or r"$^"))


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
