import { ApiOutlined, SendOutlined } from '@ant-design/icons'
import {
  Alert,
  App,
  Badge,
  Button,
  Input,
  Popover,
  Space,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson, apiSse, ApiError, type SseEvent } from '../api/client'
import {
  AgentChatCollapseButton,
  useAgentChatCollapsed,
} from '../components/AgentChatCollapse'
import AgentSessionHistory, {
  type AgentSessionSummary,
} from '../components/AgentSessionHistory'
import ApiDefinitionTreeCheck from '../components/ApiDefinitionTreeCheck'
import { useStickToBottom } from '../hooks/useStickToBottom'
import PageBanner from './PageBanner'
type ApiDef = {
  id: number
  name: string
  method: string
  path: string
  module?: string
}
type PageRes<T> = { items: T[]; total: number }

type ConsumeHint = {
  var: string
  from_step?: string
  in?: string
  key?: string
  template?: string
}

type SuiteStep = {
  id: string
  case_id?: number
  definition_id?: number
  case_name?: string
  definition_name?: string
  method?: string
  path?: string
  on_pass?: string
  on_fail?: string
  when?: { op?: string; left?: string; right?: string } | null
  extract_hints?: string[]
  consume_hints?: ConsumeHint[]
  assertions?: Array<Record<string, unknown>>
  rationale?: string
}

type ScenarioPlan = {
  title?: string
  summary?: string
  steps?: SuiteStep[]
  warnings?: string[]
}

type ScenarioSession = {
  id: number
  status: string
  prompt: string
  candidate_definition_ids: number[]
  plan: ScenarioPlan | null
  events: Array<Record<string, unknown>>
  messages: Array<{ role: string; content: string }>
  suite_id: number | null
  error_message: string
}

type ChatBubble = {
  id: string
  role: 'user' | 'assistant' | 'system' | 'log'
  content: string
}

const WELCOME =
  '你好，我是接口自动化 Agent。\n\n1. 点击输入框下方图标选择要串联的接口\n2. 用自然语言描述业务链路（可不填，将按接口信息推断）\n3. 生成计划后可继续对话修改；左侧预览步骤\n4. 确认后才会写入场景'

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function formatEventLine(ev: Record<string, unknown>): string | null {
  const t = String(ev.type || '')
  if (t === 'log') return `[${String(ev.node || '')}] ${String(ev.message || '')}`
  if (t === 'plan') return '[plan] 已收到场景计划'
  if (t === 'interrupted') return `[wait] ${String(ev.message || '等待确认')}`
  if (t === 'done') return `[done] status=${String(ev.status || '')}`
  if (t === 'error') return `[error] ${String(ev.message || '')}`
  return null
}

function formatWhen(when: SuiteStep['when']): string {
  if (!when || !when.op) return '—'
  if (when.op === 'exists') return `exists ${when.left || ''}`
  return `${when.left || ''} ${when.op} ${when.right ?? ''}`
}

function formatAssertion(a: Record<string, unknown>): string {
  const t = String(a.type || '')
  const expr = String(a.expr || '')
  const op = String(a.op || '')
  const expected = a.expected
  if (t === 'status') return `status=${String(expected ?? '')}`
  if (t === 'jsonpath') {
    if (op === 'exists') return `${expr} exists`
    if (op === 'contains') return `${expr} contains ${String(expected ?? '')}`
    if (op === 'regex') return `${expr} =~ ${String(expected ?? '')}`
    return `${expr}=${String(expected ?? '')}`
  }
  if (t === 'contains' || t === 'not_contains' || t === 'regex') {
    return `${t} ${String(expected ?? expr)}`
  }
  if (t === 'header') return `header ${expr}`
  return t || 'assert'
}

function stepTitle(s: SuiteStep): string {
  return (
    s.definition_name ||
    s.case_name ||
    (s.method && s.path ? `${s.method} ${s.path}` : '') ||
    (s.definition_id != null ? `#${s.definition_id}` : s.id)
  )
}

function consumeLine(h: ConsumeHint): string {
  const place = `${h.in || 'header'}.${h.key || h.var}`
  const from = h.from_step ? ` ← ${h.from_step}` : ''
  const tpl = h.template ? ` = ${h.template}` : ''
  return `${h.var}${from} · ${place}${tpl}`
}

function renderPath(path: string) {
  return path.split('/').map((seg, i) => (
    <span key={i}>
      {i > 0 ? (
        <>
          /<wbr />
        </>
      ) : null}
      {seg}
    </span>
  ))
}

function methodClass(method: string): string {
  const m = method.toLowerCase().replace(/[^a-z]/g, '')
  return `api-scenario-agent-method api-scenario-agent-method--${m || 'other'}`
}

function PlanStepCard({ step }: { step: SuiteStep }) {
  const extracts = (step.extract_hints || []).filter(Boolean)
  const consumes = step.consume_hints || []
  const assertions = step.assertions || []
  const showWhen = Boolean(step.when?.op)
  return (
    <article className="api-scenario-agent-step">
      <header className="api-scenario-agent-step-head">
        <span className="api-scenario-agent-step-id">{step.id}</span>
        <span className="api-scenario-agent-step-name">{stepTitle(step)}</span>
        <span className="api-scenario-agent-step-jumps">
          <span>成功 → {step.on_pass || '—'}</span>
          <span>失败 → {step.on_fail || '—'}</span>
        </span>
      </header>
      <div className="api-scenario-agent-step-endpoint">
        {step.method ? <span className={methodClass(step.method)}>{step.method}</span> : null}
        <code className="api-scenario-agent-path">
          {step.path ? renderPath(step.path) : '—'}
        </code>
      </div>
      <dl className="api-scenario-agent-step-meta">
        <div className="api-scenario-agent-step-row">
          <dt>产出</dt>
          <dd>
            {extracts.length ? (
              <span className="api-scenario-agent-tags">
                {extracts.map((name) => (
                  <Tag key={name} color="blue">
                    {name}
                  </Tag>
                ))}
              </span>
            ) : (
              <span className="api-scenario-agent-muted">—</span>
            )}
          </dd>
        </div>
        <div className="api-scenario-agent-step-row">
          <dt>消费</dt>
          <dd>
            {consumes.length ? (
              <div className="api-scenario-agent-lines">
                {consumes.map((h, i) => (
                  <div key={`${h.var}-${i}`} className="api-scenario-agent-line">
                    {consumeLine(h)}
                  </div>
                ))}
              </div>
            ) : (
              <span className="api-scenario-agent-muted">—</span>
            )}
          </dd>
        </div>
        <div className="api-scenario-agent-step-row">
          <dt>断言</dt>
          <dd>
            {assertions.length ? (
              <div className="api-scenario-agent-lines">
                {assertions.map((a, i) => (
                  <div key={`${String(a.type)}-${i}`} className="api-scenario-agent-line">
                    {formatAssertion(a)}
                  </div>
                ))}
              </div>
            ) : (
              <span className="api-scenario-agent-muted">—</span>
            )}
          </dd>
        </div>
        {showWhen ? (
          <div className="api-scenario-agent-step-row">
            <dt>条件</dt>
            <dd className="api-scenario-agent-line">{formatWhen(step.when)}</dd>
          </div>
        ) : null}
        {step.rationale ? (
          <div className="api-scenario-agent-step-row">
            <dt>说明</dt>
            <dd>{step.rationale}</dd>
          </div>
        ) : null}
      </dl>
    </article>
  )
}

function roleLabel(role: ChatBubble['role']): string {
  if (role === 'user') return '你'
  if (role === 'system') return '系统'
  if (role === 'log') return '日志'
  return 'Agent'
}

export default function ApiScenarioAgent() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const [defIds, setDefIds] = useState<number[]>([])
  const [input, setInput] = useState('')
  const [session, setSession] = useState<ScenarioSession | null>(null)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [notices, setNotices] = useState<ChatBubble[]>([])
  const [logBubbles, setLogBubbles] = useState<ChatBubble[]>([])
  const [busy, setBusy] = useState(false)
  const [apiPopoverOpen, setApiPopoverOpen] = useState(false)
  const [sessionList, setSessionList] = useState<AgentSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [engineExpired, setEngineExpired] = useState(false)
  const { collapsed: chatCollapsed, toggleCollapsed: toggleChatCollapsed } = useAgentChatCollapsed(
    Number.isFinite(pid) ? `api-agent-chat-collapsed:${pid}` : 'api-agent-chat-collapsed',
  )

  const logSeqRef = useRef(0)

  const definitions = useQuery({
    queryKey: ['api-definitions', pid, 'scenario-agent'],
    queryFn: () =>
      apiJson<PageRes<ApiDef>>(`/api/v1/projects/${pid}/api-definitions/?page=1&page_size=500`),
    enabled: Number.isFinite(pid),
  })

  const awaiting = session?.status === 'awaiting_confirm' && !busy && !engineExpired
  const finished =
    session != null &&
    (engineExpired || ['confirmed', 'cancelled', 'error', 'done'].includes(session.status))
  const canStart = !session && !busy
  const canChat = awaiting

  async function refreshSessionList() {
    if (!Number.isFinite(pid)) return
    const list = await apiJson<AgentSessionSummary[]>(
      `/api/v1/projects/${pid}/api-scenario-agent/sessions`,
    )
    setSessionList(list)
  }

  useEffect(() => {
    if (!Number.isFinite(pid)) return
    let cancelled = false
    ;(async () => {
      setHistoryLoading(true)
      try {
        const list = await apiJson<AgentSessionSummary[]>(
          `/api/v1/projects/${pid}/api-scenario-agent/sessions`,
        )
        if (!cancelled) setSessionList(list)
      } catch {
        /* list is best-effort on mount */
      } finally {
        if (!cancelled) setHistoryLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [pid])

  const displayMessages = useMemo(() => {
    const bubbles: ChatBubble[] = [{ id: 'welcome', role: 'assistant', content: WELCOME }]
    const apiMsgs = session?.messages ?? []
    apiMsgs.forEach((m, i) => {
      bubbles.push({
        id: `api-${session?.id ?? 0}-${i}`,
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content,
      })
    })
    if (pendingUser && !apiMsgs.some((m) => m.role === 'user' && m.content === pendingUser)) {
      bubbles.push({ id: 'pending-user', role: 'user', content: pendingUser })
    }
    bubbles.push(...logBubbles)
    bubbles.push(...notices)
    return bubbles
  }, [session, pendingUser, notices, logBubbles])

  const {
    containerRef: messagesRef,
    onScroll: updateStickToBottom,
    pinToBottom,
  } = useStickToBottom([displayMessages, busy])

  function appendLog(line: string) {
    logSeqRef.current += 1
    const id = `log-${logSeqRef.current}`
    setLogBubbles((prev) => [...prev, { id, role: 'log', content: line }])
  }

  function ingestEvents(events: Array<Record<string, unknown>>) {
    for (const ev of events) {
      const line = formatEventLine(ev)
      if (line) appendLog(line)
    }
  }

  function pushNotice(role: Exclude<ChatBubble['role'], 'log'>, content: string) {
    setNotices((prev) => [
      ...prev,
      { id: `n-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, role, content },
    ])
  }

  async function startPlan(prompt: string) {
    if (defIds.length === 0) {
      message.warning('请至少选择一个接口')
      setApiPopoverOpen(true)
      return
    }
    const userText = prompt.trim() || '请根据所选接口生成串联计划'
    setBusy(true)
    setLogBubbles([])
    logSeqRef.current = 0
    setNotices([])
    setPendingUser(userText)
    try {
      const created = await apiJson<ScenarioSession>(
        `/api/v1/projects/${pid}/api-scenario-agent/sessions`,
        {
          method: 'POST',
          body: JSON.stringify({
            prompt: prompt.trim(),
            definition_ids: defIds,
          }),
        },
      )
      setSession(created)
      setEngineExpired(false)
      if (created.candidate_definition_ids?.length) {
        setDefIds(created.candidate_definition_ids)
      }
      ingestEvents(created.events || [])
      try {
        await apiSse(
          `/api/v1/projects/${pid}/api-scenario-agent/sessions/${created.id}/events`,
          { method: 'GET' },
          (ev: SseEvent) => {
            ingestEvents([ev as Record<string, unknown>])
          },
        )
      } catch {
        /* stream closed */
      }
      const refreshed = await apiJson<ScenarioSession>(
        `/api/v1/projects/${pid}/api-scenario-agent/sessions/${created.id}`,
      )
      setSession(refreshed)
      setPendingUser(null)
      void refreshSessionList()
      if (refreshed.status === 'error') {
        message.error(refreshed.error_message || '生成计划失败')
        pushNotice('system', refreshed.error_message || '生成计划失败')
      } else if (refreshed.status === 'awaiting_confirm') {
        const n = refreshed.plan?.steps?.length ?? 0
        pushNotice(
          'assistant',
          `左侧可预览完整步骤（共 ${n} 步）。可继续对话修改，确认后才会写入场景。`,
        )
      }
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
      setPendingUser(null)
    } finally {
      setBusy(false)
    }
  }

  async function sendRevise(content: string) {
    if (!session) return
    setBusy(true)
    setPendingUser(content)
    try {
      const updated = await apiJson<ScenarioSession>(
        `/api/v1/projects/${pid}/api-scenario-agent/sessions/${session.id}/messages`,
        {
          method: 'POST',
          body: JSON.stringify({ content }),
        },
      )
      setSession(updated)
      setPendingUser(null)
      ingestEvents(updated.events || [])
      void refreshSessionList()
      if (updated.status === 'error') {
        pushNotice('system', updated.error_message || '修改失败')
      }
    } catch (e) {
      const text = errText(e)
      message.error(text)
      pushNotice('system', text)
      setPendingUser(null)
      // MemorySaver lost after process restart — fall back to read-only
      if (session.status === 'awaiting_confirm') {
        setEngineExpired(true)
        pushNotice('system', '会话引擎已过期，请新会话继续。当前会话可只读回看。')
      }
    } finally {
      setBusy(false)
    }
  }

  async function onSend() {
    const text = input.trim()
    if (busy) return
    if (canStart) {
      pinToBottom()
      setInput('')
      await startPlan(text)
      return
    }
    if (canChat) {
      if (!text) return
      pinToBottom()
      setInput('')
      await sendRevise(text)
    }
  }

  async function confirmAction(action: 'confirm' | 'cancel') {
    if (!session) return
    setBusy(true)
    try {
      const result = await apiJson<ScenarioSession>(
        `/api/v1/projects/${pid}/api-scenario-agent/sessions/${session.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({ action }),
        },
      )
      setSession(result)
      ingestEvents(result.events || [])
      void refreshSessionList()
      if (action === 'confirm' && result.suite_id) {
        qc.invalidateQueries({ queryKey: ['api-suites', pid] })
        qc.invalidateQueries({ queryKey: ['api-cases', pid] })
        message.success(`已写入场景 #${result.suite_id}`)
        pushNotice('assistant', `已确认并写入场景 #${result.suite_id}。`)
      } else if (action === 'cancel') {
        message.info('已取消')
        pushNotice('assistant', '已取消本次会话，未写入场景。可点「新会话」重新开始。')
      } else if (result.status === 'error') {
        message.error(result.error_message || '确认失败')
        pushNotice('system', result.error_message || '确认失败')
      }
    } catch (e) {
      const text = errText(e)
      message.error(text)
      pushNotice('system', text)
      if (session.status === 'awaiting_confirm') {
        setEngineExpired(true)
        pushNotice('system', '会话引擎已过期，请新会话继续。当前会话可只读回看。')
      }
    } finally {
      setBusy(false)
    }
  }

  function resetSession() {
    pinToBottom()
    setSession(null)
    setLogBubbles([])
    logSeqRef.current = 0
    setInput('')
    setPendingUser(null)
    setNotices([])
    setEngineExpired(false)
  }

  async function openHistorySession(id: number) {
    if (busy || session?.id === id) return
    pinToBottom()
    setBusy(true)
    setHistoryLoading(true)
    setLogBubbles([])
    logSeqRef.current = 0
    setNotices([])
    setPendingUser(null)
    setEngineExpired(false)
    try {
      const s = await apiJson<ScenarioSession>(
        `/api/v1/projects/${pid}/api-scenario-agent/sessions/${id}`,
      )
      setSession(s)
      if (s.candidate_definition_ids?.length) setDefIds(s.candidate_definition_ids)
      ingestEvents(s.events || [])
      if (['confirmed', 'cancelled', 'error', 'done'].includes(s.status)) {
        pushNotice('assistant', '已打开历史会话（只读）。可点「新会话」重新开始。')
      } else if (s.status === 'awaiting_confirm') {
        pushNotice(
          'assistant',
          '已打开历史会话。可继续修改或确认；若操作失败请新开会话。',
        )
      }
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
      setHistoryLoading(false)
    }
  }

  async function deleteHistorySession(id: number) {
    if (busy) return
    setBusy(true)
    try {
      await apiJson(`/api/v1/projects/${pid}/api-scenario-agent/sessions/${id}`, {
        method: 'DELETE',
      })
      if (session?.id === id) resetSession()
      await refreshSessionList()
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const planSteps = session?.plan?.steps ?? []
  const canSend =
    !busy && !finished && (canStart || (canChat && !!input.trim()))

  const apiPicker = (
    <div className="api-scenario-agent-picker api-scenario-agent-picker--tree">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        选择候选接口（必选）
      </Typography.Text>
      <ApiDefinitionTreeCheck
        definitions={definitions.data?.items ?? []}
        value={defIds}
        onChange={setDefIds}
        disabled={!canStart}
        loading={definitions.isLoading}
        height={320}
        title=""
        bordered={false}
      />
    </div>
  )

  return (
    <div className="api-scenario-agent-page">
      <PageBanner title="接口自动化 Agent" />
      <div className="api-scenario-agent-split">
        <aside className="api-scenario-agent-plan card">
          <div className="api-scenario-agent-chat-toolbar">
            <Typography.Text strong>{session?.plan?.title || '场景计划'}</Typography.Text>
            <Space size="small">
              <Button
                size="small"
                type="primary"
                disabled={!awaiting}
                loading={busy}
                onClick={() => void confirmAction('confirm')}
              >
                确认写入场景
              </Button>
              <Button size="small" disabled={!awaiting} onClick={() => void confirmAction('cancel')}>
                取消
              </Button>
            </Space>
          </div>
          <div className="api-scenario-agent-plan-body">
            {session?.plan?.summary && (
              <p className="api-scenario-agent-summary">{session.plan.summary}</p>
            )}
            {planSteps.length > 0 && (
              <div className="api-scenario-agent-flow">
                <span className="api-scenario-agent-flow-label">数据流</span>
                {planSteps.map((s, i) => {
                  const outs = (s.extract_hints || []).filter(Boolean)
                  const ins = (s.consume_hints || []).map((h) => h.var).filter(Boolean)
                  return (
                    <span key={s.id} className="api-scenario-agent-flow-step">
                      {i > 0 ? (
                        <span className="api-scenario-agent-flow-arrow" aria-hidden>
                          →
                        </span>
                      ) : null}
                      <span className="api-scenario-agent-flow-chip">
                        <span className="api-scenario-agent-flow-name">{stepTitle(s)}</span>
                        {outs.length > 0 && (
                          <span className="api-scenario-agent-flow-out">→ {outs.join(', ')}</span>
                        )}
                        {ins.length > 0 && (
                          <span className="api-scenario-agent-flow-in">用 {ins.join(', ')}</span>
                        )}
                      </span>
                    </span>
                  )
                })}
              </div>
            )}
            {(session?.plan?.warnings?.length ?? 0) > 0 && (
              <Alert
                className="api-scenario-agent-warnings"
                type="warning"
                showIcon
                message={
                  <ul>
                    {session?.plan?.warnings?.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                }
              />
            )}
            {planSteps.length > 0 ? (
              <div className="api-scenario-agent-steps">
                {planSteps.map((s) => (
                  <PlanStepCard key={s.id} step={s} />
                ))}
              </div>
            ) : (
              <div className="api-scenario-agent-plan-empty">生成计划后在此预览接口串联与传参</div>
            )}
          </div>
          {session?.suite_id != null && (
            <Typography.Paragraph className="api-scenario-agent-plan-foot">
              已写入{' '}
              <Link to={`/projects/${pid}/api-suites`}>场景 #{session.suite_id}</Link>
              {' · '}
              <Link to={`/projects/${pid}/api-suites/${session.suite_id}`}>去编排微调</Link>
            </Typography.Paragraph>
          )}
        </aside>

        <section
          className={`api-scenario-agent-chat card${chatCollapsed ? ' is-collapsed' : ''}`}
        >
          <div className="api-scenario-agent-chat-toolbar">
            <div className="api-scenario-agent-chat-toolbar-left">
              <Space size="small" wrap>
                {session && (
                  <Tag color={awaiting ? 'processing' : finished ? 'default' : 'blue'}>
                    {session.status}
                  </Tag>
                )}
              </Space>
            </div>
            <div className="api-scenario-agent-chat-toolbar-right">
              <AgentChatCollapseButton
                collapsed={chatCollapsed}
                onToggle={toggleChatCollapsed}
              />
              {!chatCollapsed && (
                <AgentSessionHistory
                  items={sessionList}
                  activeId={session?.id ?? null}
                  loading={historyLoading}
                  busy={busy}
                  onSelect={(id) => void openHistorySession(id)}
                  onNew={resetSession}
                  onDelete={(id) => void deleteHistorySession(id)}
                />
              )}
            </div>
          </div>

          <div
            className="api-scenario-agent-messages"
            ref={messagesRef}
            onScroll={updateStickToBottom}
          >
            {displayMessages.map((m) => (
              <div
                key={m.id}
                className={`api-scenario-agent-bubble api-scenario-agent-bubble--${m.role}`}
              >
                {m.role !== 'log' && (
                  <div className="api-scenario-agent-bubble-role">{roleLabel(m.role)}</div>
                )}
                <div className="api-scenario-agent-bubble-body">{m.content}</div>
              </div>
            ))}
            {busy && (
              <div className="api-scenario-agent-bubble api-scenario-agent-bubble--assistant">
                <div className="api-scenario-agent-bubble-role">Agent</div>
                <div className="api-scenario-agent-bubble-body muted">思考中…</div>
              </div>
            )}
          </div>

          <div className="api-scenario-agent-composer">
            <Input.TextArea
              className="api-scenario-agent-composer-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                canStart
                  ? '描述业务链路，例如：先登录拿 token，再创建订单，失败则结束'
                  : canChat
                    ? '继续修改计划，例如：把查询放到创建前面'
                    : '当前会话已结束，请点「新会话」'
              }
              autoSize={{ minRows: 2, maxRows: 5 }}
              disabled={busy || finished || (!canStart && !canChat)}
              variant="borderless"
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault()
                  void onSend()
                }
              }}
            />
            <div className="api-scenario-agent-composer-bar">
              <div className="api-scenario-agent-composer-tools">
                <Popover
                  content={apiPicker}
                  trigger="click"
                  open={apiPopoverOpen}
                  onOpenChange={setApiPopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip title={defIds.length ? `已选 ${defIds.length} 个接口` : '选择候选接口'}>
                    <Badge count={defIds.length} size="small" offset={[-2, 2]} showZero={false}>
                      <Button
                        type="text"
                        size="small"
                        className={
                          defIds.length
                            ? 'api-scenario-agent-tool-btn is-active'
                            : 'api-scenario-agent-tool-btn'
                        }
                        icon={<ApiOutlined />}
                        disabled={!canStart}
                      />
                    </Badge>
                  </Tooltip>
                </Popover>
              </div>

              <Tooltip title={canStart ? '生成计划' : '发送'}>
                <Button
                  type="primary"
                  shape="circle"
                  size="small"
                  className="api-scenario-agent-send-btn"
                  icon={<SendOutlined />}
                  loading={busy}
                  disabled={!canSend}
                  onClick={() => void onSend()}
                />
              </Tooltip>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
