import { Alert, App, Button, Checkbox, Drawer, Space, Tag, Typography } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { eventScreenshotKey, ExploreShotRail } from './ExploreShotThumb'

export type UiRunFixPlan = {
  summary?: string
  likely_causes?: string[]
  changes?: string[]
  warnings?: string[]
  suggested_script?: string
}

type FixEvent = {
  type?: string
  node?: string
  message?: string
  ts?: string
  data?: { screenshot_key?: string }
}

export type UiRunFixSession = {
  id: string
  run_id: number
  case_id: number | null
  status: string
  plan: UiRunFixPlan | null
  current_script: string
  error_message: string
  patch_message?: string
  rerun_id?: number | null
  explore_session_id?: number | null
  events?: FixEvent[]
}

type UiRunInfo = {
  id: number
  status: string
}

type UiRunStepInfo = {
  id: number
  order: number
  action: string
  status: string
  error_message: string
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function eventLine(ev: FixEvent): string {
  const msg = (ev.message || '').trim()
  if (msg) return msg
  if (ev.type === 'interrupted') return '等待确认写入'
  if (ev.type === 'error') return '探索失败'
  return ''
}

type LogKind = 'shot' | 'snapshot' | 'action' | 'system' | 'error'

function summarizeSnapshot(text: string): string {
  const url = text.match(/url=(\S+)/)?.[1]
  const count = text.match(/等\s*(\d+)\s*行/)?.[1]
  if (url && count) return `${url} · ${count} 个控件`
  if (url) return url
  return '已获取页面结构'
}

function classifyLog(text: string): { kind: LogKind; label: string; body: string } | null {
  const t = text.trim()
  if (!t) return null
  if (/^已截图\b/.test(t) || /已截图 step=/.test(t)) return { kind: 'shot', label: '', body: t }
  if (t.includes('LangGraph：snapshot')) {
    return { kind: 'snapshot', label: 'snapshot', body: summarizeSnapshot(t) }
  }
  if (/操作失败|截图失败|LLM 决策失败|探索失败/.test(t)) {
    return { kind: 'error', label: 'error', body: t.replace(/^LangGraph：/, '') }
  }
  const stripped = t.replace(/^LangGraph：/, '')
  if (/^decide\b/i.test(stripped)) {
    const body = stripped.replace(/^decide\s*/, '')
    const act = body.match(/(?:^用例\s+\S+\s+·\s+)?(fill|click|goto|press|done)\b/i)
    return { kind: 'action', label: (act?.[1] || 'decide').toLowerCase(), body }
  }
  const head = stripped.match(/^(fill|click|goto|press)\b/i)
  if (head) {
    return { kind: 'action', label: head[1].toLowerCase(), body: stripped }
  }
  return { kind: 'system', label: '', body: stripped }
}

export function useUiRunFix(projectId: number, runId: number | null) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState<UiRunFixSession | null>(null)
  const [applyPatch, setApplyPatch] = useState(true)
  const [rerun, setRerun] = useState(true)

  const poll = useQuery({
    queryKey: ['ui-run-fix', projectId, runId, session?.id],
    queryFn: () =>
      apiJson<UiRunFixSession>(`/api/v1/projects/${projectId}/ui-runs/${runId}/ai-fix/${session!.id}`),
    enabled: open && runId != null && Boolean(session?.id) && session?.status === 'exploring',
    refetchInterval: 1500,
  })

  useEffect(() => {
    if (!poll.data) return
    setSession(poll.data)
    const hasScript = Boolean(poll.data.plan?.suggested_script?.trim())
    if (poll.data.status === 'awaiting_confirm') setApplyPatch(hasScript)
    if (poll.data.status === 'error') {
      message.error(poll.data.error_message || '重新探索失败')
    }
  }, [poll.data, message])

  async function startFix() {
    if (runId == null) return
    setBusy(true)
    setSession(null)
    setApplyPatch(true)
    setRerun(true)
    setOpen(true)
    try {
      const next = await apiJson<UiRunFixSession>(
        `/api/v1/projects/${projectId}/ui-runs/${runId}/ai-fix`,
        { method: 'POST', body: '{}' },
      )
      setSession(next)
      const hasScript = Boolean(next.plan?.suggested_script?.trim())
      setApplyPatch(hasScript)
      if (next.status === 'error') {
        message.error(next.error_message || '重新探索失败')
      }
    } catch (e) {
      message.error(errText(e))
      setOpen(false)
    } finally {
      setBusy(false)
    }
  }

  async function confirmFix(
    action: 'confirm' | 'cancel',
    onApplied?: (rerunId: number | null) => void,
  ) {
    if (!session || runId == null) return
    setBusy(true)
    try {
      const next = await apiJson<UiRunFixSession>(
        `/api/v1/projects/${projectId}/ui-runs/${runId}/ai-fix/${session.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            apply_patch: action === 'confirm' ? applyPatch : false,
            rerun: action === 'confirm' ? rerun : false,
          }),
        },
      )
      setSession(next)
      if (action === 'cancel') {
        message.info('已取消')
        setOpen(false)
      } else if (next.status === 'error') {
        message.error(next.error_message || next.patch_message || '写入失败')
      } else if (next.patch_message?.startsWith('已写入')) {
        message.success(next.patch_message)
        void qc.invalidateQueries({ queryKey: ['ui-cases', projectId] })
        void qc.invalidateQueries({ queryKey: ['ui-run', projectId] })
        void qc.invalidateQueries({ queryKey: ['ui-run-steps', projectId] })
        onApplied?.(next.rerun_id ?? null)
        setOpen(false)
      } else if (next.patch_message) {
        message.warning(next.patch_message)
        setOpen(false)
      }
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return {
    open,
    setOpen,
    busy,
    session,
    applyPatch,
    setApplyPatch,
    rerun,
    setRerun,
    startFix,
    confirmFix,
  }
}

export function UiRunFixPanel({
  projectId,
  open,
  busy,
  session,
  applyPatch,
  rerun,
  zIndex,
  onApplyPatchChange,
  onRerunChange,
  onConfirm,
  onClose,
}: {
  projectId: number
  open: boolean
  busy: boolean
  session: UiRunFixSession | null
  applyPatch: boolean
  rerun: boolean
  zIndex?: number
  onApplyPatchChange: (value: boolean) => void
  onRerunChange: (value: boolean) => void
  onConfirm: (action: 'confirm' | 'cancel') => void
  onClose: () => void
}) {
  const exploring = session?.status === 'exploring' || (open && !session && busy)
  const ready = session?.status === 'awaiting_confirm'
  const canConfirm = ready && !busy
  const canCancel = Boolean(session && (exploring || ready) && !busy)
  const suggested = session?.plan?.suggested_script || ''
  const logBoxRef = useRef<HTMLDivElement | null>(null)
  const visibleLogs = useMemo(() => {
    const rows: Array<{ id: string; kind: LogKind; label: string; body: string }> = []
    for (const [i, ev] of (session?.events ?? []).entries()) {
      const text = eventLine(ev)
      const classified = classifyLog(text)
      if (!classified || classified.kind === 'shot') continue
      rows.push({ id: `${ev.ts || i}-${i}`, ...classified })
    }
    return rows
  }, [session?.events])
  const shotKeys = useMemo(() => {
    const keys: string[] = []
    for (const ev of session?.events ?? []) {
      const key = eventScreenshotKey(ev)
      if (key) keys.push(key)
    }
    return keys
  }, [session?.events])

  useEffect(() => {
    const el = logBoxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [visibleLogs.length])

  return (
    <Drawer
      title={exploring ? '正在重新探索' : 'AI 修复建议'}
      open={open}
      onClose={onClose}
      width={ready ? 560 : 720}
      destroyOnHidden
      zIndex={zIndex}
    >
      {exploring ? (
        <Typography.Paragraph type="secondary">
          正在按原环境重新探索页面，完成后才会给出修改建议。
        </Typography.Paragraph>
      ) : null}
      {session?.error_message && session.status === 'error' ? (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={session.error_message} />
      ) : null}
      {(exploring || visibleLogs.length > 0 || shotKeys.length > 0) && (
        <div className="testcase-automate-explore ui-run-fix-explore">
          <div className="testcase-automate-log" ref={logBoxRef}>
            {visibleLogs.length === 0 ? (
              <div className="testcase-automate-log-empty">等待探索日志…</div>
            ) : (
              visibleLogs.map((line) => (
                <div
                  key={line.id}
                  className={`testcase-automate-log-line testcase-automate-log-line--${line.kind}`}
                >
                  {line.label ? <span className="testcase-automate-log-tag">{line.label}</span> : null}
                  <span className="testcase-automate-log-body">{line.body}</span>
                </div>
              ))
            )}
          </div>
          {session?.explore_session_id && shotKeys.length > 0 ? (
            <ExploreShotRail
              projectId={projectId}
              sessionId={session.explore_session_id}
              storageKeys={shotKeys}
            />
          ) : null}
        </div>
      )}
      {ready && session?.plan?.summary ? (
        <Typography.Paragraph>{session.plan.summary}</Typography.Paragraph>
      ) : null}
      {ready && (session?.plan?.likely_causes ?? []).length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong>可能原因</Typography.Text>
          <ul>
            {(session?.plan?.likely_causes ?? []).map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {ready && (session?.plan?.changes ?? []).length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong>拟修改</Typography.Text>
          <ul>
            {(session?.plan?.changes ?? []).map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {ready && (session?.plan?.warnings ?? []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={(session?.plan?.warnings ?? []).join('；')}
        />
      )}
      {ready && session?.current_script ? (
        <>
          <div className="ui-run-fix-script-label">当前脚本</div>
          <pre className="ui-run-fix-script">{session.current_script}</pre>
        </>
      ) : null}
      {ready && suggested ? (
        <>
          <div className="ui-run-fix-script-label">建议脚本</div>
          <pre className="ui-run-fix-script">{suggested}</pre>
        </>
      ) : null}
      <Space wrap style={{ marginTop: 16 }}>
        {ready ? (
          <>
            <Checkbox
              checked={applyPatch}
              disabled={busy || !suggested}
              onChange={(e) => onApplyPatchChange(e.target.checked)}
            >
              确认时写入修补
            </Checkbox>
            <Checkbox
              checked={rerun}
              disabled={busy || !applyPatch}
              onChange={(e) => onRerunChange(e.target.checked)}
            >
              写入后再试跑
            </Checkbox>
            <Button type="primary" disabled={!canConfirm} loading={busy} onClick={() => onConfirm('confirm')}>
              {applyPatch ? (rerun ? '确认写入并试跑' : '确认并写入') : '确认'}
            </Button>
          </>
        ) : null}
        <Button disabled={!canCancel} onClick={() => onConfirm('cancel')}>
          取消
        </Button>
      </Space>
    </Drawer>
  )
}

export function UiTrialRunBanner({
  projectId,
  runId,
  zIndex,
  onRerun,
}: {
  projectId: number
  runId: number
  zIndex?: number
  onRerun?: (rerunId: number) => void
}) {
  const fix = useUiRunFix(projectId, runId)
  const run = useQuery({
    queryKey: ['ui-run', projectId, runId],
    queryFn: () => apiJson<UiRunInfo>(`/api/v1/projects/${projectId}/ui-runs/${runId}`),
    enabled: Number.isFinite(projectId) && Number.isFinite(runId),
    refetchInterval: (q) => {
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 1500 : false
    },
  })
  const steps = useQuery({
    queryKey: ['ui-run-steps', projectId, runId],
    queryFn: () => apiJson<UiRunStepInfo[]>(`/api/v1/projects/${projectId}/ui-runs/${runId}/steps`),
    enabled: Number.isFinite(projectId) && Number.isFinite(runId),
    refetchInterval: () => {
      const st = run.data?.status
      return st === 'pending' || st === 'running' ? 1500 : false
    },
  })
  const failed = useMemo(
    () => (steps.data ?? []).find((s) => s.status === 'failed' || s.status === 'error'),
    [steps.data],
  )
  const status = run.data?.status
  const runLink = (
    <Link to={`/projects/${projectId}/ui-runs/${runId}`}>试跑 #{runId}</Link>
  )

  return (
    <div className="ui-trial-run-banner">
      {status === 'pending' || status === 'running' || !status ? (
        <p>
          {runLink}
          {status === 'running' ? ' 执行中…' : ' 排队中…'}
        </p>
      ) : null}
      {status === 'passed' ? <p>{runLink} 已通过</p> : null}
      {(status === 'failed' || status === 'error') && (
        <Alert
          type="error"
          showIcon
          message={`${status === 'error' ? '试跑出错' : '试跑失败'} · ${runLink}`}
          description={
            <div>
              <Typography.Paragraph style={{ marginBottom: 8 }}>
                {failed?.error_message || '无详细错误信息'}
              </Typography.Paragraph>
              <Button type="primary" size="small" loading={fix.busy} onClick={() => void fix.startFix()}>
                AI 修复
              </Button>
            </div>
          }
        />
      )}
      {status && !['pending', 'running', 'passed', 'failed', 'error'].includes(status) ? (
        <p>
          {runLink}
          {' · '}
          <Tag>{status}</Tag>
        </p>
      ) : null}
      <UiRunFixPanel
        projectId={projectId}
        open={fix.open}
        busy={fix.busy}
        session={fix.session}
        applyPatch={fix.applyPatch}
        rerun={fix.rerun}
        zIndex={zIndex}
        onApplyPatchChange={fix.setApplyPatch}
        onRerunChange={fix.setRerun}
        onConfirm={(action) =>
          void fix.confirmFix(action, (rerunId) => {
            if (rerunId != null) onRerun?.(rerunId)
          })
        }
        onClose={() => fix.setOpen(false)}
      />
    </div>
  )
}
