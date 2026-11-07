import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { AnswerCard, CaliberResponse } from '../../types'
import { METRIC_NAMES } from '../../utils/format'
import { useToast } from '../Toast'
import { Icon } from '../Icon'

/* ============================================================
 * 信任条:数据已核验 + 数据截至 + 口径弹层 + 👍👎
 * ============================================================ */

const FB_TAGS = ['口径不对', '数字有误', '没听懂我的问题', '其他'] as const

function CaliberChip({ metricKey, open, onToggle, onClose }: {
  metricKey: string
  open: boolean
  onToggle: () => void
  onClose: () => void
}) {
  const wrapRef = useRef<HTMLSpanElement>(null)
  const fetchedRef = useRef<string | null>(null)
  const [info, setInfo] = useState<CaliberResponse | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle')
  const [up, setUp] = useState(false)

  useEffect(() => {
    if (!open) return
    const el = wrapRef.current
    if (el) {
      const rect = el.getBoundingClientRect()
      setUp(rect.bottom + 210 > window.innerHeight)
    }
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    /* 同一指标只请求一次;失败后允许重试(再次打开时重新请求) */
    if (fetchedRef.current === metricKey) return
    fetchedRef.current = metricKey
    setState('loading')
    api<CaliberResponse>(`/api/caliber/${encodeURIComponent(metricKey)}?mode=user`)
      .then((d) => {
        setInfo(d ?? {})
        setState('ok')
      })
      .catch(() => {
        fetchedRef.current = null
        setState('error')
      })
  }, [open, metricKey])

  const name = info?.name || METRIC_NAMES[metricKey] || metricKey

  return (
    <span className="biz-cal-wrap" ref={wrapRef}>
      <button className="biz-cal" onClick={onToggle} title="这个数怎么算的">
        <Icon name="book" />
        口径 · {METRIC_NAMES[metricKey] || metricKey}
      </button>
      {open && (
        <div className={`biz-popover${up ? ' up' : ''}`}>
          <div className="biz-pop-title">
            <Icon name="book" />
            统计口径 · {name}
          </div>
          {state === 'error' ? (
            <div className="biz-pop-def">暂时拿不到口径说明,请稍后再试。</div>
          ) : (
            <div className="biz-pop-def">{info?.plain || '正在加载口径说明…'}</div>
          )}
          <div className="biz-pop-note">这个数按上面的规则统计,查之前会先和数仓核对一遍。</div>
        </div>
      )}
    </span>
  )
}

export function TrustBar({ card, dataAsOf }: { card: AnswerCard; dataAsOf: string | null }) {
  const toast = useToast()
  const [fb, setFb] = useState<'up' | 'down' | null>(null)
  const [tagsOpen, setTagsOpen] = useState(false)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const caliber = Array.isArray(card.caliber) ? card.caliber : []

  const sendFeedback = async (rating: 'up' | 'down', tags?: string[]) => {
    setFb(rating)
    setTagsOpen(false)
    try {
      await api('/api/feedback', {
        method: 'POST',
        body: { trace_id: card.trace_id, rating, tags: tags ?? undefined },
      })
      toast('反馈已收到,谢谢', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '反馈没发出去,请稍后再试', 'error')
    }
  }

  return (
    <>
      <div className="ans-foot">
        <span className="biz-badge biz-badge-ok">
          <Icon name="shield-check" />
          数据已核验
        </span>
        <span className="biz-badge">
          <Icon name="calendar" />
          数据截至 {card.data_as_of || dataAsOf || '—'}
        </span>
        {caliber.map((k) => (
          <CaliberChip
            key={k}
            metricKey={k}
            open={openKey === k}
            onToggle={() => setOpenKey(openKey === k ? null : k)}
            onClose={() => setOpenKey(null)}
          />
        ))}
        <span className="biz-fb">
          {fb ? (
            <span className="biz-fb-done">
              <Icon name="check-circle" />
              已反馈
            </span>
          ) : (
            <>
              <button title="有帮助" onClick={() => void sendFeedback('up')}>
                <Icon name="thumbs-up" />
              </button>
              <button title="没帮上忙" onClick={() => setTagsOpen((v) => !v)}>
                <Icon name="thumbs-down" />
              </button>
            </>
          )}
        </span>
      </div>
      {tagsOpen && !fb && (
        <div className="biz-fb-tags">
          <span>哪里没帮上忙?</span>
          {FB_TAGS.map((t) => (
            <button key={t} onClick={() => void sendFeedback('down', [t])}>
              {t}
            </button>
          ))}
        </div>
      )}
    </>
  )
}
