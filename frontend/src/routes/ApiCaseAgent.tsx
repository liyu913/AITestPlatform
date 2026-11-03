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

type CaseDraft = {
  name: string
  tags?: string[]
  method?: string
  path?: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body_type?: string
  body?: string
  assertions?: Array<Record<string, unknown>>
  extractors?: Array<{ name?: string; type?: string; expr?: string }>
  timeout_ms?: number
  description?: string
  env_vars_needed?: string[]
  rationale?: string
}

type CaseGroup = {
  definition_id: number
  definition_name?: string
  method?: string
  path?: string
  cases?: CaseDraft[]
}

type CasePlan = {
  title?: string
  summary?: string
  groups?: CaseGroup[]
  warnings?: string[]
  needs_user_input?: string[]
}

type CaseSession = {
  id: number
  status: string
  prompt: string
  candidate_definition_ids: number[]
  plan: CasePlan | null
  events: Array<Record<string, unknown>>
  messages: Array<{ role: string; content: string }>
  created_case_ids: number[]
  error_message: string
}

type ChatBubble = {
  id: string
  role: 'user' | 'assistant' | 'system' | 'log'
  content: string
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError) {
    const b = ex.body as { message?: string } | null
    if (b && typeof b === 'object' && typeof b.message === 'string' && b.message) {
      return b.message
    }
    return `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function formatEventLine(ev: Record<string, unknown>): string | null {
  const t = String(ev.type || '')
  if (t === 'log') return `[${String(ev.node || '')}] ${String(ev.message || '')}`
  if (t === 'plan') return '[plan] 已收到用例草稿'
  if (t === 'interrupted') return `[wait] ${String(ev.message || '等待确认')}`
  if (t === 'done') return `[done] status=${String(ev.status || '')}`
  if (t === 'error') return `[error] ${String(ev.message || '')}`
  return null
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
  return t || 'assert'
}

function methodClass(method: string): string {
  const m = method.toLowerCase().replace(/[^a-z]/g, '')
  return `api-scenario-agent-method api-scenario-agent-method--${m || 'other'}`
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

function CaseCard({ draft }: { draft: CaseDraft }) {
  const assertions = draft.assertions || []
  const extractors = draft.extractors || []
  const envVars = draft.env_vars_needed || []
  const tags = draft.tags || []
  return (
    <article className="api-scenario-agent-step">
      <header className="api-scenario-agent-step-head">
        <span className="api-scenario-agent-step-name">{draft.name}</span>
        {tags.length > 0 && (
          <span className="api-scenario-agent-tags">
            {tags.slice(0, 4).map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </span>
        )}
      </header>
      <dl className="api-scenario-agent-step-meta">
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
        <div className="api-scenario-agent-step-row">
          <dt>提取</dt>
          <dd>
            {extractors.length ? (
              <span className="api-scenario-agent-tags">
                {extractors.map((e) => (
                  <Tag key={e.name || e.expr} color="blue">
                    {e.name}
                    {e.expr ? ` ← ${e.expr}` : ''}
                  </Tag>
                ))}
              </span>
            ) : (
              <span className="api-scenario-agent-muted">—</span>
            )}
          </dd>
        </div>
        {envVars.length > 0 && (
          <div className="api-scenario-agent-step-row">
            <dt>环境变量</dt>
            <dd>
              <span className="api-scenario-agent-tags">
                {envVars.map((v) => (
                  <Tag key={v}>{`{{${v}}}`}</Tag>
                ))}
              </span>
            </dd>
          </div>
        )}
        {(draft.rationale || draft.description) && (
          <div className="api-scenario-agent-step-row">
            <dt>说明</dt>
            <dd>{draft.rationale || draft.description}</dd>
          </div>
        )}
      </dl>
    </article>
  )
}

function GroupCard({ group }: { group: CaseGroup }) {
  const cases = group.cases || []
  const method = group.method || ''
  const path = group.path || ''
  return (
    <section className="api-scenario-agent-step" style={{ marginBottom: 16 }}>
      <header className="api-scenario-agent-step-head">
        <span className="api-scenario-agent-step-id">#{group.definition_id}</span>
        <span className="api-scenario-agent-step-name">
          {group.definition_name || `接口 #${group.definition_id}`}
        </span>
        <Tag>{cases.length} 条</Tag>
      </header>
      {(method || path) && (
        <div className="api-scenario-agent-step-endpoint">
          {method ? <span className={methodClass(method)}>{method}</span> : null}
          {path ? (
            <code className="api-scenario-agent-path">{renderPath(path)}</code>
          ) : null}
        </div>
      )}
      <div className="api-scenario-agent-steps" style={{ marginTop: 8 }}>
        {cases.map((c, i) => (
          <CaseCard key={`${c.name}-${i}`} draft={c} />
        ))}
      </div>
    </section>
  )
}

function roleLabel(role: ChatBubble['role']): string {
  if (role === 'user') return '你'
  if (role === 'system') return '系统'
  if (role === 'log') return '日志'
  return 'Agent'
}

export default function ApiCaseAgent() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const [defIds, setDefIds] = useState<number[]>([])
  const [input, setInput] = useState('')
  const [session, setSession] = useState<CaseSession | null>(null)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [notices, setNotices] = useState<ChatBubble[]>([])
  const [logBubbles, setLogBubbles] = useState<ChatBubble[]>([])
  const [busy, setBusy] = useState(false)
  const [apiPopoverOpen, setApiPopoverOpen] = useState(false)
  const [sessionList, setSessionList] = useState<AgentSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [engineExpired, setEngineExpired] = useState(false)
  const { collapsed: chatCollapsed, toggleCollapsed: toggleChatCollapsed } = useAgentChatCollapsed(
    Number.isFinite(pid) ? `api-case-agent-chat-collapsed:${pid}` : 'api-case-agent-chat-collapsed',
  )

  const logSeqRef = useRef(0)

  const definitions = useQuery({
    queryKey: ['api-definitions', pid, 'case-agent'],
    queryFn: () =>
      apiJson<PageRes<ApiDef>>(`/api/v1/projects/${pid}/api-definitions/?page=1&page_size=500`),
    enabled: Number.isFinite(pid),
  })

  const awaiting = session?.status === 'awaiting_confirm' && !engineExpired
  const finished = ['confirmed', 'cancelled', 'error', 'done'].includes(session?.status || '')
  const canStart = !session || finished
  const canChat = Boolean(awaiting)

  const displayMessages = useMemo(() => {
    const out: ChatBubble[] = []
    for (const m of session?.messages || []) {
      const role = m.role === 'user' ? 'user' : m.role === 'assistant' ? 'assistant' : 'system'
      out.push({
        id: `m-${out.length}`,
        role,
        content: m.content,
      })
    }
    if (pendingUser) {
      out.push({ id: 'pending-user', role: 'user', content: pendingUser })
    }
    out.push(...notices)
    out.push(...logBubbles)
    return out
  }, [session?.messages, pendingUser, notices, logBubbles])

  const {
    containerRef: messagesRef,
    onScroll: updateStickToBottom,
    pinToBottom,
  } = useStickToBottom([displayMessages.length, busy, chatCollapsed])

  async function refreshSessionList() {
    if (!Number.isFinite(pid)) return
    setHistoryLoading(true)
    try {
      const rows = await apiJson<AgentSessionSummary[]>(
        `/api/v1/projects/${pid}/api-case-agent/sessions`,
      )
      setSessionList(rows)
    } catch {
      /* ignore */
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => {
    void refreshSessionList()
  }, [pid])

  function appendLog(line: string) {
    logSeqRef.current += 1
    setLogBubbles((prev) => [
      ...prev,
      { id: `log-${logSeqRef.current}`, role: 'log', content: line },
    ])
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
    const userText = prompt.trim() || '请根据所选接口生成单接口用例矩阵'
    setBusy(true)
    setLogBubbles([])
    logSeqRef.current = 0
    setNotices([])
    setPendingUser(userText)
    try {
      const created = await apiJson<CaseSession>(
        `/api/v1/projects/${pid}/api-case-agent/sessions`,
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
          `/api/v1/projects/${pid}/api-case-agent/sessions/${created.id}/events`,
          { method: 'GET' },
          (ev: SseEvent) => {
            ingestEvents([ev as Record<string, unknown>])
          },
        )
      } catch {
        /* stream closed */
      }
      const refreshed = await apiJson<CaseSession>(
        `/api/v1/projects/${pid}/api-case-agent/sessions/${created.id}`,
      )
      setSession(refreshed)
      setPendingUser(null)
      void refreshSessionList()
      if (refreshed.status === 'error') {
        message.error(refreshed.error_message || '生成草稿失败')
        pushNotice('system', refreshed.error_message || '生成草稿失败')
      } else if (refreshed.status === 'awaiting_confirm') {
        const groups = refreshed.plan?.groups?.length ?? 0
        const cases =
          refreshed.plan?.groups?.reduce((n, g) => n + (g.cases?.length ?? 0), 0) ?? 0
        pushNotice(
          'assistant',
          `左侧可预览用例草稿（${groups} 个接口 / ${cases} 条）。可继续对话修改，确认后才会写入单接口用例。`,
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
      const updated = await apiJson<CaseSession>(
        `/api/v1/projects/${pid}/api-case-agent/sessions/${session.id}/messages`,
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
      const result = await apiJson<CaseSession>(
        `/api/v1/projects/${pid}/api-case-agent/sessions/${session.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({ action }),
        },
      )
      setSession(result)
      ingestEvents(result.events || [])
      void refreshSessionList()
      if (action === 'confirm' && result.created_case_ids?.length) {
        qc.invalidateQueries({ queryKey: ['api-cases', pid] })
        message.success(`已写入 ${result.created_case_ids.length} 条单接口用例`)
        pushNotice(
          'assistant',
          `已确认并写入 ${result.created_case_ids.length} 条用例。`,
        )
      } else if (action === 'cancel') {
        message.info('已取消')
        pushNotice('assistant', '已取消本次会话，未写入用例。可点「新会话」重新开始。')
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
      const s = await apiJson<CaseSession>(
        `/api/v1/projects/${pid}/api-case-agent/sessions/${id}`,
      )
      setSession(s)
      if (s.candidate_definition_ids?.length) setDefIds(s.candidate_definition_ids)
      ingestEvents(s.events || [])
      if (['confirmed', 'cancelled', 'error', 'done'].includes(s.status)) {
        pushNotice('assistant', '已打开历史会话（只读）。可点「新会话」重新开始。')
      } else if (s.status === 'awaiting_confirm') {
        pushNotice('assistant', '已打开历史会话。可继续修改或确认；若操作失败请新开会话。')
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
      await apiJson(`/api/v1/projects/${pid}/api-case-agent/sessions/${id}`, {
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

  const planGroups = session?.plan?.groups ?? []
  const canSend = !busy && !finished && (canStart || (canChat && !!input.trim()))
  const alertItems = [
    ...(session?.plan?.warnings || []),
    ...(session?.plan?.needs_user_input || []),
  ]

  const apiPicker = (
    <div className="api-scenario-agent-picker api-scenario-agent-picker--tree">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        选择候选接口（可多选；每个接口独立生成用例矩阵）
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
      <PageBanner title="单接口用例 Agent" />
      <div className="api-scenario-agent-split">
        <aside className="api-scenario-agent-plan card">
          <div className="api-scenario-agent-chat-toolbar">
            <Typography.Text strong>{session?.plan?.title || '用例草稿'}</Typography.Text>
            <Space size="small">
              <Button
                size="small"
                type="primary"
                disabled={!awaiting}
                loading={busy}
                onClick={() => void confirmAction('confirm')}
              >
                确认写入用例
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
            {alertItems.length > 0 && (
              <Alert
                className="api-scenario-agent-warnings"
                type="warning"
                showIcon
                message={
                  <ul>
                    {alertItems.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                }
              />
            )}
            {planGroups.length > 0 ? (
              <div className="api-scenario-agent-steps">
                {planGroups.map((g) => (
                  <GroupCard key={g.definition_id} group={g} />
                ))}
              </div>
            ) : (
              <div className="api-scenario-agent-plan-empty">
                选择接口并生成草稿后，在此预览各接口的正向 / 异常 / 边界用例
              </div>
            )}
          </div>
          {session?.created_case_ids?.length ? (
            <Typography.Paragraph className="api-scenario-agent-plan-foot">
              已写入 {session.created_case_ids.length} 条{' '}
              <Link to={`/projects/${pid}/api-cases`}>单接口用例</Link>
            </Typography.Paragraph>
          ) : null}
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
                  ? '可选说明，例如：登录接口多加鉴权失败；订单接口依赖 {{orderId}}'
                  : canChat
                    ? '继续修改草稿，例如：去掉所有空 Body；给登录正向加 token 提取'
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

              <Tooltip title={canStart ? '生成草稿' : '发送'}>
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
