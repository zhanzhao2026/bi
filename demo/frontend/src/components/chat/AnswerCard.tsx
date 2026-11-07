import type { AnswerCard, ChartSpec, TableSpec } from '../../types'
import { DeltaView } from './Delta'
import { TrustBar } from './TrustBar'
import { LineChart } from '../charts/LineChart'
import { BarChart } from '../charts/BarChart'
import { WaterfallChart } from '../charts/WaterfallChart'
import { GroupChart } from '../charts/GroupChart'
import { Icon } from '../Icon'

/* ============================================================
 * 答案卡渲染(§10:8 种 type 各有专属版式)
 * ============================================================ */

export type AskFn = (question: string, opts?: { assume?: string; displayText?: string }) => void

interface CardProps {
  card: AnswerCard
  dataAsOf: string | null
  onAsk: AskFn
  /** 触发本轮的原始问题(clarify 带 assume 重发) */
  question?: string
}

const TYPE_ICONS: Record<string, string> = {
  summary: 'dashboard',
  trend: 'trending-up',
  rank: 'bar-chart',
  attribution: 'search',
  compare: 'compare',
}

function KpiGrid({ card }: { card: AnswerCard }) {
  const kpis = Array.isArray(card.kpis) ? card.kpis : []
  if (!kpis.length) return null
  return (
    <div className="ans-kpis">
      {kpis.map((k, i) => (
        <div className="ans-kpi" key={i}>
          <div className="ans-kpi-label">{k.label ?? ''}</div>
          <div className="ans-kpi-val biz-num">{k.value ?? '—'}</div>
          <div className="ans-kpi-delta">
            <DeltaView delta={k.delta} baseValue={k.value} />
          </div>
        </div>
      ))}
    </div>
  )
}

function ChartBlock({ chart }: { chart: ChartSpec }) {
  const kind = String(chart.kind ?? '')
  const title = typeof chart.title === 'string' && chart.title ? chart.title : null
  return (
    <div className="ans-chart">
      {title && (
        <div className="biz-chart-title">
          <Icon name="bar-chart" />
          {title}
        </div>
      )}
      {kind === 'line' && <LineChart spec={chart} />}
      {kind === 'bar' && <BarChart spec={chart} />}
      {kind === 'waterfall' && <WaterfallChart spec={chart} />}
      {kind === 'group' && <GroupChart spec={chart} />}
      {!['line', 'bar', 'waterfall', 'group'].includes(kind) && (
        <div className="biz-chart-empty">暂无可视化数据</div>
      )}
    </div>
  )
}

function TableBlock({ table }: { table: TableSpec }) {
  const columns = Array.isArray(table.columns) ? table.columns : []
  const rows = Array.isArray(table.rows) ? table.rows : []
  if (!columns.length || !rows.length) return null
  return (
    <div className="ans-table-wrap">
      <table className="biz-table">
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th key={i} className={c.align === 'right' ? 'r' : c.align === 'center' ? 'c' : ''}>
                {c.label ?? c.key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {columns.map((c, ci) => (
                <td key={ci} className={c.align === 'right' ? 'r' : c.align === 'center' ? 'c' : ''}>
                  {row[c.key] === null || row[c.key] === undefined ? '—' : String(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FollowUps({ items, onAsk, label = '接着问' }: { items: string[]; onAsk: AskFn; label?: string }) {
  if (!items.length) return null
  return (
    <div className="ans-follow">
      <span className="ans-follow-label">
        <Icon name="corner-down-right" />
        {label}
      </span>
      {items.map((q) => (
        <button key={q} className="biz-chipx" onClick={() => onAsk(q)}>
          {q}
        </button>
      ))}
    </div>
  )
}

function Narrative({ lines }: { lines?: string[] }) {
  if (!lines?.length) return null
  return (
    <div className="ans-narr">
      {lines.map((t, i) => (
        <p key={i}>{t}</p>
      ))}
    </div>
  )
}

/* ---------- 数据类答案卡(summary / trend / rank / attribution / compare) ---------- */
function DataCardView({ card, dataAsOf, onAsk }: CardProps) {
  const typeClass = `ans-${card.type}`
  const icon = TYPE_ICONS[card.type] ?? 'info'
  return (
    <article className={`ans-card ${typeClass}`}>
      <header className="ans-head">
        <span className="ans-ico">
          <Icon name={icon} />
        </span>
        <h3 className="ans-title">{card.title || '分析结果'}</h3>
        {card.assume?.text ? (
          <span className="biz-assume">
            <Icon name="info" />
            {card.assume.text}
          </span>
        ) : null}
      </header>
      <Narrative lines={card.narrative} />
      <KpiGrid card={card} />
      {card.chart ? <ChartBlock chart={card.chart} /> : null}
      {card.table ? <TableBlock table={card.table} /> : null}
      {card.scope_note ? (
        <div className="ans-scope">
          <span className="biz-note-tag">
            <Icon name="info" />
            相关性线索
          </span>
          <span>{card.scope_note}</span>
        </div>
      ) : null}
      <TrustBar card={card} dataAsOf={dataAsOf} />
      <FollowUps items={Array.isArray(card.followups) ? card.followups : []} onAsk={onAsk} />
    </article>
  )
}

/* ---------- 澄清卡 ---------- */
function ClarifyView({ card, onAsk, question }: CardProps) {
  const options = Array.isArray(card.options) ? card.options : []
  const narrative = Array.isArray(card.narrative) ? card.narrative : []
  const resend = (value: string, label: string) => {
    const q = question || (typeof card.context?.question === 'string' ? card.context.question : '') || label
    onAsk(q, { assume: value, displayText: label })
  }
  return (
    <article className="ans-card ans-clarify">
      <div className="clarify-q">
        <span className="clarify-ico">
          <Icon name="help" />
        </span>
        <div>
          <div className="clarify-title">{card.title || '想先跟你确认一下'}</div>
          {narrative.length ? <div className="clarify-body">{narrative[0]}</div> : null}
        </div>
      </div>
      <div className="clarify-opts">
        {options.map((o, i) => (
          <button key={i} className="biz-opt" onClick={() => resend(o.value, o.label)}>
            {o.label}
          </button>
        ))}
      </div>
      {card.assume_note ? <div className="clarify-note">{card.assume_note}</div> : null}
    </article>
  )
}

/* ---------- 拒答卡 ---------- */
function RefuseView({ card, onAsk }: CardProps) {
  const lines = Array.isArray(card.narrative) && card.narrative.length
    ? card.narrative
    : [card.message || card.reason || '这类问题不在我的回答范围内。']
  return (
    <article className="ans-card ans-refuse">
      <div className="plain-head orange">
        <Icon name="alert-triangle" />
        {card.title || '这个问题我帮不了'}
      </div>
      <div className="plain-text">
        {lines.map((t, i) => (
          <p key={i}>{t}</p>
        ))}
      </div>
      <FollowUps items={Array.isArray(card.followups) ? card.followups : []} onAsk={onAsk} />
    </article>
  )
}

/* ---------- 范围外卡 ---------- */
function BoundaryView({ card, onAsk }: CardProps) {
  const lines = Array.isArray(card.narrative) && card.narrative.length
    ? card.narrative
    : [card.message || card.reason || '这个需求目前还没有开放,可以先看看汇总数据。']
  return (
    <article className="ans-card ans-boundary">
      <div className="plain-head teal">
        <Icon name="info" />
        {card.title || '这个先看不了'}
      </div>
      <div className="plain-text">
        {lines.map((t, i) => (
          <p key={i}>{t}</p>
        ))}
      </div>
      <FollowUps items={Array.isArray(card.followups) ? card.followups : []} onAsk={onAsk} label="可以换个问法" />
    </article>
  )
}

/* ---------- 出错卡 ---------- */
function ErrorView({ card, onAsk }: CardProps) {
  const lines = Array.isArray(card.narrative) && card.narrative.length
    ? card.narrative
    : [card.message || '这次没能算出结果,请换个问法试试。']
  return (
    <article className="ans-card ans-error">
      <div className="plain-head red">
        <Icon name="alert-triangle" />
        {card.title || '这次没能算出来'}
      </div>
      <div className="plain-text">
        {lines.map((t, i) => (
          <p key={i}>{t}</p>
        ))}
      </div>
      <FollowUps items={Array.isArray(card.followups) ? card.followups : []} onAsk={onAsk} label="可以这样问" />
    </article>
  )
}

/* ---------- 分发 ---------- */
export function AnswerCardView(props: CardProps) {
  const { card } = props
  switch (card.type) {
    case 'clarify':
      return <ClarifyView {...props} />
    case 'refuse':
      return <RefuseView {...props} />
    case 'boundary':
      return <BoundaryView {...props} />
    case 'error':
      return <ErrorView {...props} />
    default:
      return <DataCardView {...props} />
  }
}
