import { Icon } from './Icon'
import type { ViewKey } from '../App'

/* ============================================================
 * 顶栏:视图标题 + 数据截至 chip + 主题切换
 * ============================================================ */

const ADMIN_TITLES: Record<string, string> = {
  overview: '运营总览',
  metrics: '语义管理',
  audit: '审计日志',
  eval: '评测',
  settings: '系统设置',
}

export interface TopBarProps {
  view: ViewKey
  dataAsOf: string | null
  serverOk: boolean | null
  theme: 'light' | 'dark'
  onToggleTheme: () => void
}

export function TopBar({ view, dataAsOf, serverOk, theme, onToggleTheme }: TopBarProps) {
  const title = view === 'chat' ? '经营分析' : (ADMIN_TITLES[view] ?? '管理后台')
  const chipWarn = serverOk === false
  return (
    <div className="biz-top">
      <div className="biz-top-title">{title}</div>
      <div className="biz-top-right">
        <span className={`biz-chip${chipWarn ? ' biz-chip-warn' : ''}`}>
          <Icon name="database" />
          {chipWarn ? '服务未连接' : dataAsOf ? `数据截至 ${dataAsOf}` : '连接中…'}
        </span>
        <button
          className="biz-icon-btn"
          onClick={onToggleTheme}
          title={theme === 'dark' ? '切换到浅色' : '切换到深色'}
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
        </button>
      </div>
    </div>
  )
}
