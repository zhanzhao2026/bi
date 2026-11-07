/* ============================================================
 * REST API 客户端(§11)
 * 所有请求统一携带 X-Demo-User 演示身份头(§12)
 * ============================================================ */

const USER_KEY = 'bi_demo_user'

let demoUserId: string = (() => {
  try {
    return localStorage.getItem(USER_KEY) || 'ceo'
  } catch {
    return 'ceo'
  }
})()

export function getDemoUser(): string {
  return demoUserId
}

export function setDemoUser(id: string): void {
  demoUserId = id
  try {
    localStorage.setItem(USER_KEY, id)
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface ApiOptions {
  method?: string
  body?: unknown
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method: opts.method ?? 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-Demo-User': demoUserId,
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    })
  } catch {
    throw new ApiError(0, '连接不上服务,请确认后端已启动')
  }
  if (!res.ok) {
    let msg = `请求失败(${res.status})`
    try {
      const j: unknown = await res.json()
      const detail = (j as { detail?: unknown })?.detail
      if (typeof detail === 'string' && detail) msg = detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg)
  }
  const text = await res.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    return undefined as T
  }
}
