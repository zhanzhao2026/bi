#!/usr/bin/env python3
"""场景覆盖矩阵(§17 planner 加固 + §16.5 分析类扩容的质量护栏):
63 个场景 × 11 组,对运行中的后端做黑盒验证。

用法(标准库实现,可独立运行,不依赖 app 包与第三方库):
    cd backend && .venv/bin/python scripts/scenario_matrix.py            # 全量 63 场景
    .venv/bin/python scripts/scenario_matrix.py --group 时间             # 只跑某组
    .venv/bin/python scripts/scenario_matrix.py --filter T07,U01        # 只跑指定 id
    .venv/bin/python scripts/scenario_matrix.py --out /tmp/x.json       # 指定报告路径

- 只跑 LLM 模式(key 在 sys_settings,脚本绝不读取/修改/清除任何配置);
  规则模式表现以既有单测(run_tests.sh 300+ 项)与 eval 26/26 的已知结论
  作为「规则对照」静态列印在报告中,不重复执行。
- 每个场景:POST /api/chat(SSE)→ answer 卡 + done;再 GET /api/admin/audit/{trace_id}
  取 ir_json 做结构断言(metric/dims/filters/time/compare/limit/order)。
- 组⑦不可表达:必须全部诚实拒答(error 卡,title=「这个问题我答不了」);
  组⑥/⑦的期望由「答错比拒答严重」原则制定。
- 组⑨归因/⑩变化排名/⑪分析追问(v15):M5 归因泛化(比率+可加指标)、
  M4 两期变化贡献排名、「为什么」追问继承升级。
- 输出:控制台表格 + JSON 报告(默认 /tmp/scenario_report.json)。

时间锚点:data_as_of = 2026-11-06(与 models.yml data_as_of_query 的 DB 水位一致)。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

BASE_DEFAULT = "http://127.0.0.1:8310"
ASOF = date(2026, 11, 6)          # 数据水位(MAX(pay_date))
ASOF_S = ASOF.isoformat()

GROUPS = ["①时间", "②指标", "③维度筛选", "④商品下钻", "⑤排名", "⑥边界", "⑦不可表达", "⑧多轮继承",
          "⑨归因", "⑩变化排名", "⑪分析追问"]


# ---------------- 时间帮助函数(全部由 ASOF 推导,不写死星期几) ----------------

def _d(s: str) -> str:
    return s


def rel_days(n: int) -> list:
    """最近N天(含今天=数据截至日):[asof-(N-1), asof]。"""
    return [(ASOF - timedelta(days=n - 1)).isoformat(), ASOF.isoformat()]


def qiantian() -> list:
    """前天 = [asof-2, asof-2]。"""
    d = (ASOF - timedelta(days=2)).isoformat()
    return [d, d]


def rel_weeks(n: int) -> list:
    return [(ASOF - timedelta(days=7 * n - 1)).isoformat(), ASOF.isoformat()]


def this_week() -> list:
    """本周 = 本周一 ~ 数据截至日(timelex 语义)。"""
    monday = ASOF - timedelta(days=ASOF.weekday())
    return [monday.isoformat(), ASOF.isoformat()]


def last_cal_week() -> list:
    """日历上周(对比语境语义)。"""
    monday = ASOF - timedelta(days=ASOF.weekday())
    return [(monday - timedelta(days=7)).isoformat(), (monday - timedelta(days=1)).isoformat()]


def ytd() -> list:
    return [f"{ASOF.year}-01-01", ASOF.isoformat()]


OCT = [f"{ASOF.year}-10-01", f"{ASOF.year}-10-31"]
OCT_XIA = [f"{ASOF.year}-10-21", f"{ASOF.year}-10-31"]
OCT_SHANG = [f"{ASOF.year}-10-01", f"{ASOF.year}-10-10"]
S11 = [f"{ASOF.year}-11-01", ASOF.isoformat()]   # 双十一截断到数据水位


# ---------------- 场景定义 ----------------
# expect 关键字:
#   card: 允许的卡型(str/list);status: 期望 done.status(默认随卡型推导)
#   title: 卡 title 精确匹配(unsupportable 用)
#   metric / dims / dims_any / filters(子集) / filters_empty(必须为空的键)
#   time: {"preset":..}|{"range":[..]};time_any: 多个可接受 time(如「上周」的两种诚实口径)
#   compare: None|"prev_period"|"range";limit/order: 精确匹配
#   any_of: 备选期望组(每组是上述关键字的子集)
# rule: 规则模式对照列(既有单测/eval 已知结论,静态文本,不执行)

def _S(sid, group, q, expect, rule, pre=None, note=""):
    return {"id": sid, "group": group, "q": q, "expect": expect, "rule": rule,
            "pre": pre or [], "note": note}


def build_scenarios() -> list:
    SC = []

    # ============ ① 时间(16) ============
    g = GROUPS[0]
    SC += [
        _S("T01", g, "昨天的GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"preset": "yesterday"}},
           "规则=yesterday/gmv 汇总(单测既有结论)"),
        _S("T02", g, "前天卖了多少?",
           {"card": "summary", "metric": "gmv", "time": {"range": qiantian()}},
           "规则=error(「卖了多少」无指标词;timelex 前天已支持)", note="期望范围=[前天,前天]"),
        _S("T03", g, "最近3天订单数是多少?",
           {"card": "summary", "metric": "order_cnt", "time": {"range": rel_days(3)}},
           "规则=range 最近3天/order_cnt(v14 timelex)"),
        _S("T04", g, "最近2周GMV怎么样?",
           {"card": "summary", "metric": "gmv", "time": {"range": rel_weeks(2)}},
           "规则=range 最近2周(14天窗)"),
        _S("T05", g, "本周GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"range": this_week()}},
           "规则=range 本周一~水位(v14)"),
        _S("T06", g, "上周退款金额是多少?",
           {"card": "summary", "metric": "refund_amt",
            "time_any": [{"preset": "last_7d"}, {"range": last_cal_week()}]},
           "规则=last_7d preset(time_words「上周」)"),
        _S("T07", g, "2026年10月女装GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"range": OCT},
            "filters": {"category": ["女装"]}},
           "规则=2026-10 全月+女装(eval #3 同型;此前 LLM 错降级 this_month)"),
        _S("T08", g, "10月美妆订单数是多少?",
           {"card": "summary", "metric": "order_cnt", "time": {"range": OCT},
            "filters": {"category": ["美妆"]}},
           "规则=2026-10 全月+美妆"),
        _S("T09", g, "10月下旬GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"range": OCT_XIA}},
           "规则=旬区间(v14 timelex)"),
        _S("T10", g, "10月下旬比上旬客单价怎么样?",
           {"card": "summary", "metric": "arppu", "time": {"range": OCT_XIA},
            "compare": "range"},
           "规则=主窗口+compare 显式区间(§16.1)"),
        _S("T11", g, "今年以来GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"range": ytd()}},
           "规则=年初至今(v14)"),
        _S("T12", g, "双十一那几天的GMV怎么样?",
           {"card": "summary", "metric": "gmv", "time": {"range": S11}},
           "规则=last_30d(无双十一词,语义漂移);LLM 须给截断 range 11-01~11-06"),
        _S("T13", g, f"从2026-10-01到2026-10-15的GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"range": [f"{ASOF.year}-10-01", f"{ASOF.year}-10-15"]}},
           "规则=从X到Y 显式区间(v14)"),
        _S("T14", g, "上个月客单价是多少?",
           {"card": "summary", "metric": "arppu", "time": {"preset": "last_month"}},
           "规则=last_month/arppu"),
        _S("T15", g, "这个月GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": {"preset": "this_month"}},
           "规则=this_month"),
        _S("T16", g, "近14天退款率是多少?",
           {"card": "summary", "metric": "refund_rate", "time": {"range": rel_days(14)}},
           "规则=range 14天/refund_rate"),
    ]

    # ============ ② 指标全覆盖(12;11 指标 + 同义词) ============
    g = GROUPS[1]
    L30 = {"preset": "last_30d"}
    SC += [
        _S("M01", g, "最近30天GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": L30}, "规则=gmv(eval #1)"),
        _S("M02", g, "最近30天营业额是多少?",
           {"card": "summary", "metric": "gmv", "time": L30}, "规则=「营业额」同义词→gmv"),
        _S("M03", g, "上个月流水多少?",
           {"card": "summary", "metric": "gmv", "time": {"preset": "last_month"}},
           "规则=「流水」同义词→gmv + last_month"),
        _S("M04", g, "最近30天总共有多少笔支付订单?",
           {"card": "summary", "metric": "order_cnt", "time": L30}, "规则=order_cnt(eval #4 同型)"),
        _S("M05", g, "最近30天有多少个下单支付的用户?",
           {"card": "summary", "metric": "pay_user_cnt", "time": L30}, "规则=pay_user_cnt(eval #5)"),
        _S("M06", g, "最近30天卖了多少件?",
           {"card": "summary", "metric": "item_cnt", "time": L30}, "规则=「卖了多少件」→item_cnt"),
        _S("M07", g, "最近30天的客单价是多少?",
           {"card": "summary", "metric": "arppu", "time": L30}, "规则=arppu(eval #6)"),
        _S("M08", g, "最近30天退款了多少钱?",
           {"card": "summary", "metric": "refund_amt", "time": L30}, "规则=refund_amt(eval #7)"),
        _S("M09", g, "最近30天整体退款率是多少?",
           {"card": "summary", "metric": "refund_rate", "time": L30}, "规则=refund_rate(eval #8)"),
        _S("M10", g, "最近30天订单退款率是多少?",
           {"card": "summary", "metric": "refund_order_rate", "time": L30}, "规则=refund_order_rate"),
        _S("M11", g, "最近30天新客订单占比是多少?",
           {"card": "summary", "metric": "new_order_ratio", "time": L30}, "规则=new_order_ratio"),
        _S("M12", g, "最近30天新客订单数是多少?",
           {"any_of": [
               {"metric": "new_order_cnt"},
               {"metric": "order_cnt", "filters": {"user_type": ["新客"]}},
           ], "card": "summary", "time": L30},
           "规则=order_cnt+新客筛(词表无 new_order_cnt 词;LLM 可直选 new_order_cnt)"),
    ]

    # ============ ③ 维度与筛选(7) ============
    g = GROUPS[2]
    SC += [
        _S("F01", g, "美妆最近30天GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": L30, "filters": {"category": ["美妆"]}},
           "规则=美妆筛(类目词)"),
        _S("F02", g, "直播渠道最近7天退款率是多少?",
           {"card": "summary", "metric": "refund_rate", "time": {"preset": "last_7d"},
            "filters": {"channel": ["直播"]}},
           "规则=直播+last_7d+refund_rate"),
        _S("F03", g, "最近30天新客的GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": L30, "filters": {"user_type": ["新客"]}},
           "规则=新客筛(单值才筛)"),
        _S("F04", g, "美妆直播渠道最近30天客单价是多少?",
           {"card": "summary", "metric": "arppu", "time": L30,
            "filters": {"category": ["美妆"], "channel": ["直播"]}},
           "规则=类目+渠道复合筛选"),
        _S("F05", g, "推广渠道最近30天GMV是多少?",
           {"card": "summary", "metric": "gmv", "time": L30, "filters": {"channel": ["付费推广"]}},
           "规则=「推广」别名→付费推广(terms.channel_aliases)"),
        _S("F06", g, "私域最近30天订单数是多少?",
           {"card": "summary", "metric": "order_cnt", "time": L30, "filters": {"channel": ["私域"]}},
           "规则=私域+order_cnt"),
        _S("F07", g, "母婴最近30天有多少个下单支付的用户?",
           {"card": "summary", "metric": "pay_user_cnt", "time": L30,
            "filters": {"category": ["母婴"]}},
           "规则=母婴+pay_user_cnt"),
    ]

    # ============ ④ 商品下钻(3) ============
    g = GROUPS[3]
    SC += [
        _S("P01", g, "美妆里哪些商品退款最多?",
           {"card": "rank", "metric": "refund_amt",
            "dims_any": [["product"], ["category", "product"]],
            "filters": {"category": ["美妆"]}, "order": "desc"},
           "规则=product rank(§16.2 v14,哪些+商品)"),
        _S("P02", g, "最近30天卖得最好的10个商品",
           {"card": "rank", "metric": "gmv", "dims": ["product"], "limit": 10, "order": "desc"},
           "规则=product rank limit10(「卖得最好」+「商品」)"),
        _S("P03", g, "最近30天客单价最高的商品是哪个?",
           {"card": "rank", "metric": "arppu", "dims": ["product"], "order": "desc"},
           "规则=product rank(哪个+商品;「最高」不在规则排名词表但商品词兜底)"),
    ]

    # ============ ⑤ 排名(4) ============
    g = GROUPS[4]
    SC += [
        _S("R01", g, "最近30天哪个类目GMV最高?",
           {"card": "rank", "metric": "gmv", "dims": ["category"], "limit": 1,
            "order": "desc", "filters_empty": ["category"]},
           "规则=rank category(「哪个」);此前 LLM 曾把 7 类目塞 filters 求合计",
           note="filters.category 必须为空(禁止塞候选值)"),
        _S("R02", g, "最近30天GMV前三的类目",
           {"card": "rank", "metric": "gmv", "dims": ["category"], "limit": 3, "order": "desc"},
           "规则=rank category limit10(规则不解析「前三」条数)"),
        _S("R03", g, "最近30天退款金额最低的类目",
           {"card": "rank", "metric": "refund_amt", "dims": ["category"], "order": "asc"},
           "规则=summary(「最低」不在规则排名词表,存在语义漂移);LLM 须 dims+asc"),
        _S("R04", g, "最近30天各渠道GMV对比",
           {"card": "rank", "metric": "gmv", "dims": ["channel"], "filters_empty": ["channel"]},
           "规则=rank channel(「各渠道」)"),
    ]

    # ============ ⑥ 边界(3) ============
    g = GROUPS[5]
    SC += [
        _S("B01", g, "员工平均工资是多少?",
           {"card": "refuse", "status": "refuse"},
           "规则=refuse 守卫(terms.forbidden「工资」)", note="守卫卡,不经 planner"),
        _S("B02", g, "帮我看下具体订单明细",
           {"card": "boundary", "status": "boundary"},
           "规则=boundary 守卫(terms.boundary「明细」)", note="守卫卡,不经 planner"),
        _S("B03", g, "竞品A的GMV是多少?",
           {"card": ["error", "clarify"]},
           "规则=summary(gmv 词命中,语义漂移:拿自家 GMV 回答竞品问题);LLM 必须诚实拒答/澄清",
           note="答错比拒答严重"),
    ]

    # ============ ⑦ 不可表达(5)——必须全部诚实拒答 ============
    g = GROUPS[6]
    UNSUP_TITLE = "这个问题我答不了"
    SC += [
        _S("U01", g, "女装今年以来退款金额比数码多多少?",
           {"card": "error", "status": "error", "title": UNSUP_TITLE},
           "规则=summary(两类目合并求和,数字对但答非所问);LLM 必须 unsupportable",
           note="核心危险用例(任务背景失分场景同型)"),
        _S("U02", g, "女装客单价比数码高多少?",
           {"card": "error", "status": "error", "title": UNSUP_TITLE},
           "规则=summary(合并筛选);LLM 必须 unsupportable"),
        _S("U03", g, "明年GMV预测一下",
           {"card": "error", "status": "error", "title": UNSUP_TITLE},
           "规则=summary(未来时间照查,结果为空);LLM 必须 unsupportable(预测类)"),
        _S("U04", g, "利润有多少?",
           {"card": "error", "status": "error", "title": UNSUP_TITLE},
           "规则=error(无指标词,规则解析失败);LLM 必须 unsupportable(语义层无利润)"),
        _S("U05", g, "双十二的GMV有多少?",
           {"card": "error", "status": "error", "title": UNSUP_TITLE},
           "规则=summary(12月区间全部晚于水位,查空);LLM 必须 unsupportable(数据未发生)"),
    ]

    # ============ ⑧ 多轮继承(2) ============
    g = GROUPS[7]
    SC += [
        _S("C01", g, "那订单数呢?",
           {"card": "summary", "metric": "order_cnt", "time": {"range": OCT},
            "filters": {"category": ["美妆"]}},
           "规则=追问继承(上一轮 IR 继承时间/筛选);LLM 需借会话上下文同答",
           pre=["2026年10月美妆的GMV是多少?"],
           note="同 session:先问美妆 10 月 GMV,再追问订单数"),
        _S("C02", g, "那GMV呢?",
           {"card": "summary", "metric": "gmv", "time": {"preset": "last_7d"},
            "filters": {"channel": ["直播"]}},
           "规则=追问继承;LLM 需借会话上下文同答",
           pre=["最近7天直播渠道的退款率是多少?"],
           note="同 session:先问直播 7 天退款率,再追问 GMV"),
    ]

    # ============ ⑨ 归因(5;v15 M5 泛化,§16.5) ============
    g = GROUPS[8]
    SC += [
        _S("A01", g, "最近30天退款率为什么上升?",
           {"card": "attribution", "metric": "refund_rate", "time": L30},
           "规则=退款域归因(golden #17 同型,既有能力)"),
        _S("A02", g, "最近30天退款金额为什么上升?",
           {"card": "attribution", "metric": "refund_amt", "time": L30},
           "规则=v15 可加归因(元瀑布;此前被强制退款率)", note="golden #22 同型"),
        _S("A03", g, "最近30天新客占比为什么变化?",
           {"card": "attribution", "metric": "new_order_ratio", "time": L30,
            "filters_empty": ["user_type"]},
           "规则=v15 比率归因泛化;「新客」词不落筛选(占比口径剥离)",
           note="filters.user_type 必须为空(否则指标恒 100%)"),
        _S("A04", g, "最近30天订单退款率为什么上升?",
           {"card": "attribution", "metric": "refund_order_rate", "time": L30},
           "规则=v15 比率归因泛化(分母订单数,行级可加)"),
        _S("A05", g, "美妆最近30天退款率为什么上升?",
           {"card": "attribution", "metric": "refund_rate", "time": L30,
            "filters": {"category": ["美妆"]}},
           "规则=scoped 归因(golden #18 同型)"),
    ]

    # ============ ⑩ 变化排名(4;v15 M4 变化贡献排名) ============
    g = GROUPS[9]
    SC += [
        _S("D01", g, "最近30天哪些类目GMV下降最明显?",
           {"card": "rank", "metric": "gmv", "dims": ["category"], "compare": "prev_period"},
           "规则=v15 M4(两期变化额排序+贡献占比;此前为当期排名,语义漂移)",
           note="golden #25 同型"),
        _S("D02", g, "最近30天哪些渠道GMV上升最多?",
           {"card": "rank", "metric": "gmv", "dims": ["channel"], "compare": "prev_period"},
           "规则=v15 M4 方向=up"),
        _S("D03", g, "最近30天哪些类目退款金额下滑最明显?",
           {"card": "rank", "metric": "refund_amt", "dims": ["category"], "compare": "prev_period"},
           "规则=v15 M4(可加指标,贡献占比)"),
        _S("D04", g, "最近30天哪些类目退款率变化最大?",
           {"card": "rank", "metric": "refund_rate", "dims": ["category"], "compare": "prev_period"},
           "规则=v15 M4(比率指标,pp 变化,无贡献占比列)"),
    ]

    # ============ ⑪ 分析追问(2;v15「为什么」继承升级,doc 04 §5) ============
    g = GROUPS[10]
    SC += [
        _S("W01", g, "那为什么变化了呢?",
           {"card": "attribution", "metric": "gmv", "time": L30,
            "filters": {"category": ["美妆"]}},
           "规则=why 追问继承升级 M5(继承上一轮指标+美妆筛选;LLM clarify 被守卫忽略)",
           pre=["最近30天美妆的GMV是多少?"],
           note="同 session:先问汇总,再以「为什么」追问 → 可加归因(GMV,元瀑布)"),
        _S("W02", g, "那为什么上升呢?",
           {"card": "attribution", "metric": "refund_rate", "time": L30},
           "规则=why 追问继承升级 M5(继承退款率)",
           pre=["最近30天退款率是多少?"],
           note="同 session:先问退款率,再追问为什么上升 → 退款率归因(pp 瀑布)"),
    ]
    return SC


# ---------------- HTTP 帮助(stdlib) ----------------

def http_json(url: str, timeout: float = 30.0, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat_once(base: str, question: str, session_id: str) -> tuple[dict, dict]:
    """POST /api/chat(SSE)→ (answer 卡, done)。"""
    body = json.dumps({"question": question, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/chat", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Demo-User": "ceo"})
    card, done, ev = None, None, ""
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    continue
                if ev == "answer":
                    card = data
                elif ev == "done":
                    done = data
    if card is None or done is None:
        raise RuntimeError("SSE 流不完整(缺 answer/done)")
    return card, done


def fetch_ir(base: str, trace_id: str) -> dict:
    """audit 明细 → {ir_json, llm_used, status}。"""
    try:
        d = http_json(f"{base}/api/admin/audit/{trace_id}")
        return {"ir": d.get("ir_json"), "llm_used": bool(d.get("llm_used")),
                "status": d.get("status")}
    except Exception:
        return {"ir": None, "llm_used": None, "status": None}


# ---------------- 期望校验 ----------------

def _as_list(v):
    return v if isinstance(v, list) else [v]


def _check_one(exp: dict, card: dict, ir: dict | None, done_status: str) -> list:
    errs = []
    ctype = (card or {}).get("type")

    want_card = _as_list(exp.get("card")) if exp.get("card") else None
    if want_card and ctype not in want_card:
        errs.append(f"卡型期望 {want_card} 实际 {ctype}")

    want_status = exp.get("status")
    if want_status and done_status != want_status:
        errs.append(f"status 期望 {want_status} 实际 {done_status}")

    want_title = exp.get("title")
    if want_title and (card or {}).get("title") != want_title:
        errs.append(f"title 期望「{want_title}」实际「{(card or {}).get('title')}」")

    if ir is None:
        if want_card and set(_as_list(exp["card"])) & {"summary", "trend", "rank", "compare",
                                                       "attribution", "overview"}:
            errs.append("需要 IR 断言但审计里 ir_json 为空")
        return errs

    want_metric = _as_list(exp["metric"]) if exp.get("metric") else None
    if want_metric and ir.get("metric") not in want_metric:
        errs.append(f"metric 期望 {want_metric} 实际 {ir.get('metric')}")

    if "dims" in exp and list(ir.get("dims") or []) != list(exp["dims"]):
        errs.append(f"dims 期望 {exp['dims']} 实际 {ir.get('dims')}")
    if "dims_any" in exp:
        actual = list(ir.get("dims") or [])
        if not any(actual == list(x) for x in exp["dims_any"]):
            errs.append(f"dims 期望任一 {exp['dims_any']} 实际 {actual}")

    filt = ir.get("filters") or {}
    for k, vals in (exp.get("filters") or {}).items():
        got = list(filt.get(k) or [])
        if not set(vals) <= set(got):
            errs.append(f"filters.{k} 应含 {vals} 实际 {got}")
    for k in (exp.get("filters_empty") or []):
        got = list(filt.get(k) or [])
        if got:
            errs.append(f"filters.{k} 应为空 实际 {got}(疑似把候选值塞进筛选)")

    want_time = exp.get("time")
    if want_time:
        t = ir.get("time") or {}
        if "preset" in want_time and t.get("preset") != want_time["preset"]:
            errs.append(f"time 期望 preset={want_time['preset']} 实际 {json.dumps(t, ensure_ascii=False)}")
        if "range" in want_time and [str(x) for x in (t.get("range") or [])] != [str(x) for x in want_time["range"]]:
            errs.append(f"time 期望 range={want_time['range']} 实际 {json.dumps(t, ensure_ascii=False)}")
    if "time_any" in exp:
        t = ir.get("time") or {}
        ok = False
        for alt in exp["time_any"]:
            if "preset" in alt and t.get("preset") == alt["preset"] and "range" not in (t or {}):
                ok = True
            if "range" in alt and [str(x) for x in (t.get("range") or [])] == [str(x) for x in alt["range"]]:
                ok = True
        if not ok:
            errs.append(f"time 期望任一 {exp['time_any']} 实际 {json.dumps(t, ensure_ascii=False)}")

    if "compare" in exp:
        c = ir.get("compare")
        want = exp["compare"]
        if want == "range":
            if not (isinstance(c, dict) and c.get("type") == "range"):
                errs.append(f"compare 期望显式 range 实际 {json.dumps(c, ensure_ascii=False)}")
        elif want is None:
            if c not in (None, "none"):
                errs.append(f"compare 期望无对比 实际 {json.dumps(c, ensure_ascii=False)}")
        elif c != want:
            errs.append(f"compare 期望 {want} 实际 {json.dumps(c, ensure_ascii=False)}")

    if "limit" in exp and ir.get("limit") != exp["limit"]:
        errs.append(f"limit 期望 {exp['limit']} 实际 {ir.get('limit')}")
    if "order" in exp and ir.get("order") != exp["order"]:
        errs.append(f"order 期望 {exp['order']} 实际 {ir.get('order')}")
    return errs


def check_expect(exp: dict, card: dict, ir: dict | None, done_status: str) -> list:
    if "any_of" in exp:
        errs_list = []
        for alt in exp["any_of"]:
            merged = {k: v for k, v in exp.items() if k not in ("any_of",)}
            merged.update(alt)
            errs_list.append(_check_one(merged, card, ir, done_status))
        best = min(errs_list, key=len)
        return ([f"任一备选均不符(最接近: {best})"] if best else []) if errs_list else []
    return _check_one(exp, card, ir, done_status)


# ---------------- 主流程 ----------------

def run(scenarios: list, base: str, delay: float) -> tuple[list, dict]:
    rows = []
    for sc in scenarios:
        sid = f"mx-{sc['id']}-{uuid.uuid4().hex[:6]}"
        row = dict(sc)
        row.update({"pass": False, "card_type": None, "ir": None, "llm_used": None,
                    "failures": [], "transport": None, "pre_cards": []})
        try:
            for pre_q in sc["pre"]:
                pc, _ = chat_once(base, pre_q, sid)
                row["pre_cards"].append(pc.get("type"))
                time.sleep(delay)
            card, done = chat_once(base, sc["q"], sid)
            time.sleep(delay)
        except Exception as e:  # 传输层失败:重试一次
            try:
                for pre_q in sc["pre"]:
                    chat_once(base, pre_q, sid)
                    time.sleep(delay)
                card, done = chat_once(base, sc["q"], sid)
            except Exception as e2:
                row["transport"] = f"{type(e2).__name__}: {e2}"
                rows.append(row)
                continue
        row["card_type"] = (card or {}).get("type")
        row["done_status"] = done.get("status")
        au = fetch_ir(base, done.get("trace_id") or "")
        row["ir"] = au.get("ir")
        row["llm_used"] = au.get("llm_used")
        row["failures"] = check_expect(sc["expect"], card, au.get("ir"), done.get("status"))
        row["pass"] = not row["failures"]
        rows.append(row)
    return rows, {}


def summarize(rows: list) -> dict:
    groups = {}
    for r in rows:
        g = r["group"]
        groups.setdefault(g, {"total": 0, "passed": 0})
        groups[g]["total"] += 1
        groups[g]["passed"] += 1 if r["pass"] else 0
    total = len(rows)
    passed = sum(1 for r in rows if r["pass"])
    unsup = [r for r in rows if r["group"] == GROUPS[6]]
    unsup_ok = all(r["pass"] for r in unsup) if unsup else None
    return {
        "total": total, "passed": passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "groups": groups,
        "unsupportable_group_all_honest": unsup_ok,
    }


def print_report(rows: list, summary: dict, out_path: str):
    line = "-" * 108
    print(line)
    print(f"{'id':<5} {'组':<6} {'问题':<22} {'卡型':<9} {'指标':<16} {'判定':<4} {'失败原因 / 规则对照'}")
    print(line)
    for r in rows:
        ir = r.get("ir") or {}
        metric = ir.get("metric") or "-"
        t = (ir.get("time") or {})
        ts = t.get("preset") or (("~".join(map(str, t["range"])) if t.get("range") else "-"))
        verdict = "PASS" if r["pass"] else "FAIL"
        why = "; ".join(r["failures"])[:60] if r["failures"] else f"规则对照: {r['rule'][:44]}"
        q = r["q"] if len(r["q"]) <= 20 else r["q"][:19] + "…"
        print(f"{r['id']:<5} {r['group']:<6} {q:<22} {str(r.get('card_type')):<9} "
              f"{metric:<16} {verdict:<4} {why}")
    print(line)
    print("== 分组得分 ==")
    for g in GROUPS:
        s = summary["groups"].get(g)
        if s:
            mark = "OK" if s["passed"] == s["total"] else "!!"
            print(f"  {g}: {s['passed']}/{s['total']} {mark}")
    print(f"== 总分: {summary['passed']}/{summary['total']} = {summary['pass_rate']}%")
    print(f"== ⑦不可表达组全部诚实拒答: {summary['unsupportable_group_all_honest']}")
    print(f"== 报告已写入: {out_path}")


def main():
    ap = argparse.ArgumentParser(description="场景覆盖矩阵(只跑 LLM 模式;规则表现以对照列呈现)")
    ap.add_argument("--base", default=BASE_DEFAULT, help="后端地址(默认 %(default)s)")
    ap.add_argument("--filter", default="", help="只跑指定 id(逗号分隔,前缀匹配)")
    ap.add_argument("--group", default="", help="只跑指定组(名称包含匹配,逗号分隔)")
    ap.add_argument("--delay", type=float, default=0.4, help="请求间隔秒")
    ap.add_argument("--out", default="/tmp/scenario_report.json", help="JSON 报告路径")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    scs = build_scenarios()
    if args.filter:
        ids = [x.strip() for x in args.filter.split(",") if x.strip()]
        scs = [s for s in scs if any(s["id"].startswith(i) for i in ids)]
    if args.group:
        names = [x.strip() for x in args.group.split(",") if x.strip()]
        scs = [s for s in scs if any(n in s["group"] for n in names)]
    if not scs:
        print("没有匹配的场景")
        return 2

    # 前置健康检查(不读取任何配置,仅公开端点)
    try:
        st = http_json(f"{base}/api/admin/settings")
        print(f"[matrix] backend={base} llm_enabled={st.get('llm_enabled')} model={st.get('model')}")
        if not st.get("llm_enabled"):
            print("[matrix] 警告: llm_enabled=false,本矩阵按 LLM 模式设计,结果会系统性偏差")
    except Exception as e:
        print(f"[matrix] 后端不可达: {e}")
        return 2

    t0 = time.time()
    rows, _ = run(scs, base, args.delay)
    summary = summarize(rows)
    report = {
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": base, "asof": ASOF_S,
        "summary": summary,
        "items": [{k: v for k, v in r.items()} for r in rows],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    print_report(rows, summary, args.out)
    print(f"[matrix] 耗时 {time.time() - t0:.0f}s")
    # 退出码:全过=0,否则 1(便于 CI/脚本判断)
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
