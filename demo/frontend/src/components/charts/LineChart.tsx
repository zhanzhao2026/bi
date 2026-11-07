import { useContext, useMemo } from 'react'
import type { ChartSpec } from '../../types'
import { ThemeContext } from '../../utils/theme'
import { getChartTheme, lineOption, normalizeChart } from '../../utils/chartOptions'
import { EChart } from './EChart'

/* LineChart:本期/上一期双系列趋势图 */
export function LineChart({ spec, height = 240 }: { spec: ChartSpec; height?: number }) {
  const theme = useContext(ThemeContext)
  const option = useMemo(() => {
    const n = normalizeChart(spec)
    if (n.kind !== 'line') return null
    return lineOption(n, getChartTheme())
  }, [spec, theme])

  if (!option) return <div className="biz-chart-empty">暂无可视化数据</div>
  return <EChart option={option} height={height} themeKey={theme} />
}
