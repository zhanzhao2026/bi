import { useContext, useMemo } from 'react'
import { ThemeContext } from '../../utils/theme'
import { BarChart } from '../charts/BarChart'
import { fmtValue, pick, pickList, usePageData, LoadingBlock, ErrBlock, EmptyBlock, ACard, Gate, PageHead, Tag, statusTone } from './ui'

/* ============================================================
 * 后台 ① 运营总览(§11 GET /api/admin/overview)
 * 4 指标卡 + 近7日提问趋势 + 数据水位 + DQC 告警 + 近期错例
 * ============================================================ */

interface OverviewNorm {
  today: string
  sessions: string
  latency: string
  llmUsage: string
  llmLabel: string
  trend: { label: string; value: number }[]
  tables: { name: string; rows: string; latest: string }[]
  dqc: { text: string; tone: 'green' | 'red' | 'orange' }[]
  errors: { trace_id: string; ts: string; question: string; status: string }[]
}

function num(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return isFinite(n) ? String(Math.round(n * 100) / 100) : String(v)
}

function normalize(data: unknown): OverviewNorm {
  const trendList = pickList(data, ['trend_7d', 'trend7d', 'recent_7d', 'last_7d', 'trend', 'questions_trend', 'daily'])
  const trend = trendList.map((r) => ({
    label: String(pick(r, ['date', 'day', 'ts', 'dt', 'time', 'label']) ?? ''),
    value: Number(pick(r, ['count', 'cnt', 'num', 'value', 'questions', 'total']) ?? 0) || 0,
  }))
  const tables = pickList(data, ['data_level', 'tables', 'water_level', 'table_stats', 'data_water_level']).map((r) => ({
    name: String(pick(r, ['table', 'name', 'table_name']) ?? '—'),
    rows: num(pick(r, ['rows', 'row_count', 'cnt', 'count'])),
    latest: String(pick(r, ['latest', 'latest_date', 'max_date', 'last_date', 'updated']) ?? '—'),
  }))
  /* 兼容 data_water 为扁平对象的后端形状:{fact_order_rows: 207008, latest_date: "..."} */
  if (!tables.length) {
    const waterRaw = (data as { data_water?: unknown } | null)?.data_water
    if (waterRaw && typeof waterRaw === 'object' && !Array.isArray(waterRaw)) {
      const o = waterRaw as Record<string, unknown>
      const latest = String(pick(o, ['latest_date', 'latest', 'max_date', 'last_date']) ?? '—')
      for (const [k, v] of Object.entries(o)) {
        if (k.endsWith('_rows')) {
          tables.push({ name: k.replace(/_rows$/, ''), rows: num(v), latest })
        }
      }
    }
  }
  const dqc = pickList(data, ['dqc_alerts', 'dqc', 'alerts', 'dqc_warnings']).map((r) => {
    if (typeof r === 'string') return { text: r, tone: 'orange' as const }
    const rate = Number(pick(r, ['rate', 'refund_rate', 'value']) ?? 0)
    const text =
      String(pick(r, ['message', 'msg', 'text', 'desc', 'alert', 'detail']) ?? '') ||
      `${fmtValue(pick(r, ['category', 'category_name', 'name']))} 退款率 ${num(pick(r, ['rate', 'refund_rate', 'value']))}`
    return { text, tone: rate > 3 ? ('red' as const) : ('orange' as const) }
  })
  const errors = pickList(data, ['recent_errors', 'errors', 'error_cases', 'recent_error']).map((r) => ({
    trace_id: String(pick(r, ['trace_id', 'trace']) ?? '—'),
    ts: String(pick(r, ['ts', 'time', 'created']) ?? '—'),
    question: String(pick(r, ['question', 'q']) ?? '—'),
    status: String(pick(r, ['status']) ?? '—'),
  }))
  const llmRaw = pick(data, ['llm_usage', 'llm_usage_pct', 'llm_rate', 'llm_used_rate'])
  let llmUsage = num(llmRaw)
  let llmLabel = 'LLM 使用率'
  if (llmRaw && typeof llmRaw === 'object') {
    llmUsage = num(pick(llmRaw, ['pct', 'rate', 'value', 'usage']))
    llmLabel = String(pick(llmRaw, ['label', 'desc']) ?? llmLabel)
  }
  return {
    today: num(pick(data, ['today_questions', 'today_count', 'questions_today', 'today'])),
    sessions: num(pick(data, ['active_sessions', 'session_count', 'sessions', 'active_session_count'])),
    latency: num(pick(data, ['avg_latency_ms', 'avg_latency', 'latency_ms', 'avg_ms'])),
    llmUsage,
    llmLabel,
    trend,
    tables,
    dqc,
    errors,
  }
}

export function OverviewPage() {
  const theme = useContext(ThemeContext)
  const { data, status, err, reload } = usePageData<unknown>('/api/admin/overview')
  const o = useMemo(() => normalize(data), [data])
  const barSpec = useMemo(
    () =>
      o.trend.length
        ? { kind: 'bar' as const, x: o.trend.map((t) => t.label), data: o.trend.map((t) => t.value), title: '' }
        : null,
    [o, theme],
  )

  return (
    <div className="biz-admin">
      <div className="biz-admin-inner">
        <PageHead title="运营总览" desc="演示系统的真实运行情况:提问量、数据水位与告警" />
        {status === 'loading' ? (
          <LoadingBlock />
        ) : status === 'error' ? (
          <ErrBlock msg={err} onRetry={reload} />
        ) : (
          <>
            <div className="biz-gates" style={{ marginBottom: 14 }}>
              <Gate label="今日提问数" value={o.today} />
              <Gate label="活跃会话数" value={o.sessions} />
              <Gate label="平均时延" value={o.latency === '—' ? '—' : `${o.latency} ms`} />
              <Gate label={o.llmLabel} value={o.llmUsage === '—' ? '—' : `${o.llmUsage}%`} />
            </div>

            <ACard title="近7日提问趋势" icon="bar-chart">
              {barSpec ? <BarChart spec={barSpec} height={220} /> : <EmptyBlock text="近7日还没有提问记录" />}
            </ACard>

            <ACard title="数据水位" icon="database">
              {o.tables.length ? (
                <div className="biz-table-wrap">
                  <table className="biz-table">
                    <thead>
                      <tr>
                        <th>数据表</th>
                        <th className="r">行数</th>
                        <th className="r">最新数据日期</th>
                      </tr>
                    </thead>
                    <tbody>
                      {o.tables.map((t, i) => (
                        <tr key={i}>
                          <td>{t.name}</td>
                          <td className="r">{t.rows}</td>
                          <td className="r">{t.latest}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyBlock />
              )}
            </ACard>

            <ACard title="DQC 告警" icon="alert-triangle">
              {o.dqc.length ? (
                <div>
                  {o.dqc.map((d, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', fontSize: 13 }}>
                      <Tag tone={d.tone}>退款率异常</Tag>
                      <span>{d.text}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', fontSize: 13 }}>
                  <Tag tone="green">一切正常</Tag>
                  <span>暂无数据质量告警</span>
                </div>
              )}
            </ACard>

            <ACard title="近期错例" icon="list">
              {o.errors.length ? (
                <div>
                  {o.errors.map((e, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '130px 110px 1fr 76px',
                        gap: 8,
                        alignItems: 'center',
                        padding: '8px 10px',
                        borderRadius: 9,
                        fontSize: 13,
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      <span style={{ color: 'var(--c-text3)' }}>{e.trace_id}</span>
                      <span style={{ color: 'var(--c-text3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.ts}</span>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.question}</span>
                      <Tag tone={statusTone(e.status)}>{e.status}</Tag>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyBlock text="暂无错例" />
              )}
            </ACard>
          </>
        )}
      </div>
    </div>
  )
}
