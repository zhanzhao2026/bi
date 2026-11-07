import type { SessionInfo } from '../types'

/* ---------- 会话本地持久化 ---------- */
const KEY = 'bi_sessions_v1'

export function loadSessions(): SessionInfo[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const v: unknown = JSON.parse(raw)
    if (!Array.isArray(v)) return []
    return v.filter(
      (s): s is SessionInfo =>
        !!s &&
        typeof s === 'object' &&
        typeof (s as SessionInfo).id === 'string' &&
        Array.isArray((s as SessionInfo).messages),
    )
  } catch {
    return []
  }
}

export function saveSessions(list: SessionInfo[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, 20)))
  } catch {
    /* 存储满等异常静默 */
  }
}
