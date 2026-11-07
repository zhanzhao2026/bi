import { useEffect, useRef, useState } from 'react'
import { Icon } from '../Icon'

/* ============================================================
 * 输入区:Enter 发送 / Shift+Enter 换行 / 发送中可中断
 * ============================================================ */

export interface ComposerProps {
  sending: boolean
  onSend: (text: string) => void
  onStop: () => void
}

export function Composer({ sending, onSend, onStop }: ComposerProps) {
  const [value, setValue] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)

  /* 自动增高 */
  useEffect(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [value])

  const submit = () => {
    const t = value.trim()
    if (!t || sending) return
    onSend(t)
    setValue('')
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="biz-composer">
      <div className="biz-composer-inner">
        <textarea
          ref={taRef}
          className="biz-input"
          rows={1}
          placeholder="问问经营数据,比如:最近30天退款率为什么上升?"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {sending ? (
          <button className="biz-send" title="停止生成" onClick={onStop}>
            <Icon name="stop" />
          </button>
        ) : (
          <button
            className="biz-send"
            title="发送(Enter)"
            disabled={!value.trim()}
            onClick={submit}
          >
            <Icon name="send" />
          </button>
        )}
      </div>
      <div className="biz-composer-hint">Enter 发送,Shift + Enter 换行</div>
    </div>
  )
}
