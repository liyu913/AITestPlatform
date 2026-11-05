import {
  App,
  Button,
  Descriptions,
  Divider,
  Drawer,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { MenuProps } from 'antd'
import {
  DeleteOutlined,
  EllipsisOutlined,
  HistoryOutlined,
} from '@ant-design/icons'
import type { UploadFile } from 'antd/es/upload/interface'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  DEFECT_CATEGORY_OPTIONS,
  DEFECT_STATUS_OPTIONS,
  labelForDefectCategory,
  labelForDefectStatus,
  labelForExecutionResult,
  PRIORITY_OPTIONS,
} from '../constants/workflow'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type DefectAttachment = {
  id: number
  defect_id: number
  filename: string
  mime: string
  size_bytes: number
  uploader_id: number | null
  created_at: string
}

type Defect = {
  id: number
  project_id: number
  title: string
  description: string
  severity: string
  status: string
  category: string
  case_id: number | null
  case_title: string | null
  cycle_item_id: number | null
  requirement_id: number | null
  failure_source_type: string | null
  failure_source_id: number | null
  expected_result: string
  actual_result: string
  environment: string | null
  found_build: string | null
  fix_build: string | null
  verified_at: string | null
  verification_result: string | null
  verification_note: string
  regression_cycle_ids: number[]
  closed_at: string | null
  reporter_id: number | null
  assignee_id: number | null
  created_at: string
  updated_at: string
  attachments: DefectAttachment[]
}

type DefectHistoryRow = {
  id: number
  from_status: string
  to_status: string
  comment: string
  actor_id: number | null
  created_at: string
}

type DefectEvidence = {
  defect_id: number
  cycle: {
    id: number | null
    name: string | null
    plan_name: string | null
    target_build: string | null
    environment: string | null
  } | null
  item: {
    id: number | null
    asset_type: string | null
    asset_title: string | null
    result: string | null
    actual_result: string
    executed_at: string | null
    attempt_no: number
  } | null
  attempts: {
    id: number
    attempt_no: number
    execution_type: string
    status: string
    summary: string
    original_run_type: string | null
    original_run_id: number | null
    started_at: string | null
    finished_at: string | null
  }[]
  verification_result: string | null
  verification_note: string
  verified_at: string | null
  history: DefectHistoryRow[]
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

type CaseBrief = { id: number; title: string }

type DefectFormValues = {
  title: string
  description?: string
  severity: string
  status: string
  category: string
  case_id?: number | null
  expected_result?: string
  actual_result?: string
  environment?: string
  found_build?: string
  fix_build?: string
  status_comment?: string
}

type VerifyFormValues = {
  result: 'passed' | 'failed'
  fix_build: string
  note?: string
}

const ACCEPT =
  '.png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.md,.log,.json,.zip,.docx,image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain'
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function statusColor(s: string): string {
  if (s === 'open') return 'error'
  if (s === 'in_progress') return 'processing'
  if (s === 'resolved') return 'success'
  if (s === 'reopened') return 'warning'
  return 'default'
}

function isImage(mime: string): boolean {
  return mime.startsWith('image/')
}

async function postAttachment(projectId: number, defectId: number, file: File): Promise<string | null> {
  const fd = new FormData()
  fd.append('file', file)
  try {
    const res = await apiFetch(`/api/v1/projects/${projectId}/defects/${defectId}/attachments`, {
      method: 'POST',
      body: fd,
    })
    if (!res.ok) {
      let msg = '上传失败'
      try {
        const j = (await res.json()) as { message?: string }
        msg = j.message ?? msg
      } catch {
        /* ignore */
      }
      return msg
    }
    return null
  } catch (e) {
    return errText(e)
  }
}

export default function DefectList() {
  const { containerRef, scrollY } = useTableScrollY()
  const { message, modal } = App.useApp()
  const nav = useNavigate()
  const [regressionOpen, setRegressionOpen] = useState(false)
  const [regressionDefects, setRegressionDefects] = useState<number[]>([])
  const [regressionName, setRegressionName] = useState('回归验证')
  const [regressionBuild, setRegressionBuild] = useState('')
  const regressionMut = useMutation({
    mutationFn: () =>
      apiJson<{ cycle_id: number }>(`/api/v1/projects/${pid}/defects/regression-tasks`, {
        method: 'POST',
        body: JSON.stringify({
          defect_ids: regressionDefects,
          cycle_name: regressionName.trim() || '回归验证',
          target_build: regressionBuild.trim() || null,
        }),
      }),
    onSuccess: (row) => {
      message.success('回归任务已创建')
      setRegressionOpen(false)
      setRegressionDefects([])
      nav(`/projects/${pid}/cycles/${row.cycle_id}`)
    },
    onError: (e) => message.error(errText(e)),
  })
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editing, setEditing] = useState<Defect | null>(null)
  const [verifyTarget, setVerifyTarget] = useState<Defect | null>(null)
  const [historyTarget, setHistoryTarget] = useState<Defect | null>(null)
  const [detailTarget, setDetailTarget] = useState<Defect | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [pendingFiles, setPendingFiles] = useState<UploadFile[]>([])
  const [caseKeyword, setCaseKeyword] = useState('')
  const [caseSearch, setCaseSearch] = useState('')
  const seenCaseTitles = useRef(new Map<number, string>())
  const [form] = Form.useForm<DefectFormValues>()
  const [editForm] = Form.useForm<DefectFormValues>()
  const [verifyForm] = Form.useForm<VerifyFormValues>()
  const createCaseId = Form.useWatch('case_id', form)
  const editCaseId = Form.useWatch('case_id', editForm)

  useEffect(() => {
    const t = window.setTimeout(() => setCaseSearch(caseKeyword.trim()), 300)
    return () => window.clearTimeout(t)
  }, [caseKeyword])

  const casePickerOpen = createOpen || editOpen
  const cases = useQuery({
    queryKey: ['testcases', 'defect-picker', pid, caseSearch],
    queryFn: () => {
      const sp = new URLSearchParams({ page: '1', page_size: '100' })
      if (caseSearch) sp.set('search', caseSearch)
      return apiJson<PageRes<CaseBrief>>(`/api/v1/projects/${pid}/test-cases/?${sp}`)
    },
    enabled: Number.isFinite(pid) && casePickerOpen,
  })

  const list = useQuery({
    queryKey: ['defects', pid, statusFilter, categoryFilter, search, page, pageSize],
    queryFn: () => {
      const sp = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (statusFilter) sp.set('status', statusFilter)
      if (categoryFilter) sp.set('category', categoryFilter)
      if (search.trim()) sp.set('search', search.trim())
      return apiJson<PageRes<Defect>>(`/api/v1/projects/${pid}/defects/?${sp}`)
    },
    enabled: Number.isFinite(pid),
  })

  const historyQuery = useQuery({
    queryKey: ['defect-history', pid, historyTarget?.id],
    queryFn: () =>
      apiJson<DefectHistoryRow[]>(`/api/v1/projects/${pid}/defects/${historyTarget!.id}/history`),
    enabled: Number.isFinite(pid) && Boolean(historyTarget?.id),
  })

  const evidenceQuery = useQuery({
    queryKey: ['defect-evidence', pid, detailTarget?.id],
    queryFn: () =>
      apiJson<DefectEvidence>(`/api/v1/projects/${pid}/defects/${detailTarget!.id}/evidence`),
    enabled: Number.isFinite(pid) && Boolean(detailTarget?.id),
  })

  const detail = useQuery({
    queryKey: ['defect', pid, editing?.id],
    queryFn: () => apiJson<Defect>(`/api/v1/projects/${pid}/defects/${editing!.id}`),
    enabled: Number.isFinite(pid) && Boolean(editing?.id) && editOpen,
  })

  // Deep link from global search: /defects?defect=ID opens the evidence drawer.
  const openedDefectRef = useRef<number | null>(null)
  const defectParam = searchParams.get('defect')
  useEffect(() => {
    if (!defectParam) return
    const defectId = Number(defectParam)
    if (!Number.isFinite(defectId) || openedDefectRef.current === defectId) return
    const next = new URLSearchParams(searchParams)
    next.delete('defect')
    setSearchParams(next, { replace: true })
    openedDefectRef.current = defectId
    const cached = (list.data?.items ?? []).find((row) => row.id === defectId)
    if (cached) {
      setDetailTarget(cached)
      return
    }
    void apiJson<Defect>(`/api/v1/projects/${pid}/defects/${defectId}`)
      .then(setDetailTarget)
      .catch(() => undefined)
  }, [defectParam, list.data, pid, searchParams, setSearchParams])

  const caseOptions = useMemo(() => {
    const items = cases.data?.items ?? []
    for (const item of items) seenCaseTitles.current.set(item.id, item.title)
    if (editing?.case_id && editing.case_title) {
      seenCaseTitles.current.set(editing.case_id, editing.case_title)
    }
    if (detail.data?.case_id && detail.data.case_title) {
      seenCaseTitles.current.set(detail.data.case_id, detail.data.case_title)
    }
    const opts = items.map((item) => ({ value: item.id, label: `#${item.id} ${item.title}` }))
    const selected = createOpen ? createCaseId : editCaseId
    if (typeof selected === 'number' && !opts.some((opt) => opt.value === selected)) {
      const title = seenCaseTitles.current.get(selected)
      opts.unshift({
        value: selected,
        label: title ? `#${selected} ${title}` : `#${selected}`,
      })
    }
    return opts
  }, [cases.data, createOpen, createCaseId, editCaseId, editing, detail.data])

  const createMut = useMutation({
    mutationFn: async (values: DefectFormValues & { files: UploadFile[] }) => {
      const created = await apiJson<Defect>(`/api/v1/projects/${pid}/defects/`, {
        method: 'POST',
        body: JSON.stringify({
          title: values.title.trim(),
          description: values.description ?? '',
          severity: values.severity,
          status: values.status,
          category: values.category || 'product',
          case_id: values.case_id ?? null,
          expected_result: values.expected_result ?? '',
          actual_result: values.actual_result ?? '',
          environment: values.environment?.trim() || null,
          found_build: values.found_build?.trim() || null,
        }),
      })
      const failed: string[] = []
      for (const item of values.files) {
        const file = item.originFileObj
        if (!file) {
          failed.push(`${item.name}（无法读取文件）`)
          continue
        }
        const err = await postAttachment(pid, created.id, file)
        if (err) failed.push(`${file.name}（${err}）`)
      }
      return { failed }
    },
    onSuccess: ({ failed }) => {
      if (failed.length) {
        message.warning(`缺陷已创建，以下附件上传失败：${failed.join('、')}`)
      } else {
        message.success('缺陷已创建')
      }
      setCreateOpen(false)
      form.resetFields()
      setPendingFiles([])
      qc.invalidateQueries({ queryKey: ['defects', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchMut = useMutation({
    mutationFn: (p: { id: number; body: Record<string, unknown> }) =>
      apiJson<Defect>(`/api/v1/projects/${pid}/defects/${p.id}`, {
        method: 'PATCH',
        body: JSON.stringify(p.body),
      }),
    onSuccess: () => {
      message.success('已更新')
      setEditOpen(false)
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['defects', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const verifyMut = useMutation({
    mutationFn: (p: { id: number; body: VerifyFormValues }) =>
      apiJson<Defect>(`/api/v1/projects/${pid}/defects/${p.id}/verify`, {
        method: 'POST',
        body: JSON.stringify(p.body),
      }),
    onSuccess: (row) => {
      message.success(
        row.verification_result === 'passed'
          ? `复测通过，状态：${labelForDefectStatus(row.status)}`
          : '复测失败，缺陷已重新打开',
      )
      setVerifyTarget(null)
      verifyForm.resetFields()
      qc.invalidateQueries({ queryKey: ['defects', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const statusMut = useMutation({
    mutationFn: (p: { id: number; status: string }) =>
      apiJson<Defect>(`/api/v1/projects/${pid}/defects/${p.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: p.status }),
      }),
    onSuccess: (_row, p) => {
      message.success(p.status === 'closed' ? '缺陷已关闭' : '已重新打开')
      qc.invalidateQueries({ queryKey: ['defects', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function removeDefect(id: number) {
    try {
      const res = await apiFetch(`/api/v1/projects/${pid}/defects/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        message.error('删除失败')
        return
      }
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['defects', pid] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  async function uploadAttachment(defectId: number, file: File) {
    const err = await postAttachment(pid, defectId, file)
    if (err) {
      message.error(err)
      return
    }
    message.success('附件已上传')
    qc.invalidateQueries({ queryKey: ['defects', pid] })
    qc.invalidateQueries({ queryKey: ['defect', pid, defectId] })
  }

  async function removeAttachment(defectId: number, attachmentId: number) {
    try {
      const res = await apiFetch(
        `/api/v1/projects/${pid}/defects/${defectId}/attachments/${attachmentId}`,
        { method: 'DELETE' },
      )
      if (!res.ok) {
        message.error('删除附件失败')
        return
      }
      message.success('附件已删除')
      qc.invalidateQueries({ queryKey: ['defects', pid] })
      qc.invalidateQueries({ queryKey: ['defect', pid, defectId] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  async function openAttachment(defectId: number, att: DefectAttachment) {
    try {
      const res = await apiFetch(
        `/api/v1/projects/${pid}/defects/${defectId}/attachments/${att.id}/file`,
      )
      if (!res.ok) {
        message.error('打开附件失败')
        return
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      if (isImage(att.mime)) {
        setPreviewUrl(url)
      } else {
        const a = document.createElement('a')
        a.href = url
        a.download = att.filename
        a.click()
        URL.revokeObjectURL(url)
      }
    } catch (e) {
      message.error(errText(e))
    }
  }

  function startEditDefect(row: Defect) {
    setEditing(row)
    setCaseKeyword('')
    setCaseSearch('')
    if (row.case_id && row.case_title) {
      seenCaseTitles.current.set(row.case_id, row.case_title)
    }
    editForm.setFieldsValue({
      title: row.title,
      description: row.description,
      severity: row.severity,
      status: row.status,
      category: row.category,
      case_id: row.case_id ?? undefined,
      expected_result: row.expected_result,
      actual_result: row.actual_result,
      environment: row.environment ?? undefined,
      found_build: row.found_build ?? undefined,
      fix_build: row.fix_build ?? undefined,
    })
    setEditOpen(true)
  }

  function startVerifyDefect(row: Defect) {
    setVerifyTarget(row)
    verifyForm.setFieldsValue({
      result: 'passed',
      fix_build: row.fix_build ?? '',
      note: '',
    })
  }

  const columns: ColumnsType<Defect> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    { title: '严重度', dataIndex: 'severity', width: 80 },
    {
      title: '分类',
      dataIndex: 'category',
      width: 100,
      render: (c: string) => labelForDefectCategory(c),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{labelForDefectStatus(s)}</Tag>,
    },
    {
      title: '构建 / 复测',
      width: 190,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <span className="muted">发现：{row.found_build || '—'}</span>
          <span className="muted">修复：{row.fix_build || '—'}</span>
          <span>
            {row.verification_result === 'passed' ? (
              <Tag color="success">复测通过</Tag>
            ) : row.verification_result === 'failed' ? (
              <Tag color="error">复测失败</Tag>
            ) : (
              <span className="muted">未复测</span>
            )}
          </span>
        </Space>
      ),
    },
    {
      title: '关联用例',
      width: 200,
      ellipsis: true,
      render: (_, row) =>
        row.case_id
          ? row.case_title
            ? `#${row.case_id} ${row.case_title}`
            : `#${row.case_id}`
          : '—',
    },
    {
      title: '附件',
      width: 70,
      render: (_, row) => row.attachments?.length ?? 0,
    },
    { title: '更新时间', dataIndex: 'updated_at', ellipsis: true, render: (v: string) => formatBeijing(v) },
    {
      title: '操作',
      width: 320,
      render: (_, row) => {
        const moreItems: MenuProps['items'] = [
          {
            key: 'history',
            icon: <HistoryOutlined />,
            label: '状态历史',
            onClick: () => setHistoryTarget(row),
          },
          { type: 'divider' },
          {
            key: 'delete',
            danger: true,
            icon: <DeleteOutlined />,
            label: '删除缺陷',
            onClick: () =>
              modal.confirm({
                title: '确认删除该缺陷？',
                content: '删除后不可恢复。',
                okText: '删除',
                okButtonProps: { danger: true },
                cancelText: '取消',
                onOk: () => removeDefect(row.id),
              }),
          },
        ]
        return (
          <div className="list-card-actions-row">
            <Space size={4}>
              <Button type="primary" size="small" onClick={() => setDetailTarget(row)}>
                详情
              </Button>
              <Button size="small" onClick={() => startVerifyDefect(row)}>
                复测
              </Button>
              <Button
                type="text"
                size="small"
                loading={statusMut.isPending}
                onClick={() =>
                  statusMut.mutate({
                    id: row.id,
                    status: row.status === 'closed' ? 'reopened' : 'closed',
                  })
                }
              >
                {row.status === 'closed' ? '重新打开' : '关闭'}
              </Button>
              <Button type="text" size="small" onClick={() => startEditDefect(row)}>
                编辑
              </Button>
              <Dropdown menu={{ items: moreItems }} trigger={['click']} placement="bottomRight">
                <Button type="text" size="small" aria-label="更多操作">
                  <EllipsisOutlined />
                </Button>
              </Dropdown>
            </Space>
          </div>
        )
      },
    },
  ]

  const currentAttachments = detail.data?.attachments ?? editing?.attachments ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="缺陷与回归" />
        <Space style={{ marginBottom: 16 }} wrap>
          <Button
            type="primary"
            onClick={() => {
              form.setFieldsValue({
                severity: 'P2',
                status: 'open',
                category: 'product',
                case_id: undefined,
              })
              setCaseKeyword('')
              setCaseSearch('')
              setPendingFiles([])
              setCreateOpen(true)
            }}
          >
            新建缺陷
          </Button>
          <Button onClick={() => setRegressionOpen(true)}>创建回归任务</Button>
          <Input.Search
            allowClear
            placeholder="搜索标题"
            style={{ width: 220 }}
            onSearch={(v) => {
              setSearch(v)
              setPage(1)
            }}
          />
          <Select
            allowClear
            placeholder="按状态筛选"
            style={{ width: 140 }}
            options={DEFECT_STATUS_OPTIONS}
            value={statusFilter}
            onChange={(v) => {
              setStatusFilter(v)
              setPage(1)
            }}
          />
          <Select
            allowClear
            placeholder="按分类筛选"
            style={{ width: 150 }}
            options={DEFECT_CATEGORY_OPTIONS}
            value={categoryFilter}
            onChange={(v) => {
              setCategoryFilter(v)
              setPage(1)
            }}
          />
        </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table<Defect>
          rowKey="id"
          loading={list.isLoading}
          columns={columns}
          dataSource={list.data?.items ?? []}
          scroll={{ y: scrollY }}
          pagination={{
            current: page,
            pageSize,
            total: list.data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, s) => {
              setPage(p)
              setPageSize(s)
            },
          }}
        />
      </div>

      <Modal
        title="新建缺陷"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false)
          setPendingFiles([])
        }}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        destroyOnHidden
        width={640}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => createMut.mutate({ ...v, files: pendingFiles })}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="severity" label="严重度">
            <Select options={PRIORITY_OPTIONS} />
          </Form.Item>
          <Form.Item name="category" label="失败分类">
            <Select options={DEFECT_CATEGORY_OPTIONS} />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={DEFECT_STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item name="case_id" label="关联用例">
            <Select
              allowClear
              showSearch
              placeholder="选择关联用例"
              filterOption={false}
              onSearch={setCaseKeyword}
              options={caseOptions}
              loading={cases.isFetching}
              notFoundContent={cases.isFetching ? '加载中' : '无匹配用例'}
            />
          </Form.Item>
          <Form.Item name="expected_result" label="预期结果">
            <Input.TextArea rows={2} placeholder="按用例预期填写，便于关闭时对照" />
          </Form.Item>
          <Form.Item name="actual_result" label="实际结果">
            <Input.TextArea rows={2} placeholder="失败时的实际现象或报错" />
          </Form.Item>
          <Space style={{ width: '100%' }} size="middle">
            <Form.Item name="environment" label="环境" style={{ flex: 1 }}>
              <Input placeholder="如 预发 / staging" />
            </Form.Item>
            <Form.Item name="found_build" label="发现构建" style={{ flex: 1 }}>
              <Input placeholder="如 build-2026.11.05" />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item label="附件">
            <Upload
              multiple
              accept={ACCEPT}
              fileList={pendingFiles}
              disabled={createMut.isPending}
              beforeUpload={(file) => {
                if (file.size > MAX_ATTACHMENT_BYTES) {
                  message.error(`${file.name} 超过 10MB`)
                  return Upload.LIST_IGNORE
                }
                return false
              }}
              onChange={({ fileList }) =>
                setPendingFiles(fileList.map((item) => ({ ...item, status: 'done' })))
              }
            >
              <Button size="small">上传图片/附件</Button>
            </Upload>
            <div className="muted" style={{ marginTop: 8 }}>
              支持图片、PDF、txt/log、zip、docx，单文件 ≤10MB
            </div>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑缺陷"
        open={editOpen}
        onCancel={() => {
          setEditOpen(false)
          setEditing(null)
        }}
        onOk={() => editForm.submit()}
        confirmLoading={patchMut.isPending}
        destroyOnHidden
        width={720}
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(v) => {
            if (!editing) return
            patchMut.mutate({
              id: editing.id,
              body: {
                title: v.title.trim(),
                description: v.description ?? '',
                severity: v.severity,
                status: v.status,
                status_comment: v.status_comment?.trim() || null,
                category: v.category,
                case_id: v.case_id ?? null,
                expected_result: v.expected_result ?? '',
                actual_result: v.actual_result ?? '',
                environment: v.environment?.trim() || null,
                found_build: v.found_build?.trim() || null,
                fix_build: v.fix_build?.trim() || null,
              },
            })
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="severity" label="严重度">
            <Select options={PRIORITY_OPTIONS} />
          </Form.Item>
          <Form.Item name="category" label="失败分类">
            <Select options={DEFECT_CATEGORY_OPTIONS} />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={DEFECT_STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="status_comment"
            label="状态变更说明"
            extra="改状态时写入缺陷历史，便于回溯"
          >
            <Input placeholder="如 已提交修复 / 复测通过后关闭" />
          </Form.Item>
          <Form.Item name="case_id" label="关联用例">
            <Select
              allowClear
              showSearch
              placeholder="选择关联用例"
              filterOption={false}
              onSearch={setCaseKeyword}
              options={caseOptions}
              loading={cases.isFetching}
              notFoundContent={cases.isFetching ? '加载中' : '无匹配用例'}
            />
          </Form.Item>
          <Form.Item name="expected_result" label="预期结果">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="actual_result" label="实际结果">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Space style={{ width: '100%' }} size="middle">
            <Form.Item name="environment" label="环境" style={{ flex: 1 }}>
              <Input />
            </Form.Item>
            <Form.Item name="found_build" label="发现构建" style={{ flex: 1 }}>
              <Input />
            </Form.Item>
            <Form.Item name="fix_build" label="修复构建" style={{ flex: 1 }}>
              <Input placeholder="关闭前需填写" />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>

        {editing && (
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>附件</div>
            <Space style={{ marginBottom: 12 }}>
              <Upload
                showUploadList={false}
                accept={ACCEPT}
                beforeUpload={(file) => {
                  void uploadAttachment(editing.id, file)
                  return false
                }}
              >
                <Button size="small">上传图片/附件</Button>
              </Upload>
              <span className="muted">支持图片、PDF、txt/log、zip、docx，单文件 ≤10MB</span>
            </Space>
            <Table<DefectAttachment>
              size="small"
              rowKey="id"
              pagination={false}
              loading={detail.isLoading}
              dataSource={currentAttachments}
              columns={[
                { title: '文件名', dataIndex: 'filename', ellipsis: true },
                { title: '类型', dataIndex: 'mime', width: 140, ellipsis: true },
                {
                  title: '大小',
                  dataIndex: 'size_bytes',
                  width: 90,
                  render: (n: number) => `${Math.max(1, Math.round(n / 1024))} KB`,
                },
                {
                  title: '操作',
                  width: 140,
                  render: (_, att) => (
                    <Space size="small">
                      <Button type="link" size="small" onClick={() => openAttachment(editing.id, att)}>
                        {isImage(att.mime) ? '预览' : '下载'}
                      </Button>
                      <Popconfirm
                        title="删除该附件？"
                        onConfirm={() => removeAttachment(editing.id, att.id)}
                      >
                        <Button type="link" danger size="small">
                          删除
                        </Button>
                      </Popconfirm>
                    </Space>
                  ),
                },
              ]}
            />
          </div>
        )}
      </Modal>

      <Modal
        title={verifyTarget ? `复测缺陷 #${verifyTarget.id}` : '复测缺陷'}
        open={Boolean(verifyTarget)}
        onCancel={() => {
          setVerifyTarget(null)
          verifyForm.resetFields()
        }}
        onOk={() => verifyForm.submit()}
        confirmLoading={verifyMut.isPending}
        destroyOnHidden
        width={560}
      >
        <Form
          form={verifyForm}
          layout="vertical"
          onFinish={(v) => {
            if (!verifyTarget) return
            verifyMut.mutate({ id: verifyTarget.id, body: v })
          }}
        >
          <Form.Item
            name="result"
            label="复测结果"
            rules={[{ required: true, message: '请选择复测结果' }]}
            extra="复测失败会自动重新打开缺陷；通过后状态变为已修复"
          >
            <Select
              options={[
                { value: 'passed', label: '通过（原问题不再出现）' },
                { value: 'failed', label: '失败（问题仍存在）' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="fix_build"
            label="修复构建"
            rules={[{ required: true, message: '请填写修复构建' }]}
          >
            <Input placeholder="如 build-2026.11.10" />
          </Form.Item>
          <Form.Item name="note" label="复测说明">
            <Input.TextArea rows={3} placeholder="复测步骤、证据或未通过现象" />
          </Form.Item>
          <p className="muted" style={{ margin: 0 }}>
            关闭缺陷还要求关联回归批次全部通过；缺失项会在关闭时提示。
          </p>
        </Form>
      </Modal>

      <Modal
        title={historyTarget ? `状态历史 #${historyTarget.id}` : '状态历史'}
        open={Boolean(historyTarget)}
        footer={null}
        onCancel={() => setHistoryTarget(null)}
        destroyOnHidden
        width={640}
      >
        <Table<DefectHistoryRow>
          rowKey="id"
          size="small"
          loading={historyQuery.isLoading}
          dataSource={historyQuery.data ?? []}
          pagination={false}
          locale={{ emptyText: '暂无记录' }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 170,
              render: (v: string) => formatBeijing(v),
            },
            {
              title: '变更',
              width: 180,
              render: (_, row) => (
                <span>
                  {row.from_status ? `${labelForDefectStatus(row.from_status)} → ` : ''}
                  {labelForDefectStatus(row.to_status)}
                </span>
              ),
            },
            { title: '说明', dataIndex: 'comment', ellipsis: true },
          ]}
        />
      </Modal>

      <Drawer
        title={detailTarget ? `缺陷详情 #${detailTarget.id}` : '缺陷详情'}
        open={Boolean(detailTarget)}
        onClose={() => setDetailTarget(null)}
        width={760}
        destroyOnHidden
      >
        {evidenceQuery.isLoading ? (
          <Spin />
        ) : detailTarget ? (
          <>
            <Descriptions
              size="small"
              column={1}
              bordered
              items={[
                { key: 'title', label: '标题', children: detailTarget.title },
                {
                  key: 'status',
                  label: '状态 / 分类',
                  children: `${labelForDefectStatus(detailTarget.status)} · ${labelForDefectCategory(detailTarget.category)}`,
                },
                {
                  key: 'builds',
                  label: '构建',
                  children: `发现 ${detailTarget.found_build || '—'} · 修复 ${detailTarget.fix_build || '—'}`,
                },
                {
                  key: 'retest',
                  label: '复测',
                  children:
                    detailTarget.verification_result === 'passed'
                      ? `通过 ${detailTarget.verified_at ? formatBeijing(detailTarget.verified_at) : ''}`
                      : detailTarget.verification_result === 'failed'
                        ? '失败'
                        : '未复测',
                },
                { key: 'expected', label: '预期结果', children: detailTarget.expected_result || '—' },
                { key: 'actual', label: '实际结果', children: detailTarget.actual_result || '—' },
                { key: 'desc', label: '描述', children: detailTarget.description || '—' },
              ]}
            />

            <Divider>失败来源证据</Divider>
            {evidenceQuery.data?.item ? (
              <>
                <p className="muted" style={{ marginBottom: 8 }}>
                  批次：{evidenceQuery.data.cycle?.plan_name || '—'} /{' '}
                  {evidenceQuery.data.cycle?.name || '—'} · 构建：
                  {evidenceQuery.data.cycle?.target_build || '未知'} · 环境：
                  {evidenceQuery.data.cycle?.environment || '—'}
                </p>
                <p style={{ marginBottom: 8 }}>
                  资产：{evidenceQuery.data.item.asset_title || '—'} · 最终结果：
                  {labelForExecutionResult(evidenceQuery.data.item.result || 'untested')} · 尝试数：
                  {evidenceQuery.data.item.attempt_no}
                </p>
                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={evidenceQuery.data.attempts}
                  locale={{ emptyText: '没有尝试记录' }}
                  columns={[
                    { title: '#', dataIndex: 'attempt_no', width: 60 },
                    { title: '执行方式', dataIndex: 'execution_type', width: 110 },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 90,
                      render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag>,
                    },
                    { title: '摘要', dataIndex: 'summary', ellipsis: true },
                    {
                      title: '结束时间',
                      dataIndex: 'finished_at',
                      width: 160,
                      render: (v: string | null) => (v ? formatBeijing(v) : '—'),
                    },
                  ]}
                />
              </>
            ) : (
              <p className="muted">该缺陷没有关联批次执行项（手工创建）</p>
            )}

            <Divider>状态历史</Divider>
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={evidenceQuery.data?.history ?? []}
              locale={{ emptyText: '暂无记录' }}
              columns={[
                {
                  title: '时间',
                  dataIndex: 'created_at',
                  width: 170,
                  render: (v: string) => formatBeijing(v),
                },
                {
                  title: '变更',
                  width: 200,
                  render: (_: unknown, row: DefectHistoryRow) =>
                    `${row.from_status ? `${labelForDefectStatus(row.from_status)} → ` : ''}${labelForDefectStatus(row.to_status)}`,
                },
                { title: '说明', dataIndex: 'comment', ellipsis: true },
              ]}
            />
          </>
        ) : null}
      </Drawer>

      <Modal
        title="图片预览"
        open={Boolean(previewUrl)}
        footer={null}
        onCancel={() => {
          if (previewUrl) URL.revokeObjectURL(previewUrl)
          setPreviewUrl(null)
        }}
        width={800}
      >
        {previewUrl && (
          <img src={previewUrl} alt="preview" style={{ maxWidth: '100%', display: 'block' }} />
        )}
      </Modal>
      <Modal
        title="创建回归任务"
        open={regressionOpen}
        onCancel={() => setRegressionOpen(false)}
        onOk={() => regressionMut.mutate()}
        confirmLoading={regressionMut.isPending}
        okText="创建"
        cancelText="取消"
        destroyOnHidden
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <div className="muted" style={{ marginBottom: 4 }}>选择缺陷</div>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              placeholder="选择需要回归的缺陷"
              value={regressionDefects}
              onChange={setRegressionDefects}
              options={(list.data?.items ?? []).map((d) => ({
                value: d.id,
                label: `#${d.id} ${d.title}`,
              }))}
            />
          </div>
          <div>
            <div className="muted" style={{ marginBottom: 4 }}>批次名称</div>
            <Input value={regressionName} onChange={(e) => setRegressionName(e.target.value)} />
          </div>
          <div>
            <div className="muted" style={{ marginBottom: 4 }}>修复构建（可选）</div>
            <Input
              value={regressionBuild}
              onChange={(e) => setRegressionBuild(e.target.value)}
              placeholder="例如 build-1.2.3"
            />
          </div>
        </Space>
      </Modal>

    </div>
  )
}
