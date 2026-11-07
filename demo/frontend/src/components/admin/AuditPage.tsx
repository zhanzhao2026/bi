import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { AuditDetail, AuditItem } from '../../types'
import { fmtValue, pick, usePageData, LoadingBlock, ErrBlock, EmptyBlock, ACard, PageHead, Tag, statusTone, Btn } from './ui'
import { Icon } from '../Icon'
import { tryPretty, truncate } from '../../utils/format'

/* ============================================================
 * 后台 ③ 审计日志(§11 GET /api/admin/audit)
 * 列表 + keyword 检索 + 分页 + 行展开 trace 详情(ir/sql/阶段耗时/结果)
 * ============================================================ */

const PAGE_SIZE = 20

interface AuditListNorm {
  items: AuditItem[]
  total: number
}

function normalizeList(data: unknown): AuditListNorm {
  const d = (data ?? {}) as Record<string, unknown>
  const raw = (Array.isArray(d.items) ? d.items : Array.isArray(d.list) ? d.list : Array.isArray(data) ? data : []) as Record<string, unknown>[]
  const items: AuditItem[] = raw.map((r) => ({
    trace_id: String(pick(r, ['trace_id', 'trace']) ?? ''),
    ts: String(pick(r, ['ts', 'time']) ?? ''),
    session_id: String(pick(r, ['session_id']) ?? ''),
    demo_user: String(pick(r, ['demo_user']) ?? ''),
    question: String(pick(r, ['question']) ?? ''),
    intent: String(pick(r, ['intent']) ?? ''),
    metric_key: String(pick(r, ['metric_key']) ?? ''),
    status: String(pick(r, ['status']) ?? '—'),
    row_count: typeof r.row_count === 'number' ? r.row_count : undefined,
    latency_ms: typeof r.latency_ms === 'number' ? r.latency_ms : undefined,
    llm_used: typeof r.llm_used === 'boolean' ? r.llm_used : undefined,
  }))
  const total = typeof d.total === 'number' ? d.total : items.length
  return { items, total }
}

function StageTimings({ raw }: { raw?: string }) {
  if (!raw) return <p>—</p>
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>
    const entries = Object.entries(obj)
    if (entries.length) {
      return (
        <div className="biz-kv">
          {entries.map(([k, v]) => (
            <div key={k}>
              {k} <b>{typeof v === 'number' ? `${v} ms` : String(v)}</b>
            </div>
          ))}
        </div>
      )
    }
  } catch {
    /* 非 JSON 原样展示 */
  }
  return <pre className="biz-pre">{raw}</pre>
}

function TraceDetail({ traceId }: { traceId: string }) {
  const [detail, setDetail] = useState<AuditDetail | null>(null)
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading')
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    setState('loading')
    api<AuditDetail>(`/api/admin/audit/${encodeURIComponent(traceId)}`)
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
  }, [traceId])

  if (state === 'loading') {
    return (
      <div className="biz-trace-detail">
        <LoadingBlock text="加载 trace 详情…" />
      </div>
    )
  }
  if (state === 'error') {
    return (
      <div className="biz-trace-detail">
        <ErrBlock msg={err} />
      </div>
    )
  }
  const resultJson = tryPretty(detail?.result_json as string | undefined)
  return (
    <div className="biz-trace-detail">
      <div className="biz-kv">
        <div>
          状态 <b>{detail?.status ?? '—'}</b>
        </div>
        <div>
          意图 <b>{fmtValue(detail?.intent)}</b>
        </div>
        <div>
          指标 <b>{fmtValue(detail?.metric_key)}</b>
        </div>
        <div>
          行数 <b>{fmtValue(detail?.row_count)}</b>
        </div>
        <div>
          耗时 <b>{fmtValue(detail?.latency_ms)} ms</b>
        </div>
        <div>
          引擎 <b>{detail?.llm_used ? `LLM(${detail?.llm_model ?? ''})` : '规则'}</b>
        </div>
        <div>
          用户 <b>{fmtValue(detail?.demo_user)}</b>
        </div>
        <div>
          会话 <b>{fmtValue(detail?.session_id)}</b>
        </div>
      </div>
      {detail?.narrative ? (
        <>
          <div className="biz-pre-title">回答摘要</div>
          <div style={{ marginTop: 6, color: 'var(--c-text2)' }}>{truncate(String(detail.narrative), 300)}</div>
        </>
      ) : null}
      <div className="biz-pre-title">IR(中间理解)</div>
      <pre className="biz-pre">{tryPretty(detail?.ir_json as string | undefined) || '—'}</pre>
      <div className="biz-pre-title">SQL</div>
      <pre className="biz-pre">{String(detail?.sql_text ?? '—')}</pre>
      <div className="biz-pre-title">阶段耗时</div>
      <StageTimings raw={detail?.stage_timings as string | undefined} />
      <div className="biz-pre-title">结果集</div>
      <pre className="biz-pre">{resultJson ? truncate(resultJson, 1600) : '—'}</pre>
    </div>
  )
}

export function AuditPage() {
  const [keyword, setKeyword] = useState('')
  const [kwApplied, setKwApplied] = useState('')
  const [page, setPage] = useState(1)
  const [openTrace, setOpenTrace] = useState<string | null>(null)

  const path = `/api/admin/audit?keyword=${encodeURIComponent(kwApplied)}&page=${page}&size=${PAGE_SIZE}`
  const { data, status, err, reload } = usePageData<unknown>(path)
  const list = normalizeList(data)
  const totalPages = Math.max(1, Math.ceil(list.total / PAGE_SIZE))

  const search = () => {
    setKwApplied(keyword.trim())
    setPage(1)
    setOpenTrace(null)
  }

  return (
    <div className="biz-admin">
      <div className="biz-admin-inner">
        <PageHead title="审计日志" desc="每一问的完整处理链路:理解、查询、耗时与结果,可检索" />
        <div className="biz-audit-search">
          <Icon name="search" />
          <input
            placeholder="按问题、trace_id、指标检索"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) search()
            }}
          />
          <Btn onClick={search}>检索</Btn>
        </div>

        {status === 'loading' ? (
          <LoadingBlock />
        ) : status === 'error' ? (
          <ErrBlock msg={err} onRetry={reload} />
        ) : list.items.length === 0 ? (
          <ACard title={`共 ${list.total} 条`} icon="list">
            <EmptyBlock text="没有匹配的记录" />
          </ACard>
        ) : (
          <ACard title={`共 ${list.total} 条`} icon="list" right={`第 ${page} / ${totalPages} 页`}>
            <div className="biz-trace-head">
              <span>时间</span>
              <span className="biz-hide-n">trace</span>
              <span>问题</span>
              <span>指标</span>
              <span className="biz-hide-n">耗时</span>
              <span>状态</span>
            </div>
            {list.items.map((it, i) => {
              const tid = it.trace_id || `#${i}`
              const open = openTrace === tid
              return (
                <div key={tid}>
                  <button
                    className={`biz-trace-row${open ? ' open' : ''}`}
                    onClick={() => setOpenTrace(open ? null : tid)}
                    title="点击展开详情"
                  >
                    <span className="dim">{it.ts || '—'}</span>
                    <span className="dim biz-hide-n" style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12 }}>
                      {tid}
                    </span>
                    <span className="q">{it.question || '—'}</span>
                    <span className="dim">{it.metric_key || it.intent || '—'}</span>
                    <span className="dim biz-hide-n">{it.latency_ms !== undefined ? `${it.latency_ms} ms` : '—'}</span>
                    <span>
                      <Tag tone={statusTone(it.status)}>{it.status}</Tag>
                    </span>
                  </button>
                  {open ? <TraceDetail traceId={tid} /> : null}
                </div>
              )
            })}
            {totalPages > 1 ? (
              <div className="biz-pager">
                <button className="biz-btn" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  上一页
                </button>
                <span>
                  {page} / {totalPages}
                </span>
                <button className="biz-btn" disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                  下一页
                </button>
              </div>
            ) : null}
          </ACard>
        )}
      </div>
    </div>
  )
}
