import { App, Button, Form, Input, Modal, Segmented, Select, Space, Switch, Tag } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { eventScreenshotKey, ExploreShotRail } from '../components/ExploreShotThumb'
import { VariablesTags } from '../components/TestDataVariables'

type Step = { order: number; action: string; expected: string }

export type AutomateTestCase = {
  id: number
  title: string
  precondition: string | null
  test_data: string | null
  variables?: Record<string, string>
  overall_expected: string
  steps: Step[]
}

type UiEnv = {
  id: number
  name: string
  is_default: boolean
  base_url: string
  browser?: string
  browsers?: string[]
}

type PageRes<T> = { items: T[]; total: number }

type SessionEvent = {
  type?: string
  node?: string
  message?: string
  ts?: string
  summary?: string
  case_id?: string
  status?: string
  error?: string
  headed?: boolean
  fixed_count?: number
  data?: { screenshot_key?: string }
}

type LogLine = {
  id: string
  text: string
  screenshotKey?: string
}

type ExecutionMode = 'explore_codegen' | 'agent_once'

type AgentSession = {
  id: number
  status: string
  playwright_code: string
  dry_run_status: string | null
  dry_run_error: string
  error_message: string
  saved_ui_case_id: number | null
  run_id: number | null
  case_run_id?: number | null
  execution_mode?: ExecutionMode | string
  events: SessionEvent[]
  plan: {
    case_scripts?: Array<{ id?: string; name?: string; ready?: boolean; code?: string }>
    saved_ui_case_ids?: number[]
    _case_run_status?: string
    _execution_mode?: string
  } | null
}

type Props = {
  open: boolean
  projectId: number
  testCase: AutomateTestCase | null
  onClose: () => void
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function eventLine(ev: SessionEvent): string {
  const msg = (ev.message || '').trim()
  if (msg) return msg
  if (ev.type === 'script_review') {
    return ev.summary?.trim() || (ev.fixed_count ? `脚本自审：已修正 ${ev.fixed_count} 条` : '脚本自审完成')
  }
  if (ev.type === 'trial_start') {
    return `开始试跑 ${ev.case_id || ''}${ev.headed ? '（有头）' : '（无头）'}`
  }
  if (ev.type === 'trial_done') {
    return ev.error
      ? `试跑 ${ev.case_id || ''} ${ev.status || ''}：${ev.error}`
      : `试跑 ${ev.case_id || ''} ${ev.status || ''}`
  }
  if (ev.type === 'interrupted') return '等待确认写入 UI 用例'
  if (ev.type === 'error') return '探索失败'
  return ''
}

type LogKind = 'shot' | 'snapshot' | 'action' | 'system' | 'error'

type DisplayLog = {
  kind: LogKind
  label: string
  body: string
}

function summarizeSnapshot(text: string): string {
  const url = text.match(/url=(\S+)/)?.[1]
  const count = text.match(/等\s*(\d+)\s*行/)?.[1]
  if (url && count) return `${url} · ${count} 个控件`
  if (url) return url
  return '已获取页面结构'
}

function classifyLog(text: string): DisplayLog | null {
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
  const cased = stripped.match(/^用例\s+\S+\s+·\s+(fill|click|goto|press)\b/i)
  if (cased) {
    return { kind: 'action', label: cased[1].toLowerCase(), body: stripped }
  }
  return { kind: 'system', label: '', body: stripped }
}

export default function TestCaseAutomateModal({ open, projectId, testCase, onClose }: Props) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [envId, setEnvId] = useState<number | undefined>()
  const [authMode, setAuthMode] = useState<'follow' | 'none'>('follow')
  const [prompt, setPrompt] = useState('')
  const [executionMode, setExecutionMode] = useState<ExecutionMode>('explore_codegen')
  const [headed, setHeaded] = useState(true)
  const [trialHeaded, setTrialHeaded] = useState(true)
  const [trialBusy, setTrialBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [session, setSession] = useState<AgentSession | null>(null)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [scriptOpen, setScriptOpen] = useState(false)
  const seenKeys = useRef<Set<string>>(new Set())
  const pollRef = useRef<number | null>(null)
  const logBoxRef = useRef<HTMLDivElement | null>(null)

  const envs = useQuery({
    queryKey: ['ui-envs', projectId],
    queryFn: () =>
      apiJson<PageRes<UiEnv>>(`/api/v1/projects/${projectId}/ui-envs/?page=1&page_size=100`),
    enabled: open && Number.isFinite(projectId),
  })

  const envItems = envs.data?.items ?? []
  const selectedEnv =
    envItems.find((e) => e.id === envId) ??
    envItems.find((e) => e.is_default) ??
    envItems[0]

  useEffect(() => {
    if (!open) return
    setPrompt('')
    setExecutionMode('explore_codegen')
    setHeaded(true)
    setTrialHeaded(true)
    setTrialBusy(false)
    setBusy(false)
    setStopping(false)
    setSession(null)
    setLogs([])
    setScriptOpen(false)
    setEnvId(undefined)
    setAuthMode('follow')
    seenKeys.current = new Set()
    stopPoll()
  }, [open, testCase?.id])

  useEffect(() => {
    if (!open || envId != null) return
    if (selectedEnv) setEnvId(selectedEnv.id)
  }, [open, envId, selectedEnv])

  useEffect(() => {
    return () => stopPoll()
  }, [])

  const sortedSteps = useMemo(
    () => [...(testCase?.steps ?? [])].sort((a, b) => a.order - b.order),
    [testCase],
  )

  const envOptions = envItems.map((e) => ({
    value: e.id,
    label: e.is_default ? `${e.name}（默认）` : e.name,
  }))

  const phase =
    session == null
      ? 'config'
      : ['running', 'awaiting_human'].includes(session.status)
        ? 'running'
        : 'result'

  function stopPoll() {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  function ingestEvents(events: SessionEvent[]) {
    events.forEach((ev, i) => {
      const line = eventLine(ev)
      if (!line) return
      const key = `${ev.ts || ''}|${ev.type || ''}|${ev.node || ''}|${line}|${i}`
      if (seenKeys.current.has(key)) return
      seenKeys.current.add(key)
      const shot = eventScreenshotKey(ev as Record<string, unknown>)
      setLogs((prev) => [
        ...prev,
        { id: key, text: line, screenshotKey: shot },
      ])
    })
  }

  async function pollUntilSettled(sessionId: number) {
    stopPoll()
    return new Promise<AgentSession>((resolve, reject) => {
      const tick = async () => {
        try {
          const refreshed = await apiJson<AgentSession>(
            `/api/v1/projects/${projectId}/ui-scenario-agent/sessions/${sessionId}`,
          )
          setSession(refreshed)
          ingestEvents(refreshed.events || [])
          if (!['running', 'awaiting_human'].includes(refreshed.status)) {
            stopPoll()
            resolve(refreshed)
          }
        } catch (e) {
          stopPoll()
          reject(e)
        }
      }
      void tick()
      pollRef.current = window.setInterval(() => void tick(), 1500)
    })
  }

  async function startRun() {
    if (!testCase || !selectedEnv) return
    setBusy(true)
    setLogs([])
    seenKeys.current = new Set()
    try {
      const created = await apiJson<AgentSession>(
        `/api/v1/projects/${projectId}/ui-scenario-agent/sessions`,
        {
          method: 'POST',
          body: JSON.stringify({
            prompt: prompt.trim(),
            testcase_id: testCase.id,
            env_id: selectedEnv.id,
            browser: selectedEnv.browser || selectedEnv.browsers?.[0] || 'chromium',
            headed,
            engine: 'midscene',
            max_steps: 25,
            auth_mode: authMode === 'none' ? 'none' : null,
            execution_mode: executionMode,
          }),
        },
      )
      setSession(created)
      ingestEvents(created.events || [])
      const settled = await pollUntilSettled(created.id)
      if ((settled.execution_mode || executionMode) === 'agent_once') {
        qc.invalidateQueries({ queryKey: ['test-cases', projectId] })
        qc.invalidateQueries({ queryKey: ['test-case-runs', projectId] })
      }
    } catch (e) {
      message.error(errText(e))
      setLogs((prev) => [...prev, { id: `err-${Date.now()}`, text: errText(e) }])
    } finally {
      setBusy(false)
      setStopping(false)
    }
  }

  async function stopRun() {
    if (!session || !['running', 'awaiting_human'].includes(session.status)) return
    setStopping(true)
    try {
      const updated = await apiJson<AgentSession>(
        `/api/v1/projects/${projectId}/ui-scenario-agent/sessions/${session.id}/stop`,
        { method: 'POST' },
      )
      setSession(updated)
      ingestEvents(updated.events || [])
      message.info(updated.error_message || '已停止执行')
      // pollUntilSettled in startRun will resolve on next tick
    } catch (e) {
      message.error(errText(e))
      setStopping(false)
    }
  }

  async function saveCases() {
    if (!session) return
    setBusy(true)
    try {
      const result = await apiJson<AgentSession>(
        `/api/v1/projects/${projectId}/ui-scenario-agent/sessions/${session.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({
            action: 'confirm',
            status: 'ready',
          }),
        },
      )
      setSession(result)
      ingestEvents(result.events || [])
      if (result.saved_ui_case_id) {
        qc.invalidateQueries({ queryKey: ['ui-cases', projectId] })
        qc.invalidateQueries({ queryKey: ['test-cases', projectId] })
        message.success('已保存到 UI 用例')
      }
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  async function pollUntilTrialSettled(sessionId: number) {
    stopPoll()
    return new Promise<AgentSession>((resolve, reject) => {
      const tick = async () => {
        try {
          const refreshed = await apiJson<AgentSession>(
            `/api/v1/projects/${projectId}/ui-scenario-agent/sessions/${sessionId}`,
          )
          setSession(refreshed)
          ingestEvents(refreshed.events || [])
          if (refreshed.dry_run_status !== 'running') {
            stopPoll()
            resolve(refreshed)
          }
        } catch (e) {
          stopPoll()
          reject(e)
        }
      }
      void tick()
      pollRef.current = window.setInterval(() => void tick(), 1500)
    })
  }

  async function trialRun() {
    if (!session || !scriptCode.trim()) {
      message.warning('没有可试跑的脚本')
      return
    }
    const caseId =
      session.plan?.case_scripts?.find((c) => c.ready && (c.code || '').trim())?.id || ''
    setTrialBusy(true)
    try {
      const started = await apiJson<AgentSession>(
        `/api/v1/projects/${projectId}/ui-scenario-agent/sessions/${session.id}/trial-run`,
        {
          method: 'POST',
          body: JSON.stringify({
            case_id: caseId,
            headed: trialHeaded,
            script: scriptCode,
          }),
        },
      )
      setSession(started)
      ingestEvents(started.events || [])
      const settled = await pollUntilTrialSettled(started.id)
      if (settled.dry_run_status === 'passed') {
        message.success('试跑通过')
      } else {
        message.error(settled.dry_run_error || '试跑失败')
      }
    } catch (e) {
      message.error(errText(e))
    } finally {
      stopPoll()
      setTrialBusy(false)
    }
  }

  const savedIds =
    session?.plan?.saved_ui_case_ids?.length
      ? session.plan.saved_ui_case_ids
      : session?.saved_ui_case_id
        ? [session.saved_ui_case_id]
        : []
  const scriptCount =
    session?.plan?.case_scripts?.filter((c) => c.ready && (c.code || '').trim()).length ??
    (session?.playwright_code ? 1 : 0)
  const shotKeys = useMemo(
    () => logs.map((line) => line.screenshotKey).filter((key): key is string => Boolean(key)),
    [logs],
  )
  const visibleLogs = useMemo(() => {
    const rows: Array<{ id: string; kind: LogKind; label: string; body: string }> = []
    for (const line of logs) {
      const classified = classifyLog(line.text)
      if (!classified || classified.kind === 'shot') continue
      rows.push({ id: line.id, ...classified })
    }
    return rows
  }, [logs])
  const scriptCode = useMemo(() => {
    const fromPlan = session?.plan?.case_scripts?.find((c) => c.ready && (c.code || '').trim())?.code
    return (fromPlan || session?.playwright_code || '').trim()
  }, [session])

  useEffect(() => {
    const el = logBoxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [visibleLogs.length])

  return (
    <Modal
      className="testcase-automate-modal"
      title="自动化执行"
      open={open}
      onCancel={() => {
        stopPoll()
        onClose()
      }}
      width={phase === 'config' ? 640 : 880}
      destroyOnHidden
      footer={
        phase === 'config' ? (
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" loading={busy} disabled={!selectedEnv} onClick={() => void startRun()}>
              开始执行
            </Button>
          </Space>
        ) : phase === 'running' ? (
          <Space>
            <Button disabled>执行中…</Button>
            <Button danger loading={stopping} disabled={!session} onClick={() => void stopRun()}>
              停止
            </Button>
          </Space>
        ) : (
          <Space>
            <Button
              onClick={() => {
                setSession(null)
                setLogs([])
                setScriptOpen(false)
                seenKeys.current = new Set()
              }}
            >
              重新配置
            </Button>
            {session?.status === 'awaiting_confirm' &&
            !session.saved_ui_case_id &&
            (session.execution_mode || session.plan?._execution_mode) !== 'agent_once' ? (
              <Button type="primary" loading={busy} onClick={() => void saveCases()}>
                保存到 UI 用例
              </Button>
            ) : (
              <Button type="primary" onClick={onClose}>
                关闭
              </Button>
            )}
          </Space>
        )
      }
    >
      <section className="form-meta-panel">
        {testCase?.title ? <h3 className="form-meta-title">{testCase.title}</h3> : null}
        <dl className="form-meta-list">
          <div className="form-meta-row">
            <dt>前置条件</dt>
            <dd>{testCase?.precondition?.trim() || '—'}</dd>
          </div>
          <div className="form-meta-row">
            <dt>测试数据</dt>
            <dd>
              <VariablesTags variables={testCase?.variables} fallbackText={testCase?.test_data} />
            </dd>
          </div>
          <div className="form-meta-row">
            <dt>总体期望</dt>
            <dd>{testCase?.overall_expected?.trim() || '—'}</dd>
          </div>
          <div className="form-meta-row">
            <dt>步骤</dt>
            <dd>
              {sortedSteps.length === 0 ? (
                '—'
              ) : (
                <div className="form-meta-steps">
                  {sortedSteps.map((s, i) => (
                    <div key={`${s.order}-${i}`}>
                      {i + 1}. {s.action}
                    </div>
                  ))}
                </div>
              )}
            </dd>
          </div>
        </dl>
      </section>

      {phase === 'config' && (
        <Form layout="vertical" className="form-config-block" requiredMark={false}>
          <Form.Item
            label="执行模式"
            extra={
              executionMode === 'agent_once'
                ? '一次性执行，不生成/保存脚本，结果写入功能用例执行记录'
                : '探索页面并生成 Playwright 脚本，确认后写入 UI 用例'
            }
          >
            <Segmented
              value={executionMode}
              onChange={(v) => setExecutionMode(v as ExecutionMode)}
              options={[
                { value: 'explore_codegen', label: '探索生成脚本' },
                { value: 'agent_once', label: 'Agent 直跑' },
              ]}
            />
          </Form.Item>
          <Form.Item label="UI 环境" extra={selectedEnv?.base_url || undefined}>
            <Select
              placeholder="选择 UI 环境"
              value={selectedEnv?.id}
              options={envOptions}
              loading={envs.isLoading}
              onChange={(v) => setEnvId(v)}
            />
          </Form.Item>
          <Form.Item label="浏览器模式">
            <Switch
              checked={headed}
              onChange={setHeaded}
              checkedChildren="有头"
              unCheckedChildren="无头"
            />
          </Form.Item>
          <Form.Item
            label="环境注入"
            extra="请求头 / localStorage 模板在 UI 环境配置；登录用例请选「不注入」。"
          >
            <Select
              value={authMode}
              onChange={(v) => setAuthMode(v)}
              options={[
                { value: 'follow', label: '跟随环境注入' },
                { value: 'none', label: '不注入' },
              ]}
              style={{ width: 240 }}
            />
          </Form.Item>
          <Form.Item label="补充提示词">
            <Input.TextArea
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="可选。补充页面入口、特殊控件或断言说明"
              maxLength={8000}
            />
          </Form.Item>
        </Form>
      )}

      {phase !== 'config' && (
        <div className="testcase-automate-explore">
          <div className="testcase-automate-log" ref={logBoxRef}>
            {visibleLogs.length === 0 ? (
              <div className="testcase-automate-log-empty">等待执行日志…</div>
            ) : (
              visibleLogs.map((line) => (
                <div
                  key={line.id}
                  className={`testcase-automate-log-line testcase-automate-log-line--${line.kind}`}
                >
                  {line.label ? (
                    <span className="testcase-automate-log-tag">{line.label}</span>
                  ) : null}
                  <span className="testcase-automate-log-body">{line.body}</span>
                </div>
              ))
            )}
          </div>
          {session && shotKeys.length > 0 && (
            <ExploreShotRail
              projectId={projectId}
              sessionId={session.id}
              storageKeys={shotKeys}
            />
          )}
        </div>
      )}

      {phase === 'result' && session && (
        <div className="testcase-automate-result">
          {(session.execution_mode || session.plan?._execution_mode) === 'agent_once' ? (
            <>
              {(() => {
                const runStatus =
                  session.plan?._case_run_status ||
                  (session.status === 'cancelled'
                    ? 'cancelled'
                    : session.status === 'error' || session.error_message
                      ? 'failed'
                      : 'passed')
                const color =
                  runStatus === 'passed'
                    ? 'success'
                    : runStatus === 'cancelled'
                      ? 'default'
                      : 'error'
                return (
                  <>
                    <p>
                      Agent 直跑结果：<Tag color={color}>{runStatus}</Tag>
                      {session.case_run_id ? (
                        <>
                          {' · '}
                          <Link to={`/projects/${projectId}/test-case-runs/${session.case_run_id}`}>
                            执行记录 #{session.case_run_id}
                          </Link>
                        </>
                      ) : null}
                    </p>
                    {session.error_message ? (
                      <p className={runStatus === 'cancelled' ? undefined : 'error'}>
                        {session.error_message}
                      </p>
                    ) : null}
                  </>
                )
              })()}
            </>
          ) : (
            <>
              {(session.status === 'error' || session.status === 'cancelled') && (
                <p className={session.status === 'cancelled' ? undefined : 'error'}>
                  {session.error_message || (session.status === 'cancelled' ? '已停止执行' : '探索失败')}
                </p>
              )}
              {session.status === 'awaiting_confirm' && (
                <p>
                  已生成 {scriptCount} 条 Playwright 脚本
                  {scriptCode ? (
                    <>
                      {' · '}
                      <Button
                        type="link"
                        size="small"
                        className="testcase-automate-script-btn"
                        onClick={() => setScriptOpen(true)}
                      >
                        查看脚本
                      </Button>
                    </>
                  ) : null}
                </p>
              )}
              {savedIds.length > 0 && (
                <p>
                  已写入{' '}
                  <Link to={`/projects/${projectId}/ui-cases`}>
                    UI 用例 {savedIds.map((id) => `#${id}`).join('、')}
                  </Link>
                  {scriptCode ? (
                    <>
                      {' · '}
                      <Button
                        type="link"
                        size="small"
                        className="testcase-automate-script-btn"
                        onClick={() => setScriptOpen(true)}
                      >
                        查看脚本
                      </Button>
                    </>
                  ) : null}
                </p>
              )}
            </>
          )}
        </div>
      )}

      <Modal
        title="Playwright 脚本"
        open={scriptOpen}
        onCancel={() => setScriptOpen(false)}
        width={720}
        zIndex={1100}
        destroyOnHidden
        footer={[
          <Space key="trial" size="small" style={{ marginRight: 'auto' }}>
            <Switch
              size="small"
              checked={trialHeaded}
              onChange={setTrialHeaded}
              checkedChildren="有头"
              unCheckedChildren="无头"
              disabled={trialBusy || session?.status !== 'awaiting_confirm'}
            />
            <Button
              loading={trialBusy}
              disabled={!scriptCode || trialBusy || session?.status !== 'awaiting_confirm'}
              onClick={() => void trialRun()}
            >
              试跑
            </Button>
          </Space>,
          <Button key="close" onClick={() => setScriptOpen(false)}>
            关闭
          </Button>,
          <Button
            key="copy"
            type="primary"
            disabled={!scriptCode}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(scriptCode)
                message.success('已复制脚本')
              } catch {
                message.error('复制失败')
              }
            }}
          >
            复制
          </Button>,
        ]}
      >
        {session?.dry_run_status ? (
          <p style={{ marginBottom: 8 }}>
            <Tag
              color={
                session.dry_run_status === 'passed'
                  ? 'success'
                  : session.dry_run_status === 'running'
                    ? 'processing'
                    : 'error'
              }
            >
              试跑 {session.dry_run_status}
            </Tag>
            {session.dry_run_error ? (
              <span className="error" style={{ marginLeft: 8 }}>
                {session.dry_run_error}
              </span>
            ) : null}
          </p>
        ) : null}
        <pre className="testcase-automate-script-pre">{scriptCode || '暂无脚本'}</pre>
      </Modal>
    </Modal>
  )
}
