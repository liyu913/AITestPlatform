import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { UploadFile } from 'antd/es/upload/interface'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  DEFECT_STATUS_OPTIONS,
  labelForDefectStatus,
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
  case_id: number | null
  case_title: string | null
  cycle_item_id: number | null
  requirement_id: number | null
  reporter_id: number | null
  assignee_id: number | null
  created_at: string
  updated_at: string
  attachments: DefectAttachment[]
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

type CaseBrief = { id: number; title: string }

type DefectFormValues = {
  title: string
  description?: string
  severity: string
  status: string
  case_id?: number | null
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
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editing, setEditing] = useState<Defect | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [pendingFiles, setPendingFiles] = useState<UploadFile[]>([])
  const [caseKeyword, setCaseKeyword] = useState('')
  const [caseSearch, setCaseSearch] = useState('')
  const seenCaseTitles = useRef(new Map<number, string>())
  const [form] = Form.useForm<DefectFormValues>()
  const [editForm] = Form.useForm<DefectFormValues>()
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
    queryKey: ['defects', pid, statusFilter, search, page, pageSize],
    queryFn: () => {
      const sp = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (statusFilter) sp.set('status', statusFilter)
      if (search.trim()) sp.set('search', search.trim())
      return apiJson<PageRes<Defect>>(`/api/v1/projects/${pid}/defects/?${sp}`)
    },
    enabled: Number.isFinite(pid),
  })

  const detail = useQuery({
    queryKey: ['defect', pid, editing?.id],
    queryFn: () => apiJson<Defect>(`/api/v1/projects/${pid}/defects/${editing!.id}`),
    enabled: Number.isFinite(pid) && Boolean(editing?.id) && editOpen,
  })

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
          case_id: values.case_id ?? null,
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

  const columns: ColumnsType<Defect> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    { title: '严重度', dataIndex: 'severity', width: 80 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{labelForDefectStatus(s)}</Tag>,
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
      width: 160,
      render: (_, row) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => {
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
                case_id: row.case_id ?? undefined,
              })
              setEditOpen(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm title="确认删除？" onConfirm={() => removeDefect(row.id)}>
            <Button type="link" danger size="small">
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const currentAttachments = detail.data?.attachments ?? editing?.attachments ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="缺陷" />
        <Space style={{ marginBottom: 16 }} wrap>
          <Button
            type="primary"
            onClick={() => {
              form.setFieldsValue({ severity: 'P2', status: 'open', case_id: undefined })
              setCaseKeyword('')
              setCaseSearch('')
              setPendingFiles([])
              setCreateOpen(true)
            }}
          >
            新建缺陷
          </Button>
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
                case_id: v.case_id ?? null,
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
    </div>
  )
}
