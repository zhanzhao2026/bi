import { useContext, useMemo } from 'react'
import type { ChartSpec } from '../../types'
import { ThemeContext } from '../../utils/theme'
import { getChartTheme, normalizeChart, waterfallOption } from '../../utils/chartOptions'
import { EChart } from './EChart'

/* WaterfallChart:归因瀑布图(透明占位堆叠柱,悬浮显示贡献 pp) */
export function WaterfallChart({ spec, height = 260 }: { spec: ChartSpec; height?: number }) {
  const theme = useContext(ThemeContext)
  const option = useMemo(() => {
    const n = normalizeChart(spec)
    if (n.kind !== 'waterfall') return null
    return waterfallOption(n, getChartTheme())
  }, [spec, theme])

  if (!option) return <div className="biz-chart-empty">暂无可视化数据</div>
  return <EChart option={option} height={height} themeKey={theme} />
}
