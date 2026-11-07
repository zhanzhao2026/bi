import type { StageStepState } from '../../types'

/* ============================================================
 * 四步处理动画(SSE stage 事件驱动)
 * 理解问题 → 查询数据 → 交叉核验(共N项数字与数据仓库一致) → 生成分析
 * ============================================================ */

export function StagePipeline({ stages }: { stages: StageStepState[] }) {
  return (
    <div className="biz-pipe">
      {stages.map((s) => (
        <div key={s.key} className={`bz-stage${s.status === 'running' ? ' on' : ''}${s.status === 'done' ? ' on ok' : ''}`}>
          <span className="bz-stage-dot" />
          <span className="bz-stage-name">{s.label}</span>
          <span className="bz-stage-detail">{s.detail ?? ''}</span>
        </div>
      ))}
    </div>
  )
}
