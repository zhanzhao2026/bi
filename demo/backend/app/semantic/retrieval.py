"""BM25 语义检索(契约 §16.3):对语义层对象(metrics/dims/terms)做关键词检索。

- 纯 Python BM25(k1=1.5,b=0.75),零新增依赖;打分采用字段加权变体(BM25F-lite):
  语料按字段(名称/plain/同义词/取值...)分桶,桶内 token 去重后按字段权重求和,
  再代入标准 BM25 公式(idf×tf·(k1+1)/(tf+k1·(1-b+b·|D|/avgdl)));
- 分词:中文按 bigram(单字也算 token),英文/数字取整词(忽略大小写);
- 语料:metrics(name/plain/unit + terms.yml 中该指标相关的同义词)+ dims(desc/
  user_terms + 维度取值,便于「美妆」这类筛值命中维度)+ terms 词表条目
  (forbidden/boundary/clarify.vague_words);
- 索引随 loader 加载构建(get_index),语义文件(版本/mtime)或维度取值变更时
  自动重建;rebuild() 强制重建;
- 检索入口:search(q, k=5) -> [{kind, key, name, score, reason}],reason 给出命中
  来源(如「plain 含『退款』」「同义词『退了』」);空 q/纯符号返回空列表。
"""
import logging
import math
import re
import threading
from pathlib import Path

logger = logging.getLogger("biz.semantic.retrieval")

K1 = 1.5   # BM25 词频饱和度
B = 0.75   # BM25 文档长度归一化强度

# 字段权重(进 BM25 主公式):名称类最强,plain 次之,同义词/取值是弱证据但可命中
_W_NAME = 2.5      # 指标名 / 维度说明 / 词表词
_W_VALUE = 2.0     # 维度取值(筛值精确命中,如「美妆」→ category)
_W_VAGUE = 2.0     # 澄清模糊词 → assume_metric 的官方口语映射(销售额→gmv)
_W_TERM = 1.5      # 维度用户说法
_W_PLAIN = 0.8     # 指标口径说明
_W_SYN = 0.5       # 同义词(口语说法)
_W_MINOR = 0.5     # key / 单位

_COVERAGE_BOOST = 0.5    # 名称被查询词覆盖率加成:名称解释得越充分,匹配越精确

_MAX_QUERY_LEN = 256          # 超长查询截断,防退化
_MAX_REASON_FRAGS = 3         # reason 最多展示的命中来源数

# CJK 连续段 / 英文数字整词
_RUN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")
_ASCII_RE = re.compile(r"[A-Za-z0-9_]+")
# 同义词 pattern 中的字面词(跳过正则元字符,如 退了?多少钱 → 退了/多少钱)
_LITERAL_RE = re.compile(r"[0-9A-Za-z_\u4e00-\u9fff]{2,}")


def _tokenize(text) -> list[str]:
    """中文按单字 + bigram,英文/数字整词(小写);供语料与查询两侧共用。"""
    tokens: list[str] = []
    for m in _RUN_RE.finditer(str(text or "")):
        run = m.group(0)
        if _ASCII_RE.fullmatch(run):
            tokens.append(run.lower())
            continue
        chars = list(run)
        tokens.extend(chars)                                   # 单字也算 token
        tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))  # bigram
    return tokens


def _pattern_words(pattern) -> list[str]:
    """从同义词正则 pattern 中提取字面词(≥2 字),作为可检索的口语说法。"""
    return _LITERAL_RE.findall(str(pattern or ""))


class _Doc:
    """语料文档:分字段的去重 token 集 + 每个 token 的命中来源(reason 用)。"""

    __slots__ = ("kind", "key", "name", "fields", "sources", "length", "primary")

    def __init__(self, kind: str, key: str, name: str):
        self.kind = kind
        self.key = key
        self.name = name
        self.fields: list[tuple[str, float, set]] = []   # (字段标签, 权重, token 集)
        self.sources: dict[str, tuple[str, str]] = {}    # token -> (来源标签, 展示词)
        self.length = 0.0
        self.primary: set = set()                        # 名称类字段 token(算覆盖率)

    def add_field(self, label: str, text, weight: float = _W_NAME,
                  primary: bool = False) -> None:
        """并入一个文本字段(名称/plain/说明...),reason 显示命中的查询词本身。"""
        if text is None:
            return
        toks = set(_tokenize(text))
        if not toks:
            return
        if primary:
            self.primary = toks
        self._add(label, weight, toks,
                  lambda tok: (label, tok))

    def add_words(self, label: str, words, weight: float = _W_SYN) -> None:
        """并入一组词(同义词/用户说法/取值),reason 显示命中的词本身。"""
        seen: set = set()
        word_of: dict[str, str] = {}
        for w in words or []:
            w = str(w)
            for tok in _tokenize(w):
                if tok not in seen:
                    seen.add(tok)
                    word_of[tok] = w
        if seen:
            self._add(label, weight, seen, lambda tok: (label, word_of.get(tok, tok)))

    def _add(self, label: str, weight: float, toks: set, src) -> None:
        self.fields.append((label, weight, toks))
        for tok in toks:
            if tok not in self.sources:
                self.sources[tok] = src(tok)
        self.length += weight * len(toks)

    def etf(self, tok: str) -> float:
        """字段加权词频:含该 token 的字段权重之和(桶内已去重)。"""
        return sum(w for _, w, toks in self.fields if tok in toks)


class SemanticIndex:
    """语义层 BM25 索引:构建一次,检索多次。"""

    def __init__(self, sem, dim_values: dict | None = None):
        self.version = getattr(sem, "version", 0)
        self._docs: list[_Doc] = []
        self._avgdl = 0.0
        self._idf: dict[str, float] = {}
        self._build(sem, dim_values or {})

    # ---------------- 语料构建 ----------------
    def _build(self, sem, dim_values: dict) -> None:
        docs: list[_Doc] = []

        # 1) 指标:name/plain/unit + terms.yml 中该指标相关的同义词
        metric_syn, metric_vague = self._collect_metric_synonyms(sem)
        for key, m in (sem.metrics or {}).items():
            m = m or {}
            doc = _Doc("metric", key, str(m.get("name") or key))
            doc.add_field("key", key, _W_MINOR)
            doc.add_field("名称", m.get("name"), _W_NAME, primary=True)
            doc.add_field("plain", m.get("plain"), _W_PLAIN)
            doc.add_field("单位", m.get("unit"), _W_MINOR)
            doc.add_words("同义词", metric_syn.get(key, []), _W_SYN)
            doc.add_words("同义词", metric_vague.get(key, []), _W_VAGUE)
            docs.append(doc)

        # 2) 维度:说明 / 用户说法 / 维度取值(便于「美妆」这类筛值命中维度)
        dim_syn = self._collect_dim_synonyms(sem)
        for key, cfg in (sem.dims or {}).items():
            cfg = cfg or {}
            name = str(cfg.get("desc") or cfg.get("name") or key)
            doc = _Doc("dim", key, name)
            doc.add_field("key", key, _W_MINOR)
            doc.add_field("说明", cfg.get("desc") or cfg.get("name"), _W_NAME, primary=True)
            doc.add_words("用户说法", [str(w) for w in (cfg.get("user_terms") or [])], _W_TERM)
            doc.add_words("取值", [str(v) for v in (dim_values.get(key) or [])], _W_VALUE)
            doc.add_words("同义词", dim_syn.get(key, []), _W_SYN)
            docs.append(doc)

        # 3) terms 词表条目:拒答词 / 明细边界词 / 澄清模糊词
        terms = getattr(sem, "terms", {}) or {}
        for word in terms.get("forbidden") or []:
            docs.append(self._term_doc("forbidden", "拒答词", word))
        for word in terms.get("boundary") or []:
            docs.append(self._term_doc("boundary", "边界词", word))
        for word in (terms.get("clarify") or {}).get("vague_words") or []:
            docs.append(self._term_doc("clarify", "澄清词", word))

        self._docs = docs
        self._finalize()

    @staticmethod
    def _term_doc(category: str, label: str, word) -> _Doc:
        word = str(word)
        doc = _Doc("term", f"{category}:{word}", word)
        doc.add_field(label, word, _W_NAME, primary=True)
        return doc

    @staticmethod
    def _collect_metric_synonyms(sem) -> tuple[dict, dict]:
        """terms.yml 同义词映射:返回 (metric_patterns 字面词, 澄清模糊词→assume_metric)。"""
        out: dict[str, list[str]] = {}
        vague_out: dict[str, list[str]] = {}
        terms = getattr(sem, "terms", {}) or {}
        for entry in (terms.get("synonyms") or {}).get("metric_patterns") or []:
            try:
                metric = str((entry or {}).get("metric") or "")
                words = _pattern_words((entry or {}).get("pattern"))
            except AttributeError:
                continue
            if metric and words:
                out.setdefault(metric, []).extend(words)
        clarify = terms.get("clarify") or {}
        assume = str(clarify.get("assume_metric") or "")
        vague = [str(w) for w in (clarify.get("vague_words") or [])]
        if assume and vague:
            vague_out.setdefault(assume, []).extend(vague)
        return out, vague_out

    @staticmethod
    def _collect_dim_synonyms(sem) -> dict[str, list[str]]:
        """terms.yml synonyms.dimension(若存在,如 P2-A 下钻词)→ 维度同义词;尽力而为。"""
        out: dict[str, list[str]] = {}
        entry = ((getattr(sem, "terms", {}) or {}).get("synonyms") or {}).get("dimension")
        try:
            if isinstance(entry, dict):
                for dim, pattern in entry.items():
                    out.setdefault(str(dim), []).extend(_pattern_words(pattern))
            elif isinstance(entry, list):
                for item in entry:
                    dim = str((item or {}).get("dim") or (item or {}).get("dimension") or "")
                    words = _pattern_words((item or {}).get("pattern"))
                    if dim and words:
                        out.setdefault(dim, []).extend(words)
        except AttributeError:
            return {}
        return out

    # ---------------- BM25 ----------------
    def _finalize(self) -> None:
        n = len(self._docs)
        total = sum(d.length for d in self._docs)
        self._avgdl = (total / n) if n else 0.0
        df: dict[str, int] = {}
        for d in self._docs:
            toks = {tok for _, _, f in d.fields for tok in f}
            for tok in toks:
                df[tok] = df.get(tok, 0) + 1
        # 带 +1 平滑的 IDF,避免负值
        self._idf = {
            tok: math.log((n - c + 0.5) / (c + 0.5) + 1.0) for tok, c in df.items()
        }

    def _score(self, doc: _Doc, qtokens: list[str], qset: set) -> tuple[float, list[str]]:
        """返回 (BM25 得分, 按 IDF 降序的命中 token)。

        在标准 BM25(k1/b)之上叠加名称覆盖率加成:查询词对文档名称类字段的
        覆盖越充分(如「退款率」覆盖名称「退款率」全部词),匹配越精确。
        """
        if self._avgdl <= 0:
            return 0.0, []
        score = 0.0
        hits: list[tuple[float, str]] = []
        for tok in qtokens:                       # 查询词去重后的贡献
            tf = doc.etf(tok)
            if not tf:
                continue
            idf = self._idf.get(tok, 0.0)
            denom = tf + K1 * (1.0 - B + B * doc.length / self._avgdl)
            score += idf * tf * (K1 + 1.0) / denom
            hits.append((idf, tok))
        if score > 0.0 and doc.primary:
            cov = len(doc.primary & qset) / len(doc.primary)
            score *= 1.0 + _COVERAGE_BOOST * cov
        hits.sort(key=lambda x: (-x[0], x[1]))
        return score, [t for _, t in hits]

    # ---------------- 检索入口 ----------------
    def search(self, q, k: int = 5) -> list[dict]:
        """search(q, k=5) -> [{kind, key, name, score, reason}];空 q/纯符号返回 []。"""
        q = str(q or "").strip()[:_MAX_QUERY_LEN]
        try:
            k = int(k)
        except (TypeError, ValueError):
            k = 5
        k = max(1, min(k, 20))
        if not q or not _RUN_RE.search(q):        # 空 q / 纯符号
            return []

        qtokens = list(dict.fromkeys(_tokenize(q)))   # 去重保序
        qset = set(qtokens)
        scored: list[tuple[float, _Doc, list[str]]] = []
        for doc in self._docs:
            score, hits = self._score(doc, qtokens, qset)
            if score > 0.0:
                scored.append((score, doc, hits))
        scored.sort(key=lambda x: (-x[0], x[1].kind, x[1].key))

        results: list[dict] = []
        for score, doc, hits in scored[:k]:
            results.append({
                "kind": doc.kind,
                "key": doc.key,
                "name": doc.name,
                "score": round(score, 4),
                "reason": self._reason(doc, hits),
            })
        return results

    @staticmethod
    def _reason(doc: _Doc, hit_tokens: list[str]) -> str:
        """命中来源说明,如「名称 含『退款率』; plain 含『退款』; 同义词『退了』」。"""
        frags: list[str] = []
        for tok in hit_tokens:
            label, display = doc.sources.get(tok, ("命中", tok))
            frag = (f"{label}『{display}』" if label in ("同义词", "用户说法", "取值")
                    else f"{label} 命中『{display}』" if doc.kind == "term"
                    else f"{label} 含『{display}』")
            if frag not in frags:
                frags.append(frag)
            if len(frags) >= _MAX_REASON_FRAGS:
                break
        return "; ".join(frags) if frags else "命中"


# ---------------- 索引单例(随 loader 加载;语义文件/取值变更时重建) ----------------

_index_lock = threading.Lock()
_index_cache: dict = {"fp": None, "index": None}


def _semantic_dir() -> Path:
    from .loader import SEMANTIC_DIR
    return Path(SEMANTIC_DIR)


def _dim_values() -> dict:
    """维度取值(筛值可检索);DB 不可用时 loader 侧优雅降级为部分/空列表。"""
    try:
        from .loader import get_filter_options
        opts = get_filter_options() or {}
        return {k: list(v or []) for k, v in opts.items()}
    except Exception as e:                     # pragma: no cover - 防御
        logger.warning("load dim values for retrieval failed: %s", e)
        return {}


def _fingerprint(sem) -> tuple:
    """索引指纹:语义版本 + 语义文件 mtime + 维度取值;任一变更即重建。"""
    try:
        mtimes = tuple(
            sorted((p.name, int(p.stat().st_mtime))
                   for p in _semantic_dir().rglob("*.y*ml"))
        )
    except OSError:                            # pragma: no cover - 防御
        mtimes = ()
    return (getattr(sem, "version", 0), mtimes, tuple(sorted(
        (k, tuple(v)) for k, v in _dim_values().items())))


def build_index(sem=None, dim_values: dict | None = None) -> SemanticIndex:
    """构建索引(测试可注入语义对象与维度取值,不依赖单例)。"""
    if sem is None:
        from .loader import get_semantic
        sem = get_semantic()
    return SemanticIndex(sem, dim_values)


def get_index() -> SemanticIndex:
    """取缓存索引;指纹(版本/mtime/取值)变化时自动重建。"""
    from .loader import get_semantic
    with _index_lock:
        sem = get_semantic()
        fp = _fingerprint(sem)
        if _index_cache["index"] is None or _index_cache["fp"] != fp:
            _index_cache["index"] = SemanticIndex(sem, _dim_values())
            _index_cache["fp"] = fp
            logger.info("semantic BM25 index built (version=%s, docs=%s)",
                        getattr(sem, "version", 0), len(_index_cache["index"]._docs))
        return _index_cache["index"]


def rebuild() -> SemanticIndex:
    """强制重建索引(语义文件变更后可手动调用)。"""
    with _index_lock:
        _index_cache["fp"] = None
    return get_index()
