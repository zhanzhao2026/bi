"""LLM system prompt 渲染(契约 §15.1):planner/narrator 的 system prompt 由语义层对象渲染。

指标清单(key/name/unit/plain/ratio)、维度与筛值、术语/守卫规则均取自 semantic/
(models/metrics/dims/terms),prompt 文本不再内联在 gateway.py;词是数据,渲染逻辑在此。
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅为类型标注,避免运行时循环依赖
    from ..semantic.loader import Semantic

# IR JSON Schema 约束(结构契约,与 CONTRACT §4 一致)
_IR_SCHEMA_TMPL = (
    '{{"metric": "指标key", "time": {{"preset": "{presets}"'
    ' 或 {{"range": ["YYYY-MM-DD","YYYY-MM-DD"]}}}}, "dims": [], "filters": {{"category": [],'
    ' "channel": [], "user_type": []}}, "granularity": "day", "compare": "none|prev_period",'
    ' "order": "desc", "limit": 10}}'
)


def render_planner_system(sem: "Semantic", options: dict | None = None) -> str:
    """planner system prompt:语义层指标/维度/筛值/术语 + IR Schema 约束。"""
    options = options or {}
    lines: list[str] = []
    lines.append("你是电商 BI 系统的语义解析器,把用户的中文数据问题转换为查询 IR(中间表示)JSON。")
    lines.append("")
    lines.append("可用指标(格式: key(名称, 单位[, 比率]) — 口径说明):")
    for k, m in sem.metrics.items():
        ratio = ", 比率" if m.get("ratio") else ""
        lines.append(f"- {k}({m.get('name', '')}, {m.get('unit', '')}{ratio}): {m.get('plain', '')}")
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

    schema = _IR_SCHEMA_TMPL.format(presets="|".join(sem.time_presets))
    lines.append(f"IR JSON Schema(只能输出该 JSON,不要输出任何解释或代码块): {schema}")
    lines.append(
        "规则: 1) metric 必须从可用指标中选; 2) 时间不明确时用 last_30d; 3) 只有用户明确要求"
        "与上一期对比时 compare 才用 prev_period; 4) 趋势类问题 dims 为 [\"date\"]; "
        "5) 不确定的筛选值留空数组,绝不编造。"
    )
    lines.append("只输出一个 JSON 对象。")
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
