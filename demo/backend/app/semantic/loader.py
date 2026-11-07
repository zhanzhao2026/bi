"""语义层加载器 v2(契约 §15.1):解析 backend/semantic/ 目录结构。

    semantic/
    ├── models.yml          # 事实表声明(default_table/alias/grain/date_col)
    ├── metrics/base.yml    # 基础指标(直接聚合事实表列)
    ├── metrics/derived.yml # 派生指标(depends_on + expr 引用)
    ├── dims.yml            # 维度 + 时间口径
    ├── terms.yml           # 术语表(forbidden/boundary/clarify/time_words/synonyms/intent)
    └── version.yml         # 语义版本

- 派生指标在启动时按依赖图拓扑展开:expr 中 {key} 引用替换为所依赖指标的完整 agg;
  循环依赖 / 未知引用 / 缺字段 → 启动即抛错(fail fast)。
- 对外暴露的 metric dict 字段与 v12 完全一致:name/plain/unit/agg(+/derived/ratio/higher_is_bad),
  展开后的 agg 与 v12 metrics.yaml 逐字等价(admin/caliber/编译器调用点零改动)。
"""
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import yaml

from ..core.db import query

logger = logging.getLogger("biz.semantic")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SEMANTIC_DIR = BASE_DIR / "semantic"

_TOKEN_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


class SemanticError(Exception):
    """语义层配置错误(启动即失败,不允许带病运行)。"""


class Semantic:
    """语义层对象:指标(依赖图展开后)/维度/术语/模型声明。"""

    def __init__(self, root: Path = SEMANTIC_DIR):
        root = Path(root)
        self.root = root
        models = self._load(root / "models.yml")
        dims_doc = self._load(root / "dims.yml")
        version_doc = self._load(root / "version.yml")
        terms_doc = self._load(root / "terms.yml")
        base_doc = self._load(root / "metrics" / "base.yml")
        derived_doc = self._load(root / "metrics" / "derived.yml")

        # ---- 模型声明 ----
        self.models: dict = models or {}
        tables = self.models.get("tables") or {}
        if not tables:
            raise SemanticError("models.yml 缺少 tables 声明")
        default_table = self.models.get("default_table") or next(iter(tables))
        if default_table not in tables:
            raise SemanticError(f"models.yml default_table 未在 tables 中声明: {default_table}")
        tcfg = tables.get(default_table) or {}
        missing = [k for k in ("alias", "grain", "date_col") if not tcfg.get(k)]
        if missing:
            raise SemanticError(f"models.yml 表 {default_table} 缺少声明: {'/'.join(missing)}")
        self.fact_table = default_table
        self.fact_alias = str(tcfg["alias"])
        self.fact_grain = str(tcfg["grain"])
        self.fact_date_col = str(tcfg["date_col"])

        # ---- 版本 / 维度 / 术语 ----
        self.version = int(version_doc.get("version", 0))
        self.data_as_of_query = (self.models.get("data_as_of_query")
                                 or f"SELECT MAX({self.fact_date_col}) FROM {self.fact_table}")
        self.dims: dict = dims_doc.get("dims", {}) or {}
        self.time_presets: list = list(dims_doc.get("time_presets", []) or [])
        # 层级维度(§16.2):children 引用必须已在 dims 中声明(fail fast)
        for d, cfg in self.dims.items():
            for child in ((cfg or {}).get("children") or []):
                if child not in self.dims:
                    raise SemanticError(f"维度 {d} 的层级子维未声明: {child}")
        self.terms: dict = terms_doc or {}
        self._validate_terms(self.terms)

        # ---- 指标:base + derived 拓扑展开 ----
        self.metrics: dict = self._build_metrics(base_doc, derived_doc)
        # 原始定义(含 depends_on/expr,供测试与口径展示;不进对外 metric dict)
        self.metric_defs: dict = {
            "base": dict(base_doc.get("metrics") or {}),
            "derived": dict(derived_doc.get("metrics") or {}),
        }

    # ---------- 文件加载 ----------
    @staticmethod
    def _load(path: Path) -> dict:
        if not path.exists():
            raise SemanticError(f"语义层文件缺失: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise SemanticError(f"语义层 YAML 解析失败: {path}: {e}") from e
        if doc is None:
            raise SemanticError(f"语义层文件为空: {path}")
        if not isinstance(doc, dict):
            raise SemanticError(f"语义层文件结构应为映射: {path}")
        return doc

    @staticmethod
    def _validate_terms(terms: dict):
        for key in ("forbidden", "boundary", "time_words"):
            if not terms.get(key):
                raise SemanticError(f"terms.yml 缺少词表: {key}")
        clarify = terms.get("clarify") or {}
        if not clarify.get("vague_words") or not clarify.get("options"):
            raise SemanticError("terms.yml 缺少 clarify.vague_words / clarify.options")
        if not (terms.get("synonyms") or {}).get("metric_patterns"):
            raise SemanticError("terms.yml 缺少 synonyms.metric_patterns")
        if not terms.get("intent"):
            raise SemanticError("terms.yml 缺少 intent 词表")

    # ---------- 指标依赖图 ----------
    def _build_metrics(self, base_doc: dict, derived_doc: dict) -> dict:
        base = base_doc.get("metrics") or {}
        derived = derived_doc.get("metrics") or {}
        if not base:
            raise SemanticError("metrics/base.yml 缺少 metrics 声明")
        dup = sorted(set(base) & set(derived))
        if dup:
            raise SemanticError(f"指标重复定义(base/derived): {', '.join(dup)}")

        # 基础指标:字段原样暴露(name/plain/unit/agg/...)
        metrics: dict = {}
        agg_of: dict = {}
        for key, d in base.items():
            d = d or {}
            if not d.get("agg"):
                raise SemanticError(f"基础指标 {key} 缺少 agg")
            metrics[key] = dict(d)
            agg_of[key] = str(d["agg"])

        # 派生指标:校验 depends_on/expr
        raw: dict = {}
        for key, d in derived.items():
            d = d or {}
            expr = d.get("expr")
            deps = d.get("depends_on") or []
            if not expr:
                raise SemanticError(f"派生指标 {key} 缺少 expr")
            if not deps:
                raise SemanticError(f"派生指标 {key} 缺少 depends_on")
            for dep in deps:
                if dep not in base and dep not in derived:
                    raise SemanticError(f"派生指标 {key} 依赖未知指标: {dep}")
            for token in _TOKEN_RE.findall(str(expr)):
                if token not in deps:
                    raise SemanticError(
                        f"派生指标 {key} 的 expr 引用 {{{token}}} 未声明在 depends_on")
            raw[key] = {"deps": list(deps), "expr": str(expr), "def": d}

        # 拓扑展开(DFS;visiting 集合检测循环依赖)
        resolving: list = []

        def resolve(key: str) -> str:
            if key in agg_of:
                return agg_of[key]
            if key not in raw:
                raise SemanticError(f"未知指标引用: {key}")
            if key in resolving:
                cycle = " -> ".join(resolving[resolving.index(key):] + [key])
                raise SemanticError(f"指标循环依赖: {cycle}")
            resolving.append(key)
            info = raw[key]
            agg = info["expr"]
            for dep in info["deps"]:
                agg = agg.replace("{" + dep + "}", resolve(dep))
            resolving.pop()
            agg_of[key] = agg
            d = dict(info["def"])
            d["agg"] = agg
            d.pop("depends_on", None)   # 对外字段与 v12 完全一致:不含 depends_on/expr
            d.pop("expr", None)
            metrics[key] = d
            return agg

        for key in raw:
            resolve(key)
        return metrics

    # ---------------- 指标元数据(与 v12 接口一致) ----------------
    def metric(self, key: str):
        return self.metrics.get(key)

    def metric_name(self, key: str) -> str:
        m = self.metrics.get(key)
        return m["name"] if m else key

    def metric_type(self, key: str) -> str:
        m = self.metrics.get(key)
        if not m:
            return "unknown"
        if m.get("ratio"):
            return "ratio"
        if m.get("derived"):
            return "derived"
        return "base"

    def is_ratio(self, key: str) -> bool:
        return bool((self.metrics.get(key) or {}).get("ratio"))

    def higher_is_bad(self, key: str) -> bool:
        return bool((self.metrics.get(key) or {}).get("higher_is_bad"))

    def dim_keys(self) -> list:
        return list(self.dims.keys())

    # ---------------- 层级维度(§16.2) ----------------
    def dim_children(self, dim: str) -> list:
        """维度的下层子维列表(如 category → [product])。"""
        return list((self.dims.get(dim) or {}).get("children") or [])

    def dim_parent(self, dim: str) -> str | None:
        """维度所属的父维(如 product → category);无层级声明时返回 None。"""
        for d, cfg in self.dims.items():
            if dim in ((cfg or {}).get("children") or []):
                return d
        return None

    # ---------------- 术语访问(rules/guards/prompts 使用) ----------------
    def term(self, *path, default=None):
        """按路径取术语词表,如 sem.term('synonyms', 'channel_aliases')。"""
        node = self.terms
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node


# ---------------- 过滤可选值(dim 表中文名,带缓存) ----------------
_filter_cache = {"ts": 0.0, "data": {}}
_filter_lock = threading.Lock()
_CACHE_TTL = 60.0


def get_filter_options(force: bool = False) -> dict:
    """返回 {"category": [名...], "channel": [名...], "user_type": ["新客","老客"]}"""
    now = time.time()
    with _filter_lock:
        if not force and _filter_cache["data"] and now - _filter_cache["ts"] < _CACHE_TTL:
            return _filter_cache["data"]
        data = {"category": [], "channel": [], "user_type": ["新客", "老客"]}
        try:
            rows = query("SELECT category_name FROM dim_category ORDER BY sort, category_id")
            data["category"] = [r["category_name"] for r in rows]
        except Exception as e:
            logger.warning("load dim_category failed: %s", e)
        try:
            rows = query("SELECT channel_name FROM dim_channel ORDER BY channel_id")
            data["channel"] = [r["channel_name"] for r in rows]
        except Exception as e:
            logger.warning("load dim_channel failed: %s", e)
        _filter_cache["ts"] = now
        _filter_cache["data"] = data
        return data


# ---------------- data_as_of(带缓存) ----------------
_asof_cache = {"ts": 0.0, "value": None}
_asof_lock = threading.Lock()


def get_data_as_of(max_age: float = 30.0):
    """数据水位:MAX(pay_date),YYYY-MM-DD 字符串;DB 不可用返回 None。"""
    now = time.time()
    with _asof_lock:
        if _asof_cache["value"] and now - _asof_cache["ts"] < max_age:
            return _asof_cache["value"]
    sem = get_semantic()
    try:
        row = query(sem.data_as_of_query)
        raw = list(row[0].values())[0] if row and row[0] else None
        value = str(raw) if raw is not None else None
    except Exception as e:
        logger.warning("data_as_of query failed: %s", e)
        value = None
    if value:
        with _asof_lock:
            _asof_cache["ts"] = now
            _asof_cache["value"] = value
    return value


def get_terms() -> dict:
    """术语词表(rules.py / guards.py / prompts.py 从这里加载,词是数据)。"""
    return get_semantic().terms


@lru_cache(maxsize=1)
def get_semantic() -> Semantic:
    return Semantic()
