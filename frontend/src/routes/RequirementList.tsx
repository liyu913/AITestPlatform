import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Typography,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { UploadFile } from 'antd/es/upload/interface'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  labelForRequirementStatus,
  PRIORITY_OPTIONS,
  priorityFromForm,
  priorityToForm,
  REQUIREMENT_STATUS_OPTIONS,
} from '../constants/workflow'
import { formatBeijing } from '../utils/datetime'
import { apiFetch, apiJson, ApiError } from '../api/client'
import {
  AgentChatCollapseButton,
  useAgentChatCollapsed,
} from '../components/AgentChatCollapse'
import ListViewToggle, { useListViewMode } from '../components/ListViewToggle'
import MarkdownPane from '../components/MarkdownPane'
import RequirementAnalysisPane, {
  type RequirementAnalysisData,
} from '../components/RequirementAnalysisPane'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'
import RequirementSourcePreview from './RequirementSourcePreview'

const ATTACH_ACCEPT =
  '.png,.jpg,.jpeg,.gif,.webp,.pdf,.docx,.md,.txt,.log,.json,.zip'
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

type RequirementAttachment = {
  id: number
  requirement_id: number
  filename: string
  mime: string
  size_bytes: number
  uploader_id: number | null
  created_at: string
  updated_at: string
}

type PlanDraftChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

type AnalysisChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

type Requirement = {
  id: number
  project_id: number
  title: string
  body_md: string
  status: string
  priority: string | null
  creator_id: number | null
  attachments: RequirementAttachment[]
  has_attachments: boolean
  analysis_json: RequirementAnalysis | null
  analyzed_at: string | null
  analysis_messages_json: AnalysisChatMessage[] | null
  analysis_pending_json: RequirementAnalysis | null
  plan_draft_md: string | null
  plan_draft_at: string | null
  plan_draft_messages_json: PlanDraftChatMessage[] | null
  created_at: string
  updated_at: string
}

type AnalysisChatResponse = {
  requirement_id: number
  analysis: RequirementAnalysis
  pending_analysis: RequirementAnalysis | null
  analyzed_at: string | null
  messages: AnalysisChatMessage[]
  reply: string
}

type PreviewTarget = {
  title: string
  filename: string | null
  mime: string | null
  sourceUrl?: string
  localFile?: File | null
}

type ListPreviewSession = {
  requirement: Requirement
  attachmentId: number
}

type LinkedPlan = {
  id: number
  name: string
  version: string | null
  iteration: string | null
}

type PlanDraftResponse = {
  requirement_id: number
  body_md: string
  drafted_at: string
  linked_plans: { id: number; name: string }[]
  messages: PlanDraftChatMessage[]
}

type RequirementAnalysis = RequirementAnalysisData

type PageRes<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

function listUrl(pid: number, page: number, pageSize: number, search: string) {
  const sp = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  const s = search.trim()
  if (s) {
    sp.set('search', s)
  }
  return `/api/v1/projects/${pid}/requirements/?${sp.toString()}`
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function isMdOrTxt(file: File): boolean {
  const n = file.name.toLowerCase()
  return n.endsWith('.md') || n.endsWith('.txt')
}

function isAllowedAttachment(file: File): boolean {
  const n = file.name.toLowerCase()
  return ATTACH_ACCEPT.split(',').some((ext) => n.endsWith(ext.trim()))
}

function attachmentFileUrl(projectId: number, requirementId: number, attachmentId: number): string {
  return `/api/v1/projects/${projectId}/requirements/${requirementId}/attachments/${attachmentId}/file`
}

function attachmentLabel(r: Requirement): string {
  const atts = r.attachments ?? []
  if (atts.length === 0) return '手工'
  if (atts.length === 1) return atts[0].filename
  return `${atts[0].filename} 等 ${atts.length} 个`
}

export default function RequirementList() {
  const { containerRef, scrollY } = useTableScrollY()
  const [viewMode, setViewMode] = useListViewMode('list-view:requirements')

  const { message } = App.useApp()
  const navigate = useNavigate()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [searchInput, setSearchInput] = useState('')
  const [searchDebounced, setSearchDebounced] = useState('')

  useEffect(() => {
    const t = window.setTimeout(() => {
      setSearchDebounced(searchInput)
      setPage(1)
    }, 400)
    return () => window.clearTimeout(t)
  }, [searchInput])

  const listQuery = useQuery({
    queryKey: ['requirements', pid, page, pageSize, searchDebounced],
    queryFn: () =>
      apiJson<PageRes<Requirement>>(listUrl(pid, page, pageSize, searchDebounced)),
    enabled: Number.isFinite(pid),
  })

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Requirement | null>(null)
  const [pendingFiles, setPendingFiles] = useState<UploadFile[]>([])
  const [listPreview, setListPreview] = useState<ListPreviewSession | null>(null)
  const [formPreview, setFormPreview] = useState<PreviewTarget | null>(null)
  const [analysisReq, setAnalysisReq] = useState<Requirement | null>(null)
  const [analysisPending, setAnalysisPending] = useState<RequirementAnalysis | null>(null)
  const [analysisMessages, setAnalysisMessages] = useState<AnalysisChatMessage[]>([])
  const [analysisChatInput, setAnalysisChatInput] = useState('')
  const analysisChatEndRef = useRef<HTMLDivElement>(null)
  const { collapsed: analysisChatCollapsed, toggleCollapsed: toggleAnalysisChatCollapsed } =
    useAgentChatCollapsed('analysis-chat-collapsed')
  const [planReq, setPlanReq] = useState<Requirement | null>(null)
  const [planDraft, setPlanDraft] = useState<string | null>(null)
  const [planMessages, setPlanMessages] = useState<PlanDraftChatMessage[]>([])
  const [planChatInput, setPlanChatInput] = useState('')
  const [planName, setPlanName] = useState('')
  const [planVersion, setPlanVersion] = useState('')
  const [planIteration, setPlanIteration] = useState('')
  const [linkedPlans, setLinkedPlans] = useState<LinkedPlan[]>([])
  const [updatePlanId, setUpdatePlanId] = useState<number | null>(null)
  const planChatEndRef = useRef<HTMLDivElement>(null)
  const { collapsed: planChatCollapsed, toggleCollapsed: togglePlanChatCollapsed } =
    useAgentChatCollapsed('plan-draft-chat-collapsed')

  const statusSelectOptions = useMemo(() => {
    const opts = REQUIREMENT_STATUS_OPTIONS.map((o) => ({ value: o.value, label: o.label }))
    const cur = editing?.status
    if (cur && !opts.some((o) => o.value === cur)) {
      opts.unshift({ value: cur, label: `${cur}（历史）` })
    }
    return opts
  }, [editing])

  const prioritySelectOptions = useMemo(() => {
    const opts = PRIORITY_OPTIONS.map((o) => ({ value: o.value, label: o.label }))
    const cur = editing?.priority
    if (cur && !opts.some((o) => o.value === cur)) {
      opts.unshift({ value: cur, label: `${cur}（历史）` })
    }
    return opts
  }, [editing])
  const [form] = Form.useForm<{
    title: string
    body_md: string
    status: string
    priority?: string | undefined
  }>()
  const [importing, setImporting] = useState(false)

  const savedAttachments = editing?.attachments ?? []
  const hasAttachments = pendingFiles.length > 0 || savedAttachments.length > 0

  async function handleImportBodyFile(file: File): Promise<boolean> {
    if (!isMdOrTxt(file)) {
      message.error('导入正文仅支持 .md / .txt；其它类型请作为附件上传')
      return false
    }
    if (hasAttachments) {
      message.error('已有附件时不能导入正文，请先移除附件')
      return false
    }
    const cur = String(form.getFieldValue('body_md') ?? '').trim()
    if (cur) {
      const ok = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: '覆盖正文？',
          content: '导入将替换当前正文内容。',
          okText: '覆盖',
          cancelText: '取消',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!ok) return false
    }
    setImporting(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await apiFetch(`/api/v1/projects/${pid}/requirements/import-body`, {
        method: 'POST',
        body: fd,
      })
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
      const body = data as { body_md: string }
      form.setFieldValue('body_md', body.body_md)
      setPendingFiles([])
      setFormPreview(null)
      message.success('已从文件导入正文')
    } catch (e) {
      message.error(errText(e))
    } finally {
      setImporting(false)
    }
    return false
  }

  const createMut = useMutation({
    mutationFn: async (values: {
      title: string
      body_md: string
      status: string
      priority?: string
    }) => {
      const files = pendingFiles
        .map((f) => f.originFileObj)
        .filter((f): f is NonNullable<typeof f> => f != null)
      if (files.length > 0) {
        const fd = new FormData()
        for (const file of files) fd.append('files', file)
        fd.append('title', values.title)
        fd.append('status', values.status || 'draft')
        const p = priorityFromForm(values.priority)
        if (p) fd.append('priority', p)
        return apiJson<Requirement>(`/api/v1/projects/${pid}/requirements/from-files`, {
          method: 'POST',
          body: fd,
        })
      }
      return apiJson<Requirement>(`/api/v1/projects/${pid}/requirements/`, {
        method: 'POST',
        body: JSON.stringify({
          title: values.title,
          body_md: values.body_md,
          status: values.status || 'draft',
          priority: priorityFromForm(values.priority),
        }),
      })
    },
    onSuccess: () => {
      message.success('已创建')
      setModalOpen(false)
      setPendingFiles([])
      setFormPreview(null)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['requirements', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const updateMut = useMutation({
    mutationFn: async (values: {
      title: string
      body_md: string
      status: string
      priority?: string
    }) => {
      const id = editing!.id
      const files = pendingFiles
        .map((f) => f.originFileObj)
        .filter((f): f is NonNullable<typeof f> => f != null)
      for (const file of files) {
        const fd = new FormData()
        fd.append('file', file)
        await apiJson<RequirementAttachment>(
          `/api/v1/projects/${pid}/requirements/${id}/attachments`,
          { method: 'POST', body: fd },
        )
      }
      const patchBody: Record<string, unknown> = {
        title: values.title,
        status: values.status,
        priority: priorityFromForm(values.priority),
      }
      const stillAttachmentMode = files.length > 0 || (editing?.attachments?.length ?? 0) > 0
      if (!stillAttachmentMode) {
        patchBody.body_md = values.body_md
      }
      return apiJson<Requirement>(`/api/v1/projects/${pid}/requirements/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patchBody),
      })
    },
    onSuccess: () => {
      message.success('已保存')
      setModalOpen(false)
      setEditing(null)
      setPendingFiles([])
      setFormPreview(null)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['requirements', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function handleDeleteSavedAttachment(att: RequirementAttachment) {
    if (!editing) return
    try {
      const updated = await apiJson<Requirement>(
        `/api/v1/projects/${pid}/requirements/${editing.id}/attachments/${att.id}`,
        { method: 'DELETE' },
      )
      setEditing(updated)
      form.setFieldValue('body_md', updated.body_md)
      if (formPreview?.sourceUrl?.includes(`/attachments/${att.id}/`)) {
        setFormPreview(null)
      }
      message.success('已删除附件')
      qc.invalidateQueries({ queryKey: ['requirements', pid] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  async function handleDelete(id: number) {
    try {
      const res = await apiFetch(`/api/v1/projects/${pid}/requirements/${id}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const t = await res.text()
        let msg = '删除失败'
        try {
          const j = JSON.parse(t) as { message?: string }
          msg = j.message ?? msg
        } catch {
          /* ignore */
        }
        message.error(msg)
        return
      }
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['requirements', pid] })
    } catch {
      message.error('删除失败')
    }
  }

  const analyzeMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<Requirement>(`/api/v1/projects/${pid}/requirements/${id}/analyze`, {
        method: 'POST',
      }),
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: ['requirements', pid] })
      setAnalysisReq(row)
      setAnalysisPending(row.analysis_pending_json ?? null)
      setAnalysisMessages(row.analysis_messages_json ?? [])
      setAnalysisChatInput('')
      message.success('需求分析已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  function rememberAnalysis(
    id: number,
    patch: Partial<
      Pick<
        Requirement,
        | 'analysis_json'
        | 'analyzed_at'
        | 'analysis_messages_json'
        | 'analysis_pending_json'
      >
    >,
  ) {
    qc.setQueriesData<PageRes<Requirement>>({ queryKey: ['requirements', pid] }, (old) => {
      if (!old) return old
      return {
        ...old,
        items: old.items.map((item) => (item.id === id ? { ...item, ...patch } : item)),
      }
    })
  }

  function applyAnalysisChat(res: AnalysisChatResponse) {
    setAnalysisPending(res.pending_analysis)
    setAnalysisMessages(res.messages ?? [])
    setAnalysisReq((prev) =>
      prev && prev.id === res.requirement_id
        ? {
            ...prev,
            analysis_json: res.analysis,
            analyzed_at: res.analyzed_at,
            analysis_pending_json: res.pending_analysis,
            analysis_messages_json: res.messages ?? [],
          }
        : prev,
    )
    rememberAnalysis(res.requirement_id, {
      analysis_json: res.analysis,
      analyzed_at: res.analyzed_at,
      analysis_pending_json: res.pending_analysis,
      analysis_messages_json: res.messages ?? [],
    })
  }

  const chatAnalysisMut = useMutation({
    mutationFn: (payload: {
      requirementId: number
      message: string
      analysis?: RequirementAnalysis | null
    }) =>
      apiJson<AnalysisChatResponse>(
        `/api/v1/projects/${pid}/requirements/${payload.requirementId}/analyze/chat`,
        {
          method: 'POST',
          body: JSON.stringify({
            message: payload.message,
            analysis: payload.analysis ?? undefined,
          }),
        },
      ),
    onSuccess: (res) => {
      applyAnalysisChat(res)
      setAnalysisChatInput('')
    },
    onError: (e) => message.error(errText(e)),
  })

  const acceptAnalysisMut = useMutation({
    mutationFn: (requirementId: number) =>
      apiJson<AnalysisChatResponse>(
        `/api/v1/projects/${pid}/requirements/${requirementId}/analyze/accept`,
        { method: 'POST' },
      ),
    onSuccess: (res) => {
      applyAnalysisChat(res)
      message.success('已应用修改')
    },
    onError: (e) => message.error(errText(e)),
  })

  const discardAnalysisMut = useMutation({
    mutationFn: (requirementId: number) =>
      apiJson<AnalysisChatResponse>(
        `/api/v1/projects/${pid}/requirements/${requirementId}/analyze/discard`,
        { method: 'POST' },
      ),
    onSuccess: (res) => {
      applyAnalysisChat(res)
      message.success('已放弃修改')
    },
    onError: (e) => message.error(errText(e)),
  })

  function openAnalysis(row: Requirement) {
    setAnalysisReq(row)
    setAnalysisPending(row.analysis_pending_json ?? null)
    setAnalysisMessages(row.analysis_messages_json ?? [])
    setAnalysisChatInput('')
  }

  function closeAnalysis() {
    setAnalysisReq(null)
    setAnalysisPending(null)
    setAnalysisMessages([])
    setAnalysisChatInput('')
  }

  function sendAnalysisChat() {
    const text = analysisChatInput.trim()
    if (!analysisReq || !text || !analysisReq.analysis_json) return
    chatAnalysisMut.mutate({
      requirementId: analysisReq.id,
      message: text,
      analysis: analysisPending ?? analysisReq.analysis_json,
    })
  }

  useEffect(() => {
    if (!analysisReq?.analysis_json) return
    analysisChatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [analysisMessages, analysisReq, chatAnalysisMut.isPending])

  function rememberDraft(
    id: number,
    bodyMd: string,
    draftedAt: string,
    messages: PlanDraftChatMessage[],
  ) {
    qc.setQueriesData<PageRes<Requirement>>({ queryKey: ['requirements', pid] }, (old) => {
      if (!old) return old
      return {
        ...old,
        items: old.items.map((item) =>
          item.id === id
            ? {
                ...item,
                plan_draft_md: bodyMd,
                plan_draft_at: draftedAt,
                plan_draft_messages_json: messages,
              }
            : item,
        ),
      }
    })
  }

  function applyPlanDraft(res: PlanDraftResponse) {
    setPlanDraft(res.body_md)
    setPlanMessages(res.messages ?? [])
    rememberDraft(res.requirement_id, res.body_md, res.drafted_at, res.messages ?? [])
  }

  const generatePlanMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<PlanDraftResponse>(`/api/v1/projects/${pid}/requirements/${id}/test-plan-draft`, {
        method: 'POST',
      }),
    onSuccess: (res) => {
      applyPlanDraft(res)
      setPlanChatInput('')
      message.success('测试计划草稿已生成')
    },
    onError: (e) => message.error(errText(e)),
  })

  const chatPlanMut = useMutation({
    mutationFn: (payload: { requirementId: number; message: string; bodyMd: string }) =>
      apiJson<PlanDraftResponse>(
        `/api/v1/projects/${pid}/requirements/${payload.requirementId}/test-plan-draft/chat`,
        {
          method: 'POST',
          body: JSON.stringify({
            message: payload.message,
            body_md: payload.bodyMd,
          }),
        },
      ),
    onSuccess: (res) => {
      applyPlanDraft(res)
      setPlanChatInput('')
    },
    onError: (e) => message.error(errText(e)),
  })

  const acceptPlanMut = useMutation({
    mutationFn: (payload: {
      requirementId: number
      mode: 'create' | 'update'
      planId?: number
      bodyMd: string
      name: string
      version: string
      iteration: string
    }) =>
      apiJson<{ id: number }>(
        `/api/v1/projects/${pid}/requirements/${payload.requirementId}/test-plan-draft/accept`,
        {
          method: 'POST',
          body: JSON.stringify({
            mode: payload.mode,
            plan_id: payload.mode === 'update' ? payload.planId : null,
            body_md: payload.bodyMd,
            name: payload.name,
            version: payload.version || null,
            iteration: payload.iteration || null,
          }),
        },
      ),
    onSuccess: (plan) => {
      message.success('已写入测试计划')
      setPlanReq(null)
      setPlanDraft(null)
      setPlanMessages([])
      setPlanChatInput('')
      qc.invalidateQueries({ queryKey: ['requirements', pid] })
      qc.invalidateQueries({ queryKey: ['test-plans', pid] })
      navigate(`/projects/${pid}/test-plans/${plan.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  useEffect(() => {
    if (planReq == null || planDraft == null) return
    planChatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [planReq, planDraft, planMessages, chatPlanMut.isPending])

  function applyLinkedPlan(plan: LinkedPlan | undefined) {
    if (!plan) {
      setUpdatePlanId(null)
      return
    }
    setUpdatePlanId(plan.id)
    setPlanName(plan.name)
    setPlanVersion(plan.version ?? '')
    setPlanIteration(plan.iteration ?? '')
  }

  async function openPlanDraft(row: Requirement) {
    setPlanReq(row)
    setPlanDraft(row.plan_draft_md)
    setPlanMessages(row.plan_draft_messages_json ?? [])
    setPlanChatInput('')
    setPlanName(`测试计划：${row.title}`.slice(0, 512))
    setPlanVersion('')
    setPlanIteration('')
    setLinkedPlans([])
    setUpdatePlanId(null)
    try {
      const page = await apiJson<PageRes<LinkedPlan>>(
        `/api/v1/projects/${pid}/test-plans/?requirement_id=${row.id}&page_size=100`,
      )
      const items = page.items ?? []
      setLinkedPlans(items)
      if (items[0]) applyLinkedPlan(items[0])
    } catch (e) {
      message.error(errText(e))
    }
  }

  function closePlanDraft() {
    setPlanReq(null)
    setPlanDraft(null)
    setPlanMessages([])
    setPlanChatInput('')
  }

  function sendPlanChat() {
    const text = planChatInput.trim()
    if (!planReq || !planDraft?.trim() || !text || chatPlanMut.isPending) return
    chatPlanMut.mutate({
      requirementId: planReq.id,
      message: text,
      bodyMd: planDraft,
    })
  }

  function acceptPlan(mode: 'create' | 'update') {
    if (!planReq || !planDraft?.trim()) {
      if (planReq && !planDraft?.trim()) message.error('请填写计划正文')
      return
    }
    if (!planName.trim()) {
      message.error('请填写计划名称')
      return
    }
    if (mode === 'update' && updatePlanId == null) {
      message.error('请选择要更新的测试计划')
      return
    }
    acceptPlanMut.mutate({
      requirementId: planReq.id,
      mode,
      planId: updatePlanId ?? undefined,
      bodyMd: planDraft.trim(),
      name: planName.trim(),
      version: planVersion.trim(),
      iteration: planIteration.trim(),
    })
  }

  function openCreate() {
    setEditing(null)
    setPendingFiles([])
    setFormPreview(null)
    form.setFieldsValue({
      title: '',
      body_md: '',
      status: 'draft',
      priority: undefined,
    })
    setModalOpen(true)
  }

  function openEdit(r: Requirement) {
    setEditing(r)
    setPendingFiles([])
    setFormPreview(null)
    form.setFieldsValue({
      title: r.title,
      body_md: r.body_md,
      status: r.status,
      priority: priorityToForm(r.priority),
    })
    setModalOpen(true)
  }

  function submitModal() {
    void form.validateFields().then((values) => {
      const files = pendingFiles
        .map((f) => f.originFileObj)
        .filter((f): f is NonNullable<typeof f> => f != null)
      const hasAtt = files.length > 0 || (editing?.attachments?.length ?? 0) > 0
      if (!hasAtt && !(values.body_md || '').trim()) {
        message.error('请填写正文或上传附件')
        return
      }
      if (editing) {
        updateMut.mutate(values)
      } else {
        createMut.mutate(values)
      }
    })
  }

  function openListPreview(r: Requirement, att?: RequirementAttachment) {
    const first = att ?? r.attachments[0]
    if (!first) return
    setListPreview({ requirement: r, attachmentId: first.id })
  }

  const listPreviewAtt =
    listPreview?.requirement.attachments.find((a) => a.id === listPreview.attachmentId) ?? null

  const actionBtnStyle = { fontSize: 12, paddingInline: 4 }

  function requirementActions(r: Requirement) {
    return (
      <Space size="small" wrap>
        <Button type="link" size="small" style={actionBtnStyle} onClick={() => openEdit(r)}>
          编辑
        </Button>
        {r.has_attachments && r.attachments[0] ? (
          <Button type="link" size="small" style={actionBtnStyle} onClick={() => openListPreview(r)}>
            预览
          </Button>
        ) : null}
        <Button
          type="link"
          size="small"
          style={actionBtnStyle}
          loading={analyzeMut.isPending && analyzeMut.variables === r.id}
          onClick={() => openAnalysis(r)}
        >
          需求分析
        </Button>
        <Button
          type="link"
          size="small"
          style={actionBtnStyle}
          loading={generatePlanMut.isPending && generatePlanMut.variables === r.id}
          onClick={() => void openPlanDraft(r)}
        >
          测试计划
        </Button>
        <Button
          type="link"
          size="small"
          style={actionBtnStyle}
          onClick={() => navigate(`../test-cases?requirement_id=${r.id}`)}
        >
          查看用例
        </Button>
        <Button
          type="link"
          size="small"
          style={actionBtnStyle}
          onClick={() => navigate(`/projects/${pid}/test-cases/ai?requirement=${r.id}`)}
        >
          AI生成用例
        </Button>
        <Popconfirm title="确定删除该需求？" onConfirm={() => void handleDelete(r.id)}>
          <Button type="link" size="small" danger style={actionBtnStyle}>
            删除
          </Button>
        </Popconfirm>
      </Space>
    )
  }

  const columns: ColumnsType<Requirement> = [
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
      render: (t: string, r) => (
        <Typography.Text
          ellipsis
          title={r.has_attachments ? attachmentLabel(r) : r.body_md}
        >
          {t}
        </Typography.Text>
      ),
    },
    {
      title: '来源',
      key: 'source',
      width: 160,
      ellipsis: true,
      render: (_: unknown, r) =>
        r.has_attachments ? (
          <Typography.Text ellipsis title={attachmentLabel(r)}>
            {attachmentLabel(r)}
          </Typography.Text>
        ) : (
          '手工'
        ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => labelForRequirementStatus(v),
    },
    { title: '优先级', dataIndex: 'priority', width: 90, render: (p: string | null) => p ?? '—' },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: (v: string) => formatBeijing(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 440,
      render: (_: unknown, r) => requirementActions(r),
    },
  ]

  if (!Number.isFinite(pid)) {
    return <p className="error">无效项目</p>
  }

  const data = listQuery.data
  const items = data?.items ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="需求管理" />
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索标题、正文或文件名"
              style={{ width: 280 }}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
            <Button type="primary" onClick={openCreate}>
              新建需求
            </Button>
          </Space>
          <ListViewToggle value={viewMode} onChange={setViewMode} />
        </Space>
      </div>
      <div className={`list-page-body is-${viewMode}`}>
        {viewMode === 'table' ? (
          <div className="list-page-table" ref={containerRef}>
            <Table<Requirement>
              rowKey="id"
              loading={listQuery.isLoading}
              columns={columns}
              dataSource={items}
              pagination={{
                current: page,
                pageSize,
                total: data?.total ?? 0,
                showSizeChanger: true,
                showTotal: (t) => `共 ${t} 条`,
                pageSizeOptions: [10, 20, 50, 100],
              }}
              onChange={(pg) => {
                setPage(pg.current ?? 1)
                setPageSize(pg.pageSize ?? 20)
              }}
              scroll={{ y: scrollY }}
            />
          </div>
        ) : (
          <Spin spinning={listQuery.isLoading}>
            {items.length === 0 && !listQuery.isLoading ? (
              <p className="muted">暂无需求</p>
            ) : (
              <div className="list-card-grid">
                {items.map((r) => (
                  <article key={r.id} className="list-card">
                    <h3 className="list-card-title" title={r.title}>
                      {r.title}
                    </h3>
                    <div className="list-card-meta">
                      <span title={attachmentLabel(r)}>
                        {r.has_attachments ? attachmentLabel(r) : '手工'}
                      </span>
                      <span>{labelForRequirementStatus(r.status)}</span>
                      <span>{r.priority ?? '—'}</span>
                      <span>{formatBeijing(r.updated_at)}</span>
                    </div>
                    <div className="list-card-actions">{requirementActions(r)}</div>
                  </article>
                ))}
              </div>
            )}
            <div className="list-card-pagination">
              <Pagination
                current={page}
                pageSize={pageSize}
                total={data?.total ?? 0}
                showSizeChanger
                showTotal={(t) => `共 ${t} 条`}
                pageSizeOptions={[10, 20, 50, 100]}
                onChange={(p, s) => {
                  setPage(p)
                  setPageSize(s)
                }}
              />
            </div>
          </Spin>
        )}
      </div>
      <Modal
        title={editing ? '编辑需求' : '新建需求'}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
          setPendingFiles([])
          setFormPreview(null)
          form.resetFields()
        }}
        onOk={submitModal}
        confirmLoading={createMut.isPending || updateMut.isPending}
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input maxLength={512} />
          </Form.Item>
          <Form.Item
            label={
              <Space align="center">
                <span>正文 (Markdown)</span>
                <Upload
                  beforeUpload={(f) => handleImportBodyFile(f as File)}
                  accept=".md,.txt"
                  showUploadList={false}
                  disabled={importing || hasAttachments}
                >
                  <Button
                    type="link"
                    size="small"
                    loading={importing}
                    disabled={hasAttachments}
                    style={{ padding: 0 }}
                  >
                    从文件导入
                  </Button>
                </Upload>
              </Space>
            }
            required={!hasAttachments}
            extra={
              hasAttachments
                ? '已选择附件模式：正文与附件二选一，保存时以附件为准'
                : '正文与附件二选一必填'
            }
          >
            {hasAttachments ? (
              <Typography.Text type="secondary">附件模式下正文不可编辑</Typography.Text>
            ) : (
              <Form.Item
                name="body_md"
                noStyle
                rules={[{ required: true, message: '请输入正文或上传附件' }]}
              >
                <Input.TextArea
                  rows={8}
                  placeholder="支持 Markdown；可用 .md / .txt 导入正文；或下方上传附件"
                />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item
            label="附件"
            required={hasAttachments || !(form.getFieldValue('body_md') || '').trim()}
            extra="支持图片、PDF、DOCX、txt/md/log/json、zip，单文件 ≤20MB；与正文二选一"
          >
            <Upload
              multiple
              accept={ATTACH_ACCEPT}
              fileList={pendingFiles}
              beforeUpload={(file) => {
                if (!isAllowedAttachment(file)) {
                  message.error(`${file.name} 类型不支持`)
                  return Upload.LIST_IGNORE
                }
                if (file.size > MAX_ATTACHMENT_BYTES) {
                  message.error(`${file.name} 超过 20MB`)
                  return Upload.LIST_IGNORE
                }
                if ((form.getFieldValue('body_md') || '').trim() && pendingFiles.length === 0 && savedAttachments.length === 0) {
                  form.setFieldValue('body_md', '')
                  message.info('已切换为附件模式，正文将清空')
                }
                return false
              }}
              onChange={({ fileList }) => {
                setPendingFiles(fileList.map((item) => ({ ...item, status: 'done' })))
              }}
              onPreview={(file) => {
                const local = file.originFileObj
                if (local instanceof File) {
                  setFormPreview({
                    title: local.name,
                    filename: local.name,
                    mime: local.type,
                    localFile: local,
                  })
                }
              }}
            >
              <Button size="small">上传附件</Button>
            </Upload>
            {savedAttachments.length > 0 ? (
              <div style={{ marginTop: 12 }}>
                {savedAttachments.map((att) => (
                  <div
                    key={att.id}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}
                  >
                    <Typography.Link
                      onClick={() =>
                        setFormPreview({
                          title: att.filename,
                          filename: att.filename,
                          mime: att.mime,
                          sourceUrl: attachmentFileUrl(pid, editing!.id, att.id),
                        })
                      }
                    >
                      {att.filename}
                    </Typography.Link>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {(att.size_bytes / 1024).toFixed(1)} KB
                    </Typography.Text>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0 }}
                      onClick={() =>
                        setFormPreview({
                          title: att.filename,
                          filename: att.filename,
                          mime: att.mime,
                          sourceUrl: attachmentFileUrl(pid, editing!.id, att.id),
                        })
                      }
                    >
                      预览
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      danger
                      style={{ padding: 0 }}
                      onClick={() => void handleDeleteSavedAttachment(att)}
                    >
                      删除
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}
            {formPreview ? (
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                  预览：{formPreview.filename}
                </Typography.Text>
                <RequirementSourcePreview
                  localFile={formPreview.localFile}
                  sourceUrl={formPreview.sourceUrl}
                  mime={formPreview.mime}
                  filename={formPreview.filename}
                  height={280}
                />
              </div>
            ) : null}
          </Form.Item>
          <Form.Item name="status" label="状态" rules={[{ required: true, message: '请选择状态' }]}>
            <Select options={statusSelectOptions} placeholder="请选择" />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <Select allowClear placeholder="无优先级" options={prioritySelectOptions} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={listPreviewAtt?.filename || listPreview?.requirement.title || '预览'}
        open={listPreview != null}
        onCancel={() => setListPreview(null)}
        footer={null}
        width="min(960px, calc(100vw - 48px))"
        styles={{ body: { overflow: 'auto', maxHeight: 'calc(100vh - 160px)', paddingTop: 12 } }}
        destroyOnHidden
      >
        {listPreview ? (
          <div>
            {listPreview.requirement.attachments.length > 1 ? (
              <Space wrap style={{ marginBottom: 12 }}>
                {listPreview.requirement.attachments.map((att) => (
                  <Button
                    key={att.id}
                    size="small"
                    type={att.id === listPreview.attachmentId ? 'primary' : 'default'}
                    onClick={() => setListPreview({ ...listPreview, attachmentId: att.id })}
                  >
                    {att.filename}
                  </Button>
                ))}
              </Space>
            ) : null}
            {listPreviewAtt ? (
              <RequirementSourcePreview
                sourceUrl={attachmentFileUrl(pid, listPreview.requirement.id, listPreviewAtt.id)}
                mime={listPreviewAtt.mime}
                filename={listPreviewAtt.filename}
                height={560}
              />
            ) : (
              <Typography.Text type="secondary">未找到附件</Typography.Text>
            )}
          </div>
        ) : null}
      </Modal>

      <Modal
        className="req-analysis-modal plan-draft-modal"
        title={analysisReq ? `需求分析：${analysisReq.title}` : '需求分析'}
        open={analysisReq != null}
        onCancel={closeAnalysis}
        footer={
          <Space wrap>
            <Button onClick={closeAnalysis}>关闭</Button>
            {analysisPending ? (
              <>
                <Button
                  loading={discardAnalysisMut.isPending}
                  disabled={
                    chatAnalysisMut.isPending ||
                    acceptAnalysisMut.isPending ||
                    analyzeMut.isPending
                  }
                  onClick={() => analysisReq && discardAnalysisMut.mutate(analysisReq.id)}
                >
                  放弃修改
                </Button>
                <Button
                  type="primary"
                  loading={acceptAnalysisMut.isPending}
                  disabled={
                    chatAnalysisMut.isPending ||
                    discardAnalysisMut.isPending ||
                    analyzeMut.isPending
                  }
                  onClick={() => analysisReq && acceptAnalysisMut.mutate(analysisReq.id)}
                >
                  应用修改
                </Button>
              </>
            ) : null}
            {analysisReq?.analysis_json ? (
              <Popconfirm
                title={
                  analysisPending
                    ? '有待确认的修改，重新分析会丢弃它们并覆盖当前结果，确定继续？'
                    : '重新分析会覆盖当前结果，确定继续？'
                }
                onConfirm={() => analyzeMut.mutate(analysisReq.id)}
              >
                <Button
                  loading={analyzeMut.isPending}
                  disabled={
                    chatAnalysisMut.isPending ||
                    acceptAnalysisMut.isPending ||
                    discardAnalysisMut.isPending
                  }
                >
                  重新分析
                </Button>
              </Popconfirm>
            ) : (
              <Button
                type="primary"
                loading={analyzeMut.isPending}
                disabled={!analysisReq}
                onClick={() => analysisReq && analyzeMut.mutate(analysisReq.id)}
              >
                生成分析
              </Button>
            )}
          </Space>
        }
        width={analysisReq?.analysis_json ? 1100 : 520}
        destroyOnHidden
        styles={
          analysisReq?.analysis_json
            ? {
                body: {
                  height: 'min(75vh, 720px)',
                  overflow: 'hidden',
                  paddingTop: 8,
                  display: 'flex',
                  flexDirection: 'column',
                },
              }
            : undefined
        }
      >
        {analyzeMut.isPending && !analysisReq?.analysis_json ? (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <Spin />
          </div>
        ) : analysisReq?.analysis_json ? (
          <div className="plan-draft-modal-split">
            <div className="plan-draft-modal-main">
              <RequirementAnalysisPane
                analysis={analysisReq.analysis_json}
                pending={analysisPending}
                analyzedAt={analysisReq.analyzed_at}
              />
            </div>
            <aside
              className={`plan-draft-chat${analysisChatCollapsed ? ' is-collapsed' : ''}`}
              aria-label="分析对话"
            >
              <div className="plan-draft-chat-toolbar">
                {!analysisChatCollapsed ? (
                  <div className="plan-draft-chat-label">对话改稿</div>
                ) : null}
                <AgentChatCollapseButton
                  collapsed={analysisChatCollapsed}
                  onToggle={toggleAnalysisChatCollapsed}
                />
              </div>
              {!analysisChatCollapsed ? (
                <>
                  <div className="plan-draft-chat-messages">
                    {analysisMessages.length === 0 ? (
                      <div className="req-analysis-chat-empty">
                        <div className="req-analysis-chat-empty-title">对话改稿</div>
                        <div className="req-analysis-chat-empty-desc">
                          说明想增删或改写的内容，修订后可对照颜色确认再应用
                        </div>
                      </div>
                    ) : (
                      analysisMessages.map((m, i) => (
                        <div
                          key={`${m.role}-${i}`}
                          className={`plan-draft-chat-bubble plan-draft-chat-bubble--${m.role}`}
                        >
                          <div className="plan-draft-chat-bubble-role">
                            {m.role === 'user' ? '我' : '助手'}
                          </div>
                          <div className="plan-draft-chat-bubble-body">{m.content}</div>
                        </div>
                      ))
                    )}
                    {chatAnalysisMut.isPending && chatAnalysisMut.variables?.message ? (
                      <div className="plan-draft-chat-bubble plan-draft-chat-bubble--user">
                        <div className="plan-draft-chat-bubble-role">我</div>
                        <div className="plan-draft-chat-bubble-body">
                          {chatAnalysisMut.variables.message}
                        </div>
                      </div>
                    ) : null}
                    {chatAnalysisMut.isPending ? (
                      <div className="plan-draft-chat-bubble plan-draft-chat-bubble--assistant">
                        <div className="plan-draft-chat-bubble-role">助手</div>
                        <div className="plan-draft-chat-bubble-body muted">正在修订分析…</div>
                      </div>
                    ) : null}
                    <div ref={analysisChatEndRef} />
                  </div>
                  <div className="plan-draft-chat-composer">
                    <Input.TextArea
                      value={analysisChatInput}
                      onChange={(e) => setAnalysisChatInput(e.target.value)}
                      placeholder="例如：补充验证码相关功能，去掉过时的待澄清"
                      autoSize={{ minRows: 1, maxRows: 4 }}
                      disabled={
                        chatAnalysisMut.isPending ||
                        analyzeMut.isPending ||
                        acceptAnalysisMut.isPending ||
                        discardAnalysisMut.isPending
                      }
                      onPressEnter={(e) => {
                        if (!e.shiftKey) {
                          e.preventDefault()
                          sendAnalysisChat()
                        }
                      }}
                    />
                    <Button
                      type="primary"
                      size="small"
                      loading={chatAnalysisMut.isPending}
                      disabled={
                        !analysisChatInput.trim() ||
                        chatAnalysisMut.isPending ||
                        analyzeMut.isPending ||
                        acceptAnalysisMut.isPending ||
                        discardAnalysisMut.isPending
                      }
                      onClick={sendAnalysisChat}
                    >
                      发送
                    </Button>
                  </div>
                </>
              ) : null}
            </aside>
          </div>
        ) : (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            尚未生成分析结果，点击下方「生成分析」开始。
          </Typography.Paragraph>
        )}
      </Modal>

      <Modal
        className="plan-draft-modal"
        title={planReq ? `测试计划草稿：${planReq.title}` : '测试计划草稿'}
        open={planReq != null}
        onCancel={closePlanDraft}
        footer={
          <Space wrap>
            <Button onClick={closePlanDraft}>关闭</Button>
            {planDraft?.trim() ? (
              <Popconfirm
                title="重新生成会覆盖当前草稿与对话，确定继续？"
                onConfirm={() => planReq && generatePlanMut.mutate(planReq.id)}
              >
                <Button
                  loading={generatePlanMut.isPending}
                  disabled={!planReq || chatPlanMut.isPending}
                >
                  重新生成
                </Button>
              </Popconfirm>
            ) : (
              <Button
                loading={generatePlanMut.isPending}
                disabled={!planReq}
                onClick={() => planReq && generatePlanMut.mutate(planReq.id)}
              >
                生成草稿
              </Button>
            )}
            {linkedPlans.length > 0 ? (
              <Button
                loading={acceptPlanMut.isPending}
                disabled={!planDraft?.trim() || chatPlanMut.isPending}
                onClick={() => acceptPlan('create')}
              >
                另存为新计划
              </Button>
            ) : null}
            <Button
              type="primary"
              loading={acceptPlanMut.isPending}
              disabled={!planDraft?.trim() || chatPlanMut.isPending}
              onClick={() => acceptPlan(linkedPlans.length > 0 ? 'update' : 'create')}
            >
              {linkedPlans.length > 0 ? '更新已有计划' : '写入测试计划'}
            </Button>
          </Space>
        }
        width={planDraft != null ? 1100 : 520}
        destroyOnHidden
        styles={
          planDraft != null
            ? {
                body: {
                  height: 'min(75vh, 720px)',
                  overflow: 'hidden',
                  paddingTop: 8,
                  display: 'flex',
                  flexDirection: 'column',
                },
              }
            : undefined
        }
      >
        {generatePlanMut.isPending && planDraft == null ? (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <Spin />
          </div>
        ) : planDraft != null ? (
          <div className="plan-draft-modal-split">
            <div className="plan-draft-modal-main">
              <Form layout="vertical" className="plan-draft-modal-form" size="small">
                <Form.Item label="计划名称" required>
                  <Input
                    maxLength={512}
                    value={planName}
                    onChange={(e) => setPlanName(e.target.value)}
                  />
                </Form.Item>
                <div style={{ display: 'flex', gap: 12 }}>
                  <Form.Item label="版本" style={{ flex: 1 }}>
                    <Input value={planVersion} onChange={(e) => setPlanVersion(e.target.value)} />
                  </Form.Item>
                  <Form.Item label="迭代" style={{ flex: 1 }}>
                    <Input value={planIteration} onChange={(e) => setPlanIteration(e.target.value)} />
                  </Form.Item>
                </div>
                {linkedPlans.length > 1 ? (
                  <Form.Item label="更新目标">
                    <Select
                      value={updatePlanId ?? undefined}
                      options={linkedPlans.map((plan) => ({ value: plan.id, label: plan.name }))}
                      onChange={(id: number) =>
                        applyLinkedPlan(linkedPlans.find((plan) => plan.id === id))
                      }
                    />
                  </Form.Item>
                ) : linkedPlans.length === 1 ? (
                  <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                    已有计划「{linkedPlans[0].name}」。更新会覆盖正文，执行轮次保留。
                  </Typography.Paragraph>
                ) : null}
              </Form>
              <MarkdownPane
                withOutline
                className="plan-draft-md"
                value={planDraft}
                onChange={setPlanDraft}
              />
            </div>
            <aside
              className={`plan-draft-chat${planChatCollapsed ? ' is-collapsed' : ''}`}
              aria-label="草稿对话"
            >
              <div className="plan-draft-chat-toolbar">
                {!planChatCollapsed ? (
                  <div className="plan-draft-chat-label">对话改稿</div>
                ) : null}
                <AgentChatCollapseButton
                  collapsed={planChatCollapsed}
                  onToggle={togglePlanChatCollapsed}
                />
              </div>
              {!planChatCollapsed ? (
                <>
                  <div className="plan-draft-chat-messages">
                    {planMessages.length === 0 ? (
                      <div className="plan-draft-chat-empty">生成后可在此说明修改意见</div>
                    ) : (
                      planMessages.map((m, i) => (
                        <div
                          key={`${m.role}-${i}`}
                          className={`plan-draft-chat-bubble plan-draft-chat-bubble--${m.role}`}
                        >
                          <div className="plan-draft-chat-bubble-role">
                            {m.role === 'user' ? '我' : '助手'}
                          </div>
                          <div className="plan-draft-chat-bubble-body">{m.content}</div>
                        </div>
                      ))
                    )}
                    {chatPlanMut.isPending && chatPlanMut.variables?.message ? (
                      <div className="plan-draft-chat-bubble plan-draft-chat-bubble--user">
                        <div className="plan-draft-chat-bubble-role">我</div>
                        <div className="plan-draft-chat-bubble-body">
                          {chatPlanMut.variables.message}
                        </div>
                      </div>
                    ) : null}
                    {chatPlanMut.isPending ? (
                      <div className="plan-draft-chat-bubble plan-draft-chat-bubble--assistant">
                        <div className="plan-draft-chat-bubble-role">助手</div>
                        <div className="plan-draft-chat-bubble-body muted">正在修订草稿…</div>
                      </div>
                    ) : null}
                    <div ref={planChatEndRef} />
                  </div>
                  <div className="plan-draft-chat-composer">
                    <Input.TextArea
                      value={planChatInput}
                      onChange={(e) => setPlanChatInput(e.target.value)}
                      placeholder="例如：压缩人天表，风险补一条"
                      autoSize={{ minRows: 1, maxRows: 4 }}
                      disabled={chatPlanMut.isPending || generatePlanMut.isPending}
                      onPressEnter={(e) => {
                        if (!e.shiftKey) {
                          e.preventDefault()
                          sendPlanChat()
                        }
                      }}
                    />
                    <Button
                      type="primary"
                      size="small"
                      loading={chatPlanMut.isPending}
                      disabled={
                        !planChatInput.trim() ||
                        chatPlanMut.isPending ||
                        generatePlanMut.isPending
                      }
                      onClick={sendPlanChat}
                    >
                      发送
                    </Button>
                  </div>
                </>
              ) : null}
            </aside>
          </div>
        ) : (
          <Typography.Text type="secondary">尚未生成草稿，点击下方「生成草稿」开始。</Typography.Text>
        )}
      </Modal>
    </div>
  )
}
