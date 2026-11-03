import { SendOutlined } from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Checkbox,
  Collapse,
  Input,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useRef, useState } from 'react'
import { apiJson, apiSse, ApiError, type SseEvent } from '../api/client'
import {
  AgentChatCollapseButton,
  useAgentChatCollapsed,
} from './AgentChatCollapse'
import AgentSessionHistory, {
  type AgentSessionSummary,
} from './AgentSessionHistory'
import { useStickToBottom } from '../hooks/useStickToBottom'

type CaseDraft = {
  name?: string
  method?: string
  path?: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body_type?: string
  body?: string
  assertions?: Array<Record<string, unknown>>
  extractors?: Array<Record<string, unknown>>
  timeout_ms?: number
  description?: string
  tags?: string[]
  status?: string
}

type CaseRevision = {
  case_id: number
  name?: string
  summary?: string
  changed_fields?: string[]
  skipped?: boolean
  skip_reason?: string | null
  draft?: CaseDraft
}

type EditPlan = {
  title?: string
  summary?: string
  revisions?: CaseRevision[]
  warnings?: string[]
}

type CaseSnapshot = CaseDraft & {
  case_id: number
  name?: string
  api_id?: number | null
}

type EditSession = {
  id: number
  status: string
  prompt: string
  case_ids: number[]
  base_snapshots: CaseSnapshot[]
  plan: EditPlan | null
  events: Array<Record<string, unknown>>
  messages: Array<{ role: string; content: string }>
  applied_case_ids: number[]
  error_message: string
}

type ChatBubble = {
  id: string
  role: 'user' | 'assistant' | 'system' | 'log'
  content: string
}

const MAX_CASES = 20

export type ApiCaseEditAgentPanelProps = {
  projectId: number
  caseIds: number[]
  open: boolean
  onClose: () => void
  onApplied?: () => void
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
  if (t === 'plan') return '[plan] 已收到修订草稿'
  if (t === 'interrupted') return `[wait] ${String(ev.message || '等待确认')}`
  if (t === 'done') return `[done] status=${String(ev.status || '')}`
  if (t === 'error') return `[error] ${String(ev.message || '')}`
  return null
}

function roleLabel(role: ChatBubble['role']): string {
  if (role === 'user') return '你'
  if (role === 'system') return '系统'
  return 'Agent'
}

function pretty(v: unknown): string {
  try {
    return JSON.stringify(v ?? null, null, 2)
  } catch {
    return String(v)
  }
}

function DiffBlock({
  label,
  before,
  after,
}: {
  label: string
  before: unknown
  after: unknown
}) {
  const a = pretty(before)
  const b = pretty(after)
  if (a === b) return null
  return (
    <div style={{ marginBottom: 8 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {label}
      </Typography.Text>
      <pre
        style={{
          margin: '4px 0 0',
          padding: 8,
          background: 'var(--ant-color-fill-quaternary, #f5f5f5)',
          fontSize: 11,
          maxHeight: 160,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
        }}
      >
        <div style={{ opacity: 0.65, textDecoration: 'line-through' }}>{a}</div>
        <div>{b}</div>
      </pre>
    </div>
  )
}

export default function ApiCaseEditAgentPanel({
  projectId: pid,
  caseIds: caseIdsProp,
  open,
  onClose,
  onApplied,
}: ApiCaseEditAgentPanelProps) {
  const { message } = App.useApp()

  const [caseIds, setCaseIds] = useState<number[]>(caseIdsProp)
  const [selectedApplyIds, setSelectedApplyIds] = useState<number[]>([])
  const [input, setInput] = useState('')
  const [session, setSession] = useState<EditSession | null>(null)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [notices, setNotices] = useState<ChatBubble[]>([])
  const [logBubbles, setLogBubbles] = useState<ChatBubble[]>([])
  const [busy, setBusy] = useState(false)
  const [sessionList, setSessionList] = useState<AgentSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [engineExpired, setEngineExpired] = useState(false)
  const { collapsed: chatCollapsed, toggleCollapsed: toggleChatCollapsed } = useAgentChatCollapsed(
    Number.isFinite(pid)
      ? `api-case-edit-agent-chat-collapsed:${pid}`
      : 'api-case-edit-agent-chat-collapsed',
  )

  const logSeqRef = useRef(0)

  const awaiting = session?.status === 'awaiting_confirm' && !engineExpired
  const finished = ['confirmed', 'cancelled', 'error', 'done'].includes(session?.status || '')
  const canStart = !session || finished
  const canChat = Boolean(awaiting)

  const revisions = session?.plan?.revisions ?? []
  const snapshotById = useMemo(() => {
    const m = new Map<number, CaseSnapshot>()
    for (const s of session?.base_snapshots || []) {
      m.set(s.case_id, s)
    }
    return m
  }, [session?.base_snapshots])

  useEffect(() => {
    if (!session?.plan?.revisions) return
    const ids = session.plan.revisions.filter((r) => !r.skipped).map((r) => r.case_id)
    setSelectedApplyIds(ids)
  }, [session?.id, session?.plan?.revisions])

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

  function resetSession() {
    pinToBottom()
    setSession(null)
    setLogBubbles([])
    logSeqRef.current = 0
    setInput('')
    setPendingUser(null)
    setNotices([])
    setEngineExpired(false)
    setSelectedApplyIds([])
  }

  useEffect(() => {
    if (!open) return
    setCaseIds(caseIdsProp)
    resetSession()
    void refreshSessionList()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when modal opens / scope changes
  }, [open, caseIdsProp.join(','), pid])

  async function refreshSessionList() {
    if (!Number.isFinite(pid)) return
    setHistoryLoading(true)
    try {
      const rows = await apiJson<AgentSessionSummary[]>(
        `/api/v1/projects/${pid}/api-case-edit-agent/sessions`,
      )
      setSessionList(rows)
    } catch {
      /* ignore */
    } finally {
      setHistoryLoading(false)
    }
  }

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

  async function startSession(prompt: string) {
    if (caseIds.length === 0) {
      message.warning('请先勾选要修改的用例')
      return
    }
    if (caseIds.length > MAX_CASES) {
      message.warning(`一次最多修订 ${MAX_CASES} 条，请收窄选择`)
      return
    }
    const userText = prompt.trim()
    if (!userText) {
      message.warning('请输入修改说明')
      return
    }
    setBusy(true)
    setLogBubbles([])
    logSeqRef.current = 0
    setNotices([])
    setPendingUser(userText)
    try {
      const created = await apiJson<EditSession>(
        `/api/v1/projects/${pid}/api-case-edit-agent/sessions`,
        {
          method: 'POST',
          body: JSON.stringify({
            prompt: userText,
            case_ids: caseIds,
          }),
        },
      )
      setSession(created)
      setEngineExpired(false)
      if (created.case_ids?.length) setCaseIds(created.case_ids)
      ingestEvents(created.events || [])
      try {
        await apiSse(
          `/api/v1/projects/${pid}/api-case-edit-agent/sessions/${created.id}/events`,
          { method: 'GET' },
          (ev: SseEvent) => {
            ingestEvents([ev as Record<string, unknown>])
          },
        )
      } catch {
        /* stream closed */
      }
      const refreshed = await apiJson<EditSession>(
        `/api/v1/projects/${pid}/api-case-edit-agent/sessions/${created.id}`,
      )
      setSession(refreshed)
      setPendingUser(null)
      void refreshSessionList()
      if (refreshed.status === 'error') {
        message.error(refreshed.error_message || '生成修订草稿失败')
        pushNotice('system', refreshed.error_message || '生成修订草稿失败')
      } else if (refreshed.status === 'awaiting_confirm') {
        const n = refreshed.plan?.revisions?.filter((r) => !r.skipped).length ?? 0
        pushNotice(
          'assistant',
          `左侧可预览修订草稿（可应用 ${n} 条）。可继续对话修改，确认后才会写回用例。`,
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
      const updated = await apiJson<EditSession>(
        `/api/v1/projects/${pid}/api-case-edit-agent/sessions/${session.id}/messages`,
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
    if (busy || !text) return
    if (canStart) {
      pinToBottom()
      setInput('')
      await startSession(text)
      return
    }
    if (canChat) {
      pinToBottom()
      setInput('')
      await sendRevise(text)
    }
  }

  async function confirmAction(action: 'confirm' | 'cancel') {
    if (!session) return
    if (action === 'confirm' && selectedApplyIds.length === 0) {
      message.warning('请至少勾选一条要应用的修订')
      return
    }
    setBusy(true)
    try {
      const result = await apiJson<EditSession>(
        `/api/v1/projects/${pid}/api-case-edit-agent/sessions/${session.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            case_ids: action === 'confirm' ? selectedApplyIds : undefined,
          }),
        },
      )
      setSession(result)
      ingestEvents(result.events || [])
      void refreshSessionList()
      if (action === 'confirm') {
        if (result.applied_case_ids?.length) {
          message.success(`已写回 ${result.applied_case_ids.length} 条用例`)
          pushNotice('assistant', `已确认并写回 ${result.applied_case_ids.length} 条用例。`)
          onApplied?.()
          onClose()
        } else {
          const tip =
            result.error_message ||
            '未写回任何用例，请确认已勾选修订项，或重新生成修订后再确认'
          message.error(tip)
          pushNotice('system', tip)
        }
      } else if (action === 'cancel') {
        message.info('已取消')
        pushNotice('assistant', '已取消本次会话，未写回用例。可点「新会话」重新开始。')
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
      const s = await apiJson<EditSession>(
        `/api/v1/projects/${pid}/api-case-edit-agent/sessions/${id}`,
      )
      setSession(s)
      if (s.case_ids?.length) setCaseIds(s.case_ids)
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
      await apiJson(`/api/v1/projects/${pid}/api-case-edit-agent/sessions/${id}`, {
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

  const canSend = !busy && !finished && ((canStart && !!input.trim()) || (canChat && !!input.trim()))
  const warnings = session?.plan?.warnings || []

  const columns: ColumnsType<CaseRevision> = [
    {
      title: '',
      width: 48,
      render: (_, row) => (
        <Checkbox
          disabled={!awaiting || !!row.skipped}
          checked={!row.skipped && selectedApplyIds.includes(row.case_id)}
          onChange={(e) => {
            setSelectedApplyIds((prev) =>
              e.target.checked
                ? [...prev.filter((x) => x !== row.case_id), row.case_id]
                : prev.filter((x) => x !== row.case_id),
            )
          }}
        />
      ),
    },
    {
      title: '用例',
      dataIndex: 'name',
      ellipsis: true,
      render: (name: string, row) => (
        <Space size={4} wrap>
          <span>#{row.case_id}</span>
          <span>{name || snapshotById.get(row.case_id)?.name || '—'}</span>
          {row.skipped ? <Tag>跳过</Tag> : null}
        </Space>
      ),
    },
    {
      title: '变更',
      dataIndex: 'changed_fields',
      width: 160,
      render: (fields: string[] | undefined, row) =>
        row.skipped ? (
          <Typography.Text type="secondary">{row.skip_reason || '—'}</Typography.Text>
        ) : (
          <Space size={[4, 4]} wrap>
            {(fields || []).map((f) => (
              <Tag key={f}>{f}</Tag>
            ))}
          </Space>
        ),
    },
    {
      title: '说明',
      dataIndex: 'summary',
      ellipsis: true,
    },
  ]

  return (
    <div className="api-scenario-agent-split" style={{ height: '100%', minHeight: 0 }}>
      <aside className="api-scenario-agent-plan card" style={{ margin: 0 }}>
        <div className="api-scenario-agent-chat-toolbar">
          <Typography.Text strong>{session?.plan?.title || '修订草稿'}</Typography.Text>
          <Space size="small">
            <Button
              size="small"
              type="primary"
              disabled={!awaiting || selectedApplyIds.length === 0}
              loading={busy}
              onClick={() => void confirmAction('confirm')}
            >
              确认应用勾选
            </Button>
            <Button size="small" disabled={!awaiting} onClick={() => void confirmAction('cancel')}>
              取消会话
            </Button>
          </Space>
        </div>
        <div className="api-scenario-agent-plan-body">
          {caseIds.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                范围（{caseIds.length}）
              </Typography.Text>
              <div style={{ marginTop: 6 }}>
                <Space size={[4, 4]} wrap>
                  {caseIds.map((id) => (
                    <Tag key={id}>#{id}</Tag>
                  ))}
                </Space>
              </div>
            </div>
          )}
          {session?.plan?.summary && (
            <p className="api-scenario-agent-summary">{session.plan.summary}</p>
          )}
          {warnings.length > 0 && (
            <Alert
              className="api-scenario-agent-warnings"
              type="warning"
              showIcon
              message={
                <ul>
                  {warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              }
            />
          )}
          {revisions.length > 0 ? (
            <Table<CaseRevision>
              size="small"
              rowKey="case_id"
              pagination={false}
              columns={columns}
              dataSource={revisions}
              expandable={{
                expandedRowRender: (row) => {
                  const base = snapshotById.get(row.case_id)
                  const draft = row.draft || {}
                  const fields = row.changed_fields?.length
                    ? row.changed_fields
                    : ['headers', 'body', 'assertions', 'extractors', 'query']
                  return (
                    <Collapse
                      size="small"
                      items={fields.map((f) => ({
                        key: f,
                        label: f,
                        children: (
                          <DiffBlock
                            label={f}
                            before={(base as Record<string, unknown> | undefined)?.[f]}
                            after={(draft as Record<string, unknown>)[f]}
                          />
                        ),
                      }))}
                    />
                  )
                },
              }}
            />
          ) : (
            <div className="api-scenario-agent-plan-empty">
              {caseIds.length
                ? '在右侧说明要如何批量修改这些用例，生成草稿后在此预览与勾选'
                : '请勾选用例后再说明修改意图'}
            </div>
          )}
        </div>
        {session?.applied_case_ids?.length ? (
          <Typography.Paragraph className="api-scenario-agent-plan-foot">
            已写回 {session.applied_case_ids.length} 条用例
          </Typography.Paragraph>
        ) : null}
      </aside>

      <section
        className={`api-scenario-agent-chat card${chatCollapsed ? ' is-collapsed' : ''}`}
        style={{ margin: 0 }}
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
                ? caseIds.length
                  ? '说明如何批量修改，例如：统一加 Authorization: Bearer {{token}}；断言补 $.code=0'
                  : '请先勾选用例后再说明修改意图'
                : canChat
                  ? '继续修改草稿，例如：去掉异常用例的 Authorization；正向再补 code 断言'
                  : '当前会话已结束，请点「新会话」'
            }
            autoSize={{ minRows: 2, maxRows: 5 }}
            disabled={busy || finished || (!canStart && !canChat) || (canStart && caseIds.length === 0)}
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
              {caseIds.length > 0 && canStart ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  已选 {caseIds.length} 条
                </Typography.Text>
              ) : null}
            </div>

            <Tooltip title={canStart ? '生成修订草稿' : '发送'}>
              <Button
                type="primary"
                shape="circle"
                size="small"
                className="api-scenario-agent-send-btn"
                icon={<SendOutlined />}
                loading={busy}
                disabled={!canSend || (canStart && caseIds.length === 0)}
                onClick={() => void onSend()}
              />
            </Tooltip>
          </div>
        </div>
      </section>
    </div>
  )
}
