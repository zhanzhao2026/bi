"""答案卡构建辅助:数字格式化(千分位/万元/百分比)与图表结构。

契约 §10:数值全部来自结果集,千分位、万元换算由后端完成;
金额 >10000 元输出形如 "351.2万"。
"""
import math


def fmt_amount(v) -> str:
    """金额:>10000 元换算万元(351.2万),否则千分位。"""
    if v is None:
        return "—"
    v = float(v)
    if not math.isfinite(v):
        return "—"
    if abs(v) >= 10000:
        wan = v / 10000.0
        s = f"{wan:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return f"{s}万"
    s = f"{v:,.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return s


def fmt_ratio(v) -> str:
    """比率(v 为百分数值,如 2.80)→ "2.80%\""""
    if v is None:
        return "—"
    return f"{float(v):.2f}%"


def fmt_pct1(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.1f}%"


def fmt_count(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.0f}"


def fmt_pp(v) -> str:
    """百分点差值,带符号两位小数。"""
    if v is None:
        return "—"
    return f"{float(v):+.2f}pp"


def fmt_signed(v, kind: str = "amount") -> str:
    """带符号变化量:kind="pp" 百分点 / "amount" 金额(万元换算)/ "count" 计数。"""
    if v is None:
        return "—"
    v = float(v)
    if kind == "pp":
        return f"{v:+.2f}pp"
    sign = "+" if v >= 0 else "-"
    if kind == "amount":
        return f"{sign}{fmt_amount(abs(v))}"
    return f"{sign}{fmt_count(abs(v))}"


def fmt_pp_plain(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}pp"


def fmt_num1(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.1f}"


def make_delta(new, old, mode: str = "pct", higher_is_bad: bool = False,
               label: str = "较上一周期"):
    """构造 KPI delta:{pct, dir, label, good}。

    mode="pp":比率指标,pct 为百分点差;mode="pct":相对变化百分比。
    """
    if new is None or old is None:
        return None
    new, old = float(new), float(old)
    if mode == "pp":
        pct = round(new - old, 2)
    else:
        pct = round((new - old) / old * 100, 1) if abs(old) > 1e-9 else None
        if pct is None:
            return None
    if abs(pct) < (0.005 if mode == "pp" else 0.05):
        direction = "flat"
        good = True
    elif pct > 0:
        direction, good = "up", not higher_is_bad
    else:
        direction, good = "down", higher_is_bad
    return {"pct": pct, "dir": direction, "label": label, "good": good}


def line_chart(title: str, x: list, series: list) -> dict:
    return {"kind": "line", "title": title, "x": x, "series": series}


def bar_chart(title: str, x: list, series: list) -> dict:
    return {"kind": "bar", "title": title, "x": x, "series": series}


def group_chart(title: str, x: list, series: list) -> dict:
    return {"kind": "group", "title": title, "x": x, "series": series}


def waterfall_chart(title: str, base_name: str, items: list, total_name: str) -> dict:
    return {
        "kind": "waterfall",
        "title": title,
        "base": {"name": base_name},
        "items": items,
        "total": {"name": total_name},
    }


def table(columns: list, rows: list) -> dict:
    return {"columns": columns, "rows": rows}


def col(key: str, label: str, align: str = "left") -> dict:
    return {"key": key, "label": label, "align": align}
