import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../../api/client'
import { Icon } from '../Icon'

/* ============================================================
 * 后台管理共用部件
 * ============================================================ */

export function PageHead({ title, desc, right }: { title: string; desc?: string; right?: ReactNode }) {
  return (
    <div className="biz-page-head">
      <div>
        <div className="biz-page-title">{title}</div>
        {desc ? <div className="biz-page-desc">{desc}</div> : null}
      </div>
      {right ? <div className="biz-page-right">{right}</div> : null}
    </div>
  )
}

export function ACard({ title, icon, right, children }: {
  title: string
  icon?: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="biz-acard">
      <div className="biz-acard-title">
        {icon ? <Icon name={icon} /> : null}
        {title}
        {right ? <div className="biz-acard-right">{right}</div> : null}
      </div>
      {children}
    </div>
  )
}

export function Gate({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="biz-gate">
      <div className="biz-gate-val biz-num">{value}</div>
      <div className="biz-gate-label">{label}</div>
      {sub ? <div className="biz-gate-label">{sub}</div> : null}
    </div>
  )
}

export type TagTone = 'green' | 'red' | 'orange' | 'purple' | 'teal' | 'blue' | 'gray'

export function Tag({ tone, children }: { tone: TagTone; children: ReactNode }) {
  return <span className={`biz-tag biz-tag-${tone}`}>{children}</span>
}

export function statusTone(status?: string): TagTone {
  switch (status) {
    case 'ok':
      return 'green'
    case 'clarify':
      return 'orange'
    case 'refuse':
      return 'purple'
    case 'boundary':
      return 'teal'
    case 'error':
      return 'red'
    default:
      return 'gray'
  }
}

export function Btn({ variant, icon, loading, children, ...rest }: {
  variant?: 'primary'
  icon?: string
  loading?: boolean
  children: ReactNode
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'>) {
  return (
    <button
      className={`biz-btn${variant === 'primary' ? ' biz-btn-primary' : ''}`}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading ? <span className="biz-spin" /> : icon ? <Icon name={icon} /> : null}
      {children}
    </button>
  )
}

export function LoadingBlock({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="biz-loading">
      <span className="biz-spin" />
      {text}
    </div>
  )
}

export function ErrBlock({ msg, onRetry }: { msg: string; onRetry?: () => void }) {
  return (
    <div className="biz-error-box">
      <Icon name="alert-triangle" />
      <span>{msg}</span>
      {onRetry ? (
        <button className="biz-btn" onClick={onRetry}>
          <Icon name="refresh" />
          重试
        </button>
      ) : null}
    </div>
  )
}

export function EmptyBlock({ text = '暂无数据' }: { text?: string }) {
  return <div className="biz-empty-block">{text}</div>
}

export function Pre({ children }: { children: ReactNode }) {
  return <pre className="biz-pre">{children}</pre>
}

/* ---------- 页面数据加载 Hook ---------- */
export interface PageData<T> {
  data: T | null
  status: 'loading' | 'ok' | 'error'
  err: string
  reload: () => void
}

export function usePageData<T>(path: string): PageData<T> {
  const [data, setData] = useState<T | null>(null)
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    setStatus('loading')
    setErr('')
    api<T>(path)
      .then((d) => {
        if (!alive) return
        setData((d ?? null) as T | null)
        setStatus('ok')
      })
      .catch((e) => {
        if (!alive) return
        setErr(e instanceof Error ? e.message : '加载失败')
        setStatus('error')
      })
    return () => {
      alive = false
    }
  }, [path, tick])

  return { data, status, err, reload: () => setTick((t) => t + 1) }
}

/* ---------- 宽松取值工具(后端并行开发,字段名防御) ---------- */
/* eslint-disable @typescript-eslint/no-explicit-any */
export function pick(obj: unknown, keys: string[]): any {
  if (!obj || typeof obj !== 'object') return undefined
  const o = obj as Record<string, unknown>
  for (const k of keys) {
    const v = o[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return undefined
}

export function pickList(obj: unknown, keys: string[]): Record<string, unknown>[] {
  const v = pick(obj, keys)
  if (Array.isArray(v)) return v.filter((x) => !!x && typeof x === 'object') as Record<string, unknown>[]
  if (Array.isArray(obj)) return obj.filter((x) => !!x && typeof x === 'object') as Record<string, unknown>[]
  return []
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    return String(Math.round(v * 100) / 100)
  }
  return String(v)
}
