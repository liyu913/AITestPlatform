import {
  CloudServerOutlined,
  DesktopOutlined,
  FileTextOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  App,
  Badge,
  Button,
  Input,
  Modal,
  Popover,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson, apiSse, ApiError, type SseEvent } from '../api/client'
import { eventScreenshotKey, ExploreShotRail } from '../components/ExploreShotThumb'
import PageBanner from './PageBanner'
import {
  AgentChatCollapseButton,
  useAgentChatCollapsed,
} from '../components/AgentChatCollapse'
import AgentSessionHistory, {
  type AgentSessionSummary,
} from '../components/AgentSessionHistory'
import { useStickToBottom } from '../hooks/useStickToBottom'
type TestCase = { id: number; title: string; steps?: Array<{ action: string; expected: string }> }
type UiEnv = {
  id: number
  name: string
  is_default: boolean
  base_url: string
  browsers?: string[]
  browser?: string
}
type PageRes<T> = { items: T[]; total: number }

type UiStep = {
  action?: string
  intent?: string
  selector?: string
  value?: string
  assert?: string
  driver?: 'playwright' | 'midscene' | string
  reason?: string
  prompt?: string
  locate?: string
  index?: number
  case_id?: string
}

type MatrixStep = {
  action?: string
  target?: string
  value?: string | number | boolean
  driver?: 'playwright' | 'midscene' | string
  ready?: boolean
  placeholder?: string
  role?: string
  name?: string
  selector?: string
  prompt?: string
  locate?: string
}

type MatrixRow = {
  id?: string
  name?: string
  start_url?: string
  preconditions?: string
  steps?: Array<string | MatrixStep>
  data?: string
  expected?: string
  ready?: boolean
  has_assert?: boolean
}

type ExploreReport = {
  case_name?: string
  matrix?: MatrixRow[]
  steps?: UiStep[]
  midscene_checklist?: Array<Record<string, unknown>>
  summary?: {
    total_steps?: number
    playwright_steps?: number
    midscene_steps?: number
  }
  captcha?: Record<string, unknown> | null
}

type CaseScript = {
  id?: string
  name?: string
  ready?: boolean
  code?: string
  start_url?: string
}

type ScenarioPlan = {
  goal?: string
  start_path?: string
  checklist?: string[]
  case_name?: string
  warnings?: string[]
  matrix?: MatrixRow[]
  case_scripts?: CaseScript[]
  saved_ui_case_ids?: number[]
  explore_report?: ExploreReport
  engine?: 'midscene' | 'playwright' | string
}

type UiAgentSession = {
  id: number
  status: string
  prompt: string
  testcase_id: number | null
  env_id: number | null
  browser: string
  headed: boolean
  auth_mode?: string | null
  messages: Array<{ role: string; content: string }>
  plan: ScenarioPlan | null
  matrix: MatrixRow[]
  explore_report: ExploreReport | null
  trace: Array<Record<string, unknown>>
  result_steps: UiStep[]
  playwright_code: string
  has_credentials: boolean
  events: Array<Record<string, unknown>>
  dry_run_status: string | null
  dry_run_error: string
  error_message: string
  saved_ui_case_id: number | null
  run_id: number | null
}

type ChatBubble = {
  id: string
  role: 'user' | 'assistant' | 'system' | 'log'
  content: string
  screenshotKey?: string
}

const WELCOME =
  '你好，我是 UI 自动化 Agent。\n\n1. 探索引擎固定为 playwright+midscene\n2. 可选：点下方图标选择功能用例 / UI 环境；桌面图标切换有头/无头\n3. 用自然语言描述要自动化的流程；账号密码请直接写在对话里（例如：账号 demo 密码 secret）\n4. 先探索界面收集定位，再一次生成多条用例脚本；左侧可看报告与脚本\n5. 确认后才会写入 UI 用例'

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
  if (t === 'need_human') return `[人工协助] ${String(ev.message || '请在浏览器操作或对话中回复')}`
  if (t === 'plan') return '[plan] 已收到探索计划'
  if (t === 'matrix') {
    const matrix = Array.isArray(ev.matrix) ? ev.matrix : []
    return `[matrix] 用例矩阵 ${matrix.length} 条`
  }
  if (t === 'explore_report') {
    const report = ev.report as ExploreReport | undefined
    const s = report?.summary
    if (s) {
      return `[report] 步骤 ${s.total_steps ?? 0}（PW ${s.playwright_steps ?? 0} / Mid ${s.midscene_steps ?? 0}）`
    }
    return '[report] 已生成探索报告'
  }
  if (t === 'script_review') {
    const summary = String(ev.summary || '')
    const fixed = Number(ev.fixed_count || 0)
    if (summary) return `[review] ${summary}`
    return fixed > 0 ? `[review] 已修正 ${fixed} 条脚本` : '[review] 脚本自审完成'
  }
  if (t === 'trial_start') {
    return `[trial] 开始试跑 ${String(ev.case_id || '')}${ev.headed ? '（有头）' : '（无头）'}`
  }
  if (t === 'trial_done') {
    const st = String(ev.status || '')
    const err = String(ev.error || '')
    return err
      ? `[trial] ${String(ev.case_id || '')} ${st}：${err}`
      : `[trial] ${String(ev.case_id || '')} ${st}`
  }
  if (t === 'interrupted') return `[wait] ${String(ev.message || '等待确认')}`
  if (t === 'done') return `[done] status=${String(ev.status || '')}`
  if (t === 'error') return `[error] ${String(ev.message || '')}`
  return null
}

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    running: '探索中',
    awaiting_human: '待人工协助',
    awaiting_confirm: '待确认',
    confirmed: '已确认',
    cancelled: '已取消',
    error: '失败',
    done: '完成',
  }
  return map[s] || s
}

function roleLabel(role: ChatBubble['role']): string {
  if (role === 'user') return '你'
  if (role === 'system') return '系统'
  if (role === 'log') return '日志'
  return 'Agent'
}

function statusColor(s: string): string {
  if (s === 'confirmed' || s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'awaiting_confirm' || s === 'running' || s === 'awaiting_human') return 'processing'
  return 'default'
}

function lastSessionStorageKey(projectId: number): string {
  return `ui-scenario-agent:last:${projectId}`
}

function readLastSessionId(projectId: number): number | null {
  try {
    const key = lastSessionStorageKey(projectId)
    const raw = localStorage.getItem(key) || sessionStorage.getItem(key)
    if (!raw) return null
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

function writeLastSessionId(projectId: number, id: number | null): void {
  const key = lastSessionStorageKey(projectId)
  try {
    if (id == null) {
      localStorage.removeItem(key)
      sessionStorage.removeItem(key)
      return
    }
    localStorage.setItem(key, String(id))
    sessionStorage.removeItem(key)
  } catch {
    /* ignore */
  }
}

export default function UiScenarioAgent() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const [testcaseId, setTestcaseId] = useState<number | undefined>()
  const [envId, setEnvId] = useState<number | undefined>()
  const [browser, setBrowser] = useState<string | undefined>()
  const [headed, setHeaded] = useState(true)
  /** follow = env inject; none = login / skip inject */
  const [authMode, setAuthMode] = useState<'follow' | 'none'>('follow')
  const [input, setInput] = useState('')
  const [session, setSession] = useState<UiAgentSession | null>(null)
  const [scriptBound, setScriptBound] = useState<{ id: number | null; code: string; text: string }>({
    id: null,
    code: '',
    text: '',
  })
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [notices, setNotices] = useState<ChatBubble[]>([])
  const [logBubbles, setLogBubbles] = useState<ChatBubble[]>([])
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [casePopoverOpen, setCasePopoverOpen] = useState(false)
  const [envPopoverOpen, setEnvPopoverOpen] = useState(false)
  const [caseFilter, setCaseFilter] = useState('')
  const [sessionList, setSessionList] = useState<AgentSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null)
  const [scriptModal, setScriptModal] = useState<{ id: string; name: string; code: string } | null>(
    null,
  )
  const [trialHeaded, setTrialHeaded] = useState(true)
  const [trialBusy, setTrialBusy] = useState(false)
  /** False until last-session restore attempt finishes for current project. */
  const [sessionReady, setSessionReady] = useState(false)
  const { collapsed: chatCollapsed, toggleCollapsed: toggleChatCollapsed } = useAgentChatCollapsed(
    Number.isFinite(pid) ? `ui-agent-chat-collapsed:${pid}` : 'ui-agent-chat-collapsed',
  )

  const logSeqRef = useRef(0)
  const pollRef = useRef<number | null>(null)
  const seenLogKeysRef = useRef<Set<string>>(new Set())
  const restoredPidRef = useRef<number | null>(null)

  const cases = useQuery({
    queryKey: ['test-cases', pid, 'ui-agent'],
    queryFn: () =>
      apiJson<PageRes<TestCase>>(`/api/v1/projects/${pid}/test-cases/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const envs = useQuery({
    queryKey: ['ui-envs', pid],
    queryFn: () => apiJson<PageRes<UiEnv>>(`/api/v1/projects/${pid}/ui-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const selectedEnv =
    (envs.data?.items ?? []).find((e) => e.id === envId) ??
    (envs.data?.items ?? []).find((e) => e.is_default) ??
    envs.data?.items?.[0]

  const envName = useMemo(() => {
    if (!selectedEnv) return null
    return selectedEnv.is_default ? `${selectedEnv.name}（默认）` : selectedEnv.name
  }, [selectedEnv])

  const browserOptions = selectedEnv?.browsers?.length
    ? selectedEnv.browsers
    : [selectedEnv?.browser || 'chromium']

  const filteredCases = useMemo(() => {
    const q = caseFilter.trim().toLowerCase()
    const items = cases.data?.items ?? []
    if (!q) return items
    return items.filter((c) => c.title.toLowerCase().includes(q))
  }, [cases.data, caseFilter])

  const awaiting = session?.status === 'awaiting_confirm' && !busy
  const waitingHuman = session?.status === 'awaiting_human'
  const finished =
    session != null && ['confirmed', 'cancelled', 'done'].includes(session.status)
  const canStart = !session && !busy && sessionReady
  const canChat =
    awaiting || waitingHuman || (session?.status === 'error' && !busy)
  const canEditEnv = canStart || awaiting || (session?.status === 'error' && !busy)
  const exploring = session?.status === 'running' || waitingHuman
  const restoring = !sessionReady && !session

  async function refreshSessionList() {
    if (!Number.isFinite(pid)) return
    const list = await apiJson<AgentSessionSummary[]>(
      `/api/v1/projects/${pid}/ui-scenario-agent/sessions`,
    )
    setSessionList(list)
  }

  const caseScripts: CaseScript[] = useMemo(() => {
    const fromPlan = session?.plan?.case_scripts
    return Array.isArray(fromPlan) ? fromPlan : []
  }, [session?.plan?.case_scripts])

  const activeCase = useMemo(() => {
    if (!caseScripts.length) return null
    const hit = caseScripts.find((c) => c.id === selectedCaseId)
    return hit || caseScripts.find((c) => c.ready && c.code) || caseScripts[0]
  }, [caseScripts, selectedCaseId])

  const serverScript = (activeCase?.code || session?.playwright_code || '').trim()
    ? activeCase?.code || session?.playwright_code || ''
    : session?.playwright_code || ''
  const scriptDraft =
    scriptBound.id === (session?.id ?? null) && scriptBound.code === serverScript
      ? scriptBound.text
      : serverScript

  useEffect(() => {
    if (!caseScripts.length) {
      setSelectedCaseId(null)
      return
    }
    if (selectedCaseId && caseScripts.some((c) => c.id === selectedCaseId)) return
    const firstReady = caseScripts.find((c) => c.ready && c.code)
    setSelectedCaseId(firstReady?.id || caseScripts[0]?.id || null)
  }, [caseScripts, selectedCaseId])

  useEffect(() => {
    if (!Number.isFinite(pid)) return
    let cancelled = false
    restoredPidRef.current = null
    setSessionReady(false)
    stopPoll()
    setSession(null)
    setSessionList([])
    setLogBubbles([])
    logSeqRef.current = 0
    seenLogKeysRef.current = new Set()
    setNotices([])
    setPendingUser(null)
    setInput('')
    ;(async () => {
      setHistoryLoading(true)
      try {
        const list = await apiJson<AgentSessionSummary[]>(
          `/api/v1/projects/${pid}/ui-scenario-agent/sessions`,
        )
        if (!cancelled) setSessionList(list)
      } catch {
        /* best-effort */
      } finally {
        if (!cancelled) setHistoryLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
    // stopPoll is stable enough for mount/pid switch cleanup
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run when project changes
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

  const shotKeys = useMemo(
    () => logBubbles.map((m) => m.screenshotKey).filter((key): key is string => Boolean(key)),
    [logBubbles],
  )

  const {
    containerRef: messagesRef,
    onScroll: updateStickToBottom,
    pinToBottom: pinChatToBottom,
  } = useStickToBottom([displayMessages, busy])

  useEffect(() => {
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [])

  function appendLog(line: string, key?: string, screenshotKey?: string) {
    const dedupeKey = key || line
    if (seenLogKeysRef.current.has(dedupeKey)) return
    seenLogKeysRef.current.add(dedupeKey)
    logSeqRef.current += 1
    const id = `log-${logSeqRef.current}`
    setLogBubbles((prev) => [...prev, { id, role: 'log', content: line, screenshotKey }])
  }

  function ingestEvents(events: Array<Record<string, unknown>>) {
    events.forEach((ev, i) => {
      const line = formatEventLine(ev)
      if (!line) return
      const key = `${String(ev.ts || '')}|${String(ev.type || '')}|${String(ev.node || '')}|${String(ev.message || '')}|${i}`
      appendLog(line, key, eventScreenshotKey(ev))
    })
  }

  function pushNotice(role: Exclude<ChatBubble['role'], 'log'>, content: string) {
    setNotices((prev) => [
      ...prev,
      { id: `n-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, role, content },
    ])
  }

  function stopPoll() {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  async function waitUntilSettled(sessionId: number): Promise<UiAgentSession> {
    stopPoll()
    let ticks = 0
    let baselineEventCount: number | null = null
    return new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const refreshed = await apiJson<UiAgentSession>(
            `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${sessionId}`,
          )
          setSession(refreshed)
          if (refreshed.status === 'awaiting_human') {
            setBusy(false)
          } else if (refreshed.status === 'running') {
            setBusy(true)
          }
          const eventCount = refreshed.events?.length ?? 0
          if (baselineEventCount == null) baselineEventCount = eventCount
          ingestEvents(refreshed.events || [])
          ticks += 1
          if (
            refreshed.status === 'running' &&
            ticks === 15 &&
            eventCount <= (baselineEventCount ?? 0)
          ) {
            appendLog(
              '[hint] 仍在 running：请确认已启动 Celery Worker（uv run celery -A app.tasks.celery_app worker -l info -Q ui,celery）',
              'hint-celery',
            )
          }
          if (
            ['awaiting_confirm', 'confirmed', 'cancelled', 'error', 'done'].includes(
              refreshed.status,
            )
          ) {
            stopPoll()
            setStopping(false)
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

  async function startPlan(prompt: string) {
    if (!envs.data?.items?.length) {
      message.warning('请先配置 UI 环境')
      setEnvPopoverOpen(true)
      return
    }
    const userText = prompt.trim() || '请根据所选功能用例生成 UI 脚本'
    if (!prompt.trim() && testcaseId == null) {
      message.warning('请描述流程，或选择一个功能用例')
      return
    }
    setBusy(true)
    setLogBubbles([])
    logSeqRef.current = 0
    seenLogKeysRef.current = new Set()
    setNotices([])
    setPendingUser(userText)
    try {
      const created = await apiJson<UiAgentSession>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions`,
        {
          method: 'POST',
          body: JSON.stringify({
            prompt: prompt.trim(),
            testcase_id: testcaseId ?? null,
            env_id: envId ?? selectedEnv?.id ?? null,
            browser: browser || null,
            headed,
            engine: 'midscene',
            max_steps: 25,
            auth_mode: authMode === 'none' ? 'none' : null,
          }),
        },
      )
      setSession(created)
      if (created.env_id != null) setEnvId(created.env_id)
      if (created.testcase_id != null) setTestcaseId(created.testcase_id)
      if (created.browser) setBrowser(created.browser)
      ingestEvents(created.events || [])

      // SSE for live logs (may end early); poll until settled
      const ssePromise = apiSse(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${created.id}/events`,
        { method: 'GET' },
        (ev: SseEvent) => {
          ingestEvents([ev as Record<string, unknown>])
        },
      ).catch(() => undefined)

      const refreshed = await waitUntilSettled(created.id)
      await ssePromise
      setPendingUser(null)
      void refreshSessionList()
      if (refreshed.status === 'error') {
        message.error(refreshed.error_message || '探索失败')
        pushNotice('system', refreshed.error_message || '探索失败')
      } else if (refreshed.status === 'awaiting_confirm') {
        pushNotice(
          'assistant',
          '左侧可编辑脚本。可继续对话修改，确认后才会写入 UI 用例。',
        )
      }
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
      setPendingUser(null)
    } finally {
      stopPoll()
      setBusy(false)
      setStopping(false)
    }
  }

  async function sendRevise(content: string) {
    if (!session) return
    const wasWaitingHuman = session.status === 'awaiting_human'
    setBusy(true)
    setPendingUser(content)
    try {
      const updated = await apiJson<UiAgentSession>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${session.id}/messages`,
        {
          method: 'POST',
          body: JSON.stringify({ content }),
        },
      )
      setSession(updated)
      ingestEvents(updated.events || [])
      if (wasWaitingHuman) {
        // Parent startPlan/waitUntilSettled keeps polling; just hand reply to worker.
        setPendingUser(null)
        setBusy(true)
        void refreshSessionList()
        return
      }
      if (updated.status === 'running') {
        const settled = await waitUntilSettled(updated.id)
        setPendingUser(null)
        void refreshSessionList()
        if (settled.status === 'error') {
          pushNotice('system', settled.error_message || '重新探索失败')
        }
      } else {
        setPendingUser(null)
        void refreshSessionList()
        if (updated.status === 'error') {
          pushNotice('system', updated.error_message || '修改失败')
        }
      }
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
      setPendingUser(null)
    } finally {
      if (!wasWaitingHuman) {
        stopPoll()
        setBusy(false)
      }
    }
  }

  async function onSend() {
    const text = input.trim()
    if (busy && !waitingHuman) return
    if (canStart) {
      pinChatToBottom()
      setInput('')
      await startPlan(text)
      return
    }
    if (canChat) {
      if (!text) return
      pinChatToBottom()
      setInput('')
      await sendRevise(text)
    }
  }

  async function stopExplore() {
    if (!session || (session.status !== 'running' && session.status !== 'awaiting_human')) return
    if (stopping) return
    setStopping(true)
    try {
      const updated = await apiJson<UiAgentSession>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${session.id}/stop`,
        { method: 'POST' },
      )
      setSession(updated)
      ingestEvents(updated.events || [])
      void refreshSessionList()
      pushNotice('assistant', updated.error_message || '已手动停止探索，可继续描述后重试。')
      message.info('已停止探索')
      // waitUntilSettled will resolve on next poll tick when status is error
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
      setStopping(false)
    }
  }

  async function waitUntilTrialSettled(sessionId: number): Promise<UiAgentSession> {
    stopPoll()
    return new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const refreshed = await apiJson<UiAgentSession>(
            `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${sessionId}`,
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

  async function trialRun(opts?: { caseId?: string; code?: string }) {
    if (!session) return
    const caseId = opts?.caseId || scriptModal?.id || selectedCaseId || ''
    const code = (opts?.code ?? scriptModal?.code ?? scriptDraft).trim()
    if (!code) {
      message.warning('没有可试跑的脚本')
      return
    }
    setTrialBusy(true)
    try {
      const started = await apiJson<UiAgentSession>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${session.id}/trial-run`,
        {
          method: 'POST',
          body: JSON.stringify({
            case_id: caseId,
            headed: trialHeaded,
            script: code,
          }),
        },
      )
      setSession(started)
      ingestEvents(started.events || [])
      const settled = await waitUntilTrialSettled(started.id)
      if (settled.dry_run_status === 'passed') {
        message.success(`试跑 ${caseId || ''} 通过`)
        pushNotice('assistant', `试跑 ${caseId || '脚本'} 通过。`)
      } else {
        const err = settled.dry_run_error || '试跑失败'
        message.error(err)
        pushNotice('system', `试跑 ${caseId || '脚本'}：${err}`)
      }
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
    } finally {
      stopPoll()
      setTrialBusy(false)
    }
  }

  async function confirmAction(action: 'confirm' | 'cancel') {
    if (!session) return
    setBusy(true)
    try {
      const result = await apiJson<UiAgentSession>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${session.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({
            action,
            script: action === 'confirm' ? scriptDraft : undefined,
            status: 'ready',
          }),
        },
      )
      setSession(result)
      ingestEvents(result.events || [])
      void refreshSessionList()
      if (action === 'confirm' && result.saved_ui_case_id) {
        qc.invalidateQueries({ queryKey: ['ui-cases', pid] })
        const multi = result.plan?.saved_ui_case_ids
        const ids =
          Array.isArray(multi) && multi.length
            ? multi.map((n) => `#${n}`).join('、')
            : `#${result.saved_ui_case_id}`
        message.success(`已写入 UI 用例 ${ids}`)
        pushNotice('assistant', `已确认并写入 UI 用例 ${ids}。`)
      } else if (action === 'cancel') {
        message.info('已取消')
        pushNotice('assistant', '已取消本次会话，未写入 UI 用例。可点「新会话」重新开始。')
      } else if (result.status === 'error') {
        message.error(result.error_message || '确认失败')
        pushNotice('system', result.error_message || '确认失败')
      }
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
    } finally {
      setBusy(false)
    }
  }

  function resetSession() {
    stopPoll()
    pinChatToBottom()
    setSession(null)
    setLogBubbles([])
    logSeqRef.current = 0
    seenLogKeysRef.current = new Set()
    setInput('')
    setPendingUser(null)
    setNotices([])
    setStopping(false)
    setAuthMode('follow')
    setSessionReady(true)
    if (Number.isFinite(pid)) {
      writeLastSessionId(pid, null)
    }
  }

  async function loadSession(id: number, opts?: { quiet?: boolean; force?: boolean }) {
    if (!opts?.force && (busy || session?.id === id)) return
    if (opts?.force && session?.id === id) return
    stopPoll()
    pinChatToBottom()
    setBusy(true)
    setHistoryLoading(true)
    setLogBubbles([])
    logSeqRef.current = 0
    seenLogKeysRef.current = new Set()
    setNotices([])
    setPendingUser(null)
    try {
      const s = await apiJson<UiAgentSession>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${id}`,
      )
      setSession(s)
      if (s.env_id != null) setEnvId(s.env_id)
      if (s.testcase_id != null) setTestcaseId(s.testcase_id)
      else setTestcaseId(undefined)
      if (s.browser) setBrowser(s.browser)
      setHeaded(Boolean(s.headed))
      setTrialHeaded(Boolean(s.headed))
      setAuthMode(s.auth_mode === 'none' ? 'none' : 'follow')
      ingestEvents(s.events || [])
      if (!opts?.quiet) {
        if (['confirmed', 'cancelled', 'done'].includes(s.status)) {
          pushNotice('assistant', '已打开历史会话（只读）。可点「新会话」重新开始。')
        } else if (s.status === 'error') {
          pushNotice('assistant', '已打开失败会话，可继续描述并重新探索。')
        } else if (s.status === 'awaiting_confirm') {
          pushNotice('assistant', '已打开历史会话，可继续修改或确认写入。')
        } else if (s.status === 'awaiting_human') {
          pushNotice(
            'assistant',
            '探索暂停等待人工协助：浏览器应仍打开，可输入验证码或「继续」。',
          )
        } else if (s.status === 'running') {
          pushNotice('assistant', '会话仍在探索中，正在同步进度…')
        }
      }
      if (s.status === 'running' || s.status === 'awaiting_human') {
        void waitUntilSettled(s.id).finally(() => {
          stopPoll()
          void refreshSessionList()
        })
      }
    } catch (e) {
      if (!opts?.quiet) message.error(errText(e))
    } finally {
      setBusy(false)
      setHistoryLoading(false)
    }
  }

  async function openHistorySession(id: number) {
    await loadSession(id)
  }

  async function deleteHistorySession(id: number) {
    if (busy) return
    setBusy(true)
    try {
      await apiJson(`/api/v1/projects/${pid}/ui-scenario-agent/sessions/${id}`, {
        method: 'DELETE',
      })
      const list = await apiJson<AgentSessionSummary[]>(
        `/api/v1/projects/${pid}/ui-scenario-agent/sessions`,
      )
      setSessionList(list)
      if (session?.id === id) {
        if (list[0]) {
          setBusy(false)
          await loadSession(list[0].id, { quiet: true, force: true })
          return
        }
        resetSession()
      }
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!Number.isFinite(pid) || session?.id == null) return
    writeLastSessionId(pid, session.id)
  }, [pid, session?.id])

  useEffect(() => {
    if (!Number.isFinite(pid) || historyLoading) return
    if (restoredPidRef.current === pid) return
    restoredPidRef.current = pid
    let cancelled = false

    async function restoreLastSession() {
      const saved = readLastSessionId(pid)
      const fallbackId = sessionList[0]?.id ?? null
      const candidates = [
        ...(saved != null ? [saved] : []),
        ...(fallbackId != null && fallbackId !== saved ? [fallbackId] : []),
      ]

      for (const targetId of candidates) {
        if (cancelled) return
        try {
          const s = await apiJson<UiAgentSession>(
            `/api/v1/projects/${pid}/ui-scenario-agent/sessions/${targetId}`,
          )
          if (cancelled) return
          setSession(s)
          if (s.env_id != null) setEnvId(s.env_id)
          if (s.testcase_id != null) setTestcaseId(s.testcase_id)
          else setTestcaseId(undefined)
          if (s.browser) setBrowser(s.browser)
          setHeaded(Boolean(s.headed))
          setTrialHeaded(Boolean(s.headed))
          setAuthMode(s.auth_mode === 'none' ? 'none' : 'follow')
          setLogBubbles([])
          logSeqRef.current = 0
          seenLogKeysRef.current = new Set()
          setNotices([])
          setPendingUser(null)
          ingestEvents(s.events || [])
          writeLastSessionId(pid, s.id)
          if (s.status === 'running' || s.status === 'awaiting_human') {
            void waitUntilSettled(s.id).finally(() => {
              stopPoll()
              void refreshSessionList()
            })
          }
          break
        } catch {
          if (saved === targetId) {
            writeLastSessionId(pid, null)
          }
        }
      }
      if (!cancelled) setSessionReady(true)
    }

    void restoreLastSession()
    return () => {
      cancelled = true
    }
    // Intentionally only after list fetch for this pid settles
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, historyLoading, sessionList])

  const canSend =
    sessionReady &&
    !finished &&
    (canStart || (canChat && !!input.trim())) &&
    (!busy || waitingHuman)

  const casePicker = (
    <div className="api-scenario-agent-picker">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        功能用例（可选）
      </Typography.Text>
      <Input
        size="small"
        allowClear
        placeholder="搜索用例标题"
        value={caseFilter}
        onChange={(e) => setCaseFilter(e.target.value)}
        disabled={!canStart}
        style={{ marginBottom: 8 }}
      />
      <div className="api-scenario-agent-picker-list">
        <Space direction="vertical" style={{ width: '100%' }} size={4}>
          <Button
            size="small"
            type={!testcaseId ? 'primary' : 'default'}
            block
            disabled={!canStart}
            onClick={() => {
              setTestcaseId(undefined)
              setCasePopoverOpen(false)
            }}
          >
            不绑定功能用例
          </Button>
          {filteredCases.map((c) => (
            <Button
              key={c.id}
              size="small"
              type={testcaseId === c.id ? 'primary' : 'default'}
              block
              disabled={!canStart}
              className="api-scenario-agent-picker-item"
              onClick={() => {
                setTestcaseId(c.id)
                setCasePopoverOpen(false)
              }}
            >
              <span className="api-scenario-agent-picker-item-title">{c.title}</span>
            </Button>
          ))}
          {filteredCases.length === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              无匹配用例
            </Typography.Text>
          )}
        </Space>
      </div>
    </div>
  )

  const envPicker = (
    <div className="api-scenario-agent-picker api-scenario-agent-picker--env">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        UI 环境
      </Typography.Text>
      <Select
        allowClear
        placeholder="选择环境"
        value={envId}
        onChange={(v) => {
          setEnvId(v)
          setBrowser(undefined)
        }}
        style={{ width: '100%', marginBottom: 8 }}
        options={(envs.data?.items ?? []).map((e) => ({
          value: e.id,
          label: e.is_default ? `${e.name}（默认）` : e.name,
        }))}
        disabled={!canEditEnv}
        getPopupContainer={(n) => n.parentElement ?? document.body}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        浏览器
      </Typography.Text>
      <Select
        allowClear
        placeholder="浏览器"
        value={browser}
        onChange={(v) => {
          setBrowser(v)
          setEnvPopoverOpen(false)
        }}
        style={{ width: '100%' }}
        options={browserOptions.map((b) => ({ value: b, label: b }))}
        disabled={!canEditEnv}
        getPopupContainer={(n) => n.parentElement ?? document.body}
      />
      {selectedEnv?.base_url && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          {selectedEnv.base_url}
        </Typography.Paragraph>
      )}
    </div>
  )

  const selectedCaseTitle = (cases.data?.items ?? []).find((c) => c.id === testcaseId)?.title

  return (
    <div className="api-scenario-agent-page">
      <PageBanner title="UI 自动化 Agent" />
      <div className="api-scenario-agent-split">
        <aside className="api-scenario-agent-plan card">
          <div className="api-scenario-agent-chat-toolbar">
            <Typography.Text strong>
              {session?.plan?.case_name || session?.plan?.goal || '探索计划 / 脚本'}
            </Typography.Text>
            {awaiting && (
              <Space size="small">
                <Button
                  size="small"
                  type="primary"
                  disabled={!scriptDraft.trim()}
                  loading={busy}
                  onClick={() => void confirmAction('confirm')}
                >
                  确认写入用例
                </Button>
                <Button size="small" onClick={() => void confirmAction('cancel')}>
                  取消
                </Button>
              </Space>
            )}
          </div>
          <div className="api-scenario-agent-plan-body ui-agent-script-body">
            {session?.plan?.goal && (
              <p className="api-scenario-agent-summary">
                目标：{session.plan.goal}
                {session.plan.start_path ? ` · 起始 ${session.plan.start_path}` : ''}
              </p>
            )}
            {(() => {
              const report = session?.explore_report || session?.plan?.explore_report || null
              const hasScripts = caseScripts.some((c) => (c.code || '').trim())
              const legacyScript = !hasScripts && Boolean(scriptDraft.trim())
              // Explore-first: hide skeleton「待探索」matrix; show scripts and/or report summary only
              if (!hasScripts && !legacyScript && !report?.summary) return null
              return (
                <div className="ui-agent-explore-report">
                  {hasScripts && (
                    <div className="ui-agent-case-scripts">
                      <Typography.Text strong style={{ fontSize: 12, flexShrink: 0 }}>
                        用例脚本
                      </Typography.Text>
                      <div className="ui-agent-case-list">
                        {caseScripts.map((row, idx) => {
                          const id = row.id || `UC-${idx + 1}`
                          const name = row.name || id
                          const ready = Boolean(row.ready)
                          const code = String(row.code || '')
                          const selected = id === (activeCase?.id || selectedCaseId)
                          return (
                            <div
                              key={`${id}-${name}`}
                              role="button"
                              tabIndex={0}
                              onClick={() => {
                                setSelectedCaseId(id)
                                setScriptBound({ id: session?.id ?? null, code: '', text: '' })
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault()
                                  setSelectedCaseId(id)
                                  setScriptBound({ id: session?.id ?? null, code: '', text: '' })
                                }
                              }}
                              style={{
                                fontSize: 12,
                                lineHeight: 1.45,
                                padding: '6px 8px',
                                background: selected
                                  ? 'var(--ant-color-primary-bg, rgba(22,119,255,0.08))'
                                  : 'var(--ant-color-fill-quaternary, rgba(0,0,0,0.02))',
                                borderRadius: 6,
                                cursor: 'pointer',
                                border: selected
                                  ? '1px solid var(--ant-color-primary, #1677ff)'
                                  : '1px solid transparent',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: 8,
                              }}
                            >
                              <Space size={4} wrap style={{ flex: 1, minWidth: 0 }}>
                                <Tag>{id}</Tag>
                                <span style={{ wordBreak: 'break-word' }}>{name}</span>
                                {ready && code.trim() ? (
                                  <Tag color="success">已生成</Tag>
                                ) : (
                                  <Tag>未就绪</Tag>
                                )}
                              </Space>
                              {code.trim() ? (
                                <Button
                                  type="link"
                                  size="small"
                                  style={{ flexShrink: 0, paddingInline: 4 }}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    const sameCase = id === (selectedCaseId || activeCase?.id)
                                    const draftCode =
                                      sameCase &&
                                      scriptBound.id === (session?.id ?? null) &&
                                      scriptBound.text.trim()
                                        ? scriptBound.text
                                        : code
                                    setSelectedCaseId(id)
                                    setScriptModal({ id, name, code: draftCode })
                                  }}
                                >
                                  点击查看
                                </Button>
                              ) : null}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                  {legacyScript && (
                    <div style={{ marginBottom: 10 }}>
                      <Typography.Text strong style={{ fontSize: 12 }}>
                        用例脚本
                      </Typography.Text>
                      <div style={{ marginTop: 6 }}>
                        <Button
                          type="link"
                          size="small"
                          style={{ paddingInline: 0 }}
                          onClick={() =>
                            setScriptModal({
                              id: activeCase?.id || 'script',
                              name:
                                activeCase?.name ||
                                session?.plan?.case_name ||
                                session?.plan?.goal ||
                                '当前脚本',
                              code: scriptDraft,
                            })
                          }
                        >
                          点击查看
                        </Button>
                      </div>
                    </div>
                  )}
                  {report?.summary && (
                    <Space size={4} wrap style={{ marginBottom: 8 }}>
                      <Tag>步骤 {report.summary.total_steps ?? 0}</Tag>
                      <Tag color="blue">PW {report.summary.playwright_steps ?? 0}</Tag>
                      <Tag color="purple">Mid {report.summary.midscene_steps ?? 0}</Tag>
                    </Space>
                  )}
                </div>
              )
            })()}
            <Modal
              title={scriptModal ? `${scriptModal.id} ${scriptModal.name}` : '用例脚本'}
              open={Boolean(scriptModal)}
              onCancel={() => setScriptModal(null)}
              footer={[
                <Space key="trial-mode" size="small" style={{ marginRight: 'auto' }}>
                  <Switch
                    size="small"
                    checked={trialHeaded}
                    onChange={setTrialHeaded}
                    checkedChildren="有头"
                    unCheckedChildren="无头"
                    disabled={trialBusy || !awaiting}
                  />
                  <Button
                    key="trial"
                    loading={trialBusy}
                    disabled={!scriptModal?.code?.trim() || !awaiting || trialBusy}
                    onClick={() =>
                      void trialRun({
                        caseId: scriptModal?.id,
                        code: scriptModal?.code,
                      })
                    }
                  >
                    试跑
                  </Button>
                </Space>,
                <Button key="close" onClick={() => setScriptModal(null)}>
                  关闭
                </Button>,
                <Button
                  key="copy"
                  type="primary"
                  disabled={!scriptModal?.code?.trim()}
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(scriptModal?.code || '')
                      message.success('已复制脚本')
                    } catch {
                      message.error('复制失败')
                    }
                  }}
                >
                  复制
                </Button>,
              ]}
              width={720}
              destroyOnClose
            >
              {awaiting ? (
                <Input.TextArea
                  className="ui-agent-script-input"
                  value={scriptModal?.code || ''}
                  onChange={(e) => {
                    const text = e.target.value
                    setScriptModal((prev) => (prev ? { ...prev, code: text } : prev))
                    setScriptBound({
                      id: session?.id ?? null,
                      code: serverScript,
                      text,
                    })
                  }}
                  wrap="off"
                  spellCheck={false}
                  autoSize={{ minRows: 12, maxRows: 28 }}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
                />
              ) : (
                <pre
                  style={{
                    margin: 0,
                    padding: 12,
                    maxHeight: '60vh',
                    overflow: 'auto',
                    fontSize: 12,
                    lineHeight: 1.45,
                    background: 'rgba(0,0,0,0.04)',
                    borderRadius: 6,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {scriptModal?.code || ''}
                </pre>
              )}
            </Modal>
          </div>
          <div className="ui-agent-script-foot">
            <div className="ui-agent-script-foot-row">
              <Button
                size="small"
                disabled={!scriptDraft.trim()}
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(scriptDraft)
                    message.success('已复制脚本')
                  } catch {
                    message.error('复制失败')
                  }
                }}
              >
                复制脚本
              </Button>
              {awaiting && (
                <>
                  <Switch
                    size="small"
                    checked={trialHeaded}
                    onChange={setTrialHeaded}
                    checkedChildren="有头"
                    unCheckedChildren="无头"
                    disabled={trialBusy}
                  />
                  <Button
                    size="small"
                    loading={trialBusy}
                    disabled={!scriptDraft.trim() || trialBusy}
                    onClick={() =>
                      void trialRun({
                        caseId: selectedCaseId || activeCase?.id || undefined,
                        code: scriptDraft,
                      })
                    }
                  >
                    试跑
                  </Button>
                </>
              )}
              {session?.dry_run_status && (
                <Tag
                  color={
                    session.dry_run_status === 'passed'
                      ? 'success'
                      : session.dry_run_status === 'running'
                        ? 'processing'
                        : 'error'
                  }
                >
                  试跑{' '}
                  {session.dry_run_status === 'passed'
                    ? '通过'
                    : session.dry_run_status === 'running'
                      ? '中'
                      : session.dry_run_status === 'failed'
                        ? '失败'
                        : '出错'}
                </Tag>
              )}
              {session?.saved_ui_case_id != null && (
                <span>
                  已写入{' '}
                  <Link to={`/projects/${pid}/ui-cases`}>用例 #{session.saved_ui_case_id}</Link>
                </span>
              )}
            </div>
            {session?.dry_run_error ? (
              <Typography.Paragraph
                type="danger"
                style={{ fontSize: 12, marginTop: 6, marginBottom: 0 }}
                ellipsis={{ rows: 2, tooltip: session.dry_run_error }}
              >
                {session.dry_run_error}
              </Typography.Paragraph>
            ) : null}
          </div>
        </aside>

        <section
          className={`api-scenario-agent-chat card${chatCollapsed ? ' is-collapsed' : ''}`}
        >
          <div className="api-scenario-agent-chat-toolbar">
            <div className="api-scenario-agent-chat-toolbar-left">
              <Space size="small" wrap>
                {session && (
                  <Tag color={statusColor(session.status)}>{statusLabel(session.status)}</Tag>
                )}
                {session?.has_credentials && <Tag>已解析对话账号</Tag>}
                {(session?.headed || (canStart && headed)) && <Tag color="blue">有头</Tag>}
                {(session?.auth_mode === 'none' || (canStart && authMode === 'none')) && (
                  <Tag>不注入</Tag>
                )}
                {session?.dry_run_status === 'passed' && <Tag color="success">试跑通过</Tag>}
                {session?.dry_run_status === 'running' && <Tag color="processing">试跑中</Tag>}
                {(session?.dry_run_status === 'failed' || session?.dry_run_status === 'error') && (
                  <Tag color="error">试跑未通过</Tag>
                )}
                {selectedCaseTitle && canStart && (
                  <Tag>
                    用例：
                    {selectedCaseTitle.length > 16
                      ? `${selectedCaseTitle.slice(0, 16)}…`
                      : selectedCaseTitle}
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
                <div className="api-scenario-agent-bubble-body">
                  {m.content}
                </div>
              </div>
            ))}
            {busy && !waitingHuman && (
              <div className="api-scenario-agent-bubble api-scenario-agent-bubble--assistant">
                <div className="api-scenario-agent-bubble-role">Agent</div>
                <div className="api-scenario-agent-bubble-body muted">
                  {session?.status === 'running' ? '探索中…' : '思考中…'}
                </div>
              </div>
            )}
            {waitingHuman && (
              <div className="api-scenario-agent-bubble api-scenario-agent-bubble--assistant">
                <div className="api-scenario-agent-bubble-role">Agent</div>
                <div className="api-scenario-agent-bubble-body muted">
                  浏览器保持打开。请手动操作，或输入验证码/「继续」后发送。
                </div>
              </div>
            )}
          </div>

          <div className="api-scenario-agent-composer">
            <Input.TextArea
              className="api-scenario-agent-composer-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                restoring
                  ? '正在恢复上次会话…'
                  : canStart
                    ? '描述流程，例如：账号 demo 密码 xxx，登录后打开公寓列表并搜索'
                    : waitingHuman
                      ? '输入验证码/说明，或「继续」（浏览器保持打开）'
                      : exploring
                        ? '探索进行中，可点右侧停止…'
                        : session?.status === 'error'
                          ? '继续描述，将重新探索页面'
                          : canChat
                            ? '继续修改，例如：第 2 步改用 role=button[name="查询"]；或说「重新探索」'
                            : '当前会话已结束，请点「新会话」'
              }
              autoSize={{ minRows: 2, maxRows: 5 }}
              disabled={
                restoring ||
                finished ||
                (!canStart && !canChat) ||
                (busy && !waitingHuman)
              }
              variant="borderless"
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault()
                  void onSend()
                }
              }}
            />
            <div className="api-scenario-agent-composer-bar">
              <Tag className="api-scenario-agent-engine-tag">playwright+midscene</Tag>
              <div className="api-scenario-agent-composer-tools">
                <Popover
                  content={casePicker}
                  trigger="click"
                  open={casePopoverOpen}
                  onOpenChange={setCasePopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip title={testcaseId ? `已选功能用例 #${testcaseId}` : '功能用例（可选）'}>
                    <Badge count={testcaseId ? 1 : 0} size="small" offset={[-2, 2]} showZero={false}>
                      <Button
                        type="text"
                        size="small"
                        className={
                          testcaseId
                            ? 'api-scenario-agent-tool-btn is-active'
                            : 'api-scenario-agent-tool-btn'
                        }
                        icon={<FileTextOutlined />}
                        disabled={!canStart}
                      />
                    </Badge>
                  </Tooltip>
                </Popover>

                <Popover
                  content={envPicker}
                  trigger="click"
                  open={envPopoverOpen}
                  onOpenChange={setEnvPopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip title={envName ? `环境：${envName}` : 'UI 环境'}>
                    <Button
                      type="text"
                      size="small"
                      className={
                        envId != null || selectedEnv
                          ? 'api-scenario-agent-tool-btn is-active'
                          : 'api-scenario-agent-tool-btn'
                      }
                      icon={<CloudServerOutlined />}
                      disabled={!canEditEnv}
                    />
                  </Tooltip>
                </Popover>

                <Tooltip title={headed ? '有头模式（默认，可见浏览器）' : '无头模式'}>
                  <Button
                    type="text"
                    size="small"
                    className={
                      headed
                        ? 'api-scenario-agent-tool-btn is-active'
                        : 'api-scenario-agent-tool-btn'
                    }
                    icon={<DesktopOutlined />}
                    disabled={!canStart}
                    onClick={() => setHeaded((v) => !v)}
                  />
                </Tooltip>

                <Tooltip title="登录用例选「不注入」，跳过环境 localStorage/请求头">
                  <Select
                    size="small"
                    value={authMode}
                    disabled={!canStart}
                    style={{ width: 148 }}
                    options={[
                      { value: 'follow', label: '跟随环境注入' },
                      { value: 'none', label: '不注入' },
                    ]}
                    onChange={(v) => setAuthMode(v)}
                  />
                </Tooltip>
              </div>

              {exploring && !waitingHuman ? (
                <Tooltip title="停止探索">
                  <Button
                    danger
                    shape="circle"
                    size="small"
                    className="api-scenario-agent-send-btn"
                    icon={<StopOutlined />}
                    loading={stopping}
                    onClick={() => void stopExplore()}
                  />
                </Tooltip>
              ) : waitingHuman ? (
                <Space size={4}>
                  <Tooltip title="停止探索并关闭浏览器">
                    <Button
                      danger
                      shape="circle"
                      size="small"
                      className="api-scenario-agent-send-btn"
                      icon={<StopOutlined />}
                      loading={stopping}
                      onClick={() => void stopExplore()}
                    />
                  </Tooltip>
                  <Tooltip title="发送协助信息">
                    <Button
                      type="primary"
                      shape="circle"
                      size="small"
                      className="api-scenario-agent-send-btn"
                      icon={<SendOutlined />}
                      disabled={!input.trim()}
                      onClick={() => void onSend()}
                    />
                  </Tooltip>
                </Space>
              ) : (
                <Tooltip title={canStart ? '开始探索' : '发送'}>
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
              )}
            </div>
          </div>
        </section>
        {!chatCollapsed && session && shotKeys.length > 0 && (
          <ExploreShotRail
            projectId={pid}
            sessionId={session.id}
            storageKeys={shotKeys}
          />
        )}
      </div>
    </div>
  )
}
