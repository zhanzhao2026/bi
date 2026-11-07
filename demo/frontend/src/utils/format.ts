/* ---------- 通用格式化工具 ---------- */

/** 指标 key → 业务名(用户视角展示用,不露出 key) */
export const METRIC_NAMES: Record<string, string> = {
  gmv: 'GMV',
  order_cnt: '支付订单数',
  pay_user_cnt: '支付人数',
  arppu: '客单价',
  item_cnt: '支付件数',
  refund_amt: '退款金额',
  refund_rate: '退款率',
  refund_order_rate: '订单退款率',
  new_order_ratio: '新客订单占比',
}

/** 从字符串里解析首个数字("上一期 2.40" → 2.4) */
export function parseNumIn(s: unknown): number | null {
  if (typeof s === 'number' && isFinite(s)) return s
  if (typeof s !== 'string') return null
  const m = s.match(/-?\d+(\.\d+)?/)
  if (!m) return null
  const n = Number(m[0])
  return isFinite(n) ? n : null
}

/** 数值裁剪显示:0.4 → "0.4",5.2000001 → "5.2" */
export function trimNum(n: number): string {
  if (!isFinite(n)) return '—'
  const r = Math.round(n * 100) / 100
  return String(r)
}

/** 图表轴/标签的大数缩写(万/亿) */
export function fmtWan(v: unknown): string {
  const n = typeof v === 'number' ? v : parseNumIn(v)
  if (n === null) return '—'
  if (Math.abs(n) >= 1e8) return `${trimNum(Math.round((n / 1e8) * 10) / 10)}亿`
  if (Math.abs(n) >= 1e4) return `${Math.round(n / 1e4)}万`
  return trimNum(n)
}

/** 去掉名称里嵌的数字("上一期 2.40" → "上一期") */
export function shortLabel(name: string): string {
  const s = name.replace(/[-+]?[\d.]+(%|pp|万元|元|人|单|件)?\s*$/, '').trim()
  return s || name
}

/** 稳定 id */
export function genId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
  } catch {
    /* ignore */
  }
  return `id_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
}

/** JSON 字符串尽量格式化(后台展示用) */
export function tryPretty(s?: string | null): string {
  if (s === null || s === undefined || s === '') return ''
  try {
    const v = JSON.parse(s)
    return JSON.stringify(v, null, 2)
  } catch {
    return String(s)
  }
}

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s
}

/** 按小时问候 */
export function greeting(): string {
  const h = new Date().getHours()
  if (h >= 5 && h < 11) return '早上好'
  if (h >= 11 && h < 13) return '中午好'
  if (h >= 13 && h < 18) return '下午好'
  return '晚上好'
}

/** 统计答案卡里数字的项数(核验文案用) */
export function countAnswerNumbers(card: unknown): number {
  if (!card || typeof card !== 'object') return 0
  const o = card as {
    kpis?: unknown
    table?: { rows?: unknown }
    chart?: { kind?: unknown; data?: unknown; items?: unknown; series?: unknown }
  }
  let n = Array.isArray(o.kpis) ? o.kpis.length : 0
  n += Array.isArray(o.table?.rows) ? (o.table.rows.length ?? 0) : 0
  const c = o.chart
  if (c && typeof c === 'object') {
    if (c.kind === 'waterfall') {
      n += Array.isArray(c.items) ? c.items.length : 0
    } else if (c.kind === 'bar') {
      n += Array.isArray(c.data) ? c.data.length : 0
    } else if (Array.isArray(c.series)) {
      for (const s of c.series) {
        if (s && typeof s === 'object') {
          const d = (s as { data?: unknown }).data
          n += Array.isArray(d) ? d.length : 0
        }
      }
    }
  }
  return n
}
