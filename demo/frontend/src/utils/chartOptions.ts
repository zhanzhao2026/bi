/* ============================================================
 * ECharts 配置构建:答案卡 chart 字段归一化 + 主题色
 * 兼容多种后端数据形状(并行开发,防御式归一化)
 * ============================================================ */

import type { EChartsOption } from 'echarts'
import * as echarts from 'echarts'
import type { ChartSpec } from '../types'
import { fmtWan, parseNumIn, shortLabel, trimNum } from './format'

export interface ChartTheme {
  text: string
  text2: string
  text3: string
  grid: string
  panel: string
  primary: string
  purple: string
  teal: string
  orange: string
  red: string
  green: string
}

/* ---------- 从 CSS 变量解析真实颜色(兼容 light-dark()) ---------- */
let probeEl: HTMLDivElement | null = null

function resolveColor(varName: string, fallback: string): string {
  try {
    const root = document.querySelector('.biz-demo') ?? document.body
    if (!probeEl || !probeEl.isConnected) {
      probeEl = document.createElement('div')
      probeEl.style.display = 'none'
      root.appendChild(probeEl)
    }
    probeEl.style.color = `var(${varName})`
    const c = getComputedStyle(probeEl).color
    if (c && c !== 'transparent' && !c.includes('rgba(0, 0, 0, 0)')) return c
  } catch {
    /* ignore */
  }
  return fallback
}

export function getChartTheme(): ChartTheme {
  return {
    text: resolveColor('--c-text', '#1b1e26'),
    text2: resolveColor('--c-text2', '#5d6572'),
    text3: resolveColor('--c-text3', '#9099a8'),
    grid: resolveColor('--c-grid', '#e9ecf3'),
    panel: resolveColor('--c-panel', '#ffffff'),
    primary: resolveColor('--c-primary', '#4152e0'),
    purple: '#7b4fd3',
    teal: '#0e8fa3',
    orange: '#c2680f',
    red: resolveColor('--c-red', '#d94343'),
    green: resolveColor('--c-green', '#0f9d6c'),
  }
}

/* 颜色 → rgba(透明度渐变用) */
function withAlpha(color: string, alpha: number): string {
  const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
  if (m) return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${alpha})`
  if (color.startsWith('#')) {
    let hex = color.slice(1)
    if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('')
    const n = parseInt(hex, 16)
    if (!isNaN(n)) {
      return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
    }
  }
  return color
}

/* ---------- 归一化 ---------- */
export type NormChart =
  | { kind: 'none' }
  | { kind: 'line'; title?: string; x: string[]; series: { name: string; values: (number | null)[] }[] }
  | { kind: 'bar'; title?: string; x: string[]; name: string; values: number[] }
  | {
      kind: 'waterfall'
      title?: string
      base: { label: string; value: number }
      items: { label: string; value: number }[]
      total: { label: string; value: number }
    }
  | { kind: 'group'; title?: string; x: string[]; series: { name: string; values: number[] }[] }

function asRecords(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v) ? (v.filter((x) => !!x && typeof x === 'object') as Record<string, unknown>[]) : []
}

function numOf(v: unknown): number | null {
  if (typeof v === 'number' && isFinite(v)) return v
  if (typeof v === 'string') {
    const t = v.trim()
    if (!t) return null
    const n = Number(t.replace(/[%,]/g, ''))
    if (isFinite(n)) return n
  }
  return null
}

/** data 数组 → labels + values(容忍 number[] / [label,value][] / 对象数组) */
function valuesFrom(data: unknown): { labels: string[]; values: (number | null)[] } {
  const labels: string[] = []
  const values: (number | null)[] = []
  if (!Array.isArray(data)) return { labels, values }
  for (const it of data) {
    if (typeof it === 'number') {
      labels.push('')
      values.push(it)
    } else if (Array.isArray(it)) {
      labels.push(it.length ? String(it[0]) : '')
      values.push(numOf(it[1]))
    } else if (it && typeof it === 'object') {
      const o = it as Record<string, unknown>
      labels.push(String(o.label ?? o.name ?? o.category ?? o.date ?? o.day ?? o.x ?? ''))
      values.push(numOf(o.value ?? o.count ?? o.cnt ?? o.total ?? o.amount ?? o.num))
    } else {
      labels.push('')
      values.push(null)
    }
  }
  return { labels, values }
}

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : []
}

function padLabels(labels: string[], len: number): string[] {
  const out = labels.slice(0, len)
  while (out.length < len) out.push('')
  return out
}

function seriesList(spec: ChartSpec): { name: string; labels: string[]; values: (number | null)[] }[] {
  const out: { name: string; labels: string[]; values: (number | null)[] }[] = []
  for (const s of asRecords(spec.series)) {
    const r = valuesFrom(s.data ?? s.values)
    out.push({ name: String(s.name ?? s.label ?? '系列'), labels: r.labels, values: r.values })
  }
  return out
}

export function normalizeChart(spec: ChartSpec | null | undefined): NormChart {
  if (!spec || typeof spec !== 'object') return { kind: 'none' }
  const kind = String(spec.kind ?? '')
  const title = typeof spec.title === 'string' ? spec.title : undefined

  if (kind === 'waterfall') {
    const baseObj = (spec.base ?? {}) as Record<string, unknown>
    const totalObj = (spec.total ?? {}) as Record<string, unknown>
    const baseName = String(baseObj.name ?? baseObj.label ?? '上一期')
    const totalName = String(totalObj.name ?? totalObj.label ?? '当前')
    const baseVal = numOf(baseObj.value) ?? parseNumIn(baseName) ?? 0
    const totalVal = numOf(totalObj.value) ?? parseNumIn(totalName) ?? 0
    const items = asRecords(spec.items).map((it) => {
      const name = String(it.name ?? it.label ?? '')
      return { label: shortLabel(name), value: numOf(it.value) ?? parseNumIn(name) ?? 0 }
    })
    if (!items.length && baseVal === 0 && totalVal === 0) return { kind: 'none' }
    return {
      kind: 'waterfall',
      title,
      base: { label: shortLabel(baseName), value: baseVal },
      items,
      total: { label: shortLabel(totalName), value: totalVal },
    }
  }

  if (kind === 'group') {
    const list = seriesList(spec)
    if (!list.length) return { kind: 'none' }
    const explicitX = strArray(spec.x ?? spec.categories)
    const labels = explicitX.length
      ? explicitX
      : list.find((s) => s.labels.some((l) => l !== ''))?.labels ?? []
    const len = Math.max(labels.length, ...list.map((s) => s.values.length))
    const x = padLabels(labels, len).map((l, i) => l || `第${i + 1}项`)
    const series = list.map((s) => ({
      name: s.name,
      values: padLabels(s.labels, s.values.length).length ? s.values.map((v) => v ?? 0) : s.values.map((v) => v ?? 0),
    }))
    if (!x.length || series.every((s) => !s.values.length)) return { kind: 'none' }
    return { kind: 'group', title, x, series }
  }

  if (kind === 'line') {
    const list = seriesList(spec)
    if (!list.length) return { kind: 'none' }
    const explicitX = strArray(spec.x ?? spec.categories)
    const labels = explicitX.length
      ? explicitX
      : list.find((s) => s.labels.some((l) => l !== ''))?.labels ?? []
    const len = Math.max(labels.length, ...list.map((s) => s.values.length))
    const x = padLabels(labels, len).map((l, i) => l || `${i + 1}`)
    return {
      kind: 'line',
      title,
      x,
      series: list.map((s) => ({ name: s.name, values: s.values })),
    }
  }

  if (kind === 'bar' || (!kind && (spec.data || asRecords(spec.series).length))) {
    let labels: string[] = []
    let values: (number | null)[] = []
    let name = ''
    const list = seriesList(spec)
    if (list.length) {
      labels = list[0].labels
      values = list[0].values
      name = list[0].name
    } else {
      const r = valuesFrom(spec.data)
      labels = r.labels
      values = r.values
    }
    if (!values.length) return { kind: 'none' }
    const x = padLabels(labels, values.length).map((l, i) => l || `第${i + 1}项`)
    return {
      kind: 'bar',
      title,
      x,
      name: name || '数值',
      values: values.map((v) => (v === null ? 0 : v)),
    }
  }

  return { kind: 'none' }
}

/* ---------- ECharts option 构建 ---------- */

function baseTooltip(t: ChartTheme): Record<string, unknown> {
  return {
    backgroundColor: t.panel,
    borderColor: t.grid,
    textStyle: { color: t.text, fontSize: 12 },
    extraCssText: 'box-shadow: 0 6px 20px rgba(20,26,45,.12); border-radius: 10px;',
  }
}

export function lineOption(n: Extract<NormChart, { kind: 'line' }>, t: ChartTheme): EChartsOption {
  const colors = [t.primary, t.purple, t.teal]
  return {
    animationDuration: 400,
    color: colors,
    grid: { left: 6, right: 16, top: 32, bottom: 2, containLabel: true },
    legend: {
      top: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 14,
      itemHeight: 4,
      itemGap: 14,
      textStyle: { color: t.text2, fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      ...baseTooltip(t),
      valueFormatter: (v: unknown) => (typeof v === 'number' ? trimNum(v) : '-'),
    },
    xAxis: {
      type: 'category',
      data: n.x,
      boundaryGap: false,
      axisLine: { lineStyle: { color: t.grid } },
      axisTick: { show: false },
      axisLabel: { color: t.text3, fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: t.text3, fontSize: 11, formatter: (v: number) => fmtWan(v) },
      splitLine: { lineStyle: { color: t.grid, type: 'dashed' } },
    },
    series: n.series.map((s, i) => ({
      name: s.name,
      type: 'line' as const,
      data: s.values,
      smooth: 0.3,
      showSymbol: false,
      connectNulls: true,
      lineStyle: { width: i === 0 ? 2.4 : 2, type: i === 0 ? ('solid' as const) : ('dashed' as const) },
      areaStyle:
        i === 0
          ? {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: withAlpha(colors[0], 0.14) },
                { offset: 1, color: withAlpha(colors[0], 0) },
              ]),
            }
          : undefined,
    })),
  }
}

export function barOption(n: Extract<NormChart, { kind: 'bar' }>, t: ChartTheme): EChartsOption {
  const rotate = n.x.length > 6 && n.x.some((l) => l.length > 4) ? 28 : 0
  const showLabel = n.x.length <= 12
  return {
    animationDuration: 400,
    grid: { left: 6, right: 14, top: showLabel ? 26 : 14, bottom: 2, containLabel: true },
    tooltip: {
      trigger: 'axis',
      ...baseTooltip(t),
      axisPointer: { type: 'shadow', shadowStyle: { color: withAlpha(t.primary, 0.06) } },
      valueFormatter: (v: unknown) => (typeof v === 'number' ? trimNum(v) : '-'),
    },
    xAxis: {
      type: 'category',
      data: n.x,
      axisLine: { lineStyle: { color: t.grid } },
      axisTick: { show: false },
      axisLabel: { color: t.text3, fontSize: 11, interval: 0, hideOverlap: true, rotate },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: t.text3, fontSize: 11, formatter: (v: number) => fmtWan(v) },
      splitLine: { lineStyle: { color: t.grid, type: 'dashed' } },
    },
    series: [
      {
        name: n.name,
        type: 'bar' as const,
        data: n.values,
        barMaxWidth: 34,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: t.primary },
            { offset: 1, color: withAlpha(t.primary, 0.62) },
          ]),
          borderRadius: [6, 6, 0, 0],
        },
        label: showLabel
          ? { show: true, position: 'top' as const, color: t.text2, fontSize: 11, formatter: (p: { value?: unknown }) => fmtWan(p.value) }
          : undefined,
      },
    ],
  }
}

export function waterfallOption(
  n: Extract<NormChart, { kind: 'waterfall' }>,
  t: ChartTheme,
): EChartsOption {
  const cats = [n.base.label, ...n.items.map((i) => i.label), n.total.label]
  const baseColor = withAlpha(t.primary, 0.45)
  const totalColor = t.primary
  const visible: number[] = [n.base.value]
  const offsets: number[] = [0]
  let run = n.base.value
  for (const it of n.items) {
    const next = run + it.value
    offsets.push(Math.min(run, next))
    visible.push(Math.abs(it.value))
    run = next
  }
  offsets.push(0)
  visible.push(n.total.value)

  const itemData = n.items.map((it, i) => ({
    value: Math.abs(it.value),
    itemStyle: { color: it.value >= 0 ? t.red : t.green },
    label: {
      show: true,
      position: (it.value >= 0 ? 'top' : 'bottom') as 'top' | 'bottom',
      color: it.value >= 0 ? t.red : t.green,
      fontSize: 11,
      formatter: `${it.value >= 0 ? '+' : '-'}${trimNum(Math.abs(it.value))}`,
    },
    _idx: i + 1,
  }))

  const tooltipFmt = (p: { dataIndex?: number }): string => {
    const i = typeof p.dataIndex === 'number' ? p.dataIndex : 0
    if (i === 0) return `${esc(n.base.label)}<br/><b>${trimNum(n.base.value)}</b>`
    if (i === n.items.length + 1) return `${esc(n.total.label)}<br/><b>${trimNum(n.total.value)}</b>`
    const it = n.items[i - 1]
    if (!it) return '-'
    return `${esc(it.label)}<br/>贡献 <b>${it.value >= 0 ? '+' : '-'}${trimNum(Math.abs(it.value))} pp</b>`
  }

  return {
    animationDuration: 400,
    grid: { left: 6, right: 14, top: 14, bottom: 2, containLabel: true },
    tooltip: {
      trigger: 'item',
      ...baseTooltip(t),
      formatter: tooltipFmt as unknown as string | ((params: unknown) => string),
    },
    xAxis: {
      type: 'category',
      data: cats,
      axisLine: { lineStyle: { color: t.grid } },
      axisTick: { show: false },
      axisLabel: { color: t.text3, fontSize: 11, interval: 0, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      min: 0,
      axisLabel: { color: t.text3, fontSize: 11, formatter: (v: number) => fmtWan(v) },
      splitLine: { lineStyle: { color: t.grid, type: 'dashed' } },
    },
    series: [
      {
        name: 'base',
        type: 'bar',
        stack: 'wf',
        silent: true,
        itemStyle: { color: 'transparent' },
        emphasis: { disabled: true },
        data: offsets,
        barMaxWidth: 42,
      },
      {
        name: 'value',
        type: 'bar',
        stack: 'wf',
        barMaxWidth: 42,
        data: [
          { value: n.base.value, itemStyle: { color: baseColor, borderRadius: [6, 6, 0, 0] } },
          ...itemData,
          { value: n.total.value, itemStyle: { color: totalColor, borderRadius: [6, 6, 0, 0] } },
        ],
      },
    ],
  }
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function groupOption(n: Extract<NormChart, { kind: 'group' }>, t: ChartTheme): EChartsOption {
  const colors = [t.primary, t.purple, t.teal, t.orange]
  const rotate = n.x.length > 6 && n.x.some((l) => l.length > 4) ? 28 : 0
  return {
    animationDuration: 400,
    color: colors,
    grid: { left: 6, right: 14, top: 32, bottom: 2, containLabel: true },
    legend: {
      top: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 14,
      itemHeight: 4,
      itemGap: 14,
      textStyle: { color: t.text2, fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      ...baseTooltip(t),
      axisPointer: { type: 'shadow', shadowStyle: { color: withAlpha(t.primary, 0.06) } },
      valueFormatter: (v: unknown) => (typeof v === 'number' ? trimNum(v) : '-'),
    },
    xAxis: {
      type: 'category',
      data: n.x,
      axisLine: { lineStyle: { color: t.grid } },
      axisTick: { show: false },
      axisLabel: { color: t.text3, fontSize: 11, interval: 0, hideOverlap: true, rotate },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: t.text3, fontSize: 11, formatter: (v: number) => fmtWan(v) },
      splitLine: { lineStyle: { color: t.grid, type: 'dashed' } },
    },
    series: n.series.map((s, i) => ({
      name: s.name,
      type: 'bar' as const,
      data: s.values,
      barMaxWidth: 26,
      itemStyle: { color: colors[i % colors.length], borderRadius: [5, 5, 0, 0] },
    })),
  }
}
