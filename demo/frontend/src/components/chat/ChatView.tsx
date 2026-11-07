import { useCallback, useEffect, useRef, useState } from 'react'
import type { AssistantMsg, QuickviewResponse, SessionInfo } from '../../types'
import { api } from '../../api/client'
import { ApiError } from '../../api/client'
import { streamChat } from '../../api/sse'
import { genId } from '../../utils/format'
import { applyAnswer, applyStage, finalizeMsg, initialStages } from '../../utils/stages'
import { Icon } from '../Icon'
import { EmptyState } from './EmptyState'
import { Composer } from './Composer'
import { StagePipeline } from './StagePipeline'
import { AnswerCardView } from './AnswerCard'
import type { AskFn } from './AnswerCard'

/* ============================================================
 * 用户对话台(§13)
 * ============================================================ */

export interface ChatViewProps {
  session: SessionInfo | null
  sessions: SessionInfo[]
  userId: string
  userDisplayName: string
  dataAsOf: string | null
  updateSessions: (fn: (prev: SessionInfo[]) => SessionInfo[]) => void
  onOpenSession: (id: string) => void
}

export function ChatView({
  session,
  sessions,
  userId,
  userDisplayName,
  dataAsOf,
  updateSessions,
  onOpenSession,
}: ChatViewProps) {
  const [quickview, setQuickview] = useState<QuickviewResponse | null>(null)
  const [qvStatus, setQvStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [qvTick, setQvTick] = useState(0)
  const [sending, setSending] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const sendingRef = useRef(false)
  const activeIdRef = useRef<string | null>(session?.id ?? null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  activeIdRef.current = session?.id ?? null

  /* 空态速览:身份切换后重新拉取(行级权限不同) */
  useEffect(() => {
    let alive = true
    setQvStatus('loading')
    api<QuickviewResponse>('/api/quickview')
      .then((d) => {
        if (!alive) return
        setQuickview(d && typeof d === 'object' ? d : null)
        setQvStatus(d && typeof d === 'object' ? 'ok' : 'error')
      })
      .catch(() => {
        if (alive) setQvStatus('error')
      })
    return () => {
      alive = false
    }
  }, [userId, qvTick])

  /* 离开页面时中断请求 */
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  /* 自动滚底(仅在用户本就停留在底部时) */
  const lastMsg = session?.messages[session.messages.length - 1]
  const stageSignature =
    lastMsg && lastMsg.role === 'assistant'
      ? lastMsg.stages.map((s) => s.status + (s.detail ?? '')).join('|')
      : ''
  const lastStatus = lastMsg && lastMsg.role === 'assistant' ? lastMsg.status : undefined
  useEffect(() => {
    const el = scrollRef.current
    if (!el || !stickRef.current) return
    el.scrollTop = el.scrollHeight
  }, [session?.messages.length, stageSignature, lastStatus])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90
  }

  const updateAssistant = useCallback(
    (sessionId: string, msgId: string, fn: (m: AssistantMsg) => AssistantMsg) => {
      updateSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId
            ? {
                ...s,
                messages: s.messages.map((m) =>
                  m.id === msgId && m.role === 'assistant' ? fn(m) : m,
                ),
              }
            : s,
        ),
      )
    },
    [updateSessions],
  )

  const sendMessage = useCallback<AskFn>(
    async (question, opts) => {
      const text = question.trim()
      if (!text || sendingRef.current) return
      sendingRef.current = true
      setSending(true)

      /* 确保会话存在:首条消息创建会话 */
      let sessionId = activeIdRef.current
      if (!sessionId || !sessions.some((s) => s.id === sessionId)) {
        sessionId = genId()
        const s: SessionInfo = { id: sessionId, title: text.slice(0, 24), ts: Date.now(), messages: [] }
        updateSessions((prev) => [s, ...prev].slice(0, 20))
        onOpenSession(sessionId)
        activeIdRef.current = sessionId
      }
      const sid: string = sessionId

      const ac = new AbortController()
      abortRef.current = ac
      const startedAt = Date.now()

      /* 用户气泡(澄清选项点选时展示选项文案) */
      updateSessions((prev) =>
        prev.map((s) =>
          s.id === sid
            ? { ...s, messages: [...s.messages, { id: genId(), role: 'user' as const, text: opts?.displayText ?? text }] }
            : s,
        ),
      )
      const asstId = genId()
      updateSessions((prev) =>
        prev.map((s) =>
          s.id === sid
            ? {
                ...s,
                messages: [
                  ...s.messages,
                  { id: asstId, role: 'assistant' as const, status: 'pending' as const, stages: initialStages(), question: text },
                ],
              }
            : s,
        ),
      )
      stickRef.current = true

      try {
        await streamChat(
          { question: text, session_id: sid, assume: opts?.assume },
          {
            onStage: (ev) => updateAssistant(sid, asstId, (m) => ({ ...m, stages: applyStage(m.stages, ev) })),
            onAnswer: (card) => updateAssistant(sid, asstId, (m) => applyAnswer(m, card && typeof card === 'object' ? card : undefined)),
            onError: (msg) => updateAssistant(sid, asstId, (m) => ({ ...m, sseError: msg })),
          },
          ac.signal,
        )
        /* 等一拍,让"四步全部完成 + 核验文案"被看到 */
        const wait = Math.max(400, Math.min(1200, 1100 - (Date.now() - startedAt)))
        await new Promise((r) => window.setTimeout(r, wait))
        updateAssistant(sid, asstId, finalizeMsg)
      } catch (e) {
        if (ac.signal.aborted) {
          updateAssistant(sid, asstId, (m) =>
            m.answer ? finalizeMsg(m) : { ...m, status: 'error', errorText: '已停止生成', stopped: true },
          )
        } else {
          const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '网络异常,请稍后重试'
          updateAssistant(sid, asstId, (m) => (m.answer ? finalizeMsg(m) : { ...m, status: 'error', errorText: msg }))
        }
      } finally {
        sendingRef.current = false
        setSending(false)
        abortRef.current = null
      }
    },
    [sessions, updateSessions, updateAssistant, onOpenSession],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const isEmpty = !session || session.messages.length === 0

  return (
    <div className="biz-chatwrap">
      <div className="biz-thread-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="biz-thread">
          {isEmpty ? (
            <EmptyState
              userName={userDisplayName}
              quickview={quickview}
              status={qvStatus}
              onRetryQv={() => setQvTick((t) => t + 1)}
              onAsk={(q) => void sendMessage(q)}
            />
          ) : (
            session?.messages.map((m) => {
              if (m.role === 'user') {
                return (
                  <div className="biz-msg biz-msg-user" key={m.id}>
                    <div className="biz-bubble-user">{m.text}</div>
                  </div>
                )
              }
              return <AssistantBlock key={m.id} msg={m} dataAsOf={dataAsOf} onAsk={sendMessage} />
            })
          )}
        </div>
      </div>
      <Composer sending={sending} onSend={(t) => void sendMessage(t)} onStop={stop} />
    </div>
  )
}

/* ---------- 单条助手消息 ---------- */
function AssistantBlock({
  msg,
  dataAsOf,
  onAsk,
}: {
  msg: AssistantMsg
  dataAsOf: string | null
  onAsk: AskFn
}) {
  if (msg.status === 'pending') {
    return (
      <div className="biz-msg biz-msg-assistant">
        <span className="biz-ava">
          <Icon name="sparkles" />
        </span>
        <div className="biz-bubble">
          <StagePipeline stages={msg.stages} />
        </div>
      </div>
    )
  }
  if (msg.status === 'error') {
    return (
      <div className="biz-msg biz-msg-assistant">
        <span className="biz-ava">
          <Icon name="sparkles" />
        </span>
        <div className="biz-bubble biz-bubble-err">
          <div className="biz-err-line">
            <Icon name="alert-triangle" />
            <span>{msg.errorText ?? '这次没能给出回答'}</span>
          </div>
          {!msg.stopped && msg.question ? (
            <button className="biz-link-btn" onClick={() => onAsk(msg.question ?? '')}>
              重试
            </button>
          ) : null}
        </div>
      </div>
    )
  }
  if (!msg.answer) return null
  return (
    <div className="biz-msg biz-msg-assistant">
      <span className="biz-ava">
        <Icon name="sparkles" />
      </span>
      <div className="biz-bubble-none">
        <AnswerCardView card={msg.answer} dataAsOf={dataAsOf} onAsk={onAsk} question={msg.question} />
      </div>
    </div>
  )
}
