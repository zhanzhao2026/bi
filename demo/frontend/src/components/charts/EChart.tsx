import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

/* ============================================================
 * ECharts 基础封装:初始化 / 自适应 / 卸载销毁
 * ============================================================ */

export interface EChartProps {
  option: echarts.EChartsOption
  height?: number
  /** 主题标识变化时强制重建配色 */
  themeKey?: string
}

export function EChart({ option, height = 240, themeKey }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const chart = echarts.init(el)
    chartRef.current = chart
    const ro = new ResizeObserver(() => {
      chart.resize()
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, true)
  }, [option, themeKey])

  return <div ref={ref} style={{ width: '100%', height }} />
}
