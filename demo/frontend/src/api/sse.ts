/* ============================================================
 * SSE 客户端(§6)
 * fetch POST /api/chat + ReadableStream 手写解析:
 * 按 "\n\n" 分帧,帧内解析 event: 与 data: 行
 * ============================================================ */

import { getDemoUser } from './client'
import type { AnswerCard, StageEvent } from '../types'

export interface SseHandlers {
  onStage?: (ev: StageEvent) => void
  onAnswer?: (card: AnswerCard) => void
  onError?: (msg: string) => void
  onDone?: () => void
}

export interface ChatRequestBody {
  question: string
  session_id: string
  assume?: string
}

function extractErrText(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload) return payload
  if (payload && typeof payload === 'object') {
    const o = payload as Record<string, unknown>
    for (const k of ['message', 'detail', 'error', 'text']) {
      const v = o[k]
      if (typeof v === 'string' && v) return v
    }
  }
  return fallback
}

export async function streamChat(
  body: ChatRequestBody,
  handlers: SseHandlers,
  signal: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        'X-Demo-User': getDemoUser(),
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    if (signal.aborted) throw e
    throw new Error('连接不上服务,请确认后端已启动')
  }

  if (!res.ok || !res.body) {
    let msg = `服务暂不可用(${res.status})`
    try {
      const j: unknown = await res.json()
      const detail = (j as { detail?: unknown })?.detail
      if (typeof detail === 'string' && detail) msg = detail
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''

  const handleFrame = (frame: string): void => {
    const lines = frame.split('\n')
    let event = 'message'
    const dataLines: string[] = []
    for (const raw of lines) {
      const line = raw.replace(/\r$/, '')
      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).replace(/^ /, ''))
      }
      /* 其余行(id:/注释/空行)忽略 */
    }
    if (!dataLines.length) return
    const dataText = dataLines.join('\n')
    let payload: unknown = null
    try {
      payload = JSON.parse(dataText)
    } catch {
      payload = dataText
    }
    switch (event) {
      case 'stage':
        handlers.onStage?.(payload as StageEvent)
        break
      case 'answer':
        handlers.onAnswer?.(payload as AnswerCard)
        break
      case 'error':
        handlers.onError?.(extractErrText(payload, '这次处理出了点问题,请稍后重试'))
        break
      case 'done':
        handlers.onDone?.()
        break
      default:
        break
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx = buf.indexOf('\n\n')
      while (idx >= 0) {
        handleFrame(buf.slice(0, idx))
        buf = buf.slice(idx + 2)
        idx = buf.indexOf('\n\n')
      }
    }
    /* 兜底:流结束时残留未成帧的内容 */
    if (buf.trim()) handleFrame(buf)
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* ignore */
    }
  }
}
