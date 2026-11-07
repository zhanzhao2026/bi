"""会话上下文(契约 §6):内存 dict,记录最近一轮 IR 的时间与筛选,支持追问继承。"""
import threading
import time
import uuid

_store: dict = {}
_lock = threading.Lock()
_MAX = 2000
_TTL = 24 * 3600.0


def new_session_id() -> str:
    return uuid.uuid4().hex


def get(session_id: str):
    with _lock:
        item = _store.get(session_id)
        if not item:
            return None
        if time.time() - item["ts"] > _TTL:
            _store.pop(session_id, None)
            return None
        return item


def save(session_id: str, ir: dict):
    if not session_id:
        return
    with _lock:
        # 简单容量控制:超限清理最旧的一半
        if len(_store) >= _MAX:
            ordered = sorted(_store.items(), key=lambda kv: kv[1]["ts"])
            for k, _ in ordered[: _MAX // 2]:
                _store.pop(k, None)
        _store[session_id] = {"ir": ir, "ts": time.time()}


def summary(session_ctx) -> dict:
    """供 LLM planner 的会话上下文摘要。"""
    if not session_ctx:
        return {}
    ir = session_ctx.get("ir") or {}
    return {"time": ir.get("time"), "filters": ir.get("filters"), "metric": ir.get("metric")}
