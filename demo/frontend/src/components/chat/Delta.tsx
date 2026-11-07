import type { DeltaInfo } from '../../types'
import { trimNum } from '../../utils/format'

/* ============================================================
 * 涨跌着色:中国习惯红涨绿跌;后端 good=false 时一律红(如退款率上升)
 * ============================================================ */

export function deltaClass(dir: 'up' | 'down' | 'flat', good?: boolean): 'biz-delta-red' | 'biz-delta-green' | 'biz-delta-flat' {
  if (dir === 'up') return 'biz-delta-red'
  if (dir === 'down') return good === false ? 'biz-delta-red' : 'biz-delta-green'
  return 'biz-delta-flat'
}

export function DeltaView({ delta, baseValue }: { delta?: DeltaInfo | null; baseValue?: string }) {
  if (!delta) return null
  let dir = delta.dir
  if (dir !== 'up' && dir !== 'down' && dir !== 'flat') {
    const pct = typeof delta.pct === 'number' ? delta.pct : null
    dir = pct === null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat'
  }
  const cls = deltaClass(dir, delta.good)
  const arrow = dir === 'up' ? '↑' : dir === 'down' ? '↓' : '→'
  /* 比率类(值带 %)的涨跌用 pp,金额类用 % */
  const unit = baseValue && baseValue.includes('%') ? 'pp' : '%'
  const num = typeof delta.pct === 'number' ? `${delta.pct > 0 ? '+' : ''}${trimNum(delta.pct)}${unit}` : ''
  const text = typeof delta.text === 'string' && delta.text ? delta.text : num
  return (
    <span className={`biz-delta ${cls}`}>
      <span>{arrow}</span>
      {text ? <span>{text}</span> : null}
      {delta.label ? <span className="biz-delta-label">{delta.label}</span> : null}
    </span>
  )
}
