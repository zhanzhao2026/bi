import { createContext, useCallback, useContext, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Icon } from './Icon'

/* ============================================================
 * Toast 全局提示
 * ============================================================ */

export type ToastType = 'info' | 'success' | 'error'

interface ToastItem {
  id: number
  msg: string
  type: ToastType
}

type ShowToast = (msg: string, type?: ToastType) => void

const ToastContext = createContext<ShowToast>(() => {})

export function useToast(): ShowToast {
  return useContext(ToastContext)
}

let seq = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef<Set<number>>(new Set())

  const show = useCallback<ShowToast>((msg, type = 'info') => {
    const id = ++seq
    setItems((prev) => [...prev.slice(-3), { id, msg, type }])
    const timer = window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id))
      timers.current.delete(timer)
    }, 3600)
    timers.current.add(timer)
  }, [])

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="biz-toasts">
        {items.map((t) => (
          <div key={t.id} className={`biz-toast biz-toast-${t.type}`}>
            <Icon name={t.type === 'error' ? 'alert-triangle' : t.type === 'success' ? 'check-circle' : 'info'} />
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
