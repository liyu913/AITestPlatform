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
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  labelForPlanStatus,
  PLAN_STATUS_OPTIONS,
} from '../constants/workflow'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type TestPlan = {
  id: number
  project_id: number
  name: string
  version: string | null
  iteration: string | null
  description: string | null
  status: string
  requirement_id: number | null
  requirement_title: string | null
  scheme_id: number | null
  scheme_version_id: number | null
  scheme_name: string | null
  scheme_version_no: number | null
  creator_id: number | null
  created_at: string
  updated_at: string
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function TestPlanList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<{
    name: string
    version?: string
    iteration?: string
    description?: string
    status: string
    scheme_version_id?: number
  }>()

  const list = useQuery({
    queryKey: ['test-plans', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<TestPlan>>(`/api/v1/projects/${pid}/test-plans/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const [schemeId, setSchemeId] = useState<number | undefined>()
  const schemesQuery = useQuery({
    queryKey: ['schemes', pid, 'picker'],
    queryFn: () => apiJson<{ items: { id: number; name: string }[] }>(`/api/v1/projects/${pid}/schemes/?page_size=100`),
    enabled: Number.isFinite(pid),
  })
  const schemeVersionsQuery = useQuery({
    queryKey: ['scheme-versions', pid, schemeId],
    queryFn: () =>
      apiJson<{ id: number; version_no: number; change_note: string | null }[]>(
        `/api/v1/projects/${pid}/schemes/${schemeId}/versions`,
      ),
    enabled: Boolean(schemeId),
  })

  const createMut = useMutation({
    mutationFn: (values: {
      name: string
      version?: string
      iteration?: string
      description?: string
      status: string
      scheme_version_id?: number
    }) =>
      apiJson<TestPlan>(`/api/v1/projects/${pid}/test-plans/`, {
        method: 'POST',
        body: JSON.stringify({
          name: values.name.trim(),
          version: values.version?.trim() || null,
          iteration: values.iteration?.trim() || null,
          description: values.description?.trim() || null,
          status: values.status,
          scheme_version_id: values.scheme_version_id ?? null,
        }),
      }),
    onSuccess: () => {
      message.success('计划已创建')
      setOpen(false)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['test-plans', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function removePlan(id: number) {
    try {
      const res = await apiFetch(`/api/v1/projects/${pid}/test-plans/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        message.error('删除失败')
        return
      }
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['test-plans', pid] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  const columns: ColumnsType<TestPlan> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string, row) => (
        <Link to={`/projects/${pid}/test-plans/${row.id}`}>{name}</Link>
      ),
    },
    { title: '版本', dataIndex: 'version', width: 110, render: (v) => v || '—' },
    { title: '迭代', dataIndex: 'iteration', width: 120, render: (v) => v || '—' },
    {
      title: '测试方案',
      dataIndex: 'scheme_name',
      width: 180,
      ellipsis: true,
      render: (v: string | null, row) =>
        v ? `${v}${row.scheme_version_no ? ` · v${row.scheme_version_no}` : ''}` : '—',
    },
    {
      title: '来源需求',
      dataIndex: 'requirement_title',
      width: 160,
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag>{labelForPlanStatus(s)}</Tag>,
    },
    { title: '更新时间', dataIndex: 'updated_at', ellipsis: true, render: (v: string) => formatBeijing(v) },
    {
      title: '操作',
      width: 100,
      render: (_, row) => (
        <Popconfirm title="确认删除该计划？" onConfirm={() => removePlan(row.id)}>
          <Button type="link" danger size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
      <PageBanner title="测试计划" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            form.setFieldsValue({ status: 'active' })
            setOpen(true)
          }}
        >
          新建计划
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索计划名称"
          style={{ width: 240 }}
          onSearch={(v) => {
            setSearch(v)
            setPage(1)
          }}
        />
      </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table<TestPlan>
        rowKey="id"
        loading={list.isLoading}
        columns={columns}
        dataSource={list.data?.items ?? []}
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
       scroll={{ y: scrollY }} />
      </div>
      <Modal
        title="新建测试计划"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMut.isPending}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => createMut.mutate(v)}
          initialValues={{ status: 'active' }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：v1.0 回归" />
          </Form.Item>
          <Form.Item name="version" label="版本">
            <Input placeholder="如：1.0.0" />
          </Form.Item>
          <Form.Item name="iteration" label="迭代">
            <Input placeholder="如：Sprint-12" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={PLAN_STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item label="测试方案">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Select
                allowClear
                placeholder="选择方案（可选）"
                options={(schemesQuery.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))}
                value={schemeId}
                onChange={(v) => {
                  setSchemeId(v)
                  form.setFieldValue('scheme_version_id', undefined)
                }}
              />
              <Form.Item name="scheme_version_id" noStyle>
                <Select
                  allowClear
                  disabled={!schemeId}
                  placeholder="选择方案版本"
                  options={(schemeVersionsQuery.data ?? []).map((v) => ({
                    value: v.id,
                    label: `v${v.version_no}${v.change_note ? ` · ${v.change_note}` : ''}`,
                  }))}
                />
              </Form.Item>
            </Space>
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
