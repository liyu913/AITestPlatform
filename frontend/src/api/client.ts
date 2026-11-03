const base = import.meta.env.VITE_API_BASE ?? ''

/** AuthContext 监听此事件以同步清空内存 token，避免登录页与 /projects 死循环。 */
export const AUTH_EXPIRED_EVENT = 'raina:auth-expired'

let handling401 = false

function handleUnauthorized(): void {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  if (handling401) return
  handling401 = true
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    super(`HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = `${base}${path}`
  const headers = new Headers(init.headers)
  const token = localStorage.getItem('access_token')
  if (token) {
    handling401 = false
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(url, { ...init, headers })
  if (res.status === 401 && !path.includes('/auth/login')) {
    handleUnauthorized()
  }
  return res
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init)
  if (res.status === 204) {
    return undefined as T
  }
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text) as unknown
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, data)
  }
  return data as T
}

export type SseEvent = { type: string; [key: string]: unknown }

export async function apiSse(
  path: string,
  init: RequestInit,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  const res = await apiFetch(path, init)
  if (!res.ok) {
    const text = await res.text()
    let data: unknown = null
    if (text) {
      try {
        data = JSON.parse(text) as unknown
      } catch {
        data = text
      }
    }
    throw new ApiError(res.status, data)
  }
  if (!res.body) {
    throw new ApiError(500, { message: '流式响应为空' })
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let splitAt = buffer.indexOf('\n\n')
    while (splitAt >= 0) {
      const block = buffer.slice(0, splitAt)
      buffer = buffer.slice(splitAt + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw) continue
        const event = JSON.parse(raw) as SseEvent
        onEvent(event)
        if (event.type === 'error') {
          throw new ApiError(422, { message: String(event.message ?? '生成失败') })
        }
      }
      splitAt = buffer.indexOf('\n\n')
    }
  }
}
