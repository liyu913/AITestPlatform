import { Alert, App, Button, Card, Col, Descriptions, Popconfirm, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type EndpointStat = {
  name: string
  requests: number
  failures: number
  fail_ratio: number
  avg_ms: number | null
  p95_ms: number | null
  rps: number
}

type SlaCheck = {
  name: string
  expected: number
  actual: number | null
  ok: boolean
}

type PerfRun = {
  id: number
  project_id: number
  scenario_id: number | null
  status: string
  summary_json: {
    total_requests?: number
    failures?: number
    fail_ratio?: number
    rps?: number
    avg_ms?: number | null
    p50_ms?: number | null
    p90_ms?: number | null
    p95_ms?: number | null
    p99_ms?: number | null
    elapsed_sec?: number
    endpoints?: EndpointStat[]
    sla?: { passed?: boolean; checks?: SlaCheck[] }
    error?: string
    cancelled?: boolean
  } | null
  config_snapshot_json: {
    scenario_name?: string
    users?: number
    spawn_rate?: number
    run_time_sec?: number
    think_time_ms?: number
    base_url?: string
  } | null
  started_at: string | null
  finished_at: string | null
  env_id: number | null
}

type CompareMetric = {
  metric: string
  current: number | null
  baseline: number | null
  delta: number | null
  delta_pct: number | null
  better: string
  improved: boolean | null
}

type CompareOut = {
  current_run_id: number
  baseline_run_id: number | null
  baseline_finished_at: string | null
  metrics: CompareMetric[]
}

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'cancelled') return 'default'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return Number(v).toFixed(digits)
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

const METRIC_LABEL: Record<string, string> = {
  p95_ms: 'P95 (ms)',
  p90_ms: 'P90 (ms)',
  avg_ms: '平均 (ms)',
  fail_ratio: '错误率',
  rps: 'RPS',
  total_requests: '总请求',
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

export default function PerfRunDetail() {
  const { message } = App.useApp()
  const { projectId, runId } = useParams()
  const pid = Number(projectId)
  const rid = Number(runId)
  const qc = useQueryClient()
  const [stuckHint, setStuckHint] = useState(false)
  const seenPendingAt = useRef<number | null>(null)

  const run = useQuery({
    queryKey: ['perf-run', pid, rid],
    queryFn: () => apiJson<PerfRun>(`/api/v1/projects/${pid}/perf-runs/${rid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: (q) => {
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const compare = useQuery({
    queryKey: ['perf-run-compare', pid, rid],
    queryFn: () => apiJson<CompareOut>(`/api/v1/projects/${pid}/perf-runs/${rid}/compare`),
    enabled:
      Number.isFinite(pid) &&
      Number.isFinite(rid) &&
      !!run.data &&
      (run.data.status === 'passed' || run.data.status === 'failed' || run.data.status === 'cancelled'),
  })

  const cancel = useMutation({
    mutationFn: () =>
      apiJson<PerfRun>(`/api/v1/projects/${pid}/perf-runs/${rid}/cancel`, { method: 'POST', body: '{}' }),
    onSuccess: () => {
      message.success('已取消')
      void run.refetch()
      void qc.invalidateQueries({ queryKey: ['perf-runs', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  useEffect(() => {
    const st = run.data?.status
    if (st === 'pending' || st === 'running') {
      if (seenPendingAt.current == null) seenPendingAt.current = Date.now()
      const timer = window.setInterval(() => {
        if (seenPendingAt.current && Date.now() - seenPendingAt.current > 12000) {
          setStuckHint(true)
        }
      }, 2000)
      return () => window.clearInterval(timer)
    }
    seenPendingAt.current = null
    setStuckHint(false)
  }, [run.data?.status])

  const summary = run.data?.summary_json
  const config = run.data?.config_snapshot_json
  const endpoints = summary?.endpoints ?? []
  const slaChecks = summary?.sla?.checks ?? []
  const canCancel = run.data?.status === 'pending' || run.data?.status === 'running'

  const columns: ColumnsType<EndpointStat> = [
    { title: '接口', dataIndex: 'name', ellipsis: true },
    { title: '请求数', dataIndex: 'requests', width: 90 },
    { title: '失败', dataIndex: 'failures', width: 70 },
    {
      title: '错误率',
      width: 90,
      render: (_, row) => `${(row.fail_ratio * 100).toFixed(2)}%`,
    },
    { title: '平均(ms)', width: 100, render: (_, row) => fmt(row.avg_ms) },
    { title: 'P95(ms)', width: 100, render: (_, row) => fmt(row.p95_ms) },
    { title: 'RPS', width: 80, render: (_, row) => fmt(row.rps) },
  ]

  const compareColumns: ColumnsType<CompareMetric> = [
    {
      title: '指标',
      dataIndex: 'metric',
      render: (m: string) => METRIC_LABEL[m] || m,
    },
    {
      title: '当前',
      render: (_, row) =>
        row.metric === 'fail_ratio' && row.current != null
          ? `${(Number(row.current) * 100).toFixed(2)}%`
          : fmt(row.current),
    },
    {
      title: '基线',
      render: (_, row) =>
        row.metric === 'fail_ratio' && row.baseline != null
          ? `${(Number(row.baseline) * 100).toFixed(2)}%`
          : fmt(row.baseline),
    },
    {
      title: '差值',
      render: (_, row) => {
        if (row.delta == null) return '—'
        const color = row.improved === true ? '#389e0d' : row.improved === false ? '#cf1322' : undefined
        const sign = row.delta > 0 ? '+' : ''
        const text =
          row.metric === 'fail_ratio'
            ? `${sign}${(row.delta * 100).toFixed(2)}%`
            : `${sign}${fmt(row.delta)}`
        return <span style={{ color }}>{text}</span>
      },
    },
    {
      title: '变化',
      width: 100,
      render: (_, row) => {
        if (row.delta_pct == null) return '—'
        const color = row.improved === true ? '#389e0d' : row.improved === false ? '#cf1322' : undefined
        const sign = row.delta_pct > 0 ? '+' : ''
        return <span style={{ color }}>{sign}{fmt(row.delta_pct)}%</span>
      },
    },
  ]

  return (
    <div>
      <PageBanner title={`压测记录 #${rid}`} />
      <Space style={{ marginBottom: 16 }} wrap>
        <Link to={`/projects/${pid}/perf-runs`}>
          <Button>全部记录</Button>
        </Link>
        <Link to={`/projects/${pid}/perf-scenarios`}>
          <Button>压测场景</Button>
        </Link>
        <Button onClick={() => void run.refetch()}>刷新</Button>
        <Button
          onClick={() => {
            void downloadHtml(pid, rid).catch(() => message.error('导出失败'))
          }}
        >
          导出 HTML
        </Button>
        {canCancel && (
          <Popconfirm title="确认取消本次压测？" onConfirm={() => cancel.mutate()}>
            <Button danger loading={cancel.isPending}>
              取消压测
            </Button>
          </Popconfirm>
        )}
      </Space>

      {stuckHint && (run.data?.status === 'pending' || run.data?.status === 'running') && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="执行时间较长或可能卡住：请确认 Celery Worker 已启动，且已安装 locust 依赖。"
        />
      )}

      {run.data?.status === 'cancelled' && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message="本次压测已取消" />
      )}

      {summary?.error && run.data?.status !== 'cancelled' && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={summary.error} />
      )}

      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="RPS" value={summary?.rps ?? undefined} precision={2} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="P95 (ms)" value={summary?.p95_ms ?? undefined} precision={2} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="错误率"
              value={summary?.fail_ratio != null ? Number(summary.fail_ratio) * 100 : undefined}
              precision={2}
              suffix="%"
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="总请求" value={summary?.total_requests ?? undefined} />
          </Card>
        </Col>
      </Row>

      <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="状态">
          <Tag color={statusColor(run.data?.status || '')}>{run.data?.status || '—'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="场景">
          {config?.scenario_name || (run.data?.scenario_id ? `#${run.data.scenario_id}` : '—')}
        </Descriptions.Item>
        <Descriptions.Item label="并发 / 时长">
          {config?.users ?? '—'} / {config?.run_time_sec ?? '—'}s
        </Descriptions.Item>
        <Descriptions.Item label="加压速率">{config?.spawn_rate ?? '—'}/s</Descriptions.Item>
        <Descriptions.Item label="开始">{formatBeijing(run.data?.started_at)}</Descriptions.Item>
        <Descriptions.Item label="结束">{formatBeijing(run.data?.finished_at)}</Descriptions.Item>
        <Descriptions.Item label="平均延迟">{fmt(summary?.avg_ms)} ms</Descriptions.Item>
        <Descriptions.Item label="P50 / P90 / P95 / P99">
          {fmt(summary?.p50_ms)} / {fmt(summary?.p90_ms)} / {fmt(summary?.p95_ms)} / {fmt(summary?.p99_ms)} ms
        </Descriptions.Item>
      </Descriptions>

      {slaChecks.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Typography.Title level={5}>SLA</Typography.Title>
          <Alert
            type={summary?.sla?.passed ? 'success' : 'error'}
            showIcon
            message={summary?.sla?.passed ? 'SLA 通过' : 'SLA 未通过'}
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {slaChecks.map((c) => (
                  <li key={c.name} style={{ color: c.ok ? undefined : '#cf1322' }}>
                    {c.ok ? '✓' : '✗'} {c.name}: 期望 ≤ {c.expected}，实际 {c.actual ?? '—'}
                  </li>
                ))}
              </ul>
            }
          />
        </div>
      )}

      <div style={{ marginBottom: 16 }}>
        <Typography.Title level={5}>基线对比</Typography.Title>
        {!compare.data?.baseline_run_id ? (
          <Alert type="info" showIcon message="暂无可对比基线（同场景下尚未有更早的 passed/failed 记录）" />
        ) : (
          <>
            <Typography.Paragraph type="secondary">
              对比基线：#{compare.data.baseline_run_id}
              {compare.data.baseline_finished_at ? `（${formatBeijing(compare.data.baseline_finished_at)}）` : ''}
              。绿色表示相对基线变好，红色表示变差。
            </Typography.Paragraph>
            <Table<CompareMetric>
              rowKey="metric"
              size="small"
              columns={compareColumns}
              dataSource={compare.data.metrics}
              pagination={false}
              loading={compare.isLoading}
            />
          </>
        )}
      </div>

      <Typography.Title level={5}>接口拆分</Typography.Title>
      <Table<EndpointStat>
        rowKey="name"
        size="small"
        columns={columns}
        dataSource={endpoints}
        pagination={false}
        locale={{ emptyText: run.isLoading ? '加载中…' : '暂无数据' }}
      />
    </div>
  )
}
