import { useContext, useMemo } from 'react'
import type { ChartSpec } from '../../types'
import { ThemeContext } from '../../utils/theme'
import { getChartTheme, groupOption, normalizeChart } from '../../utils/chartOptions'
import { EChart } from './EChart'

/* GroupChart:分组对比柱图(如新老客两指标) */
export function GroupChart({ spec, height = 240 }: { spec: ChartSpec; height?: number }) {
  const theme = useContext(ThemeContext)
  const option = useMemo(() => {
    const n = normalizeChart(spec)
    if (n.kind !== 'group') return null
    return groupOption(n, getChartTheme())
  }, [spec, theme])

  if (!option) return <div className="biz-chart-empty">暂无可视化数据</div>
  return <EChart option={option} height={height} themeKey={theme} />
}
