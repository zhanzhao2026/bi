import type { DeltaInfo } from '../../types'
import { trimNum } from '../../utils/format'

/* ============================================================
 * 涨跌着色:按 good 布尔(契约 §13「退款率上升显示红」)
 * good=true → 绿(变好),good=false → 红(变坏,含退款率上升/GMV下滑)
 * 无 good 信息时退回中文习惯:涨红跌绿
 * ============================================================ */

export function deltaClass(
  dir: 'up' | 'down' | 'flat',
  good?: boolean,
): 'biz-delta-red' | 'biz-delta-green' | 'biz-delta-flat' {
  if (dir === 'flat') return 'biz-delta-flat'
  if (good === true) return 'biz-delta-green'
  if (good === false) return 'biz-delta-red'
  if (dir === 'up') return 'biz-delta-red'
  if (dir === 'down') return 'biz-delta-green'
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
