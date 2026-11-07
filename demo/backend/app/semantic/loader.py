"""语义层加载器:解析 semantic/metrics.yaml,提供指标/维度/过滤值元数据。"""
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path

import yaml

from ..core.db import query

logger = logging.getLogger("biz.semantic")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SEMANTIC_PATH = BASE_DIR / "semantic" / "metrics.yaml"


class Semantic:
    def __init__(self, path: Path = SEMANTIC_PATH):
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        self.version = int(doc.get("version", 0))
        self.data_as_of_query = doc.get("data_as_of_query", "SELECT MAX(pay_date) FROM fact_order")
        self.metrics: dict = doc.get("metrics", {}) or {}
        self.dims: dict = doc.get("dims", {}) or {}
        self.time_presets: list = doc.get("time_presets", []) or []

    # ---- 指标元数据 ----
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


@lru_cache(maxsize=1)
def get_semantic() -> Semantic:
    return Semantic()
