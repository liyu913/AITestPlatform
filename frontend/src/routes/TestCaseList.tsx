import {
  App,
  Button,
  Descriptions,
  Drawer,
  Dropdown,
  Form,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
} from 'antd'
import type { MenuProps } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  CASE_TYPE_OPTIONS,
  labelForCaseResult,
  labelForLifecycleStatus,
  LIFECYCLE_STATUS_OPTIONS,
  PRIORITY_OPTIONS,
  priorityFromForm,
  priorityToForm,
} from '../constants/workflow'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import ListViewToggle, { useListViewMode } from '../components/ListViewToggle'
import {
  dictToRows,
  parseTestDataToRows,
  rowsToDict,
  TestDataKvList,
  VariablesTags,
  type Kv,
} from '../components/TestDataVariables'
import { STEPS_EXPECTED_PLACEHOLDER } from '../constants/testcase'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'
import TestCaseAutomateModal from './TestCaseAutomateModal'
import CaseDesignEvidence, { type DesignTrace } from '../components/CaseDesignEvidence'

type Step = {
  id: number
  order: number
  action: string
  expected: string
}

type TestCase = {
  design_trace?: DesignTrace | null
  id: number
  project_id: number
  requirement_id: number | null
  title: string
  module: string | null
  precondition: string | null
  test_data: string | null
  variables?: Record<string, string>
  case_type: string | null
  overall_expected: string
  priority: string | null
  status: string
  lifecycle_status?: string
  last_result?: string | null
  last_result_at?: string | null
  version_no?: number
  tags_json: string[] | null
  creator_id: number | null
  created_at: string
  updated_at: string
  steps: Step[]
  linked_ui_case_count?: number
}

type CaseVersion = {
  id: number
  case_id: number
  version_no: number
  lifecycle_status: string
  change_source: string
  change_note: string | null
  created_by_id: number | null
  created_at: string
  snapshot: {
    title?: string
    overall_expected?: string
    steps?: { order: number; action: string; expected: string }[]
  } | null
}

type CaseReview = {
  id: number
  case_id: number
  version_id: number | null
  version_no: number
  decision: string
  comment: string
  reviewer_id: number | null
  created_at: string
}

type PageRes<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

type CaseListFilters = {
  search: string
  requirementId: number | undefined
  module: string | undefined
  priority: string | undefined
  status: string | undefined
}

function appendCaseFilters(sp: URLSearchParams, filters: CaseListFilters) {
  const s = filters.search.trim()
  if (s) {
    sp.set('search', s)
  }
  if (filters.requirementId !== undefined) {
    sp.set('requirement_id', String(filters.requirementId))
  }
  if (filters.module) {
    sp.set('module', filters.module)
  }
  if (filters.priority) {
    sp.set('priority', filters.priority)
  }
  if (filters.status) {
    // Filter by authoring lifecycle; execution outcome is a separate facet.
    sp.set('lifecycle_status', filters.status)
  }
}

function listUrl(
  pid: number,
  page: number,
  pageSize: number,
  filters: CaseListFilters,
) {
  const sp = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  appendCaseFilters(sp, filters)
  return `/api/v1/projects/${pid}/test-cases/?${sp.toString()}`
}

function exportUrl(pid: number, format: 'xlsx' | 'xmind', filters: CaseListFilters) {
  const sp = new URLSearchParams({ format })
  appendCaseFilters(sp, filters)
  return `/api/v1/projects/${pid}/test-cases/export?${sp.toString()}`
}

async function downloadExport(path: string, fallbackName: string) {
  const res = await apiFetch(path)
  if (!res.ok) {
    const text = await res.text()
    let data: unknown = text
    try {
      data = text ? JSON.parse(text) : null
    } catch {
      /* keep text */
    }
    throw new ApiError(res.status, data)
  }
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') || ''
  const m = /filename="?([^";]+)"?/i.exec(cd)
  const filename = m?.[1] || fallbackName
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

type StepLike = { order: number; action: string; expected: string }

const CHANGE_SOURCE_LABEL: Record<string, string> = {
  create: '创建',
  migration: '迁移',
  edit: '编辑',
  review: '评审',
  archive: '归档',
  ai_confirmed: 'AI 确认',
}

function structuredSteps(c: TestCase | null): boolean {
  return !!c && (!!c.design_trace || c.steps.length > 1 || c.steps.some(s => s.expected && s.expected !== STEPS_EXPECTED_PLACEHOLDER))
}

function stepsToText(steps: StepLike[]): string {
  const sorted = [...steps].sort((a, b) => a.order - b.order)
  if (sorted.length === 0) return ''
  if (sorted.length === 1 && sorted[0].expected === STEPS_EXPECTED_PLACEHOLDER) {
    return sorted[0].action
  }
  return sorted
    .map((s, i) => {
      const n = i + 1
      if (s.expected && s.expected !== STEPS_EXPECTED_PLACEHOLDER) {
        return `${n}. ${s.action}\n   期望：${s.expected}`
      }
      return `${n}. ${s.action}`
    })
    .join('\n\n')
}

function textToSteps(text: string): { order: number; action: string; expected: string }[] {
  const t = text.trim()
  if (!t) return []
  return [{ order: 0, action: t, expected: STEPS_EXPECTED_PLACEHOLDER }]
}

type ReqBrief = { id: number; title: string }

type CaseFormValues = {
  title: string
  module?: string
  precondition?: string
  variable_rows?: Kv[]
  case_type?: string | undefined
  overall_expected: string
  lifecycle_status: string
  priority?: string | undefined
  requirement_id?: number | undefined
  steps_text: string
  step_items?: StepLike[]
}

function caseVariableRows(c: TestCase): Kv[] {
  const vars = c.variables
  if (vars && Object.keys(vars).length) return dictToRows(vars)
  return parseTestDataToRows(c.test_data)
}

export default function TestCaseList() {
  const { containerRef, scrollY } = useTableScrollY()
  const [viewMode, setViewMode] = useListViewMode('list-view:test-cases')

  const { message } = App.useApp()
  const { projectId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const ridRaw = searchParams.get('requirement_id')
  const requirementFilter =
    ridRaw && /^\d+$/.test(ridRaw) ? Number(ridRaw) : undefined
  // Deep link from the workbench: /test-cases?status=pending_review
  const statusFromUrl = searchParams.get('status') ?? undefined

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [searchInput, setSearchInput] = useState('')
  const [searchDebounced, setSearchDebounced] = useState('')
  const [moduleFilter, setModuleFilter] = useState<string | undefined>()
  const [priorityFilter, setPriorityFilter] = useState<string | undefined>()
  const [statusFilter, setStatusFilter] = useState<string | undefined>(statusFromUrl)
  const [exporting, setExporting] = useState(false)
  const [automateCase, setAutomateCase] = useState<TestCase | null>(null)

  useEffect(() => {
    const t = window.setTimeout(() => {
      setSearchDebounced(searchInput)
      setPage(1)
    }, 400)
    return () => window.clearTimeout(t)
  }, [searchInput])

  const listFilters: CaseListFilters = {
    search: searchDebounced,
    requirementId: requirementFilter,
    module: moduleFilter,
    priority: priorityFilter,
    status: statusFilter,
  }

  const listQuery = useQuery({
    queryKey: [
      'testcases',
      pid,
      page,
      pageSize,
      searchDebounced,
      requirementFilter,
      moduleFilter,
      priorityFilter,
      statusFilter,
    ],
    queryFn: () =>
      apiJson<PageRes<TestCase>>(listUrl(pid, page, pageSize, listFilters)),
    enabled: Number.isFinite(pid),
  })

  const modulesQuery = useQuery({
    queryKey: ['testcases', 'modules', pid],
    queryFn: () => apiJson<string[]>(`/api/v1/projects/${pid}/test-cases/modules`),
    enabled: Number.isFinite(pid),
  })

  // Deep link from global search: /test-cases?case=ID opens that case for editing.
  const openedCaseRef = useRef<number | null>(null)
  const caseParam = searchParams.get('case')
  useEffect(() => {
    if (!caseParam) return
    const caseId = Number(caseParam)
    if (!Number.isFinite(caseId) || openedCaseRef.current === caseId) return
    const next = new URLSearchParams(searchParams)
    next.delete('case')
    setSearchParams(next, { replace: true })
    openedCaseRef.current = caseId
    const cached = (listQuery.data?.items ?? []).find((row) => row.id === caseId)
    if (cached) {
      openEdit(cached)
      return
    }
    void apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/${caseId}`)
      .then(openEdit)
      .catch(() => undefined)
  }, [caseParam, listQuery.data, pid, searchParams, setSearchParams])

  const [reviewTarget, setReviewTarget] = useState<TestCase | null>(null)
  const [reviewComment, setReviewComment] = useState('')

  const versionsQuery = useQuery({
    queryKey: ['case-versions', pid, reviewTarget?.id],
    queryFn: () =>
      apiJson<CaseVersion[]>(`/api/v1/projects/${pid}/test-cases/${reviewTarget!.id}/versions`),
    enabled: Boolean(reviewTarget?.id),
  })

  const reviewsQuery = useQuery({
    queryKey: ['case-reviews', pid, reviewTarget?.id],
    queryFn: () =>
      apiJson<CaseReview[]>(`/api/v1/projects/${pid}/test-cases/${reviewTarget!.id}/reviews`),
    enabled: Boolean(reviewTarget?.id),
  })

  function refreshCase(id: number) {
    void qc.invalidateQueries({ queryKey: ['testcases', pid] })
    void qc.invalidateQueries({ queryKey: ['case-versions', pid, id] })
    void qc.invalidateQueries({ queryKey: ['case-reviews', pid, id] })
  }

  const submitReviewMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/${id}/submit-review`, {
        method: 'POST',
      }),
    onSuccess: (row) => {
      message.success('已提交评审')
      setReviewTarget(row)
      refreshCase(row.id)
    },
    onError: (e) => message.error(errText(e)),
  })

  const decideReviewMut = useMutation({
    mutationFn: (p: { id: number; decision: 'approved' | 'rejected' }) =>
      apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/${p.id}/review`, {
        method: 'POST',
        body: JSON.stringify({ decision: p.decision, comment: reviewComment.trim() }),
      }),
    onSuccess: (row, p) => {
      message.success(p.decision === 'approved' ? '评审通过，用例已确认' : '已驳回，回到草稿')
      setReviewTarget(row)
      setReviewComment('')
      refreshCase(row.id)
    },
    onError: (e) => message.error(errText(e)),
  })

  const archiveMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/${id}/archive`, {
        method: 'POST',
        body: JSON.stringify({}),
      }),
    onSuccess: (row) => {
      message.success('已归档')
      setReviewTarget(row)
      refreshCase(row.id)
    },
    onError: (e) => message.error(errText(e)),
  })

  const restoreMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/${id}/restore`, { method: 'POST' }),
    onSuccess: (row) => {
      message.success('已恢复为草稿')
      setReviewTarget(row)
      refreshCase(row.id)
    },
    onError: (e) => message.error(errText(e)),
  })

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<TestCase | null>(null)
  const [stepsModalCase, setStepsModalCase] = useState<TestCase | null>(null)
  const [form] = Form.useForm<CaseFormValues>()

  const requirementsPicker = useQuery({    queryKey: ['requirements', 'picker', pid],
    queryFn: () =>
      apiJson<PageRes<ReqBrief>>(
        `/api/v1/projects/${pid}/requirements/?page=1&page_size=100`,
      ),
    enabled: Number.isFinite(pid),
  })

  const requirementFilterOptions = useMemo(() => {
    const items = requirementsPicker.data?.items ?? []
    return items.map((r) => ({ value: r.id, label: `${r.id} · ${r.title}` }))
  }, [requirementsPicker.data])

  function setRequirementUrlFilter(nextId: number | undefined) {
    setPage(1)
    const next = new URLSearchParams(searchParams)
    if (nextId === undefined) {
      next.delete('requirement_id')
    } else {
      next.set('requirement_id', String(nextId))
    }
    setSearchParams(next, { replace: true })
  }

  const moduleFilterOptions = useMemo(
    () => (modulesQuery.data ?? []).map((m) => ({ value: m, label: m })),
    [modulesQuery.data],
  )

  async function handleExport(format: 'xlsx' | 'xmind') {
    if (!Number.isFinite(pid) || exporting) return
    setExporting(true)
    try {
      await downloadExport(
        exportUrl(pid, format, listFilters),
        `test-cases-${pid}.${format}`,
      )
      message.success(format === 'xlsx' ? '已导出 Excel' : '已导出 XMind')
    } catch (ex) {
      message.error(errText(ex))
    } finally {
      setExporting(false)
    }
  }

  const exportMenuItems: MenuProps['items'] = [
    { key: 'xlsx', label: '导出 Excel' },
    { key: 'xmind', label: '导出 XMind' },
  ]

  const requirementSelectOptions = useMemo(() => {
    const items = requirementsPicker.data?.items ?? []
    const base = items.map((r) => ({ value: r.id, label: `${r.id} · ${r.title}` }))
    const rid = editing?.requirement_id
    if (rid != null && !base.some((o) => o.value === rid)) {
      base.unshift({ value: rid, label: `${rid}（当前关联）` })
    }
    return base
  }, [requirementsPicker.data, editing?.requirement_id])

  const caseStatusSelectOptions = useMemo(() => {
    const opts = LIFECYCLE_STATUS_OPTIONS.map((o) => ({ value: o.value, label: o.label }))
    const cur = editing?.lifecycle_status ?? editing?.status
    if (cur && !opts.some((o) => o.value === cur)) {
      opts.unshift({ value: cur, label: `${cur}（历史）` })
    }
    return opts
  }, [editing])

  const casePrioritySelectOptions = useMemo(() => {
    const opts = PRIORITY_OPTIONS.map((o) => ({ value: o.value, label: o.label }))
    const cur = editing?.priority
    if (cur && !opts.some((o) => o.value === cur)) {
      opts.unshift({ value: cur, label: `${cur}（历史）` })
    }
    return opts
  }, [editing])

  const createMut = useMutation({
    mutationFn: (values: CaseFormValues) => {
      const steps = textToSteps(values.steps_text)
      const variables = rowsToDict(values.variable_rows)
      return apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/`, {
        method: 'POST',
        body: JSON.stringify({
          title: values.title,
          module: values.module?.trim() || null,
          precondition: values.precondition?.trim() || null,
          variables,
          case_type: values.case_type || null,
          overall_expected: values.overall_expected,
          lifecycle_status: values.lifecycle_status || 'draft',
          priority: priorityFromForm(values.priority),
          requirement_id: values.requirement_id ?? null,
          steps,
        }),
      })
    },
    onSuccess: () => {
      message.success('已创建')
      setModalOpen(false)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['testcases', pid] })
      qc.invalidateQueries({ queryKey: ['testcases', 'modules', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const updateMut = useMutation({
    mutationFn: (values: CaseFormValues) => {
      const steps = structuredSteps(editing)
        ? (values.step_items ?? []).map((s, order) => ({ order, action: s.action, expected: s.expected }))
        : textToSteps(values.steps_text)
      const variables = rowsToDict(values.variable_rows)
      return apiJson<TestCase>(`/api/v1/projects/${pid}/test-cases/${editing!.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          title: values.title,
          module: values.module?.trim() || null,
          precondition: values.precondition?.trim() || null,
          variables,
          case_type: values.case_type || null,
          overall_expected: values.overall_expected,
          lifecycle_status: values.lifecycle_status,
          priority: priorityFromForm(values.priority),
          requirement_id: values.requirement_id ?? null,
          steps,
        }),
      })
    },
    onSuccess: () => {
      message.success('已保存')
      setModalOpen(false)
      setEditing(null)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['testcases', pid] })
      qc.invalidateQueries({ queryKey: ['testcases', 'modules', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function handleDelete(id: number) {
    try {
      const res = await apiFetch(`/api/v1/projects/${pid}/test-cases/${id}`, {
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
      qc.invalidateQueries({ queryKey: ['testcases', pid] })
      qc.invalidateQueries({ queryKey: ['testcases', 'modules', pid] })
    } catch {
      message.error('删除失败')
    }
  }

  function openCreate() {
    setEditing(null)
    form.setFieldsValue({
      title: '',
      module: '',
      precondition: '',
      variable_rows: [{ key: '', value: '' }],
      case_type: undefined,
      overall_expected: '',
      lifecycle_status: 'draft',
      priority: undefined,
      requirement_id: undefined,
      steps_text: '',
    })
    setModalOpen(true)
  }

  function openEdit(c: TestCase) {
    setEditing(c)
    form.setFieldsValue({
      title: c.title,
      module: c.module ?? '',
      precondition: c.precondition ?? '',
      variable_rows: caseVariableRows(c),
      case_type: c.case_type ?? undefined,
      overall_expected: c.overall_expected ?? '',
      lifecycle_status: c.lifecycle_status ?? c.status,
      priority: priorityToForm(c.priority),
      requirement_id: c.requirement_id ?? undefined,
      steps_text: stepsToText(c.steps),
      step_items: [...c.steps].sort((a, b) => a.order - b.order),
    })
    setModalOpen(true)
  }

  function submitModal() {
    void form.validateFields().then((values) => {
      if (editing) {
        updateMut.mutate(values)
      } else {
        createMut.mutate(values)
      }
    })
  }

  function requirementLabel(rid: number | null): string {
    if (rid == null) return '—'
    const req = (requirementsPicker.data?.items ?? []).find((x) => x.id === rid)
    return req?.title?.trim() || String(rid)
  }

  const actionBtnStyle = { fontSize: 12, paddingInline: 4 }

  function caseActions(r: TestCase, opts?: { showSteps?: boolean }) {
    const showSteps = opts?.showSteps ?? true
    return (
      <Space size="small" wrap>
        <Button
          type="link"
          size="small"
          style={actionBtnStyle}
          onClick={() => setAutomateCase(r)}
        >
          自动化执行
        </Button>
        {(r.linked_ui_case_count ?? 0) > 0 ? (
          <Link to={`/projects/${pid}/ui-cases?manual_case_id=${r.id}`}>
            <Button type="link" size="small" style={actionBtnStyle}>
              查看 UI 用例
            </Button>
          </Link>
        ) : null}
        {showSteps && (r.steps?.length ?? 0) > 0 ? (
          <Button
            type="link"
            size="small"
            style={actionBtnStyle}
            onClick={() => setStepsModalCase(r)}
          >
            查看步骤
          </Button>
        ) : null}
        <Button type="link" size="small" style={actionBtnStyle} onClick={() => openEdit(r)}>
          编辑
        </Button>
        <Button
          type="link"
          size="small"
          style={actionBtnStyle}
          onClick={() => {
            setReviewTarget(r)
            setReviewComment('')
          }}
        >
          版本/评审
        </Button>
        <Popconfirm title="确定删除该用例？" onConfirm={() => void handleDelete(r.id)}>
          <Button type="link" size="small" danger style={actionBtnStyle}>
            删除
          </Button>
        </Popconfirm>
      </Space>
    )
  }

  const columns: ColumnsType<TestCase> = [
    {
      title: '标题',
      dataIndex: 'title',
      ellipsis: true,
    },
    {
      title: '生命周期',
      dataIndex: 'lifecycle_status',
      width: 100,
      render: (v: string | undefined, r) => labelForLifecycleStatus(v ?? r.status),
    },
    {
      title: '最近结果',
      dataIndex: 'last_result',
      width: 100,
      render: (v: string | null | undefined) => labelForCaseResult(v),
    },
    { title: '类型', dataIndex: 'case_type', width: 80, render: (v: string | null) => v || '—' },
    { title: '优先级', dataIndex: 'priority', width: 90, render: (p: string | null) => p ?? '—' },
    {
      title: '模块',
      dataIndex: 'module',
      width: 140,
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
    {
      title: '需求',
      dataIndex: 'requirement_id',
      width: 160,
      ellipsis: true,
      render: (rid: number | null) => {
        const label = requirementLabel(rid)
        return <span title={label}>{label}</span>
      },
    },
    {
      title: '步骤',
      key: 'nsteps',
      width: 72,
      render: (_: unknown, r) => {
        const n = r.steps?.length ?? 0
        if (n === 0) return '—'
        return (
          <Button
            type="link"
            size="small"
            style={actionBtnStyle}
            onClick={() => setStepsModalCase(r)}
          >
            {n}
          </Button>
        )
      },
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 170,
      render: (v: string) => formatBeijing(v),
    },
    {
      title: '操作',
      key: 'actions',
      width: 280,
      render: (_: unknown, r) => caseActions(r, { showSteps: false }),
    },
  ]

  if (!Number.isFinite(pid)) {
    return <p className="error">无效项目</p>
  }

  const data = listQuery.data

  const stepsModalColumns: ColumnsType<StepLike> = [
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
      dataIndex: 'expected',
    },
  ]

  const stepsModalData = (stepsModalCase?.steps ?? [])
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((s, i) => ({ order: i, action: s.action, expected: s.expected }))

  const items = data?.items ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
      <PageBanner title="用例管理" />
      <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="搜索标题"
            style={{ width: 240 }}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <Select
            allowClear
            placeholder="全部需求"
            style={{ minWidth: 220 }}
            value={requirementFilter}
            options={requirementFilterOptions}
            loading={requirementsPicker.isLoading}
            onChange={(v) => setRequirementUrlFilter(v ?? undefined)}
          />
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="全部模块"
            style={{ minWidth: 140 }}
            value={moduleFilter}
            options={moduleFilterOptions}
            loading={modulesQuery.isLoading}
            onChange={(v) => {
              setPage(1)
              setModuleFilter(v ?? undefined)
            }}
          />
          <Select
            allowClear
            placeholder="全部优先级"
            style={{ minWidth: 130 }}
            value={priorityFilter}
            options={PRIORITY_OPTIONS}
            onChange={(v) => {
              setPage(1)
              setPriorityFilter(v ?? undefined)
            }}
          />
          <Select
            allowClear
            placeholder="全部状态"
            style={{ minWidth: 120 }}
            value={statusFilter}
            options={LIFECYCLE_STATUS_OPTIONS}
            onChange={(v) => {
              const next = v ?? undefined
              setPage(1)
              setStatusFilter(next)
              const params = new URLSearchParams(searchParams)
              if (next) {
                params.set('status', next)
              } else {
                params.delete('status')
              }
              setSearchParams(params, { replace: true })
            }}
          />
          <Button type="primary" onClick={openCreate}>
            新建用例
          </Button>
          <Dropdown
            menu={{
              items: exportMenuItems,
              onClick: ({ key }) => {
                if (key === 'xlsx' || key === 'xmind') {
                  void handleExport(key)
                }
              },
            }}
            disabled={!Number.isFinite(pid)}
          >
            <Button loading={exporting}>导出</Button>
          </Dropdown>
        </Space>
        <ListViewToggle value={viewMode} onChange={setViewMode} />
      </Space>
      </div>
      <div className={`list-page-body is-${viewMode}`}>
        {viewMode === 'table' ? (
          <div className="list-page-table" ref={containerRef}>
            <Table<TestCase>
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
              <p className="muted">暂无用例</p>
            ) : (
              <div className="list-card-grid">
                {items.map((r) => (
                  <article key={r.id} className="list-card">
                    <h3 className="list-card-title" title={r.title}>
                      {r.title}
                    </h3>
                    <div className="list-card-meta">
                      <span>{labelForLifecycleStatus(r.lifecycle_status ?? r.status)}</span>
                      <span>{labelForCaseResult(r.last_result)}</span>
                      <span>{r.case_type || '—'}</span>
                      <span>{r.priority ?? '—'}</span>
                      <span>{r.module || '—'}</span>
                      <span>{formatBeijing(r.updated_at)}</span>
                    </div>
                    <div className="list-card-actions">{caseActions(r)}</div>
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
      <Drawer
        title={reviewTarget ? `版本与评审 #${reviewTarget.id}` : '版本与评审'}
        open={Boolean(reviewTarget)}
        onClose={() => setReviewTarget(null)}
        width={760}
        destroyOnHidden
      >
        {reviewTarget ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions
              size="small"
              column={1}
              bordered
              items={[
                { key: 'title', label: '标题', children: reviewTarget.title },
                {
                  key: 'lifecycle',
                  label: '生命周期',
                  children: labelForLifecycleStatus(
                    reviewTarget.lifecycle_status ?? reviewTarget.status,
                  ),
                },
                {
                  key: 'result',
                  label: '最近结果',
                  children: labelForCaseResult(reviewTarget.last_result),
                },
                {
                  key: 'version',
                  label: '当前版本',
                  children: `v${reviewTarget.version_no ?? 1}`,
                },
              ]}
            />

            <Space wrap>
              {['draft', 'reopened'].includes(
                reviewTarget.lifecycle_status ?? reviewTarget.status,
              ) ? (
                <Button
                  type="primary"
                  loading={submitReviewMut.isPending}
                  onClick={() => submitReviewMut.mutate(reviewTarget.id)}
                >
                  提交评审
                </Button>
              ) : null}
              {(reviewTarget.lifecycle_status ?? reviewTarget.status) === 'pending_review' ? (
                <>
                  <Button
                    type="primary"
                    loading={decideReviewMut.isPending}
                    onClick={() =>
                      decideReviewMut.mutate({ id: reviewTarget.id, decision: 'approved' })
                    }
                  >
                    评审通过
                  </Button>
                  <Button
                    danger
                    loading={decideReviewMut.isPending}
                    onClick={() =>
                      decideReviewMut.mutate({ id: reviewTarget.id, decision: 'rejected' })
                    }
                  >
                    驳回
                  </Button>
                </>
              ) : null}
              {(reviewTarget.lifecycle_status ?? reviewTarget.status) === 'archived' ? (
                <Button
                  loading={restoreMut.isPending}
                  onClick={() => restoreMut.mutate(reviewTarget.id)}
                >
                  恢复为草稿
                </Button>
              ) : (
                <Button
                  loading={archiveMut.isPending}
                  onClick={() => archiveMut.mutate(reviewTarget.id)}
                >
                  归档
                </Button>
              )}
            </Space>

            {(reviewTarget.lifecycle_status ?? reviewTarget.status) === 'pending_review' ? (
              <Input.TextArea
                rows={3}
                value={reviewComment}
                placeholder="评审意见（驳回必填）"
                onChange={(event) => setReviewComment(event.target.value)}
              />
            ) : null}

            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>
                版本历史（{versionsQuery.data?.length ?? 0}）
              </div>
              <Table<CaseVersion>
                rowKey="id"
                size="small"
                loading={versionsQuery.isLoading}
                dataSource={versionsQuery.data ?? []}
                pagination={false}
                locale={{ emptyText: '暂无版本' }}
                expandable={{
                  expandedRowRender: (row) => (
                    <div>
                      <p className="muted" style={{ marginBottom: 4 }}>
                        {row.snapshot?.overall_expected
                          ? `总体期望：${row.snapshot.overall_expected}`
                          : '总体期望：—'}
                      </p>
                      <ol style={{ margin: 0, paddingInlineStart: 20 }}>
                        {(row.snapshot?.steps ?? []).map((step, index) => (
                          <li key={`${row.id}-${index}`}>
                            {step.action} → {step.expected}
                          </li>
                        ))}
                      </ol>
                    </div>
                  ),
                }}
                columns={[
                  { title: '版本', dataIndex: 'version_no', width: 70, render: (v) => `v${v}` },
                  {
                    title: '来源',
                    dataIndex: 'change_source',
                    width: 110,
                    render: (v: string) => CHANGE_SOURCE_LABEL[v] ?? v,
                  },
                  {
                    title: '说明',
                    dataIndex: 'change_note',
                    ellipsis: true,
                    render: (v: string | null) => v || '—',
                  },
                  {
                    title: '生命周期',
                    dataIndex: 'lifecycle_status',
                    width: 100,
                    render: (v: string) => labelForLifecycleStatus(v),
                  },
                  {
                    title: '时间',
                    dataIndex: 'created_at',
                    width: 160,
                    render: (v: string) => formatBeijing(v),
                  },
                ]}
              />
            </div>

            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>
                评审记录（{reviewsQuery.data?.length ?? 0}）
              </div>
              <Table<CaseReview>
                rowKey="id"
                size="small"
                loading={reviewsQuery.isLoading}
                dataSource={reviewsQuery.data ?? []}
                pagination={false}
                locale={{ emptyText: '暂无评审记录' }}
                columns={[
                  {
                    title: '结论',
                    dataIndex: 'decision',
                    width: 90,
                    render: (v: string) =>
                      v === 'approved' ? (
                        <Tag color="success">通过</Tag>
                      ) : (
                        <Tag color="error">驳回</Tag>
                      ),
                  },
                  { title: '版本', dataIndex: 'version_no', width: 70, render: (v) => `v${v}` },
                  { title: '意见', dataIndex: 'comment', ellipsis: true },
                  {
                    title: '时间',
                    dataIndex: 'created_at',
                    width: 160,
                    render: (v: string) => formatBeijing(v),
                  },
                ]}
              />
            </div>
          </Space>
        ) : null}
      </Drawer>

      <Modal
        className="draft-steps-modal"
        title={stepsModalCase?.title ?? '用例步骤'}
        open={stepsModalCase !== null}
        footer={
          <Button type="primary" onClick={() => setStepsModalCase(null)}>
            关闭
          </Button>
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
          <VariablesTags
            variables={stepsModalCase?.variables}
            fallbackText={stepsModalCase?.test_data}
          />
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
          dataSource={stepsModalData}
          locale={{ emptyText: '暂无步骤' }}
        />
        <CaseDesignEvidence trace={stepsModalCase?.design_trace} />
      </Modal>
      <Modal
        title={editing ? '编辑用例' : '新建用例'}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
          form.resetFields()
        }}
        onOk={submitModal}
        confirmLoading={createMut.isPending || updateMut.isPending}
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" size="middle">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input maxLength={512} />
          </Form.Item>
          <Form.Item
            name="lifecycle_status"
            label="生命周期"
            rules={[{ required: true, message: '请选择生命周期状态' }]}
            extra="执行结果由执行记录自动写入，不在此处设置"
          >
            <Select options={caseStatusSelectOptions} placeholder="请选择" />
          </Form.Item>
          <Form.Item name="module" label="模块">
            <Input maxLength={128} placeholder="所属功能模块" />
          </Form.Item>
          <Form.Item name="case_type" label="类型">
            <Select allowClear placeholder="未分类" options={CASE_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="priority" label="优先级">
            <Select
              allowClear
              placeholder="无优先级"
              options={casePrioritySelectOptions}
            />
          </Form.Item>
          <Form.Item name="requirement_id" label="关联需求">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="不关联需求"
              loading={requirementsPicker.isLoading}
              options={requirementSelectOptions}
            />
          </Form.Item>
          <Form.Item name="precondition" label="前置条件">
            <Input.TextArea rows={3} placeholder="执行步骤前需要满足的条件，没有可留空" />
          </Form.Item>
          <Form.Item
            label="测试数据"
            extra="步骤中可用 {{key}} 引用（转 UI 自动化时会合并进执行变量）"
          >
            <TestDataKvList />
          </Form.Item>
          {structuredSteps(editing) ? (
            <Form.List name="step_items">
              {(fields, { add, remove }) => (
                <>
                  {fields.map(({ key, name }, i) => (
                    <div key={key}>
                      <Form.Item name={[name, 'action']} label={`步骤 ${i + 1}`} rules={[{ required: true, whitespace: true }]}>
                        <Input.TextArea autoSize />
                      </Form.Item>
                      <Form.Item name={[name, 'expected']} label={`步骤 ${i + 1} 的预期结果`} rules={[{ required: true, whitespace: true }]}>
                        <Input.TextArea autoSize />
                      </Form.Item>
                      {fields.length > 1 && <Button type="link" onClick={() => remove(name)}>移除此步</Button>}
                    </div>
                  ))}
                  <Button onClick={() => add({ action: '', expected: '' })}>添加步骤</Button>
                </>
              )}
            </Form.List>
          ) : <Form.Item
            name="steps_text"
            label="步骤"
            rules={[{ required: true, message: '请填写步骤' }]}
          >
            <Input.TextArea
              rows={8}
              placeholder="按顺序描述测试步骤，每行一步或自由书写"
            />
          </Form.Item>}
          <Form.Item
            name="overall_expected"
            label="总体期望"
            rules={[{ required: true, message: '请填写该用例的总体期望' }]}
          >
            <Input.TextArea rows={3} placeholder="描述执行完所有步骤后，系统或业务应达到的整体结果" />
          </Form.Item>
        </Form>
      </Modal>
      <TestCaseAutomateModal
        open={automateCase !== null}
        projectId={pid}
        testCase={automateCase}
        onClose={() => setAutomateCase(null)}
      />
    </div>
  )
}
