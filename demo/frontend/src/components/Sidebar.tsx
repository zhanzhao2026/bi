import { useEffect, useRef, useState } from 'react'
import type { DemoUser, SessionInfo } from '../types'
import { Icon } from './Icon'
import type { ViewKey } from '../App'

/* ============================================================
 * 左侧栏:Logo + 新对话 + 最近会话 + 管理后台入口 + 身份切换
 * ============================================================ */

const ADMIN_ITEMS: { key: ViewKey; label: string; icon: string }[] = [
  { key: 'overview', label: '运营总览', icon: 'dashboard' },
  { key: 'metrics', label: '语义管理', icon: 'book' },
  { key: 'audit', label: '审计日志', icon: 'list' },
  { key: 'eval', label: '评测', icon: 'flask' },
  { key: 'settings', label: '系统设置', icon: 'settings' },
]

export function displayName(u?: DemoUser | null): string {
  if (!u) return '张总 · 总经理'
  const name = u.name ?? u.id
  if (name.includes('·')) return name
  return u.scope ? `${name} · ${u.scope}` : name
}

export function shortName(u?: DemoUser | null): string {
  if (!u) return '张总'
  const name = u.name ?? u.id
  return name.split('·')[0].trim() || name
}

export interface SidebarProps {
  view: ViewKey
  sessions: SessionInfo[]
  activeId: string | null
  users: DemoUser[]
  currentUserId: string
  onNewChat: () => void
  onSelectSession: (id: string) => void
  onNav: (v: ViewKey) => void
  onSwitchUser: (id: string) => void
}

export function Sidebar({
  view,
  sessions,
  activeId,
  users,
  currentUserId,
  onNewChat,
  onSelectSession,
  onNav,
  onSwitchUser,
}: SidebarProps) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const current = users.find((u) => u.id === currentUserId) ?? users[0]

  return (
    <aside className="biz-side">
      <div className="biz-logo">
        <span className="biz-logo-badge">
          <Icon name="sparkles" size={17} />
        </span>
        <div>
          <div className="biz-logo-name">BizInsight</div>
          <div className="biz-logo-sub">经营分析助手</div>
        </div>
      </div>

      <button className="biz-newchat" onClick={onNewChat}>
        <Icon name="plus" />
        新对话
      </button>

      <div className="biz-side-label">最近会话</div>
      <div className="biz-sessions">
        {sessions.length === 0 ? (
          <div className="biz-sessions-empty">还没有会话记录</div>
        ) : (
          sessions.map((s) => (
            <button
              key={s.id}
              className={`biz-session${view === 'chat' && s.id === activeId ? ' active' : ''}`}
              onClick={() => onSelectSession(s.id)}
              title={s.title}
            >
              <Icon name="message" />
              <span>{s.title}</span>
            </button>
          ))
        )}
      </div>

      <div className="biz-side-label">管理后台</div>
      <div className="biz-admins">
        {ADMIN_ITEMS.map((a) => (
          <button
            key={a.key}
            className={`biz-admin-item${view === a.key ? ' active' : ''}`}
            onClick={() => onNav(a.key)}
          >
            <Icon name={a.icon} />
            <span>{a.label}</span>
          </button>
        ))}
      </div>

      <div className="biz-side-foot">
        <div className="biz-user-wrap" ref={wrapRef}>
          {open && (
            <div className="biz-user-pop">
              {users.map((u) => (
                <button
                  key={u.id}
                  className={u.id === currentUserId ? 'on' : ''}
                  onClick={() => {
                    setOpen(false)
                    if (u.id !== currentUserId) onSwitchUser(u.id)
                  }}
                >
                  <span className="biz-user-ava sm">{shortName(u).charAt(0)}</span>
                  <span className="biz-user-pop-name">{displayName(u)}</span>
                  {u.id === currentUserId ? <Icon name="check" /> : null}
                </button>
              ))}
            </div>
          )}
          <button className="biz-user" onClick={() => setOpen((v) => !v)} title="切换身份">
            <span className="biz-user-ava">{shortName(current).charAt(0)}</span>
            <span className="biz-user-meta">
              <span className="biz-user-name">{shortName(current)}</span>
              <span className="biz-user-role">{current?.scope ?? ''}</span>
            </span>
            <Icon name={open ? 'chevron-down' : 'chevron-up'} className="biz-user-arrow" />
          </button>
        </div>
      </div>
    </aside>
  )
}
