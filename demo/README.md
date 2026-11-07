# BizInsight Chat · 可演示系统 Demo(第一版 · 交易域)

真实前后端 + 本地 MySQL 测试数据的完整演示系统:自然语言问数 → 语义层编译 SQL → 真实查询 → 分析答案卡。
配套契约:[CONTRACT.md](CONTRACT.md) | 设计文档:[docs/10-UI交互原型设计.md](../docs/10-UI交互原型设计.md)(界面按其 MVP 双视角实现)

## 0. 一键演示

```bash
# 1) 重建演示库(首次或需要重置时;20.7万订单,seed=42 可复现,约7秒)
bash demo/scripts/reset_db.sh

# 2) 构建前端(首次)
cd demo/frontend && npm install && npm run build && cd ../..

# 3) 启动(后端 8310,同端口托管前端)
bash demo/scripts/run_backend.sh
# 打开 http://127.0.0.1:8310
```

前端开发模式(改前端代码时):`bash demo/scripts/run_frontend.sh` → http://127.0.0.1:5174(已配 /api 代理)

## 1. 组成与架构

| 模块 | 技术 | 说明 |
|---|---|---|
| `db/` | MySQL 8.4(docker) | 交易域测试数据:12 表、fact_order 20.7 万行(90 天),故事线数据(退款率 2.4%→2.8% 由美妆驱动、类目 GMV 结构变化),`verify.py` 43 项校验 |
| `backend/` | Python 3.14 + FastAPI | 语义层(YAML v12,9 指标 4 维度)→ IR → 确定性 SQL 编译(行级权限注入)→ 编排管道(SSE)→ 答案卡;LLM 网关(OpenAI 兼容);审计落库;12 个管理端点;评测 runner |
| `frontend/` | React 18 + TS + ECharts | 用户对话台(零技术元素)+ 后台管理五页,SSE 驱动处理动画,light-dark 双主题 |

查询链路(与 docs/02 架构对应):`守卫(拒答/边界/澄清) → 意图理解(LLM或规则) → 语义规划(IR) → SQL编译+权限注入 → MySQL 执行 → 数字核验 → 叙事生成 → 答案卡`

## 2. LLM 接入(token 配置)

两种方式,优先级:**后台系统设置 > backend/.env**。未配置 token 时全链路规则兜底,演示不中断(答案质量略模板化)。

1. **界面**:管理后台 → 系统设置 → 填 base_url / model / token → 「测试连接」→ 保存;顶部徽章显示「LLM 已接入 / 规则兜底模式」
2. **文件**:`demo/backend/.env` 的 `BIZ_LLM_API_KEY`(默认 OpenAI 兼容协议,base_url 默认 `https://open.bigmodel.cn/api/paas/v4`,model 默认 `glm-4-flash`)

token 只存本地(sys_settings 表),接口对外仅回掩码。

## 3. 演示脚本(3 分钟)

1. 空态速览:4 张 KPI 卡 + 退款率告警卡 → 点告警卡「近30天退款率为什么上升」→ 四步处理动画 → **归因瀑布**(2.40%→2.81%,美妆 +0.42pp)+ 类目明细 + 「相关性线索」标注;
2. 追问「只看美妆的退款率走势」(多轮继承)→ 再问「那退款金额呢」→ 回答注明「已沿用上一轮的时间范围/类目筛选」;
3. 输入「销售额怎么样」→ 澄清卡(选「最近30天」)→ 答案带「按 GMV 理解「销售额」」标注;点口径 chip → 人话定义弹层;
4. 输入「员工平均工资」→ 拒答卡;「看下具体订单明细」→ 边界卡(替代问法);
5. 侧栏底部切身份「林岚 · 美妆负责人」→ 再问类目 GMV → **只见美妆**(行级权限);切「管理后台」:
6. 运营总览(真实统计/数据水位/DQC 告警)→ 语义管理(客单价抽屉:定义/编译 SQL/版本历史)→ 审计日志(检索「退款」展开 trace)→ 评测(「运行评测」21/21)→ 系统设置(LLM token/测试连接)。

## 4. 运维备忘

- 重建库:`demo/scripts/reset_db.sh`(DROP 重建,自动恢复种子数据与演示审计记录)
- 后端日志:`/tmp/biz_demo_logs/backend.log`;健康检查:`curl 127.0.0.1:8310/api/health`
- MySQL 停了:`docker start hanzipress-mysql`
- 评测:管理后台「评测」页运行(golden_qa 21 条,规则模式 21/21)
- 指标测试:bash demo/backend/run_tests.sh(语义层守门,改 metric/术语必跑)
