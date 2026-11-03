import { Image } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../api/client'

export function ExploreShotThumb({
  projectId,
  sessionId,
  storageKey,
  width = 220,
}: {
  projectId: number
  sessionId: number
  storageKey: string
  width?: number
}) {
  const q = useQuery({
    queryKey: ['ui-explore-shot', projectId, sessionId, storageKey],
    queryFn: async () => {
      const res = await apiFetch(
        `/api/v1/projects/${projectId}/ui-scenario-agent/sessions/${sessionId}/screenshots?key=${encodeURIComponent(storageKey)}`,
      )
      if (!res.ok) throw new Error('截图加载失败')
      const blob = await res.blob()
      return URL.createObjectURL(blob)
    },
    enabled: Boolean(storageKey),
  })
  if (q.isLoading) return <span className="explore-shot-loading">加载截图…</span>
  if (!q.data) return null
  return (
    <div className="explore-shot-thumb">
      <Image src={q.data} width={width} alt="explore step" />
    </div>
  )
}

export function ExploreShotRail({
  projectId,
  sessionId,
  storageKeys,
  width = 200,
}: {
  projectId: number
  sessionId: number
  storageKeys: string[]
  width?: number
}) {
  const keys: string[] = []
  const seen = new Set<string>()
  for (const key of storageKeys) {
    if (!key || seen.has(key)) continue
    seen.add(key)
    keys.push(key)
  }
  if (!keys.length) return null
  return (
    <aside className="explore-shot-rail">
      {keys.map((key) => (
        <ExploreShotThumb
          key={key}
          projectId={projectId}
          sessionId={sessionId}
          storageKey={key}
          width={width}
        />
      ))}
    </aside>
  )
}

export function CaseRunShotThumb({
  projectId,
  runId,
  storageKey,
  width = 220,
}: {
  projectId: number
  runId: number
  storageKey: string
  width?: number
}) {
  const q = useQuery({
    queryKey: ['case-run-shot', projectId, runId, storageKey],
    queryFn: async () => {
      const res = await apiFetch(
        `/api/v1/projects/${projectId}/test-case-runs/${runId}/screenshots?key=${encodeURIComponent(storageKey)}`,
      )
      if (!res.ok) throw new Error('截图加载失败')
      const blob = await res.blob()
      return URL.createObjectURL(blob)
    },
    enabled: Boolean(storageKey),
  })
  if (q.isLoading) return <span className="explore-shot-loading">加载截图…</span>
  if (!q.data) return null
  return (
    <div className="explore-shot-thumb">
      <Image src={q.data} width={width} alt="case run step" />
    </div>
  )
}

export function CaseRunShotRail({
  projectId,
  runId,
  storageKeys,
  width = 200,
}: {
  projectId: number
  runId: number
  storageKeys: string[]
  width?: number
}) {
  const keys: string[] = []
  const seen = new Set<string>()
  for (const key of storageKeys) {
    if (!key || seen.has(key)) continue
    seen.add(key)
    keys.push(key)
  }
  if (!keys.length) return null
  return (
    <aside className="explore-shot-rail">
      {keys.map((key) => (
        <CaseRunShotThumb
          key={key}
          projectId={projectId}
          runId={runId}
          storageKey={key}
          width={width}
        />
      ))}
    </aside>
  )
}

export function eventScreenshotKey(ev: Record<string, unknown>): string | undefined {
  const data = ev.data
  if (data && typeof data === 'object' && data !== null && 'screenshot_key' in data) {
    const key = (data as { screenshot_key?: unknown }).screenshot_key
    if (typeof key === 'string' && key) return key
  }
  return undefined
}
