import { Card, Col, Row, Select, Space, Statistic, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type AppRun = {
  id: number
  trigger_type: string
  target_id: number
  platform: string
  status: string
  summary_json: Record<string, number> | null
  created_at: string
}

type PageRes<T> = { items: T[]; total: number }
type Trend = {
  points: Array<{ day: string; total: number; passed: number; failed: number; error: number }>
  totals: Record<string, number>
}

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

export default function AppRunList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { projectId } = useParams()
  const pid = Number(projectId)
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const list = useQuery({
    queryKey: ['app-runs', pid, status, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (status) qs.set('status', status)
      return apiJson<PageRes<AppRun>>(`/api/v1/projects/${pid}/app-runs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? []
      return items.some((r) => r.status === 'pending' || r.status === 'running') ? 3000 : false
    },
  })

  const trends = useQuery({
    queryKey: ['app-run-trends', pid],
    queryFn: () => apiJson<Trend>(`/api/v1/projects/${pid}/app-runs/trends?days=14`),
    enabled: Number.isFinite(pid),
  })

  const columns: ColumnsType<AppRun> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '类型', dataIndex: 'trigger_type', width: 80 },
    { title: '目标', dataIndex: 'target_id', width: 80 },
    { title: '平台', dataIndex: 'platform', width: 90 },
    {
      title: '汇总',
      render: (_, row) =>
        row.summary_json
          ? `✓${row.summary_json.passed ?? 0} ✗${row.summary_json.failed ?? 0} !${row.summary_json.error ?? 0}`
          : '—',
    },
    { title: '创建', dataIndex: 'created_at', ellipsis: true, render: (v: string) => formatBeijing(v) },
    {
      title: '操作',
      width: 80,
      render: (_, row) => <Link to={`/projects/${pid}/app-runs/${row.id}`}>详情</Link>,
    },
  ]

  const t = trends.data?.totals

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
      <PageBanner title="APP 执行记录" />
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="近14天总数" value={t?.total ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="通过" value={t?.passed ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="失败" value={t?.failed ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="错误" value={t?.error ?? 0} />
          </Card>
        </Col>
      </Row>
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
        <Table
        rowKey="id"
        loading={list.isLoading}
        columns={columns}
        dataSource={list.data?.items}
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
