import { useContext, useMemo } from 'react'
import type { ChartSpec } from '../../types'
import { ThemeContext } from '../../utils/theme'
import { barOption, getChartTheme, normalizeChart } from '../../utils/chartOptions'
import { EChart } from './EChart'

/* BarChart:排名/对比条形图 */
export function BarChart({ spec, height = 240 }: { spec: ChartSpec; height?: number }) {
  const theme = useContext(ThemeContext)
  const option = useMemo(() => {
    const n = normalizeChart(spec)
    if (n.kind !== 'bar') return null
    return barOption(n, getChartTheme())
  }, [spec, theme])

  if (!option) return <div className="biz-chart-empty">暂无可视化数据</div>
  return <EChart option={option} height={height} themeKey={theme} />
}
