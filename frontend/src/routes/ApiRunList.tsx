import { Select, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type ApiRun = {
  id: number
  trigger_type: string
  target_id: number
  env_id: number | null
  status: string
  summary_json: Record<string, number> | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

const STATUS_OPTIONS = [
  { value: 'pending', label: 'pending' },
  { value: 'running', label: 'running' },
  { value: 'passed', label: 'passed' },
  { value: 'failed', label: 'failed' },
  { value: 'error', label: 'error' },
  { value: 'cancelled', label: 'cancelled' },
]

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

function triggerLabel(triggerType: string): string {
  if (triggerType === 'case') return '单接口'
  if (triggerType === 'suite') return '场景'
  if (triggerType === 'batch') return '单接口批量'
  return triggerType || '—'
}

export default function ApiRunList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { projectId } = useParams()
  const pid = Number(projectId)
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const list = useQuery({
    queryKey: ['api-runs', pid, status, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (status) qs.set('status', status)
      return apiJson<PageRes<ApiRun>>(`/api/v1/projects/${pid}/api-runs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? []
      return items.some((r) => r.status === 'pending' || r.status === 'running') ? 3000 : false
    },
  })

  const columns: ColumnsType<ApiRun> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    {
      title: '汇总',
      render: (_, row) =>
        row.summary_json
          ? `✓${row.summary_json.passed ?? 0}  ✗${row.summary_json.failed ?? 0}  !${row.summary_json.error ?? 0}`
          : '—',
    },
    {
      title: '类型',
      dataIndex: 'trigger_type',
      width: 120,
      render: (triggerType: string) => triggerLabel(triggerType),
    },
    { title: '开始', dataIndex: 'started_at', ellipsis: true, render: (v: string | null) => formatBeijing(v) },
    { title: '创建时间', dataIndex: 'created_at', ellipsis: true, render: (v: string) => formatBeijing(v) },
    {
      title: '操作',
      width: 100,
      render: (_, row) => <Link to={`/projects/${pid}/api-runs/${row.id}`}>详情</Link>,
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
      <PageBanner title="执行记录" />
      <Space style={{ marginBottom: 12 }}>
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 160 }}
          options={STATUS_OPTIONS}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
        />
      </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table<ApiRun>
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
    </div>
  )
}
