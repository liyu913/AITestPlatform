import { Button, Card, Col, Row, Select, Space, Statistic, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiFetch, apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type PerfRun = {
  id: number
  scenario_id: number | null
  env_id: number | null
  status: string
  summary_json: Record<string, unknown> | null
  config_snapshot_json: { scenario_name?: string; users?: number; run_time_sec?: number } | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

type TrendPoint = {
  run_id: number
  scenario_id: number | null
  scenario_name: string | null
  status: string
  created_at: string | null
  p95_ms: number | null
  rps: number | null
  fail_ratio: number | null
  avg_ms: number | null
  total_requests: number | null
}

type Trend = {
  points: TrendPoint[]
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
  if (s === 'cancelled') return 'default'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(digits)
}

function fmtSummary(s: Record<string, unknown> | null): string {
  if (!s) return '—'
  if (typeof s.error === 'string') return String(s.error)
  const rps = s.rps != null ? `RPS ${s.rps}` : ''
  const p95 = s.p95_ms != null ? `P95 ${s.p95_ms}ms` : ''
  const err = s.fail_ratio != null ? `Err ${(Number(s.fail_ratio) * 100).toFixed(2)}%` : ''
  return [rps, p95, err].filter(Boolean).join(' · ') || '—'
}

async function downloadHtml(pid: number, rid: number) {
  const res = await apiFetch(`/api/v1/projects/${pid}/perf-runs/${rid}/export?format=html`)
  if (!res.ok) throw new Error('导出失败')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `perf-run-${rid}.html`
  a.click()
  URL.revokeObjectURL(url)
}

export default function PerfRunList() {
  const { containerRef, scrollY } = useTableScrollY()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const list = useQuery({
    queryKey: ['perf-runs', pid, status, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (status) qs.set('status', status)
      return apiJson<PageRes<PerfRun>>(`/api/v1/projects/${pid}/perf-runs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? []
      return items.some((r) => r.status === 'pending' || r.status === 'running') ? 3000 : false
    },
  })

  const trends = useQuery({
    queryKey: ['perf-run-trends', pid],
    queryFn: () => apiJson<Trend>(`/api/v1/projects/${pid}/perf-runs/trends?days=14&limit=50`),
    enabled: Number.isFinite(pid),
  })

  const columns: ColumnsType<PerfRun> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    {
      title: '场景',
      render: (_, row) => row.config_snapshot_json?.scenario_name || (row.scenario_id ? `#${row.scenario_id}` : '—'),
    },
    {
      title: '指标',
      render: (_, row) => fmtSummary(row.summary_json),
      ellipsis: true,
    },
    { title: '开始', dataIndex: 'started_at', ellipsis: true, render: (v: string | null) => formatBeijing(v) },
    { title: '创建时间', dataIndex: 'created_at', ellipsis: true, render: (v: string) => formatBeijing(v) },
    {
      title: '操作',
      width: 160,
      render: (_, row) => (
        <Space>
          <Link to={`/projects/${pid}/perf-runs/${row.id}`}>详情</Link>
          <Button type="link" size="small" onClick={() => void downloadHtml(pid, row.id)}>
            HTML
          </Button>
        </Space>
      ),
    },
  ]

  const trendColumns: ColumnsType<TrendPoint> = [
    {
      title: 'Run',
      width: 80,
      render: (_, row) => <Link to={`/projects/${pid}/perf-runs/${row.run_id}`}>#{row.run_id}</Link>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '场景', dataIndex: 'scenario_name', ellipsis: true, render: (v: string | null, row) => v || (row.scenario_id ? `#${row.scenario_id}` : '—') },
    { title: 'P95', width: 90, render: (_, row) => fmt(row.p95_ms) },
    { title: 'RPS', width: 80, render: (_, row) => fmt(row.rps) },
    {
      title: '错误率',
      width: 90,
      render: (_, row) => (row.fail_ratio != null ? `${(row.fail_ratio * 100).toFixed(2)}%` : '—'),
    },
    { title: '时间', dataIndex: 'created_at', ellipsis: true, render: (v: string | null) => formatBeijing(v) },
  ]

  const t = trends.data?.totals
  const points = trends.data?.points ?? []
  const last = points.length ? points[points.length - 1] : null
  const prev = points.length > 1 ? points[points.length - 2] : null

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="压测记录" />
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="近14天次数" value={t?.total ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="通过" value={t?.passed ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="失败" value={t?.failed ?? 0} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic
                title="最近 P95"
                value={last?.p95_ms ?? undefined}
                precision={1}
                suffix={
                  prev?.p95_ms != null && last?.p95_ms != null
                    ? `（较上次 ${last.p95_ms - prev.p95_ms >= 0 ? '+' : ''}${(last.p95_ms - prev.p95_ms).toFixed(1)}）`
                    : undefined
                }
              />
            </Card>
          </Col>
        </Row>

        {(points.length > 0 || trends.isLoading) && (
          <Card size="small" title="近14天指标趋势" style={{ marginBottom: 16 }}>
            <Table<TrendPoint>
              rowKey="run_id"
              size="small"
              loading={trends.isLoading}
              columns={trendColumns}
              dataSource={[...points].reverse()}
              pagination={false}
              scroll={{ y: 260 }}
            />
          </Card>
        )}

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
        <Table<PerfRun>
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
    </div>
  )
}
