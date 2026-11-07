import type { AssistantMsg, StageEvent, StageStepState } from '../types'
import { countAnswerNumbers } from './format'

/* ============================================================
 * SSE stage 事件 → 用户视角四步动画映射(§6 / §13)
 * 理解问题 → 查询数据 → 交叉核验 → 生成分析
 * ============================================================ */

const UI_STEPS: { key: string; label: string; matches: string[] }[] = [
  { key: 'understand', label: '理解问题', matches: ['guard', 'understand', 'intent', 'plan'] },
  { key: 'query', label: '查询数据', matches: ['compile', 'execute', 'query', 'sql'] },
  { key: 'verify', label: '交叉核验', matches: ['verify', 'check'] },
  { key: 'narrate', label: '生成分析', matches: ['narrate', 'narration', 'answer'] },
]

export function uiStepIndex(step?: string): number {
  const s = (step ?? '').toLowerCase()
  const idx = UI_STEPS.findIndex((u) => u.matches.some((m) => s === m || s.includes(m)))
  return idx >= 0 ? idx : 0
}

export function initialStages(): StageStepState[] {
  return UI_STEPS.map((s, i) => ({
    key: s.key,
    label: s.label,
    status: i === 0 ? 'running' : 'idle',
    detail: undefined,
  }))
}

export function applyStage(stages: StageStepState[], ev: StageEvent): StageStepState[] {
  const idx = uiStepIndex(ev.step)
  const isDone = ev.status === 'done'
  return stages.map((st, i) => {
    if (i < idx && st.status !== 'done') return { ...st, status: 'done' }
    if (i === idx) {
      const detail = ev.detail || (ev.label && ev.label !== st.label ? ev.label : undefined)
      if (isDone) return { ...st, status: 'done', detail: detail ?? st.detail }
      return { ...st, status: 'running', detail: detail ?? st.detail }
    }
    return st
  })
}

/** 收到答案卡:四步全部完成,核验步骤补齐文案 */
export function applyAnswer(msg: AssistantMsg, card: AssistantMsg['answer']): AssistantMsg {
  const n = card ? countAnswerNumbers(card) : 0
  const stages = msg.stages.map((st) => ({
    ...st,
    status: 'done' as const,
    detail:
      st.key === 'verify' && !st.detail && n > 0
        ? `共 ${n} 项数字与数据仓库一致`
        : st.detail,
  }))
  return { ...msg, answer: card, stages }
}

export function finalizeMsg(msg: AssistantMsg): AssistantMsg {
  if (msg.answer) return { ...msg, status: 'done' }
  if (msg.sseError) return { ...msg, status: 'error', errorText: msg.sseError }
  return { ...msg, status: 'error', errorText: '这次没有等到回答,请稍后重试' }
}
