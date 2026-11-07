import type { QuickviewResponse } from '../../types'
import { greeting } from '../../utils/format'
import { Icon } from '../Icon'
import { DeltaView } from './Delta'

/* ============================================================
 * 空态:问候 + quickview 速览 + 告警卡 + 三组引导问法
 * ============================================================ */

const GUIDE_GROUPS: { title: string; items: string[] }[] = [
  {
    title: '快速了解经营情况',
    items: ['最近30天的整体经营情况', '昨天的GMV和订单数有多少', '这个月GMV怎么样'],
  },
  {
    title: '找找变化的原因',
    items: ['最近30天退款率为什么上升', '哪些类目拖累了GMV', '美妆最近的退款情况怎么样'],
  },
  {
    title: '换个角度看数据',
    items: ['各类目的GMV排名', '新老客的消费对比', '各渠道的GMV排名'],
  },
]

export interface EmptyStateProps {
  userName: string
  quickview: QuickviewResponse | null
  status: 'loading' | 'ok' | 'error'
  onRetryQv: () => void
  onAsk: (q: string) => void
}

export function EmptyState({ userName, quickview, status, onRetryQv, onAsk }: EmptyStateProps) {
  const kpis = Array.isArray(quickview?.kpis) ? quickview.kpis : []
  const alert = quickview?.alert && typeof quickview.alert === 'object' ? quickview.alert : null

  return (
    <div className="biz-empty">
      <div className="biz-hello">
        <span className="biz-hello-ava">
          <Icon name="sparkles" />
        </span>
        <div>
          <div className="biz-hello-title">
            {greeting()},{userName}
          </div>
          <div className="biz-hello-sub">
            把经营问题直接问我:GMV、退款率、客单价、类目与渠道表现,我查完数、核对完再回答。
          </div>
        </div>
      </div>

      {status === 'loading' && (
        <div className="biz-glance">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="biz-sk" style={{ height: 84 }} />
          ))}
        </div>
      )}

      {status === 'error' && (
        <div className="biz-qv-err">
          <Icon name="alert-triangle" />
          <span>暂时拿不到经营速览,可能服务还没就绪。</span>
          <button className="biz-link-btn" onClick={onRetryQv}>
            重试
          </button>
        </div>
      )}

      {status === 'ok' && (
        <>
          {kpis.length ? (
            <div className="biz-glance">
              {kpis.map((k, i) => (
                <div className="biz-gtile" key={i}>
                  <div className="biz-gtile-label">
                    <Icon name="trending-up" />
                    {k.label ?? ''}
                  </div>
                  <div className="biz-gtile-val biz-num">{k.value ?? '—'}</div>
                  <div className="biz-gtile-delta">
                    <DeltaView delta={k.delta} baseValue={k.value} />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          {alert?.text ? (
            <button
              className="biz-alert-card"
              onClick={() => alert.question && onAsk(alert.question)}
              title={alert.question}
            >
              <span className="biz-alert-ico">
                <Icon name="alert-triangle" />
              </span>
              <span className="biz-alert-text">{alert.text}</span>
              <span className="biz-alert-go">
                查看原因
                <Icon name="chevron-right" />
              </span>
            </button>
          ) : null}
        </>
      )}

      <div>
        {GUIDE_GROUPS.map((g) => (
          <div className="biz-quick-sec" key={g.title}>
            <div className="biz-quick-title">{g.title}</div>
            <div className="biz-quick-row">
              {g.items.map((q) => (
                <button key={q} className="biz-qcard" onClick={() => onAsk(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
