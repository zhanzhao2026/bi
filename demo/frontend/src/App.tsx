import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DemoUser, HealthResponse, MeResponse, SessionInfo } from './types'
import { api, getDemoUser, setDemoUser } from './api/client'
import { loadSessions, saveSessions } from './utils/storage'
import { displayName } from './components/Sidebar'
import { ThemeContext } from './utils/theme'
import type { ThemeName } from './utils/theme'
import { ToastProvider, useToast } from './components/Toast'
import { Sidebar } from './components/Sidebar'
import { TopBar } from './components/TopBar'
import { ChatView } from './components/chat/ChatView'
import { OverviewPage } from './components/admin/OverviewPage'
import { MetricsPage } from './components/admin/MetricsPage'
import { AuditPage } from './components/admin/AuditPage'
import { EvalPage } from './components/admin/EvalPage'
import { SettingsPage } from './components/admin/SettingsPage'

export type ViewKey = 'chat' | 'overview' | 'metrics' | 'audit' | 'eval' | 'settings'

/* 后端不可用时的兜底身份列表(§12) */
const FALLBACK_USERS: DemoUser[] = [
  { id: 'ceo', name: '张总', scope: '总经理' },
  { id: 'beauty', name: '林岚', scope: '美妆负责人' },
]

function AppInner() {
  const toast = useToast()
  const [me, setMe] = useState<MeResponse | null>(null)
  const [userId, setUserId] = useState<string>(() => getDemoUser())
  const [view, setView] = useState<ViewKey>('chat')
  const [dataAsOf, setDataAsOf] = useState<string | null>(null)
  const [serverOk, setServerOk] = useState<boolean | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>(loadSessions)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [theme, setTheme] = useState<ThemeName>(() => {
    try {
      const saved = localStorage.getItem('bi_theme')
      if (saved === 'light' || saved === 'dark') return saved
    } catch {
      /* ignore */
    }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.style.colorScheme = theme
    try {
      localStorage.setItem('bi_theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  /* 启动探活 + 身份列表 */
  useEffect(() => {
    let alive = true
    api<HealthResponse>('/api/health')
      .then((h) => {
        if (!alive) return
        setServerOk(true)
        if (h?.data_as_of) setDataAsOf(h.data_as_of)
      })
      .catch(() => {
        if (alive) setServerOk(false)
      })
    api<MeResponse>('/api/me')
      .then((m) => {
        if (alive && m && typeof m === 'object') setMe(m)
      })
      .catch(() => {
        if (alive) setMe(null)
      })
    return () => {
      alive = false
    }
  }, [])

  /* 会话本地持久化 */
  useEffect(() => {
    saveSessions(sessions)
  }, [sessions])

  const users: DemoUser[] = useMemo(() => {
    const list = Array.isArray(me?.users) && me.users.length ? me.users : FALLBACK_USERS
    return list
  }, [me])

  const updateSessions = useCallback((fn: (prev: SessionInfo[]) => SessionInfo[]) => {
    setSessions((prev) => fn(prev))
  }, [])

  const openSession = useCallback((id: string) => {
    setActiveId(id)
    setView('chat')
  }, [])

  const newChat = useCallback(() => {
    setActiveId(null)
    setView('chat')
  }, [])

  const switchUser = useCallback(
    (id: string) => {
      setUserId(id)
      setDemoUser(id)
      const u = users.find((x) => x.id === id)
      toast(`已切换为 ${displayName(u)}`, 'success')
    },
    [users, toast],
  )

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  }, [])

  const activeSession = sessions.find((s) => s.id === activeId) ?? null
  const currentUser = users.find((u) => u.id === userId) ?? users[0]

  return (
    <ThemeContext.Provider value={theme}>
      <div className="biz-shell">
        <Sidebar
          view={view}
          sessions={sessions}
          activeId={activeId}
          users={users}
          currentUserId={currentUser?.id ?? 'ceo'}
          onNewChat={newChat}
          onSelectSession={openSession}
          onNav={setView}
          onSwitchUser={switchUser}
        />
        <main className="biz-main">
          <TopBar
            view={view}
            dataAsOf={dataAsOf}
            serverOk={serverOk}
            theme={theme}
            onToggleTheme={toggleTheme}
          />
          {view === 'chat' ? (
            <ChatView
              session={activeSession}
              sessions={sessions}
              userId={currentUser?.id ?? 'ceo'}
              userDisplayName={currentUser?.name ? displayName(currentUser).split('·')[0].trim() : '朋友'}
              dataAsOf={dataAsOf}
              updateSessions={updateSessions}
              onOpenSession={openSession}
            />
          ) : view === 'overview' ? (
            <OverviewPage />
          ) : view === 'metrics' ? (
            <MetricsPage />
          ) : view === 'audit' ? (
            <AuditPage />
          ) : view === 'eval' ? (
            <EvalPage />
          ) : (
            <SettingsPage />
          )}
        </main>
      </div>
    </ThemeContext.Provider>
  )
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  )
}
