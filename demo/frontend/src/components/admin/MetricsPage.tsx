import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { MetricDetail } from '../../types'
import { pick, usePageData, LoadingBlock, ErrBlock, EmptyBlock, ACard, PageHead, Tag, Btn } from './ui'
import type { TagTone } from './ui'
import { Icon } from '../Icon'

/* ============================================================
 * 后台 ② 语义管理(§11 GET /api/admin/metrics)
 * 语义检索测试卡(§16.3)+ 指标表 + 行点击抽屉:定义 / agg / 编译 SQL / 版本历史
 * ============================================================ */

function typeLabel(t?: string): { text: string; tone: 'purple' | 'blue' | 'gray' } {
  if (!t) return { text: '基础', tone: 'gray' }
  if (t.includes('ratio')) return { text: '比率 · 派生', tone: 'purple' }
  if (t.includes('derived')) return { text: '派生', tone: 'purple' }
  if (t.includes('base')) return { text: '基础', tone: 'blue' }
  return { text: t, tone: 'gray' }
}

/* ---------- 语义检索测试(§16.3 GET /api/admin/semantic/search) ---------- */

interface SearchHit {
  kind: string
  key: string
  name: string
  score: number
  reason: string
}

function kindBadge(kind?: string): { text: string; tone: TagTone } {
  if (kind === 'metric') return { text: '指标', tone: 'purple' }
  if (kind === 'dim') return { text: '维度', tone: 'teal' }
  if (kind === 'term') return { text: '术语', tone: 'orange' }
  return { text: kind || '—', tone: 'gray' }
}

function SemanticSearchCard() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const run = async () => {
    const query = q.trim()
    if (!query) {
      setErr('请输入检索词,如:退款率为什么高')
      setHits(null)
      return
    }
    setLoading(true)
    setErr('')
    try {
      const res = await api<SearchHit[]>(
        `/api/admin/semantic/search?q=${encodeURIComponent(query)}&k=8`,
      )
      setHits(Array.isArray(res) ? res : [])
    } catch (e) {
      setErr(e instanceof Error ? e.message : '检索失败')
      setHits(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <ACard title="语义检索测试" icon="search">
      <div className="biz-audit-search">
        <Icon name="search" />
        <input
          placeholder="输入问法或关键词,如:退款率为什么高 / 美妆 / 新客"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) run()
          }}
        />
        <Btn variant="primary" onClick={run} loading={loading}>检索</Btn>
      </div>
      {err ? (
        <div className="biz-error-box">
          <Icon name="alert-triangle" />
          <span>{err}</span>
        </div>
      ) : null}
      {hits !== null && !err ? (
        hits.length === 0 ? (
          <EmptyBlock text="无命中结果(空查询或纯符号不参与检索)" />
        ) : (
          <div className="biz-table-wrap">
            <table className="biz-table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>名称(key)</th>
                  <th>得分</th>
                  <th>命中原因</th>
                </tr>
              </thead>
              <tbody>
                {hits.map((h, i) => {
                  const badge = kindBadge(h.kind)
                  return (
                    <tr key={`${h.kind}-${h.key}-${i}`}>
                      <td>
                        <Tag tone={badge.tone}>{badge.text}</Tag>
                      </td>
                      <td>
                        <b style={{ fontWeight: 500 }}>{h.name || '—'}</b>
                        <span style={{ marginLeft: 8, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12, color: 'var(--c-text3)' }}>({h.key})</span>
                      </td>
                      <td style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12 }}>
                        {typeof h.score === 'number' ? h.score.toFixed(3) : String(h.score ?? '—')}
                      </td>
                      <td style={{ color: 'var(--c-text2)' }}>{h.reason || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </ACard>
  )
}

function MetricDrawer({ metricKey, onClose }: { metricKey: string; onClose: () => void }) {
  const [detail, setDetail] = useState<MetricDetail | null>(null)
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading')
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    setState('loading')
    api<MetricDetail>(`/api/admin/metrics/${encodeURIComponent(metricKey)}`)
      .then((d) => {
        if (!alive) return
        setDetail(d ?? {})
        setState('ok')
      })
      .catch((e) => {
        if (!alive) return
        setErr(e instanceof Error ? e.message : '加载失败')
        setState('error')
      })
    return () => {
      alive = false
    }
  }, [metricKey])

  const versionsRaw = detail?.versions ?? (detail as Record<string, unknown> | null)?.version_history
  const versionsArr = Array.isArray(versionsRaw) ? versionsRaw : []
  const sqlText = (detail?.sql ?? detail?.sql_example ?? '') as string

  return (
    <>
      <div className="biz-drawer-mask" onClick={onClose} />
      <div className="biz-drawer">
        <div className="biz-drawer-head">
          <div className="biz-drawer-title">
            {detail?.name || metricKey}
            <div className="biz-drawer-sub">key: {metricKey}</div>
          </div>
          <button className="biz-icon-btn" onClick={onClose} title="关闭">
            <Icon name="x" />
          </button>
        </div>

        {state === 'loading' ? (
          <LoadingBlock />
        ) : state === 'error' ? (
          <ErrBlock msg={err} />
        ) : (
          <>
            <div className="biz-drawer-sec">
              <h4>类型 / 单位</h4>
              <div className="biz-kv">
                <div>
                  <b>{typeLabel(detail?.type as string | undefined).text}</b>
                </div>
                <div>
                  单位 <b>{(detail?.unit as string) || '—'}</b>
                </div>
                <div>
                  当前版本 <b>v{detail?.version ?? '—'}</b>
                </div>
              </div>
            </div>

            <div className="biz-drawer-sec">
              <h4>业务定义</h4>
              <p>{(detail?.plain as string) || '—'}</p>
            </div>

            <div className="biz-drawer-sec">
              <h4>计算方式(agg)</h4>
              {detail?.agg ? <pre className="biz-pre">{String(detail.agg)}</pre> : <p>—</p>}
            </div>

            <div className="biz-drawer-sec">
              <h4>编译 SQL 示例</h4>
              {sqlText ? <pre className="biz-pre">{sqlText}</pre> : <p>—</p>}
            </div>

            <div className="biz-drawer-sec">
              <h4>版本历史</h4>
              {versionsArr.length ? (
                <div className="biz-hist">
                  {versionsArr.map((v, i) => (
                    <div key={i}>
                      <b>v{v.version ?? '—'}</b>
                      <span className="t">{v.released ?? ''}</span>
                      <span style={{ flex: 1 }}>{v.change_desc ?? ''}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p>暂无版本记录</p>
              )}
            </div>
          </>
        )}
      </div>
    </>
  )
}

interface MetricRow {
  key: string
  name: string
  plain: string
  unit: string
  type: string
  version: string
}

export function MetricsPage() {
  const { data, status, err, reload } = usePageData<unknown>('/api/admin/metrics')
  const [openKey, setOpenKey] = useState<string | null>(null)

  const rows: MetricRow[] = Array.isArray(data)
    ? (data as Record<string, unknown>[]).map((m) => ({
        key: String(pick(m, ['key']) ?? '—'),
        name: String(pick(m, ['name']) ?? '—'),
        plain: String(pick(m, ['plain', 'desc']) ?? ''),
        unit: String(pick(m, ['unit']) ?? ''),
        type: String(pick(m, ['type']) ?? ''),
        version: String(pick(m, ['version']) ?? '—'),
      }))
    : []

  return (
    <div className="biz-admin">
      <div className="biz-admin-inner">
        <PageHead title="语义管理" desc="指标口径、计算方式与版本;行点击查看详情" />
        <SemanticSearchCard />
        {status === 'loading' ? (
          <LoadingBlock />
        ) : status === 'error' ? (
          <ErrBlock msg={err} onRetry={reload} />
        ) : rows.length === 0 ? (
          <EmptyBlock text="还没有指标定义" />
        ) : (
          <ACard title={`指标定义(${rows.length})`} icon="book">
            <div className="biz-table-wrap">
              <table className="biz-table">
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>key</th>
                    <th>类型</th>
                    <th>单位</th>
                    <th>版本</th>
                    <th>定义</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => {
                    const t = typeLabel(r.type)
                    return (
                      <tr key={i} className="clickable" onClick={() => setOpenKey(r.key)}>
                        <td>
                          <b style={{ fontWeight: 500 }}>{r.name}</b>
                        </td>
                        <td style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12, color: 'var(--c-text3)' }}>{r.key}</td>
                        <td>
                          <Tag tone={t.tone}>{t.text}</Tag>
                        </td>
                        <td>{r.unit || '—'}</td>
                        <td>v{r.version}</td>
                        <td style={{ color: 'var(--c-text2)', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {r.plain || '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </ACard>
        )}
      </div>
      {openKey ? <MetricDrawer metricKey={openKey} onClose={() => setOpenKey(null)} /> : null}
    </div>
  )
}
