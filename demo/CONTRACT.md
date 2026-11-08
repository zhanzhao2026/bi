# Demo 系统实现契约(v1 · 交易域)

> 本文档是多代理并行实现的**唯一权威契约**。所有模块必须严格按本文档实现接口与数据结构。
> 范围:**只做交易域**。流量/UV/CVR、库存、营销费用等不在 v1 范围。

## 0. 环境事实(已验证)

| 项 | 值 |
|---|---|
| MySQL | Docker 容器 `hanzipress-mysql`(mysql:8.4.11),`127.0.0.1:3306`,已运行 |
| 管理账号 | `root` / `root`(仅初始化用) |
| 应用账号 | `appuser` / `apppass`(应用运行时使用,`init_db.py` 负责 GRANT) |
| Python | 3.14.4(系统无 pip,venv 已建好:`/home/link/code/bi/demo/backend/.venv`,内含 pip) |
| Node | v22.22.1 / npm 9.2.0,网络可访问 npm registry |
| 端口 | 后端 `8310`(FastAPI/uvicorn),前端 dev `5174`(Vite,proxy /api→8310);生产:后端托管 `frontend/dist`,单地址 `http://127.0.0.1:8310` |
| 项目根 | `/home/link/code/bi/demo/`(各代理只允许写自己模块的子目录) |
| 今天 | 2026-11-07;数据 T+1,**data_as_of = 2026-11-06** |

## 1. 项目结构

```
demo/
├── CONTRACT.md              # 本契约
├── db/                      # 【代理A·数据层】
│   ├── init_db.py           # 建库建表+GRANT(用 root 连)
│   ├── seed_data.py         # 造数(固定随机种子 42)
│   ├── verify.py            # 校验故事线指标,全部 PASS 才算完成
│   └── run_all.sh           # python init_db.py && python seed_data.py && python verify.py
├── backend/                 # 【代理B·后端】
│   ├── .venv/               # 已建好,依赖见 requirements.txt(已安装)
│   ├── requirements.txt
│   ├── .env / .env.example
│   ├── semantic/metrics.yaml
│   └── app/                 # FastAPI 应用(uvicorn app.main:app --port 8310)
├── frontend/                # 【代理C·前端】Vite + React + TS + ECharts
├── scripts/
│   ├── run_backend.sh
│   └── run_frontend.sh
└── docs/README.md           # 集成阶段由主控编写
```

## 2. 数据层规格(代理A)

### 2.1 建库与账号
- `CREATE DATABASE IF NOT EXISTS biz_demo DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`
- `CREATE USER IF NOT EXISTS 'appuser'@'%' IDENTIFIED BY 'apppass'; GRANT ALL ON biz_demo.* TO 'appuser'@'%'; FLUSH PRIVILEGES;`

### 2.2 表结构(DDL,严格照此)

```sql
dim_date     (d DATE PK, dow TINYINT, is_weekend TINYINT(1), month CHAR(7))
dim_category (category_id TINYINT PK, category_name VARCHAR(32), sort INT)
dim_channel  (channel_id TINYINT PK, channel_name VARCHAR(32))
dim_product  (product_id INT PK, product_name VARCHAR(64), category_id TINYINT, price DECIMAL(10,2))
dim_user     (user_id INT PK, reg_date DATE)
fact_order   (
  order_id BIGINT PK, pay_date DATE NOT NULL, product_id INT NOT NULL,
  category_id TINYINT NOT NULL, channel_id TINYINT NOT NULL, user_id INT NOT NULL,
  user_type VARCHAR(4) NOT NULL,          -- '新客'/'老客':该用户首次付款日=本单日→新客
  item_cnt INT NOT NULL,
  pay_amount DECIMAL(12,2) NOT NULL,       -- 付款金额(退款不扣减)
  refund_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  is_refund TINYINT(1) NOT NULL DEFAULT 0, -- 1=有退款发生(仅退款或退货退款)
  KEY idx_date (pay_date), KEY idx_cat (category_id, pay_date), KEY idx_user (user_id)
)
audit_log    (
  id BIGINT PK AUTO_INCREMENT, trace_id VARCHAR(24), ts DATETIME, session_id VARCHAR(40),
  demo_user VARCHAR(32), question TEXT, intent VARCHAR(32), metric_key VARCHAR(32),
  ir_json TEXT, sql_text TEXT, row_count INT, result_json TEXT, narrative TEXT,
  llm_used TINYINT(1), llm_model VARCHAR(64), latency_ms INT, stage_timings TEXT,
  status VARCHAR(16) DEFAULT 'ok',   -- ok / clarify / refuse / boundary / error
  KEY idx_ts (ts), KEY idx_sess (session_id)
)
feedback_log (id BIGINT PK AUTO_INCREMENT, ts DATETIME, trace_id VARCHAR(24), session_id VARCHAR(40),
  demo_user VARCHAR(32), rating VARCHAR(4), tags TEXT, comment TEXT)
golden_qa    (id INT PK AUTO_INCREMENT, question TEXT, kind VARCHAR(12),   -- value/trend/rank/compare/attribution/clarify/refuse/boundary
  expect_metric VARCHAR(32), expect_dims TEXT, ref_sql TEXT, ref_value DECIMAL(18,4) NULL,
  tolerance_pct DECIMAL(5,2) DEFAULT 2.0, tag VARCHAR(16))
semantic_version (id INT PK AUTO_INCREMENT, version INT, released DATE, author VARCHAR(32),
  change_desc TEXT, yaml_snapshot LONGTEXT)
eval_history (id BIGINT PK AUTO_INCREMENT, ts DATETIME, mode VARCHAR(8), pass_rate DECIMAL(5,2),
  total INT, passed INT, report_json LONGTEXT)
sys_settings (skey VARCHAR(32) PK, svalue TEXT)
```

### 2.3 维度取值
- 类目 7 个(按 sort):美妆 / 女装 / 数码 / 家居 / 食品 / 母婴 / 运动
- 渠道 4 个:直播 / 自然流量 / 私域 / 付费推广(用户提问用词:直播、私域、自然流量、付费/推广)
- 商品:每类目 8~12 个,中文商品名(如"水光焕颜精华液 30ml"),价格带:美妆 80-300、女装 100-400、数码 500-3000、家居 150-800、食品 30-80、母婴 60-250、运动 120-500

### 2.4 造数范围与故事线(必须全部命中,verify.py 逐一校验)
- 日期:2026-08-09 ~ 2026-11-06(90 天);窗口定义:last30 = 2026-10-08~11-06,prior30 = 2026-09-08~10-07
- 订单量:日均约 2300 单(总量约 20.7 万行);用户 5.5 万人;随机种子固定 42,结果可复现
- 季节性:周末 GMV ×1.2,日常 ±10% 噪声
- **故事线 1 · 类目 GMV 变化**(last30 vs prior30,容差 ±1.5pp):
  美妆 +26.0%、家居 +23.6%、食品 +18.0%、母婴 -3.5%、运动 -6.1%、女装 -9.2%、数码 -18.4%,整体约 +5%
- **故事线 2 · 退款率上升**(last30 vs prior30,容差 ±0.1pp):
  整体 2.4% → 2.8%(+0.4pp),由美妆驱动(美妆约 2.0% → 4.0%,贡献约 +0.4pp,其他类目基本持平 ±0.05pp)
  实现:美妆 last30 内 3 款"问题商品"(指定 product,退款概率 12%~20%)拉高整类目;退款金额 = 付款金额 × 0.7~1.0
- **故事线 3 · 渠道结构**:直播 35%、自然流量 30%、私域 20%、付费推广 15%(容差 ±3pp)
- **故事线 4 · 新客订单占比**:last30 约 24%~28%
- AOV(客单价)预期 140~220 元(只报告,不强制)

### 2.5 golden_qa(种子时用 ref_sql **实时计算** ref_value 写入)
至少 16 条,覆盖:value(GMV/客单价/支付人数/退款金额/订单数,含 last_30d/yesterday/last_month/只看美妆/只看直播)、trend、rank(类目 GMV、类目退款率)、compare(新老客)、attribution(退款率归因)、clarify(「销售额怎么样」)、refuse(「员工平均工资」)、boundary(「看下具体订单明细」)。question 用口语化中文。

### 2.6 其他种子数据
- `semantic_version`:v10(2026-09-12,初版语义层)、v11(2026-10-18,退款率口径说明补充"含仅退款")、v12(2026-11-06,当前,分母统一为同范围 GMV)
- `audit_log`:预置 6~8 条近几日的演示记录(含 1 条 status='clarify'、1 条 'refuse',trace_id 形如 `tr_1a2b3c4d`)
- `sys_settings`:llm_base_url=`https://open.bigmodel.cn/api/paas/v4`,llm_model=`glm-4-flash`,llm_api_key=空

### 2.7 verify.py 输出
逐条打印 PASS/FAIL 及实测值:整体与各故事线指标 + 表行数 + golden_qa 条数。**全部 PASS 后数据层才算完成**,并在最终报告中给出:行数、last30/prior30 GMV 与退款率、各品类 delta 实测、美妆退款率贡献实测、渠道占比、新客占比、AOV。

## 3. 语义层规格(backend/semantic/metrics.yaml,代理B 按此实现加载器)

```yaml
version: 12
data_as_of_query: SELECT MAX(pay_date) FROM fact_order
metrics:
  gmv:            {name: "GMV",        plain: "用户付款成功的总金额,含后续退款部分,退款不从中扣除。", unit: 元,  agg: "SUM(f.pay_amount)"}
  order_cnt:      {name: "支付订单数",  plain: "付款成功的订单数量,按订单去重。", unit: 单, agg: "COUNT(DISTINCT f.order_id)"}
  pay_user_cnt:   {name: "支付人数",    plain: "付款成功的去重用户数,同一人多笔订单只算一次。", unit: 人, agg: "COUNT(DISTINCT f.user_id)"}
  arppu:          {name: "客单价",      plain: "客单价 = GMV ÷ 支付人数,同一人买多件只算一次人头。", unit: 元/人, agg: "SUM(f.pay_amount)/NULLIF(COUNT(DISTINCT f.user_id),0)", derived: true}
  item_cnt:       {name: "支付件数",    plain: "付款成功的商品件数合计。", unit: 件, agg: "SUM(f.item_cnt)"}
  refund_amt:     {name: "退款金额",    plain: "已发生的退款总额,含仅退款与退货退款。", unit: 元, agg: "SUM(f.refund_amount)"}
  refund_rate:    {name: "退款率",      plain: "退款率 = 退款金额 ÷ GMV,两边按同一时间与同一范围统计。", unit: "%", agg: "SUM(f.refund_amount)/NULLIF(SUM(f.pay_amount),0)", ratio: true, derived: true, higher_is_bad: true}
  refund_order_rate: {name: "订单退款率", plain: "发生退款的订单数 ÷ 支付订单数。", unit: "%", agg: "SUM(f.is_refund)/NULLIF(COUNT(DISTINCT f.order_id),0)", ratio: true, derived: true, higher_is_bad: true}
  new_order_ratio: {name: "新客订单占比", plain: "新客(当日首次付款用户)下的订单占全部订单的比例。", unit: "%", agg: "SUM(f.user_type='新客')/NULLIF(COUNT(DISTINCT f.order_id),0)", ratio: true, derived: true}
dims:
  date:      {column: "f.pay_date", desc: 支付日期}
  category:  {column: "f.category_id", name_table: dim_category, name_col: category_name, desc: 商品类目, user_terms: [类目,品类]}
  channel:   {column: "f.channel_id", name_table: dim_channel, name_col: channel_name, desc: 成交渠道, user_terms: [渠道,直播,私域]}
  user_type: {column: "f.user_type", desc: "用户类型(新客/老客)", user_terms: [新客,老客,新老]}
time_presets: [yesterday, last_7d, last_30d, this_month, last_month]
```
语义版本快照存 `semantic_version.yaml_snapshot`。口径弹层:用户模式用 `plain`,管理模式给 name+plain+SQL+版本。

## 4. IR 规范(编排引擎的中间表示)

```json
{
  "metric": "gmv",                          // 必须在 metrics 内,否则澄清/报错
  "time": {"preset": "last_30d"}            // 或 {"range": ["2026-10-08","2026-11-06"]}
  ,"dims": ["category"],                    // ⊆ [date, category, channel, user_type]
  "filters": {"category": ["美妆"], "channel": [], "user_type": []},
  "granularity": "day",                     // date 维展示粒度:day/none(dims 含 date 时)
  "compare": "prev_period",                 // prev_period | none
  "order": "desc", "limit": 10
}
```
校验失败(未知 metric/dim/filter 值)→ 返回 clarify 或 error 卡,绝不猜。

## 5. 编译规则(semantic compiler)
- 基础模板:`FROM fact_order f`,时间列 `f.pay_date`;date 维 JOIN dim_date 可省(直接 GROUP BY f.pay_date);category/channel 维 JOIN 对应 dim 取中文名
- 维度展示名:`category→name_col`,date 维格式化为 `MM-DD`
- **行级权限谓词**(§12)以 AND 注入 WHERE;比率指标分母加 NULLIF 防除零
- compare=prev_period:同长度窗口平移再查一次,Python 侧合并(不搞 SQL 窗口函数)
- 大金额输出统一换算"万元"(>10000 元时),比率保留 2 位小数(单位 %)

## 6. 编排管道与 SSE 协议
流程:`guards → understand(intent) → plan(IR) → compile → execute → verify → narrate → answer`
用户端四步动画映射:理解问题 / 查询数据 / 交叉核验(核验项数=答案数字个数)/ 生成分析。stage_timings 记录各阶段毫秒。

SSE(`POST /api/chat`,text/event-stream),事件:`stage`、`answer`、`error`、`done`,格式 `event: <name>\ndata: <json>\n\n`。
- stage data:`{"step":"understand","label":"理解问题","status":"start|done","detail":"退款率上升的原因"}`(detail 是业务话术)
- answer data:答案卡 JSON(§10);answer 后追加 `done`。

会话上下文(内存 dict,session_id=uuid):记录最近 IR 的 time/filters;追问未指明时间/范围时**继承上一轮**并在叙事中说明"已沿用上一轮的时间范围/类目"。追问 chips 由后端随答案卡给出。

## 7. 守卫规则(顺序执行)
1. **refuse**:薪酬/工资/薪资/工资条/身份证/手机号/银行卡/社保 → `{"type":"refuse"}`卡
2. **boundary**:明细/订单列表/具体商品/哪款商品/导出 → `{"type":"boundary"}`卡("商品级明细 v1 未开放",给替代问法:按类目/按渠道汇总)
3. **clarify**:模糊指标词(销售额/卖得怎么样/业绩)且无时间词 → clarify 卡:时间选项 + assume_note「未指定时我会按 GMV 理解"销售额"」;用户选项通过 chat 请求的 `assume` 字段回传
4. 正常路径。**所有数字只能来自 SQL 结果集**(叙事模板直接引用计算值);提问与语义层不相关且规则/LLM 都无法映射 → error 卡(坦诚说明 + 能答什么)

## 8. LLM 网关(可配置 token)
- OpenAI 兼容 `/chat/completions`;httpx,timeout 20s,temperature 0.2
- 配置优先级:`sys_settings` 表 > `.env`(BIZ_LLM_BASE_URL/BIZ_LLM_API_KEY/BIZ_LLM_MODEL)
- `llm_enabled` = api_key 非空;**未配置时全链路走规则兜底,演示不中断**
- 两个角色:①planner:semantic context + 问题(+会话上下文)→ 仅输出 IR JSON(system prompt 带 JSON Schema 约束);②narrator:结果集 JSON → 2~3 句中文结论(禁编造数字,数字必须引用输入)
- api_key 永不回传前端(GET settings 只给 has_key + 掩码 `sk-***last4`);日志脱敏
- `POST /api/admin/settings/test` 发一条 1-token 测试消息验证连通性

## 9. 规则兜底意图(无 LLM 时必须可用,≥8 场景)
退款(走势→trend;类目/原因/为什么→attribution;默认 summary)/ GMV·销售额·成交(走势→trend;排名/类目贡献→rank;**变化排名(下降最明显/上升最多/变化最大)→ rank+prev_period(§17)**;渠道→rank(channel);默认 summary;**为什么→attribution(可归因指标,§17)**)/ 客单价 / 支付人数 / 订单数 / 退款金额 / 新老客对比 / 昨天·上月·最近7天·最近30天 时间解析 / 美妆·女装…类目词解析 / 直播·私域…渠道词解析。关键词命中表写在 `app/engine/rules.py`,分析意图词(why/delta_rank)在 `semantic/terms.yml intent`。

## 10. 答案卡 Schema(前后端唯一数据契约)
```json
{
  "type": "summary|trend|rank|attribution|compare|clarify|refuse|boundary|error",
  "trace_id": "tr_xxxx", "title": "近30天退款率归因", "data_as_of": "2026-11-06",
  "assume": {"text": "按 GMV 理解「销售额」"} | null,
  "narrative": ["近30天整体退款率 2.80%,较上一个30天上升 0.4pp。", "上升主要由美妆类目贡献 +0.41pp……"],
  "kpis": [{"label":"退款率","value":"2.80%","delta":{"pct":0.4,"dir":"up","label":"较上一个30天","good":false}}],
  "chart": {"kind":"waterfall","title":"退款率变化归因(pp)","base":{"name":"上一期 2.40"},"items":[{"name":"美妆","value":0.41},{"name":"数码","value":-0.02}],"total":{"name":"当前 2.80"}},
  "table": {"columns":[{"key":"category","label":"类目"},{"key":"rate","label":"退款率","align":"right"}],"rows":[{"category":"美妆","rate":"4.01%"}]},
  "caliber": ["refund_rate","gmv"],
  "scope_note": "以上为相关性线索,建议业务侧进一步确认。" | null,
  "followups": ["只看美妆的退款率走势", "哪些渠道退款更高"],
  "context": {"time_preset":"last_30d","category":[]}
}
```
- chart.kind:`line`(趋势,compare 时两条线系列名"本期/上一期")、`bar`、`waterfall`(如上)、`group`(分组对比,如新老客两指标)
- rank 卡 chart.kind=bar + 完整 table;summary 卡可无 chart;clarify 卡有 `options:[{label,value}]` + `assume_note`
- narrative 每句一元素;数值全部来自结果集(千分位、万元换算由后端做好)
- **归因卡泛化(§17)**:type=attribution 覆盖 `ATTR_SPECS` 内指标——比率型(退款率/订单退款率/新客订单占比,瀑布单位 pp)与可加型(GMV/退款金额/订单数/支付件数/退款订单数/新客订单数,瀑布单位 元/单/件);去重口径指标(客单价/支付人数)不可归因,其「为什么」问法由 summary+环比承接
- **rank 卡两期变化形态(§17)**:IR `compare=prev_period` 且维度为类目/渠道时,输出两期变化排名(列:上期/本期/变化/贡献占比);变化额按问句方向排序(下降最明显=Δ 升序);贡献占比仅可加指标且整体变化非零时展示
- **compare 卡指标感知(§17)**:问句指定退款率/退款金额/支付件数/支付人数/订单退款率时,追加该指标的分组序列/表列/叙事(默认 GMV/订单数/客单价/新客占比四件套不变)

## 11. API 规范(前缀 /api;除 chat 外均为 JSON)
| 方法/路径 | 说明 |
|---|---|
| GET /api/health | `{status:"ok", db:"ok", data_as_of:"2026-11-06"}` |
| GET /api/me | `{current:"ceo", users:[{id,name,scope}]}` |
| POST /api/chat | body `{question, session_id, assume?}` → SSE(§6)。头 `X-Demo-User`(缺省 ceo) |
| GET /api/quickview | 空态速览:`{data_as_of, kpis:[GMV/退款率/客单价/新客订单占比 last30 vs prior30], alert:{text,question} }`(alert=退款率上升,question 为跳转问法) |
| GET /api/caliber/{key} | `?mode=user|admin`:user→plain;admin→name+plain+sql+version+updated |
| POST /api/feedback | `{trace_id, rating:"up|down", tags?:[], comment?}` |
| GET /api/admin/overview | 今日提问数、近7日趋势(按天 bar)、活跃会话数、平均时延、LLM 使用率、数据水位(表行数+最新日期)、DQC 告警(退款率>3% 类目)、近期错例(status!=ok 前5条) |
| GET /api/admin/metrics | metrics.yaml 全量:`[{key,name,plain,unit,type,version}]` |
| GET /api/admin/metrics/{key} | 单指标详情:定义+agg+编译 SQL 示例+版本历史 |
| GET /api/admin/audit | `?keyword&page&size` 列表(不含 ir/sql 大字段) |
| GET /api/admin/audit/{trace_id} | 详情:全字段展开(ir_json/sql_text/stage_timings/result_json) |
| GET /api/admin/eval | golden_qa 列表 + 最近 eval_history 5 次 |
| POST /api/admin/eval/run | 跑全部 golden_qa(26 条,按当前 llm/rule 模式),写 eval_history,返回报告 |
| GET /api/admin/settings | llm_enabled/base_url/model/has_key/key_masked |
| PUT /api/admin/settings | 更新 llm_base_url/model/api_key(空串=清除)→ 写 sys_settings |
| POST /api/admin/settings/test | 实测连通:`{ok,latency_ms,model,reply?|error?}` |
| GET /api/admin/versions | semantic_version 全量倒序 |

错误统一 `{"detail": "..."}` 400/500。

## 12. RBAC 演示用户(后端内置,前端切换)
| id | 名称 | 行级范围 |
|---|---|---|
| ceo | 张总 · 总经理 | 全部类目 |
| beauty | 林岚 · 美妆负责人 | `category_id = 美妆`(编译时注入) |

请求头 `X-Demo-User`;未识别 → ceo。权限谓词在编译层注入,审计记录 demo_user。

## 13. 前端规格(代理C)
- 技术栈:Vite + React 18 + TypeScript(strict) + ECharts 5(npm),无 UI 框架,手写 CSS
- 视觉语言参考 `/home/link/code/bi/prototype/bizinsight-chat-ui-prototype.html`(提取 token,不要整页照抄):底 `#eef0f5`、白卡、描边 `#e0e3ec`、主色 `#4152e0`、圆角 13px、字号 14、正文行高 1.6;`light-dark()` 双主题;根类名 `biz-demo`
- **用户视角(默认)**:左侧栏(新对话、最近会话、底部身份切换=§12、管理后台入口),顶部 chip「数据截至 {data_as_of}」;空态=问候 + quickview 4 KPI 卡 + alert 告警卡(点击直接发 alert.question)+ 三组引导问法;消息流=用户气泡 / 答案卡(clarify/refuse/boundary 各有样式)/ 四步处理动画(stage 事件驱动)/ 信任条(「数据已核验」+「数据截至」+ 口径 chip 弹层=GET /api/caliber?mode=user + 👍👎)/ 追问 chips(点击即发送);输入框 Enter 发送
- **零技术元素铁律**:用户视角不出现 SQL/IR/语义/版本号/耗时等技术词;技术细节只在后台
- **后台管理**(侧栏切换):①运营总览(真实 overview 数据)②语义管理(指标表+抽屉:定义/agg/编译 SQL/版本历史)③审计日志(列表+keyword 过滤+行展开 trace 详情)④评测(golden 列表+「运行评测」+结果表+历史)⑤系统设置(LLM 表单 base_url/model/token + 测试连接 + 保存;当前模式 徽章「LLM 已接入/规则兜底模式」)
- SSE 消费:fetch + ReadableStream 手写解析(区分 event/data 行);断线/错误有兜底提示
- 图表组件:LineChart(双系列)、BarChart、WaterfallChart(base/items/total 悬停增值)、GroupChart;ECharts 按主题色封装,涨跌红绿仅语义色
- `npm run build` 产出 `frontend/dist`;`vite.config.ts`:port 5174,proxy `/api`→`http://127.0.0.1:8310`
- 卡片类型≠表格遍历渲染:每 type 有专属版式

## 14. 各代理验收清单
- **A(数据层)**:`bash db/run_all.sh` 全 PASS;输出故事线实测数字报告
- **B(后端)**:`.venv/bin/uvicorn app.main:app --port 8310` 可启动;curl 验证 health/quickview/me/chat(SSE 4 事件)/caliber/feedback/全部 admin 端点;至少 8 个规则兜底场景端到端出正确答案卡类型与数字;切 `X-Demo-User: beauty` 行级过滤生效(audit 可见);无 LLM key 时全流程可用;golden eval 跑通
- **C(前端)**:`npm install && npm run build` 通过;dev server 起后页面可交互(自测:空态渲染、发一问、答案卡渲染、口径弹层、后台五页、设置表单)
- 通用:不改契约外文件、不 git commit、不动其他模块目录、UI 文案全中文、完成后输出「实现摘要+验证证据+遗留问题」

## 15. Phase 1 · 语义层 v2(结构化拆分 + 依赖图 + 术语入层 + 指标单测)

> 目标:消灭四个地基问题 —— 单表硬编码 / 派生指标表达式重抄 / 术语硬编码在代码 / 指标定义零测试。
> **铁律:对外行为完全不变**——API 响应结构、IR Schema、答案卡、所有数字、前端全部零改动;eval 21/21 必须保持;现有问法命中行为不变。

### 15.1 semantic/ 目录重构(代理B1,只写 backend/)
```
backend/semantic/
├── models.yml          # 事实表声明:fact_order {alias: f, grain: order_id, date_col: pay_date};预留第二张表声明位(注释形式)
├── metrics/
│   ├── base.yml        # 基础指标 7 个:gmv/order_cnt/pay_user_cnt/item_cnt/refund_amt/refund_order_cnt(新增,SUM(f.is_refund))/new_order_cnt(新增,SUM(f.user_type='新客'))
│   └── derived.yml     # 派生指标 4 个,用 depends_on + expr 引用:
│                       #   arppu: {depends_on: [gmv, pay_user_cnt], expr: "{gmv}/NULLIF({pay_user_cnt},0)"}
│                       #   refund_rate: {depends_on: [refund_amt, gmv], expr: "{refund_amt}/NULLIF({gmv},0)"}
│                       #   refund_order_rate / new_order_ratio 同理;NULLIF 守卫写在 expr 模板里
├── dims.yml            # 现 metrics.yaml 的 dims 节原样迁移
├── terms.yml           # 自 rules.py 迁移的全部词表:synonyms(指标/维度/筛值同义词)/forbidden(拒答)/boundary(明细边界)/clarify(模糊词+assume_metric)/time_words(时间词→preset)
└── version.yml         # version: 13, updated: 2026-11-07, changelog
```
规则:
- loader 拓扑排序解析 depends_on,把 `{gmv}` 引用展开为完整 agg 字符串;**loader 对外暴露的 metric dict 字段与现在完全一致**(key/name/plain/unit/agg/ratio/derived/higher_is_bad),admin/caliber/编译器接口零改动;循环依赖/未知引用 → 启动即抛错(fail fast)
- compiler 从 models.yml 读表名与别名,消灭硬编码 `fact_order`
- rules.py 关键词/正则词表全部改为从 terms.yml 加载(词是数据,意图构造逻辑留在代码);行为回归:现有全部问法命中不变
- 新建 `app/llm/prompts.py`:planner/narrator 的 system prompt 由语义层对象渲染(指标清单/维度/术语/守卫规则);gateway.py 改用之,不再内联 prompt 字符串
- `demo/backend/migrate_v13.py`:幂等插入 semantic_version v13(author=数据小组,change_desc=语义层结构化拆分 models/metrics/terms + 指标依赖图,口径零变化;yaml_snapshot 存新 YAML 拼接)
- 最终验证:重启 uvicorn(8310)后 curl 回归——health/quickview/chat 5 问(归因 2.40%→2.81%、美妆+0.42pp 不变)/caliber user+admin/admin 全套/eval run 21/21;测完保持 uvicorn 运行

### 15.2 指标单测体系(代理B2,只写 backend/tests/ 与 backend/run_tests.sh)
- 用标准库 unittest(**不新增依赖**);`bash demo/backend/run_tests.sh` 一键执行
- 指标一致性:11 个指标 × 组合(全窗口/last30/last7/指定月/按类目/按渠道/按新客)——**独立手写的期望 SQL** 结果 vs 编译器管道(IR→编译→执行)结果,容差 1e-6;两者必须来自不同代码路径(期望 SQL 手写,不经过 loader/compiler)
- 依赖图测试:展开后的 agg 必须包含所依赖基础指标的 agg 片段(防止绕开 depends_on 重抄);将 base.yml 复制到临时目录、篡改 gmv 的 agg,断言 arppu/refund_rate 展开结果随之变化(不污染真实文件)
- 术语表测试:forbidden/boundary/clarify/synonyms 逐条走 rules.parse,断言意图与现状一致
- 守卫回归:拒答/边界/澄清各 ≥3 用例
- B2 编码完成后联测:若 semantic/ 新结构未就绪(B1 并行中),轮询 `semantic/metrics/` 目录出现再跑,最多等 10 分钟
- 可选交付:`.github/workflows/demo-ci.yml`(push 时自动跑 run_tests.sh)
- README 补「指标测试」一节(demo/README.md 的运维备忘处加一行即可,不动其他内容)

### 15.3 验收
- B1:重启后 curl 回归全过 + eval 21/21;报告:新目录树、arppu 依赖展开后的最终 SQL 文本、术语迁移清单(迁移前→后)、遗留问题
- B2:run_tests.sh 全 PASS;报告:用例总数与分类、耗时;遗留问题

## 16. Phase 2 · 语义层表达力(时间表达式 / BM25 检索 / 层级下钻)

> 目标:让语义层从「5 个写死的 preset + 平面维度」升级为可组合的表达力层。
> **回归铁律不变**:173 个既有单测 PASS、eval 21/21、现有问法行为不变;本阶段允许**新增**能力(新问法命中)。
> 金线:每个新能力都要有「手写 SQL 对拍」的端到端数字验证,不只是解析测试。

### 16.1 时间表达式层(代理P2-A,只写 backend/)
- 新建 `app/semantic/timelex.py`:中文时间短语 → IR time 结构(`{range:[d1,d2]}` 或 `{preset}`),纯函数、无副作用:
  - 保留现有 5 preset;新增:最近N天/N周、昨天/前天、本周/上周、本月/上月、N月与2026年N月(已有)、**上旬/中旬/下旬**(「10月下旬」「最近一期下旬」;旬=1-10/11-20/21-月末)、「今年以来/年初至今」、显式「从X到Y」区间
  - 相邻比较:「本周比上周」「下旬比上旬」「最近7天比上一个7天」→ 主窗口 + compare 的 range 对(平移等长),替代现有 prev_period 仅按 preset 平移的写法
- rules.py 时间解析改走 timelex(terms.yml 的 time_words 仍提供词→意图映射);**现有时间问法解析结果逐字不变**(B2 的 time_words 用例必须保持 PASS)
- 版本登记 v14(migrate_v14.py,幂等,同 §15.1 模式);scripts/reset_db.sh 由主控链接

### 16.2 层级维度下钻(代理P2-A)
- dims.yml:category 增加层级声明 `children: [product]`;dims 增 `product`(column=f.product_id,name_table=dim_product,name_col=product_name)
- IR dims 支持 `["category","product"]` 或 `["product"]`(自动带上其父 category 名列);compiler 支持 product 维 JOIN dim_product
- **聚合守卫(红线)**:product 维查询只输出聚合行(每行=该商品区间内聚合),绝不出现订单级明细;答案卡 scope_note 加「商品聚合视角,不含订单明细」;product 维与除 category/date 外的其他维组合 → 澄清(防笛卡尔爆炸)
- 新问法验收:「美妆里哪些商品退款最多」「最近30天卖得最好的10个商品」「美妆哪个商品客单价最高」→ rank 卡(商品行:商品名/所属类目/指标值);rules 增「商品/爆款/哪款商品卖/商品维度」词(入 terms.yml synonyms.dimension)
- 端到端对拍:至少 3 条手写 SQL 对拍(如美妆 last30 退款金额 TOP1 商品、全店 last30 GMV TOP3)

### 16.3 BM25 语义检索(代理P2-B,backend/semantic+api/admin.py + frontend/)
- 新建 `app/semantic/retrieval.py`:纯 Python BM25(k1=1.5,b=0.75),**零新增依赖**;中文 bigram 分词;语料=metrics(name/plain/unit/synonyms 相关词)+dims(name/desc/user_terms)+terms 词表;启动时建索引、语义版本变更时重建
- `GET /api/admin/semantic/search?q=&k=5`:返回 `[{kind: metric|dim|term, key, name, score, reason}]`(reason=命中词说明);错误处理与现有 admin 端点一致
- planner 集成:LLM 启用时 system prompt 的指标清单改为**检索 top-k 注入**(k 默认 8;q=用户问题;检索空或指标总数≤12 时回退全量,当前 11 指标=回退全量,行为不变);narrator 不受影响
- 前端语义管理页:顶部加「语义检索测试」卡片(输入框+检索按钮+结果表:类型/名称/得分/命中原因);仅后台可见,用户视角不出现;`npm run build` 通过
- 单测:检索质量 ≥10 例(「退款率为什么高」top1=refund_rate;「客单价」top1=arppu;「新客」→ new_order_ratio/new_order_cnt 进 top3;「美妆退款」→ refund_rate+category 维等)+ 边界(空 q、纯符号、超长 q)

### 16.4 验收
- P2-A/P2-B 共同回归:173 既有单测 + eval 21/21 全 PASS;uvicorn 保持运行
- P2-A:timelex 解析表 ≥20 例;时间/下钻端到端对拍 ≥6 条;报告含「10月下旬数码客单价 vs 上旬」的完整对拍数字
- P2-B:检索单测 ≥10 例;前端 build 通过;报告含 3 个检索查询的实际 top-k
- 主控浏览器实测:3 条新问法(旬对比/商品下钻/检索面板)+ 提交

## 17. Phase 3 · 分析类场景扩容(§16.5,语义层 v15)

> 目标:兑现 docs/04 的「分析助手 vs Text-to-SQL 工具」分水岭 —— 把 M4 变化贡献排名、
> M5 归因泛化(比率+可加指标)、「为什么」多轮追问升级做成自由提问能力,并把分析类
> 问法纳入场景矩阵与 golden 评测。
> **回归铁律不变**:既有 276 项单测 PASS、既有 21 题 eval 全过(扩容后 26/26)、
> 现有问法命中行为不变(退款率归因/新老客对比/当期排名逐字保留)。

### 17.1 归因泛化(app/engine/attribution.py)
- 模块级 `ATTR_SPECS`(归因指标规格,与语义层 agg 一致性由单测锁定):
  - 比率型(分母按维度可加,rate×share 精确可加分解,Σ贡献≡整体Δ):refund_rate(den=GMV)、refund_order_rate / new_order_ratio(den=订单数)
  - 可加型(贡献=两期绝对差):gmv / refund_amt / order_cnt / item_cnt / refund_order_cnt / new_order_cnt
- **边界(诚实性)**:分母为去重计数的指标(arppu ÷ 去重人数、pay_user_cnt)**不纳入归因**
  ——去重数跨维度不可加,分解残差失真;其「为什么」问法由 summary+环比叙事承接
- `compute(metric_key=...)` 参数化;refund_rate 路径数值与 v1 逐位一致(单测锁定)
- 口径守卫:「新客占比」问法的「新客」词会误挂 user_type=新客(指标恒 100%),
  由编排层 `_answer_path` 统一剥离并在叙事注明(同时拦 LLM IR,单一剥离点)

### 17.2 变化贡献排名(M4,docs/04 Q4)
- 触发:排名词 + 变化排名句式(delta_rank:下降最明显/上升最多/变化最大/贡献最大…)→
  rank 卡 + IR `compare=prev_period`
- **仅类目/渠道维**:两窗全量 ≤ LIMIT,合并精确;商品维会被 LIMIT 截断(「下滑王」
  可能不在当期 TOP-N),不支持(商品问法仍走当期排名)
- 输出:变化额按方向排序(下降=Δ升序)、表列 上期/本期/变化/贡献占比(占比仅可加指标
  且整体变化非零时)、KPI「{指标}{方向}最明显的{维度}」;比率指标呈现 pp 变化、无占比列

### 17.3 分析追问与 LLM 逃生口守卫
- 「那为什么…呢」追问:规则层 followup 分支带 `why_attr` 标记,编排层在继承上一轮
  指标/筛选后将 summary 升级为 attribution(doc 04 §5「『为什么』继承主题升级 M5」)
- LLM 逃生口守卫:规则已识别 why 问法时,LLM 的 unsupportable/clarify 不得覆盖
  (归因是确定性分析模式;U01 跨类目对比等不可表达场景不含 why 词,行为不变)

### 17.4 compare 指标感知
- 问句指定指标(退款率/退款金额/支付件数/支付人数/订单退款率)时,compare 卡追加该
  指标的分组序列/表列/叙事(含两期变化句);默认四件套不变(既有 compare 场景零回归)

### 17.5 评测与场景矩阵
- golden_qa 21 → **26**(新增:退款金额归因/新客占比归因/订单退款率归因/GMV 变化排名/
  新老客退款率对比;ref_sql 种子期实时计算)
- 场景矩阵 52 → **63**(⑨归因 5 / ⑩变化排名 4 / ⑪分析追问 2);
  `scripts/scenario_matrix.py --out demo/docs/scenario-matrix-report.json` 归档
- 单测 276 → **301+**(test_attribution.py:规格一致性/Σ贡献≡Δ 恒等式/refund_rate v1
  逐位对拍/可加恒等式/美妆 scoped 对拍/新卡 E2E×5)

### 17.6 验收
- run_tests.sh 全 PASS;eval 26/26(规则模式当场验证;LLM 模式待 token 后复核)
- 场景矩阵:⑨⑩⑪ 全过 + 既有 52 场景不回归(LLM 模式归档报告)
- 语义版本登记 v15(terms.yml intent.why/delta_rank;semantic_version 快照)
- 不做(数据诚实边界):M6 漏斗、M8 广告相关性(库中无流量/广告数据,fact_traffic 仅占位);
  趋势日级异常点检测(种子故事无日级拐点)
