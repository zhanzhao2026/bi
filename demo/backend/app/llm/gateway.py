"""LLM 网关(契约 §8):OpenAI 兼容 /chat/completions。

- httpx,timeout 20s,temperature 0.2
- 配置优先级:sys_settings 表 > .env
- llm_enabled = api_key 非空;未配置时全链路走规则兜底,演示不中断
- 两个角色:planner(→ 仅 IR JSON)/ narrator(结果集 → 2~3 句中文,禁造数字)
- api_key 永不回传前端,只给掩码 sk-***last4;日志脱敏
"""
import json
import logging
import re
import time

import httpx

from ..core.config import LLM_API_KEY_DEFAULT, LLM_BASE_URL_DEFAULT, LLM_MODEL_DEFAULT
from ..core.db import query

logger = logging.getLogger("biz.llm")

SETTLE_KEYS = ("llm_base_url", "llm_model", "llm_api_key")
_TIMEOUT = 20.0
_TEMPERATURE = 0.2


def mask_key(key: str) -> str:
    if not key:
        return ""
    return "sk-***" + key[-4:] if len(key) >= 4 else "sk-***"


class LLMGateway:
    def __init__(self):
        self._cache = {"ts": 0.0, "cfg": {}}
        self._ttl = 5.0

    # ---------------- 配置 ----------------
    def get_config(self, force: bool = False) -> dict:
        """sys_settings 表 > .env;带 5s 缓存。"""
        now = time.time()
        if not force and self._cache["cfg"] and now - self._cache["ts"] < self._ttl:
            return self._cache["cfg"]
        cfg = {
            "base_url": LLM_BASE_URL_DEFAULT,
            "model": LLM_MODEL_DEFAULT,
            "api_key": LLM_API_KEY_DEFAULT,
        }
        try:
            rows = query(
                "SELECT skey, svalue FROM sys_settings WHERE skey IN (%s, %s, %s)",
                list(SETTLE_KEYS),
            )
            for r in rows:
                v = r.get("svalue")
                if v is None:
                    continue
                if r["skey"] == "llm_base_url" and v.strip():
                    cfg["base_url"] = v.strip()
                elif r["skey"] == "llm_model" and v.strip():
                    cfg["model"] = v.strip()
                elif r["skey"] == "llm_api_key":
                    cfg["api_key"] = v.strip()
        except Exception as e:
            logger.warning("read sys_settings failed: %s", e)
        self._cache = {"ts": now, "cfg": cfg}
        return cfg

    def invalidate(self):
        self._cache = {"ts": 0.0, "cfg": {}}

    def enabled(self) -> bool:
        return bool(self.get_config().get("api_key"))

    def public_settings(self) -> dict:
        cfg = self.get_config()
        key = cfg.get("api_key") or ""
        return {
            "llm_enabled": bool(key),
            "base_url": cfg.get("base_url"),
            "model": cfg.get("model"),
            "has_key": bool(key),
            "key_masked": mask_key(key),
        }

    # ---------------- 底层调用 ----------------
    def chat(self, messages: list, max_tokens: int = 512, temperature: float = _TEMPERATURE,
             timeout: float = _TIMEOUT) -> str:
        cfg = self.get_config()
        base = (cfg.get("base_url") or "").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg.get('api_key')}", "Content-Type": "application/json"}
        payload = {
            "model": cfg.get("model"),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        return content or ""

    # ---------------- planner:问题 → IR JSON ----------------
    def plan_ir(self, question: str, semantic, session_summary: dict, options: dict) -> dict | None:
        metrics_desc = "; ".join(
            f"{k}({m.get('name', '')})" for k, m in semantic.metrics.items()
        )
        schema = (
            '{"metric": "指标key", "time": {"preset": "yesterday|last_7d|last_30d|this_month|last_month"'
            ' 或 {"range": ["YYYY-MM-DD","YYYY-MM-DD"]}}, "dims": [], "filters": {"category": [],'
            ' "channel": [], "user_type": []}, "granularity": "day", "compare": "none|prev_period",'
            ' "order": "desc", "limit": 10}'
        )
        system = (
            "你是电商 BI 系统的语义解析器,把用户的中文数据问题转换为查询 IR(中间表示)JSON。\n"
            f"可用指标: {metrics_desc}\n"
            "可用维度(dims): date, category, channel, user_type\n"
            f"可用时间口径(preset): {', '.join(semantic.time_presets)}\n"
            f"可用筛选值 category: {options.get('category', [])}; channel: {options.get('channel', [])};"
            " user_type: [新客, 老客]\n"
            f"IR JSON Schema(只能输出该 JSON,不要输出任何解释或代码块): {schema}\n"
            "规则: 1) metric 必须从可用指标中选; 2) 时间不明确时用 last_30d; 3) 只有用户明确要求"
            "与上一期对比时 compare 才用 prev_period; 4) 趋势类问题 dims 为 [\"date\"]; "
            "5) 不确定的筛选值留空数组,绝不编造。\n"
            "只输出一个 JSON 对象。"
        )
        user = f"会话上下文: {json.dumps(session_summary, ensure_ascii=False)}\n用户问题: {question}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err = None
        for attempt in range(2):  # 1 次重试
            try:
                text = self.chat(messages, max_tokens=400)
                ir = _extract_json(text)
                if ir is None:
                    raise ValueError("输出不是合法 JSON")
                logger.info("planner ok (attempt %s)", attempt + 1)
                return ir
            except Exception as e:
                last_err = e
                logger.warning("planner attempt %s failed: %s", attempt + 1, e)
                messages = messages[:2] + [
                    {"role": "assistant", "content": "(上一条输出无效)"},
                    {"role": "user", "content": f"{user}\n注意:上次输出无效({last_err}),请只输出符合 Schema 的 JSON。"},
                ]
        return None

    # ---------------- narrator:结果集 → 2~3 句中文 ----------------
    def narrate(self, payload: dict) -> list[str] | None:
        system = (
            "你是电商 BI 助手的结论撰写器。根据给定的统计数据,输出 2~3 句简短的中文结论。\n"
            "硬性要求:1) 禁止编造数字,所有数字必须与输入完全一致(原样引用);\n"
            "2) 不出现 SQL/IR/接口等技术词;3) 每句一行,直接给句子,不要编号。"
        )
        user = "统计数据:\n" + json.dumps(payload, ensure_ascii=False)
        try:
            text = self.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=300,
            )
        except Exception as e:
            logger.warning("narrator failed: %s", e)
            return None
        sents = [s.strip() for s in re.split(r"[。!?\n]", text or "") if s.strip()]
        sents = sents[:3]
        if not sents:
            return None
        # 防幻觉校验:输出中的数字必须能在输入里找到
        input_text = json.dumps(payload, ensure_ascii=False)
        for s in sents:
            for num in re.findall(r"\d+(?:\.\d+)?", s):
                if num not in input_text:
                    logger.warning("narrator hallucinated number %s, fallback to rules", num)
                    return None
        return sents

    # ---------------- 连通性测试 ----------------
    def test_connection(self) -> dict:
        if not self.enabled():
            return {"ok": False, "latency_ms": 0, "model": self.get_config().get("model"),
                    "error": "未配置 API Key,当前为规则兜底模式"}
        t0 = time.perf_counter()
        try:
            reply = self.chat(
                [{"role": "user", "content": "请回复:ok"}],
                max_tokens=1,
            )
            ms = int((time.perf_counter() - t0) * 1000)
            return {"ok": True, "latency_ms": ms,
                    "model": self.get_config().get("model"), "reply": reply.strip() or "(空)"}
        except Exception as e:
            ms = int((time.perf_counter() - t0) * 1000)
            return {"ok": False, "latency_ms": ms,
                    "model": self.get_config().get("model"), "error": _safe_err(e)}


def _extract_json(text: str):
    """从模型输出中提取 JSON 对象(容忍 ```json 围栏)。"""
    if not text:
        return None
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _safe_err(e: Exception) -> str:
    s = str(e)
    return re.sub(r"(sk-[A-Za-z0-9]+)", "sk-***", s)[:300]
