/* ============================================================
 * 类型定义 · 唯一依据 CONTRACT.md(§10 答案卡 / §11 API / §6 SSE)
 * 后端并行开发中,所有响应字段均按可选 + 防御式处理
 * ============================================================ */

/* ---------- 答案卡(§10)---------- */
export type AnswerType =
  | 'summary'
  | 'trend'
  | 'rank'
  | 'attribution'
  | 'compare'
  | 'clarify'
  | 'refuse'
  | 'boundary'
  | 'error'

export interface DeltaInfo {
  pct?: number
  dir?: 'up' | 'down' | 'flat'
  label?: string
  good?: boolean
  text?: string
  [k: string]: unknown
}

export interface KpiItem {
  label?: string
  value?: string
  delta?: DeltaInfo | null
  [k: string]: unknown
}

export interface TableColumn {
  key: string
  label?: string
  align?: 'left' | 'right' | 'center'
  [k: string]: unknown
}

export interface TableSpec {
  columns?: TableColumn[]
  rows?: Record<string, unknown>[]
  [k: string]: unknown
}

export interface ChartSpec {
  kind?: string
  title?: string
  unit?: string
  /** line / bar / group */
  x?: unknown
  categories?: unknown
  series?: unknown
  data?: unknown
  /** waterfall */
  base?: unknown
  items?: unknown
  total?: unknown
  [k: string]: unknown
}

export interface AnswerCard {
  type: AnswerType
  trace_id?: string
  title?: string
  data_as_of?: string
  assume?: { text?: string } | null
  narrative?: string[]
  kpis?: KpiItem[]
  chart?: ChartSpec | null
  table?: TableSpec | null
  caliber?: string[]
  scope_note?: string | null
  followups?: string[]
  context?: Record<string, unknown>
  /** clarify 专属 */
  options?: { label: string; value: string }[]
  assume_note?: string
  /** refuse / boundary / error 可用 */
  message?: string
  reason?: string
  [k: string]: unknown
}

/* ---------- SSE(§6)---------- */
export interface StageEvent {
  step?: string
  label?: string
  status?: string
  detail?: string
  [k: string]: unknown
}

export type StageStatus = 'idle' | 'running' | 'done'

export interface StageStepState {
  key: string
  label: string
  status: StageStatus
  detail?: string
}

/* ---------- 会话消息 ---------- */
export interface AssistantMsg {
  id: string
  role: 'assistant'
  status: 'pending' | 'done' | 'error'
  stages: StageStepState[]
  answer?: AnswerCard
  /** 流中断/网络失败等本地错误文案 */
  errorText?: string
  /** 后端 error 事件文案 */
  sseError?: string
  stopped?: boolean
  /** 触发本轮的原始问题(clarify 带 assume 重发、失败重试) */
  question?: string
}

export type ChatMessage = { id: string; role: 'user'; text: string } | AssistantMsg

export interface SessionInfo {
  id: string
  title: string
  ts: number
  messages: ChatMessage[]
}

/* ---------- API(§11)---------- */
export interface DemoUser {
  id: string
  name?: string
  scope?: string
  [k: string]: unknown
}

export interface MeResponse {
  current?: string
  users?: DemoUser[]
}

export interface HealthResponse {
  status?: string
  db?: string
  data_as_of?: string
}

export interface QuickviewResponse {
  data_as_of?: string
  kpis?: QuickviewKpi[]
  alert?: { text?: string; question?: string } | null
}

export interface QuickviewKpi {
  label?: string
  value?: string
  delta?: DeltaInfo | null
}

export interface CaliberResponse {
  key?: string
  name?: string
  plain?: string
  unit?: string
  version?: number
  sql?: string
  updated?: string
  [k: string]: unknown
}

/* ---------- 后台 ---------- */
export interface AdminMetricsItem {
  key?: string
  name?: string
  plain?: string
  unit?: string
  type?: string
  version?: number
  [k: string]: unknown
}

export interface MetricVersion {
  version?: number
  released?: string
  author?: string
  change_desc?: string
}

export interface MetricDetail {
  key?: string
  name?: string
  plain?: string
  unit?: string
  type?: string
  agg?: string
  sql?: string
  sql_example?: string
  version?: number
  versions?: MetricVersion[]
  dims?: unknown
  [k: string]: unknown
}

export interface AuditItem {
  trace_id?: string
  ts?: string
  session_id?: string
  demo_user?: string
  question?: string
  intent?: string
  metric_key?: string
  status?: string
  row_count?: number
  latency_ms?: number
  llm_used?: boolean
  [k: string]: unknown
}

export interface AuditDetail extends AuditItem {
  ir_json?: string | Record<string, unknown>
  sql_text?: string
  stage_timings?: string | Record<string, unknown>
  result_json?: string | Record<string, unknown>
  narrative?: string | string[]
  llm_model?: string
  [k: string]: unknown
}

export interface AuditListResponse {
  items?: AuditItem[]
  list?: AuditItem[]
  total?: number
  page?: number
  size?: number
  [k: string]: unknown
}

export interface GoldenItem {
  id?: number
  question?: string
  kind?: string
  expect_metric?: string
  expect_dims?: string
  tag?: string
  tolerance_pct?: number
  [k: string]: unknown
}

export interface EvalHistoryItem {
  id?: number
  ts?: string
  mode?: string
  pass_rate?: number
  total?: number
  passed?: number
  [k: string]: unknown
}

export interface EvalResultItem {
  question?: string
  kind?: string
  passed?: boolean
  expect?: string
  actual?: string
  note?: string
  [k: string]: unknown
}

export interface EvalResponse {
  golden_qa?: GoldenItem[]
  goldens?: GoldenItem[]
  questions?: GoldenItem[]
  eval_history?: EvalHistoryItem[]
  history?: EvalHistoryItem[]
  last_report?: EvalRunResponse
  [k: string]: unknown
}

export interface EvalRunResponse {
  total?: number
  passed?: number
  pass_rate?: number
  mode?: string
  results?: EvalResultItem[]
  report?: EvalResultItem[]
  [k: string]: unknown
}

export interface SettingsResponse {
  llm_enabled?: boolean
  base_url?: string
  model?: string
  has_key?: boolean
  key_masked?: string
  [k: string]: unknown
}

export interface SettingsTestResponse {
  ok?: boolean
  latency_ms?: number
  model?: string
  reply?: string
  error?: string
  [k: string]: unknown
}
