import {
  AuditOutlined,
  BookOutlined,
  FileTextOutlined,
  ImportOutlined,
  SendOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import {
  App,
  Button,
  Checkbox,
  Input,
  Modal,
  Pagination,
  Popover,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { apiJson, apiSse, ApiError } from '../api/client'
import {
  AgentChatCollapseButton,
  useAgentChatCollapsed,
} from '../components/AgentChatCollapse'
import AgentSessionHistory, {
  type AgentSessionSummary,
} from '../components/AgentSessionHistory'
import ListViewToggle, { useListViewMode } from '../components/ListViewToggle'
import { VariablesTags } from '../components/TestDataVariables'
import CaseDesignEvidence, { type DesignTrace } from '../components/CaseDesignEvidence'
import {
  CASE_REVIEW_DIMENSIONS,
  CASE_REVIEW_DIM_TITLE,
  DEFAULT_REVIEW_WEIGHT,
  defaultReviewDimensions,
  type ReviewDimensionConfig,
} from '../constants/caseReviewDims'
import { STEPS_EXPECTED_PLACEHOLDER } from '../constants/testcase'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'
type CaseReviewIssue = {
  case_title?: string
  issue_type?: string
  detail?: string
}

type CaseReviewReport = {
  summary: string
  issues: CaseReviewIssue[]
  dimension_scores: Record<string, number> | null
}

type GeneratedCase = {
  draft_id?: string | null
  design_trace?: DesignTrace | null
  title: string
  overall_expected?: string | null
  module?: string | null
  precondition?: string | null
  test_data?: string | null
  case_type?: string | null
  priority?: string | null
  steps: { order: number; action: string; expected?: string }[]
}

type LlmOptions = {
  default_model: string
  models: string[]
  case_design_skill?: string | null
  mode?: string
  source?: string
  skill_name?: string | null
  skill_version?: number | null
  model_profile_id?: number | null
}

type PageRes<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

type ReqBrief = { id: number; title: string }

type KnowledgeLibraryBrief = {
  id: number
  name: string
  document_count: number
}

type ChatMsg = { role: 'user' | 'assistant'; content: string }

type ChatBubble = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
}

type AiSessionOut = {
  id: number
  title: string
  messages: ChatMsg[]
  drafts: GeneratedCase[]
  requirement_id: number | null
  knowledge_library_id: number | null
  llm_model: string | null
  generate_mode: string
  artifacts: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

type FormPrefs = {
  requirementId?: string
  knowledgeLibraryId?: string
  llmModel?: string
  enableCaseReview?: boolean
  reviewDimensions?: ReviewDimensionConfig[]
}

const WELCOME =
  '你好，我是用例生成助手。\n\n1. 点击输入框下方图标选择需求文档、知识库、大模型，并可开启多维用例评审\n2. 用自然语言描述要测的范围（可不填，将按所选需求/知识库生成）\n3. 生成后可继续对话修改：改优先级、补异常、删某条等，左侧草稿会整表更新\n4. 确认后才会写入项目用例'

const WEIGHT_OPTIONS = [1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) }))

function normalizeReviewPrefs(
  raw: ReviewDimensionConfig[] | undefined,
): ReviewDimensionConfig[] {
  if (!raw?.length) return defaultReviewDimensions()
  const known = new Set(CASE_REVIEW_DIMENSIONS.map((d) => d.key))
  const seen = new Set<string>()
  const out: ReviewDimensionConfig[] = []
  for (const item of raw) {
    if (!known.has(item.key) || seen.has(item.key)) continue
    seen.add(item.key)
    const weight = Number(item.weight)
    out.push({
      key: item.key,
      weight:
        Number.isFinite(weight) && weight >= 1 && weight <= 5
          ? Math.round(weight)
          : DEFAULT_REVIEW_WEIGHT,
    })
  }
  return out.length > 0 ? out : defaultReviewDimensions()
}

function formPrefsStorageKey(projectId: number) {
  return `ai-generate-form:${projectId}`
}

function loadFormPrefs(projectId: number): FormPrefs {
  try {
    const raw = localStorage.getItem(formPrefsStorageKey(projectId))
    if (!raw) return {}
    return JSON.parse(raw) as FormPrefs
  } catch {
    return {}
  }
}

function saveFormPrefs(projectId: number, prefs: FormPrefs) {
  localStorage.setItem(formPrefsStorageKey(projectId), JSON.stringify(prefs))
}

function modelAllowedByOptions(current: string, options: LlmOptions | undefined): string {
  if (!options) return ''
  const models = options.models ?? []
  const name = current.trim()
  if (name && models.includes(name)) return name
  const fallback = (options.default_model || '').trim()
  if (fallback && (models.length === 0 || models.includes(fallback))) return fallback
  return models[0] ?? ''
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function sortedSteps(steps: GeneratedCase['steps']) {
  return (steps ?? [])
    .filter((s) => s.action?.trim())
    .sort((a, b) => a.order - b.order)
    .map((s, i) => ({
      order: i,
      action: s.action.trim(),
      expected: s.expected?.trim() || '',
    }))
}

function normalizeStepsForApi(steps: GeneratedCase['steps']) {
  return sortedSteps(steps).map((s, i) => ({
    order: i,
    action: s.action,
    expected: s.expected || STEPS_EXPECTED_PLACEHOLDER,
  }))
}

const PRIORITY_TAG_COLORS: Record<string, string> = {
  P0: 'magenta',
  P1: 'volcano',
  P2: 'gold',
  P3: 'blue',
  P4: 'default',
}

function renderPriorityTag(priority: string | null | undefined) {
  if (!priority?.trim()) return '—'
  const value = priority.trim()
  return <Tag color={PRIORITY_TAG_COLORS[value] ?? 'default'}>{value}</Tag>
}

function draftKey(row: GeneratedCase, index: number): string {
  return row.draft_id || `case-${index}`
}

function roleLabel(role: ChatBubble['role']): string {
  if (role === 'user') return '你'
  if (role === 'system') return '系统'
  return '助手'
}

function defaultFirstPrompt(requirementId?: string, knowledgeLibraryId?: string): string {
  if (requirementId) return '根据所选需求文档生成测试用例'
  if (knowledgeLibraryId) return '根据项目知识库生成测试用例'
  return '请生成测试用例'
}

export default function AiGenerate() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { projectId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const pid = Number(projectId)

  const savedPrefs = Number.isFinite(pid) ? loadFormPrefs(pid) : {}
  const libraryFromUrl = searchParams.get('library')
  const libraryFromUrlOk = libraryFromUrl != null && /^\d+$/.test(libraryFromUrl)
  const requirementFromUrl = searchParams.get('requirement')
  const requirementFromUrlOk = requirementFromUrl != null && /^\d+$/.test(requirementFromUrl)

  const [requirementId, setRequirementId] = useState<string | undefined>(
    requirementFromUrlOk ? requirementFromUrl : savedPrefs.requirementId,
  )
  const [knowledgeLibraryId, setKnowledgeLibraryId] = useState<string | undefined>(
    libraryFromUrlOk ? libraryFromUrl : savedPrefs.knowledgeLibraryId,
  )
  const [llmModel, setLlmModel] = useState(savedPrefs.llmModel ?? '')
  const [enableCaseReview, setEnableCaseReview] = useState(
    Boolean(savedPrefs.enableCaseReview),
  )
  const [reviewDimensions, setReviewDimensions] = useState<ReviewDimensionConfig[]>(() =>
    normalizeReviewPrefs(savedPrefs.reviewDimensions),
  )
  const [reviewReport, setReviewReport] = useState<CaseReviewReport | null>(null)
  const [input, setInput] = useState('')
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([])
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [notices, setNotices] = useState<ChatBubble[]>([])
  const [thinking, setThinking] = useState<string | null>(null)
  const [generatedCases, setGeneratedCases] = useState<GeneratedCase[]>([])
  const [writingMode, setWritingMode] = useState<null | 'all' | 'batch' | number>(null)
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([])
  const [stepsModalCase, setStepsModalCase] = useState<GeneratedCase | null>(null)
  const [editingDraftKey, setEditingDraftKey] = useState<string | null>(null)
  const [savingSteps, setSavingSteps] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reqPopoverOpen, setReqPopoverOpen] = useState(false)
  const [libPopoverOpen, setLibPopoverOpen] = useState(false)
  const [modelPopoverOpen, setModelPopoverOpen] = useState(false)
  const [reviewPopoverOpen, setReviewPopoverOpen] = useState(false)
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [sessionList, setSessionList] = useState<AgentSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const { collapsed: chatCollapsed, toggleCollapsed: toggleChatCollapsed } = useAgentChatCollapsed(
    Number.isFinite(pid) ? `ai-generate-chat-collapsed:${pid}` : 'ai-generate-chat-collapsed',
  )
  const { containerRef: draftTableRef, scrollY: draftScrollY } = useTableScrollY()
  const [draftViewMode, setDraftViewMode] = useListViewMode('list-view:ai-generate-drafts')
  const [gridPage, setGridPage] = useState(1)
  const [gridPageSize, setGridPageSize] = useState(10)

  const chatEndRef = useRef<HTMLDivElement>(null)
  const sessionIdRef = useRef<number | null>(null)
  const pinnedRequirementRef = useRef<string | undefined>(
    requirementFromUrlOk ? requirementFromUrl : undefined,
  )

  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  const llmOpts = useQuery({
    queryKey: ['ai', 'llm-options', pid],
    queryFn: () => apiJson<LlmOptions>(`/api/v1/projects/${pid}/ai/llm-options`),
    enabled: Number.isFinite(pid),
  })
  const resolvedLlmModel = modelAllowedByOptions(llmModel, llmOpts.data)

  const requirementsQuery = useQuery({
    queryKey: ['requirements', 'picker', pid],
    queryFn: () =>
      apiJson<PageRes<ReqBrief>>(`/api/v1/projects/${pid}/requirements/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const librariesQuery = useQuery({
    queryKey: ['knowledge-libraries', pid],
    queryFn: () =>
      apiJson<KnowledgeLibraryBrief[]>(`/api/v1/projects/${pid}/knowledge/libraries`),
    enabled: Number.isFinite(pid),
  })

  useEffect(() => {
    const next = new URLSearchParams(searchParams)
    let changed = false
    if (/^\d+$/.test(next.get('library') || '')) {
      next.delete('library')
      changed = true
    }
    if (/^\d+$/.test(next.get('requirement') || '')) {
      next.delete('requirement')
      changed = true
    }
    if (changed) setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    if (!Number.isFinite(pid)) return
    saveFormPrefs(pid, {
      requirementId,
      knowledgeLibraryId,
      llmModel: llmOpts.data ? resolvedLlmModel : undefined,
      enableCaseReview,
      reviewDimensions,
    })
  }, [
    pid,
    requirementId,
    knowledgeLibraryId,
    llmOpts.data,
    resolvedLlmModel,
    enableCaseReview,
    reviewDimensions,
  ])

  async function refreshSessionList(): Promise<AgentSessionSummary[]> {
    const list = await apiJson<AgentSessionSummary[]>(`/api/v1/projects/${pid}/ai/sessions`)
    setSessionList(list)
    return list
  }

  function applySession(s: AiSessionOut) {
    setSessionId(s.id)
    setChatMessages(
      (s.messages ?? []).map((m) => ({
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content,
      })),
    )
    setGeneratedCases(s.drafts ?? [])
    setSelectedRowKeys([])
    setPendingUser(null)
    setNotices([])
    setThinking(null)
    setInput('')
    setStepsModalCase(null)
    setReviewReport(null)
    if (s.requirement_id != null) setRequirementId(String(s.requirement_id))
    else setRequirementId(undefined)
    if (s.knowledge_library_id != null) setKnowledgeLibraryId(String(s.knowledge_library_id))
    else setKnowledgeLibraryId(undefined)
    const sessionModel = (s.llm_model || '').trim()
    const allowed = llmOpts.data?.models ?? []
    if (sessionModel && allowed.includes(sessionModel)) setLlmModel(sessionModel)
  }

  async function persistSession(opts?: {
    messages?: ChatMsg[]
    drafts?: GeneratedCase[]
  }) {
    const sid = sessionIdRef.current
    if (!sid || !Number.isFinite(pid)) return false
    const messages = opts?.messages ?? chatMessages
    const drafts = opts?.drafts ?? generatedCases
    try {
      const updated = await apiJson<AiSessionOut>(`/api/v1/projects/${pid}/ai/sessions/${sid}`, {
        method: 'PUT',
        body: JSON.stringify({
          messages,
          drafts,
          requirement_id: requirementId !== undefined ? Number(requirementId) : null,
          knowledge_library_id:
            knowledgeLibraryId !== undefined ? Number(knowledgeLibraryId) : null,
          llm_model: resolvedLlmModel || null,
          generate_mode: 'fast',
        }),
      })
      setSessionList((prev) => {
        const next = prev.filter((x) => x.id !== updated.id)
        return [
          {
            id: updated.id,
            title: updated.title,
            updated_at: updated.updated_at,
            message_count: updated.messages?.length ?? 0,
            draft_count: updated.drafts?.length ?? 0,
          },
          ...next,
        ]
      })
      return true
    } catch (error) {
      message.error(`会话保存失败，刷新前请重试。${errText(error)}`)
      return false
    }
  }

  useEffect(() => {
    if (!Number.isFinite(pid)) return
    let cancelled = false
    ;(async () => {
      setHistoryLoading(true)
      try {
        const list = await apiJson<AgentSessionSummary[]>(`/api/v1/projects/${pid}/ai/sessions`)
        if (cancelled) return
        setSessionList(list)
        if (list.length === 0) {
          const created = await apiJson<AiSessionOut>(`/api/v1/projects/${pid}/ai/sessions`, {
            method: 'POST',
          })
          if (cancelled) return
          applySession(created)
          const again = await apiJson<AgentSessionSummary[]>(
            `/api/v1/projects/${pid}/ai/sessions`,
          )
          if (!cancelled) setSessionList(again)
        } else {
          const latest = await apiJson<AiSessionOut>(
            `/api/v1/projects/${pid}/ai/sessions/${list[0].id}`,
          )
          if (cancelled) return
          applySession(latest)
        }
        const pinned = pinnedRequirementRef.current
        if (pinned) {
          setRequirementId(pinned)
          pinnedRequirementRef.current = undefined
        }
      } catch (e) {
        if (!cancelled) message.error(errText(e))
      } finally {
        if (!cancelled) setHistoryLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- bootstrap per project
  }, [pid])

  const started = chatMessages.length > 0
  const canStart = !started && !busy
  const canChat = started && !busy

  const displayMessages = useMemo(() => {
    const bubbles: ChatBubble[] = [{ id: 'welcome', role: 'assistant', content: WELCOME }]
    chatMessages.forEach((m, i) => {
      bubbles.push({
        id: `m-${i}`,
        role: m.role,
        content: m.content,
      })
    })
    if (pendingUser && !chatMessages.some((m) => m.role === 'user' && m.content === pendingUser)) {
      bubbles.push({ id: 'pending-user', role: 'user', content: pendingUser })
    }
    bubbles.push(...notices)
    return bubbles
  }, [chatMessages, pendingUser, notices])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [displayMessages, busy, thinking])

  function pushNotice(role: ChatBubble['role'], content: string) {
    setNotices((prev) => [
      ...prev,
      { id: `n-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, role, content },
    ])
  }

  function canSendNow(): boolean {
    if (busy) return false
    if (!resolvedLlmModel) return false
    if (canStart) {
      return Boolean(requirementId || knowledgeLibraryId || input.trim())
    }
    if (canChat) return Boolean(input.trim())
    return false
  }

  async function sendTurn(userText: string) {
    if (!resolvedLlmModel) {
      message.warning('请选择大模型')
      setModelPopoverOpen(true)
      return
    }
    if (!requirementId && !knowledgeLibraryId && !userText.trim() && !started) {
      message.warning('请至少选择需求、知识库或填写说明')
      return
    }
    if (busy) return

    const text =
      userText.trim() || defaultFirstPrompt(requirementId, knowledgeLibraryId)
    const nextMessages: ChatMsg[] = [...chatMessages, { role: 'user', content: text }]

    setBusy(true)
    setThinking('正在生成用例…')
    setPendingUser(text)
    setInput('')

    const body: Record<string, unknown> = {
      generate_mode: 'fast',
      llm_model: resolvedLlmModel,
      messages: nextMessages,
      enable_case_review: enableCaseReview,
    }
    if (enableCaseReview) {
      body.review_dimensions =
        reviewDimensions.length > 0 ? reviewDimensions : defaultReviewDimensions()
    }
    if (requirementId !== undefined) {
      body.requirement_id = Number(requirementId)
    }
    if (knowledgeLibraryId !== undefined) {
      body.knowledge_library_id = Number(knowledgeLibraryId)
    }
    if (generatedCases.length > 0) {
      body.current_drafts = generatedCases
    }

    try {
      // Mutable bag so assignments inside the SSE callback are visible to TS after await.
      const streamAcc: {
        doneCases: GeneratedCase[] | null
        assistantText: string
        latestReview: CaseReviewReport | null
      } = { doneCases: null, assistantText: '', latestReview: null }
      await apiSse(
        `/api/v1/projects/${pid}/ai/generate-testcases/stream`,
        {
          method: 'POST',
          body: JSON.stringify(body),
        },
        (event) => {
          if (event.type === 'status' && typeof event.message === 'string') {
            setThinking(event.message)
            return
          }
          if (event.type === 'review') {
            streamAcc.latestReview = {
              summary: typeof event.summary === 'string' ? event.summary : '',
              issues: Array.isArray(event.issues) ? (event.issues as CaseReviewIssue[]) : [],
              dimension_scores:
                event.dimension_scores &&
                typeof event.dimension_scores === 'object' &&
                !Array.isArray(event.dimension_scores)
                  ? (event.dimension_scores as Record<string, number>)
                  : null,
            }
            setReviewReport(streamAcc.latestReview)
            return
          }
          if (event.type === 'done') {
            streamAcc.doneCases = (event.cases as GeneratedCase[] | undefined) ?? []
            streamAcc.assistantText =
              (typeof event.assistant_message === 'string' && event.assistant_message.trim()) ||
              (streamAcc.doneCases.length > 0
                ? `已生成 ${streamAcc.doneCases.length} 条用例`
                : '未生成用例')
          }
        },
      )
      if (!streamAcc.latestReview) setReviewReport(null)
      if (streamAcc.doneCases) {
        const doneCases = streamAcc.doneCases
        const assistantText = streamAcc.assistantText
        const nextChat: ChatMsg[] = [
          ...nextMessages,
          { role: 'assistant', content: assistantText },
        ]
        setGeneratedCases(doneCases)
        setSelectedRowKeys([])
        setChatMessages(nextChat)
        await persistSession({ messages: nextChat, drafts: doneCases })
        if (doneCases.length > 0) {
          message.success(assistantText.split('\n')[0])
        } else {
          message.warning(assistantText.split('\n')[0])
        }
      }
      setPendingUser(null)
    } catch (e) {
      message.error(errText(e))
      pushNotice('system', errText(e))
      setPendingUser(null)
    } finally {
      setBusy(false)
      setThinking(null)
    }
  }

  async function onSend() {
    if (!canSendNow()) return
    await sendTurn(input)
  }

  async function createNewSession() {
    if (busy) return
    setBusy(true)
    try {
      const created = await apiJson<AiSessionOut>(`/api/v1/projects/${pid}/ai/sessions`, {
        method: 'POST',
      })
      applySession(created)
      await refreshSessionList()
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  async function openSession(id: number) {
    if (busy || id === sessionId) return
    setBusy(true)
    setHistoryLoading(true)
    try {
      const s = await apiJson<AiSessionOut>(`/api/v1/projects/${pid}/ai/sessions/${id}`)
      applySession(s)
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
      setHistoryLoading(false)
    }
  }

  async function deleteSession(id: number) {
    if (busy) return
    setBusy(true)
    try {
      await apiJson(`/api/v1/projects/${pid}/ai/sessions/${id}`, { method: 'DELETE' })
      const list = await refreshSessionList()
      if (id === sessionId) {
        if (list.length > 0) {
          const s = await apiJson<AiSessionOut>(
            `/api/v1/projects/${pid}/ai/sessions/${list[0].id}`,
          )
          applySession(s)
        } else {
          const created = await apiJson<AiSessionOut>(`/api/v1/projects/${pid}/ai/sessions`, {
            method: 'POST',
          })
          applySession(created)
          await refreshSessionList()
        }
      }
    } catch (e) {
      message.error(errText(e))
    } finally {
      setBusy(false)
    }
  }

  function partitionWritableIndices(indices: number[]) {
    const unique = [...new Set(indices)].filter((i) => i >= 0 && i < generatedCases.length)
    const valid: number[] = []
    let noSteps = 0
    let noExpected = 0
    for (const index of unique) {
      const row = generatedCases[index]
      if (!row) continue
      const steps = normalizeStepsForApi(row.steps)
      if (steps.length === 0) {
        noSteps += 1
        continue
      }
      if (!row.overall_expected?.trim()) {
        noExpected += 1
        continue
      }
      valid.push(index)
    }
    return { valid, noSteps, noExpected }
  }

  async function writeCases(indices: number[], mode: 'all' | 'batch' | number) {
    const { valid, noSteps, noExpected } = partitionWritableIndices(indices)
    const skipped = noSteps + noExpected
    if (skipped > 0) {
      const parts: string[] = []
      if (noSteps > 0) parts.push(`${noSteps} 条缺少有效步骤`)
      if (noExpected > 0) parts.push(`${noExpected} 条缺少总体期望`)
      message.warning(`跳过 ${skipped} 条：${parts.join('，')}`)
    }
    if (valid.length === 0) {
      if (skipped === 0) message.warning('没有可写入的用例')
      return
    }

    const body: Record<string, unknown> = {
      cases: valid.map((index) => {
        const row = generatedCases[index]
        return {
          draft_id: row.draft_id ?? null,
          design_trace: row.design_trace ?? null,
          title: row.title,
          module: row.module?.trim() || null,
          precondition: row.precondition?.trim() || null,
          test_data: row.test_data?.trim() || null,
          case_type: row.case_type?.trim() || null,
          overall_expected: row.overall_expected ?? null,
          priority: row.priority ?? null,
          steps: normalizeStepsForApi(row.steps),
        }
      }),
    }
    if (requirementId !== undefined) {
      body.requirement_id = Number(requirementId)
    }

    setWritingMode(mode)
    try {
      await apiJson(`/api/v1/projects/${pid}/ai/confirm-testcases`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
      message.success(`已写入 ${valid.length} 条用例`)
      const removeSet = new Set(valid)
      const nextDrafts = generatedCases.filter((_, i) => !removeSet.has(i))
      setGeneratedCases(nextDrafts)
      setSelectedRowKeys([])
      await persistSession({ drafts: nextDrafts })
      qc.invalidateQueries({ queryKey: ['testcases', pid] })
    } catch (e) {
      message.error(errText(e))
    } finally {
      setWritingMode(null)
    }
  }

  async function writeOneCase(index: number) {
    await writeCases([index], index)
  }

  function writeAllCases() {
    void writeCases(
      generatedCases.map((_, i) => i),
      'all',
    )
  }

  function writeSelectedCases() {
    const indices = generatedCases.flatMap((row, i) => selectedRowKeys.includes(draftKey(row, i)) ? [i] : [])
    void writeCases(indices, 'batch')
  }

  function openDraft(row: GeneratedCase) {
    setEditingDraftKey(draftKey(row, generatedCases.indexOf(row)))
    setStepsModalCase({ ...row, steps: sortedSteps(row.steps) })
  }

  async function saveDraftSteps() {
    if (!stepsModalCase || savingSteps) return
    if (stepsModalCase.design_trace && stepsModalCase.steps.some(s => !s.expected?.trim() || s.expected.trim() === '—')) {
      message.warning('请填写每一步的可观察预期')
      return
    }
    const updated = generatedCases.map((row, i) => draftKey(row, i) === editingDraftKey ? stepsModalCase : row)
    setSavingSteps(true)
    try {
      if (await persistSession({ drafts: updated })) {
        setGeneratedCases(updated)
        setStepsModalCase(null)
        message.success('已保存草稿步骤')
      }
    } finally { setSavingSteps(false) }
  }

  if (!Number.isFinite(pid)) {
    return <p className="error">无效项目</p>
  }

  const llmModels = llmOpts.data?.models ?? []
  const llmModelsLoading = llmOpts.isLoading && llmModels.length === 0
  const reqName = (requirementsQuery.data?.items ?? []).find(
    (r) => String(r.id) === requirementId,
  )
  const libName = (librariesQuery.data ?? []).find(
    (l) => String(l.id) === knowledgeLibraryId,
  )

  const requirementOptions = (requirementsQuery.data?.items ?? []).map((r) => ({
    value: String(r.id),
    label: `${r.id} · ${r.title}`,
  }))

  const libraryOptions = (librariesQuery.data ?? []).map((lib) => ({
    value: String(lib.id),
    label: `${lib.name}（${lib.document_count} 个文件）`,
  }))

  const columns: ColumnsType<GeneratedCase> = [
    {
      title: '标题',
      dataIndex: 'title',
      width: 140,
      ellipsis: true,
      render: (t: string) => (
        <Typography.Text ellipsis title={t}>
          {t}
        </Typography.Text>
      ),
    },
    {
      title: '模块',
      dataIndex: 'module',
      width: 88,
      ellipsis: true,
      render: (v: string | null | undefined) => v?.trim() || '—',
    },
    {
      title: '类型',
      dataIndex: 'case_type',
      width: 72,
      render: (v: string | null | undefined) => v?.trim() || '—',
    },
    {
      title: '步骤',
      key: 'steps',
      width: 88,
      render: (_: unknown, r) => {
        const steps = sortedSteps(r.steps)
        if (steps.length === 0) return '—'
        return (
          <Button
            type="link"
            size="small"
            style={{ padding: 0, height: 'auto' }}
            onClick={() => openDraft(r)}
          >
            查看步骤
          </Button>
        )
      },
    },
    {
      title: '测试数据',
      dataIndex: 'test_data',
      width: 180,
      ellipsis: true,
      render: (v: string | null | undefined) => (
        <VariablesTags fallbackText={v} />
      ),
    },
    {
      title: '总体期望',
      dataIndex: 'overall_expected',
      width: 160,
      ellipsis: true,
      render: (v: string | null | undefined) => v?.trim() || '—',
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 96,
      render: (p: string | null | undefined) => renderPriorityTag(p),
    },
    {
      title: '操作',
      key: 'actions',
      width: 72,
      fixed: 'right',
      render: (_: unknown, record) => {
        const index = generatedCases.indexOf(record)
        return (
          <Button
            type="primary"
            size="small"
            loading={writingMode === index}
            disabled={writingMode !== null && writingMode !== index}
            onClick={() => void writeOneCase(index)}
          >
            写入
          </Button>
        )
      },
    },
  ]

  const stepsModalColumns: ColumnsType<GeneratedCase['steps'][number]> = [
    {
      title: '序号',
      key: 'idx',
      width: 56,
      render: (_: unknown, __, index) => index + 1,
    },
    {
      title: '操作',
      dataIndex: 'action',
    },
    {
      title: '逐步预期',
      key: 'expected',
      render: (_, step, index) => (
        <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} aria-label={`步骤 ${index + 1} 的预期结果`}
          value={step.expected ?? ''} onChange={e => {
            const expected = e.target.value
            setStepsModalCase(row => row && ({ ...row, steps: row.steps.map((s, i) => i === index ? { ...s, expected } : s) }))
          }} />
      ),
    },
  ]

  const reqPicker = (
    <div className="api-scenario-agent-picker">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        需求文档（可选）
      </Typography.Text>
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="不关联需求"
        value={requirementId}
        loading={requirementsQuery.isLoading}
        options={requirementOptions}
        onChange={(v) => {
          setRequirementId(v)
          setReqPopoverOpen(false)
        }}
        style={{ width: '100%' }}
        disabled={busy}
        getPopupContainer={(n) => n.parentElement ?? document.body}
      />
    </div>
  )

  const libPicker = (
    <div className="api-scenario-agent-picker">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        知识库 RAG（可选）
      </Typography.Text>
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="不使用知识库"
        value={knowledgeLibraryId}
        loading={librariesQuery.isLoading}
        options={libraryOptions}
        onChange={(v) => {
          setKnowledgeLibraryId(v)
          setLibPopoverOpen(false)
        }}
        style={{ width: '100%' }}
        disabled={busy}
        getPopupContainer={(n) => n.parentElement ?? document.body}
      />
    </div>
  )

  const modelPicker = (
    <div className="api-scenario-agent-picker api-scenario-agent-picker--env">
      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        大模型（必选）
      </Typography.Text>
      {llmOpts.data ? (
        <Typography.Text
          type="secondary"
          style={{ fontSize: 12, display: 'block', marginBottom: 8 }}
        >
          生效配置：
          {llmOpts.data.source === 'action'
            ? '动作绑定'
            : llmOpts.data.source === 'project'
              ? '项目默认'
              : llmOpts.data.source === 'call'
                ? '本次指定'
                : '系统默认'}
          {llmOpts.data.skill_name
            ? ` · ${llmOpts.data.skill_name} v${llmOpts.data.skill_version}`
            : ''}
          {llmOpts.data.default_model ? ` · ${llmOpts.data.default_model}` : ''}
        </Typography.Text>
      ) : null}
      <Select
        showSearch
        optionFilterProp="label"
        placeholder="选择大模型"
        value={resolvedLlmModel || undefined}
        loading={llmModelsLoading}
        options={llmModels.map((m) => ({ value: m, label: m }))}
        onChange={(v) => {
          setLlmModel(v)
          setModelPopoverOpen(false)
        }}
        style={{ width: '100%' }}
        disabled={busy}
        getPopupContainer={(n) => n.parentElement ?? document.body}
      />
    </div>
  )

  const selectedReviewKeys = reviewDimensions.map((d) => d.key)
  const reviewWeightByKey = Object.fromEntries(
    reviewDimensions.map((d) => [d.key, d.weight]),
  ) as Record<string, number>

  function toggleReviewKey(key: string, checked: boolean) {
    setReviewDimensions((prev) => {
      if (checked) {
        if (prev.some((d) => d.key === key)) return prev
        return [...prev, { key, weight: DEFAULT_REVIEW_WEIGHT }]
      }
      const next = prev.filter((d) => d.key !== key)
      return next.length > 0 ? next : prev
    })
  }

  function setReviewWeight(key: string, weight: number) {
    setReviewDimensions((prev) =>
      prev.map((d) => (d.key === key ? { ...d, weight } : d)),
    )
  }

  const reviewPicker = (
    <div className="api-scenario-agent-picker" style={{ width: 320 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 10,
          gap: 12,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          用例评审（AI 多维检查）
        </Typography.Text>
        <Switch
          size="small"
          checked={enableCaseReview}
          disabled={busy}
          onChange={(v) => {
            setEnableCaseReview(v)
            if (v && reviewDimensions.length === 0) {
              setReviewDimensions(defaultReviewDimensions())
            }
          }}
        />
      </div>
      {enableCaseReview && (
        <>
          <Typography.Text
            type="secondary"
            style={{ fontSize: 12, display: 'block', marginBottom: 8 }}
          >
            勾选维度并设置权重（1–5，越高越优先）
          </Typography.Text>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {CASE_REVIEW_DIMENSIONS.map((dim) => {
              const checked = selectedReviewKeys.includes(dim.key)
              return (
                <div
                  key={dim.key}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                  }}
                >
                  <Checkbox
                    checked={checked}
                    disabled={busy || (checked && selectedReviewKeys.length <= 1)}
                    onChange={(e) => toggleReviewKey(dim.key, e.target.checked)}
                  >
                    {dim.title}
                  </Checkbox>
                  <Select
                    size="small"
                    style={{ width: 64 }}
                    disabled={busy || !checked}
                    value={reviewWeightByKey[dim.key] ?? DEFAULT_REVIEW_WEIGHT}
                    options={WEIGHT_OPTIONS}
                    onChange={(w) => setReviewWeight(dim.key, w)}
                    getPopupContainer={(n) => n.parentElement ?? document.body}
                  />
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )

  const issuesByDim = useMemo(() => {
    if (!reviewReport?.issues?.length) return []
    const groups = new Map<string, CaseReviewIssue[]>()
    for (const issue of reviewReport.issues) {
      const key = (issue.issue_type || '').trim() || 'other'
      const list = groups.get(key) ?? []
      list.push(issue)
      groups.set(key, list)
    }
    return Array.from(groups.entries())
  }, [reviewReport])

  const gridPageItems = useMemo(() => {
    const start = (gridPage - 1) * gridPageSize
    return generatedCases.slice(start, start + gridPageSize).map((row, i) => ({
      row,
      index: start + i,
    }))
  }, [generatedCases, gridPage, gridPageSize])

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(generatedCases.length / gridPageSize) || 1)
    if (gridPage > maxPage) setGridPage(maxPage)
  }, [generatedCases.length, gridPage, gridPageSize])

  function toggleDraftKey(key: string, checked: boolean) {
    setSelectedRowKeys((prev) =>
      checked ? (prev.includes(key) ? prev : [...prev, key]) : prev.filter((k) => k !== key),
    )
  }

  const draftEmpty = (
    <div className="ai-generate-empty">
      <span className="ai-generate-empty-title">暂无草稿</span>
      <span className="ai-generate-empty-hint">请在右侧对话生成用例，确认后再写入项目</span>
    </div>
  )

  return (
    <div className="api-scenario-agent-page ai-generate-page">
      <PageBanner title="AI 生成用例" />
      <div className="api-scenario-agent-split">
        <aside className="api-scenario-agent-plan card">
          <div className="api-scenario-agent-chat-toolbar">
            <Typography.Text strong>AI 生成结果</Typography.Text>
            <Space size="small">
              <Button
                type="primary"
                size="small"
                loading={writingMode === 'all'}
                disabled={
                  generatedCases.length === 0 || (writingMode !== null && writingMode !== 'all')
                }
                onClick={() => writeAllCases()}
              >
                一键写入
              </Button>
              <Button
                size="small"
                loading={writingMode === 'batch'}
                disabled={
                  selectedRowKeys.length === 0 ||
                  (writingMode !== null && writingMode !== 'batch')
                }
                onClick={() => writeSelectedCases()}
              >
                写入选中{selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : ''}
              </Button>
            </Space>
          </div>
          <p className="field-hint" style={{ marginTop: 0 }}>
            {llmOpts.data?.case_design_skill
              ? '用例设计规则已启用。打开草稿可核对设计依据、修改逐步预期，确认后写入用例库。'
              : '在此预览生成的草稿，确认后写入用例库。'}
            {llmOpts.data ? (
              <>
                {' '}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  当前生效：
                  {llmOpts.data.source === 'action'
                    ? '动作绑定'
                    : llmOpts.data.source === 'project'
                      ? '项目默认'
                      : llmOpts.data.source === 'call'
                        ? '本次指定'
                        : '系统默认'}
                  {llmOpts.data.skill_name
                    ? ` · ${llmOpts.data.skill_name} v${llmOpts.data.skill_version}`
                    : ''}
                  {llmOpts.data.default_model ? ` · ${llmOpts.data.default_model}` : ''}
                </Typography.Text>
              </>
            ) : null}
          </p>
          <Tabs
            className="ai-generate-result-tabs"
            defaultActiveKey="drafts"
            items={[
              {
                key: 'drafts',
                label: `用例草稿${generatedCases.length > 0 ? `（${generatedCases.length}）` : ''}`,
                children: (
                  <div className={`ai-generate-drafts-body is-${draftViewMode}`}>
                    <div className="ai-generate-drafts-toolbar">
                      <ListViewToggle value={draftViewMode} onChange={setDraftViewMode} />
                    </div>
                    {draftViewMode === 'table' ? (
                      <div className="api-scenario-agent-plan-table" ref={draftTableRef}>
                        <Table<GeneratedCase>
                          rowKey={(record) => draftKey(record, generatedCases.indexOf(record))}
                          size="small"
                          scroll={{ x: 1060, y: draftScrollY }}
                          pagination={{
                            pageSize: 10,
                            showSizeChanger: true,
                            pageSizeOptions: [10, 20, 50],
                            showTotal: (t) => `共 ${t} 条`,
                          }}
                          rowSelection={{
                            selectedRowKeys,
                            onChange: (keys) => setSelectedRowKeys(keys as string[]),
                            getCheckboxProps: () => ({ disabled: writingMode !== null }),
                          }}
                          columns={columns}
                          dataSource={generatedCases}
                          locale={{ emptyText: draftEmpty }}
                        />
                      </div>
                    ) : (
                      <>
                        {generatedCases.length === 0 ? (
                          draftEmpty
                        ) : (
                          <div className="ai-generate-draft-grid">
                            {gridPageItems.map(({ row, index }) => {
                              const key = draftKey(row, index)
                              const steps = sortedSteps(row.steps)
                              return (
                                <article key={key} className="ai-generate-draft-card">
                                  <div className="ai-generate-draft-card-head">
                                    <Checkbox
                                      checked={selectedRowKeys.includes(key)}
                                      disabled={writingMode !== null}
                                      onChange={(e) => toggleDraftKey(key, e.target.checked)}
                                    />
                                    <div className="ai-generate-draft-card-title-wrap">
                                      <div className="ai-generate-draft-card-title" title={row.title}>
                                        {row.title}
                                      </div>
                                    </div>
                                    <Space size={4} className="ai-generate-draft-card-ops">
                                      <Tooltip title={steps.length > 0 ? '查看步骤' : '无步骤'}>
                                        <span>
                                          <Button
                                            type="text"
                                            size="small"
                                            icon={<UnorderedListOutlined />}
                                            disabled={steps.length === 0}
                                            onClick={() => openDraft(row)}
                                          />
                                        </span>
                                      </Tooltip>
                                      <Tooltip title="写入">
                                        <span>
                                          <Button
                                            type="text"
                                            size="small"
                                            icon={<ImportOutlined />}
                                            loading={writingMode === index}
                                            disabled={
                                              writingMode !== null && writingMode !== index
                                            }
                                            onClick={() => void writeOneCase(index)}
                                          />
                                        </span>
                                      </Tooltip>
                                    </Space>
                                  </div>
                                  <div className="ai-generate-draft-card-meta">
                                    <span>{row.module?.trim() || '—'}</span>
                                    <span>{row.case_type?.trim() || '—'}</span>
                                    <span>{renderPriorityTag(row.priority)}</span>
                                  </div>
                                </article>
                              )
                            })}
                          </div>
                        )}
                        <div className="list-card-pagination">
                          <Pagination
                            current={gridPage}
                            pageSize={gridPageSize}
                            total={generatedCases.length}
                            showSizeChanger
                            showTotal={(t) => `共 ${t} 条`}
                            pageSizeOptions={[10, 20, 50]}
                            onChange={(p, s) => {
                              setGridPage(p)
                              setGridPageSize(s)
                            }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                ),
              },
              {
                key: 'review',
                label: '多维评审报告',
                children: (
                  <div className="ai-generate-review-pane">
                    {!reviewReport ? (
                      <div className="ai-generate-empty">
                        <span className="ai-generate-empty-title">暂无评审报告</span>
                        <span className="ai-generate-empty-hint">
                          开启多维评审并生成用例后，报告会显示在这里
                        </span>
                      </div>
                    ) : (
                      <div style={{ fontSize: 13 }}>
                        {reviewReport.summary.trim() ? (
                          <p style={{ marginTop: 0 }}>{reviewReport.summary.trim()}</p>
                        ) : (
                          <p className="field-hint" style={{ marginTop: 0 }}>
                            无摘要
                          </p>
                        )}
                        {reviewReport.dimension_scores &&
                          Object.keys(reviewReport.dimension_scores).length > 0 && (
                            <div
                              style={{
                                marginBottom: 8,
                                display: 'flex',
                                flexWrap: 'wrap',
                                gap: 6,
                              }}
                            >
                              {Object.entries(reviewReport.dimension_scores).map(([key, score]) => (
                                <Tag key={key}>
                                  {CASE_REVIEW_DIM_TITLE[key] ?? key}: {score}/5
                                </Tag>
                              ))}
                            </div>
                          )}
                        {issuesByDim.length === 0 ? (
                          <Typography.Text type="secondary">未发现具体问题</Typography.Text>
                        ) : (
                          issuesByDim.map(([dimKey, items]) => (
                            <div key={dimKey} style={{ marginBottom: 8 }}>
                              <Typography.Text strong>
                                {CASE_REVIEW_DIM_TITLE[dimKey] ?? dimKey}
                              </Typography.Text>
                              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                                {items.map((iss, i) => (
                                  <li key={`${dimKey}-${i}`}>
                                    {iss.case_title ? (
                                      <Typography.Text type="secondary">
                                        [{iss.case_title}]{' '}
                                      </Typography.Text>
                                    ) : null}
                                    {iss.detail || '—'}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <Modal
            className="draft-steps-modal"
            title={stepsModalCase?.title ?? '用例步骤'}
            open={stepsModalCase !== null}
            footer={
              <Space>
                <Button onClick={() => setStepsModalCase(null)}>关闭</Button>
                <Button type="primary" loading={savingSteps} disabled={busy || writingMode !== null}
                  onClick={() => void saveDraftSteps()}>保存草稿</Button>
              </Space>
            }
            width={760}
            destroyOnHidden
            onCancel={() => setStepsModalCase(null)}
          >
            <p className="draft-overall-expected">
              <span className="draft-overall-expected-label">类型：</span>
              {stepsModalCase?.case_type?.trim() || '—'}
            </p>
            <p className="draft-overall-expected">
              <span className="draft-overall-expected-label">前置条件：</span>
              {stepsModalCase?.precondition?.trim() || '—'}
            </p>
            <p className="draft-overall-expected">
              <span className="draft-overall-expected-label">测试数据：</span>
              <VariablesTags fallbackText={stepsModalCase?.test_data} />
            </p>
            <p className="draft-overall-expected">
              <span className="draft-overall-expected-label">总体期望：</span>
              {stepsModalCase?.overall_expected?.trim() || '—'}
            </p>
            <Table
              rowKey={(_, i) => `step-${i}`}
              size="small"
              pagination={false}
              columns={stepsModalColumns}
              dataSource={stepsModalCase?.steps ?? []}
              locale={{ emptyText: '暂无步骤' }}
            />
            <CaseDesignEvidence trace={stepsModalCase?.design_trace} />
          </Modal>
        </aside>

        <section
          className={`api-scenario-agent-chat card${chatCollapsed ? ' is-collapsed' : ''}`}
        >
          <div className="api-scenario-agent-chat-toolbar">
            <div className="api-scenario-agent-chat-toolbar-left">
              <Space size="small" wrap>
                {started && <Tag color={busy ? 'processing' : 'blue'}>对话中</Tag>}
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
                  activeId={sessionId}
                  loading={historyLoading}
                  busy={busy || writingMode !== null}
                  showStatus={false}
                  onSelect={(id) => void openSession(id)}
                  onNew={() => void createNewSession()}
                  onDelete={(id) => void deleteSession(id)}
                />
              )}
            </div>
          </div>

          <div className="api-scenario-agent-messages">
            {displayMessages.map((m) => (
              <div
                key={m.id}
                className={`api-scenario-agent-bubble api-scenario-agent-bubble--${m.role}`}
              >
                <div className="api-scenario-agent-bubble-role">{roleLabel(m.role)}</div>
                <div className="api-scenario-agent-bubble-body">{m.content}</div>
              </div>
            ))}
            {busy && (
              <div className="api-scenario-agent-bubble api-scenario-agent-bubble--assistant">
                <div className="api-scenario-agent-bubble-role">助手</div>
                <div className="api-scenario-agent-bubble-body muted">
                  {thinking || '思考中…'}
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div className="api-scenario-agent-composer">
            <Input.TextArea
              className="api-scenario-agent-composer-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                !started
                  ? '描述要测的范围，例如：覆盖登录失败与锁定策略'
                  : '继续修改草稿，例如：把第 2 条改成 P0，再补异常场景'
              }
              autoSize={{ minRows: 2, maxRows: 5 }}
              disabled={busy}
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
                  content={reqPicker}
                  trigger="click"
                  open={reqPopoverOpen}
                  onOpenChange={setReqPopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip title={reqName ? `需求：${reqName.title}` : '需求文档（可选）'}>
                    <Button
                      type="text"
                      size="small"
                      className={
                        requirementId
                          ? 'api-scenario-agent-tool-btn is-active'
                          : 'api-scenario-agent-tool-btn'
                      }
                      icon={<FileTextOutlined />}
                      disabled={busy}
                    />
                  </Tooltip>
                </Popover>

                <Popover
                  content={libPicker}
                  trigger="click"
                  open={libPopoverOpen}
                  onOpenChange={setLibPopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip title={libName ? `知识库：${libName.name}` : '知识库（可选）'}>
                    <Button
                      type="text"
                      size="small"
                      className={
                        knowledgeLibraryId
                          ? 'api-scenario-agent-tool-btn is-active'
                          : 'api-scenario-agent-tool-btn'
                      }
                      icon={<BookOutlined />}
                      disabled={busy}
                    />
                  </Tooltip>
                </Popover>

                <Popover
                  content={modelPicker}
                  trigger="click"
                  open={modelPopoverOpen}
                  onOpenChange={setModelPopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip title={resolvedLlmModel ? `模型：${resolvedLlmModel}` : '选择大模型'}>
                    <Button
                      type="text"
                      size="small"
                      className={
                        resolvedLlmModel
                          ? 'api-scenario-agent-tool-btn is-active'
                          : 'api-scenario-agent-tool-btn'
                      }
                      icon={<ThunderboltOutlined />}
                      disabled={busy}
                    />
                  </Tooltip>
                </Popover>

                <Popover
                  content={reviewPicker}
                  trigger="click"
                  open={reviewPopoverOpen}
                  onOpenChange={setReviewPopoverOpen}
                  placement="topLeft"
                  overlayClassName="api-scenario-agent-popover"
                >
                  <Tooltip
                    title={
                      enableCaseReview
                        ? `用例评审：已开（${selectedReviewKeys.length} 维）`
                        : '用例评审（可选）'
                    }
                  >
                    <Button
                      type="text"
                      size="small"
                      className={
                        enableCaseReview
                          ? 'api-scenario-agent-tool-btn is-active'
                          : 'api-scenario-agent-tool-btn'
                      }
                      icon={<AuditOutlined />}
                      disabled={busy}
                    />
                  </Tooltip>
                </Popover>
              </div>

              <Tooltip title={canStart ? '生成用例' : '发送'}>
                <Button
                  type="primary"
                  shape="circle"
                  size="small"
                  className="api-scenario-agent-send-btn"
                  icon={<SendOutlined />}
                  loading={busy}
                  disabled={!canSendNow()}
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
