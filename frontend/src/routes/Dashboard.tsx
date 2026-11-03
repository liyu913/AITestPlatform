import { Card, Col, Row, Spin, Statistic, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type CountBucket = {
  total: number
  by_status: Record<string, number>
}

type RunTrendPoint = {
  day: string
  total: number
  passed: number
  failed: number
  error: number
}

type RunTrend = {
  points: RunTrendPoint[]
  totals: Record<string, number>
}

type RecentRun = {
  kind: 'functional' | 'api' | 'ui'
  id: number
  status: string
  created_at: string
}

type DashboardData = {
  assets: {
    requirements: CountBucket
    test_cases: CountBucket
    defects: CountBucket
    test_plans: CountBucket
    knowledge_documents: CountBucket
    modules: CountBucket
    members: CountBucket
    api_cases: CountBucket
    api_suites: CountBucket
    ui_cases: CountBucket
    ui_suites: CountBucket
  }
  coverage: {
    requirement_total: number
    blind_count: number
    avg_ratio: number
  }
  runs: {
    functional: RunTrend
    api: RunTrend
    ui: RunTrend
  }
  recent_runs: RecentRun[]
}

type TrendRow = {
  day: string
  functionalPassed: number
  functionalFailed: number
  apiPassed: number
  apiFailed: number
  uiPassed: number
  uiFailed: number
}

const DAYS = 14
const KIND_LABEL: Record<RecentRun['kind'], string> = {
  functional: '功能',
  api: '接口',
  ui: 'UI',
}
const KIND_PATH: Record<RecentRun['kind'], string> = {
  functional: 'test-case-runs',
  api: 'api-runs',
  ui: 'ui-runs',
}
const PIE_COLORS = ['#3f8600', '#d48806']

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

function beijingYmd(date: Date): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

function lastNDays(days: number): string[] {
  const out: string[] = []
  const now = Date.now()
  for (let i = days - 1; i >= 0; i--) {
    out.push(beijingYmd(new Date(now - i * 86400000)))
  }
  return out
}

function indexPoints(points: RunTrendPoint[]): Map<string, RunTrendPoint> {
  const map = new Map<string, RunTrendPoint>()
  for (const point of points) map.set(point.day, point)
  return map
}

function failCount(point: RunTrendPoint | undefined): number {
  return (point?.failed ?? 0) + (point?.error ?? 0)
}

function mergeTrendRows(runs: DashboardData['runs'], days: number): TrendRow[] {
  const functional = indexPoints(runs.functional.points)
  const api = indexPoints(runs.api.points)
  const ui = indexPoints(runs.ui.points)
  return lastNDays(days).map((day) => {
    const f = functional.get(day)
    const a = api.get(day)
    const u = ui.get(day)
    return {
      day: day.slice(5),
      functionalPassed: f?.passed ?? 0,
      functionalFailed: failCount(f),
      apiPassed: a?.passed ?? 0,
      apiFailed: failCount(a),
      uiPassed: u?.passed ?? 0,
      uiFailed: failCount(u),
    }
  })
}

function trendTotal(trend: RunTrend): number {
  return trend.totals.total ?? 0
}

function failTotals(runs: DashboardData['runs']): number {
  const sum = (t: RunTrend) => (t.totals.failed ?? 0) + (t.totals.error ?? 0)
  return sum(runs.functional) + sum(runs.api) + sum(runs.ui)
}

export default function Dashboard() {
  const { projectId } = useParams()
  const pid = Number(projectId)

  const query = useQuery({
    queryKey: ['project-dashboard', pid, DAYS],
    queryFn: () => apiJson<DashboardData>(`/api/v1/projects/${pid}/dashboard?days=${DAYS}`),
    enabled: Number.isFinite(pid),
  })

  const data = query.data
  const assets = data?.assets
  const coverage = data?.coverage
  const openDefects = assets?.defects.by_status.open ?? 0
  const coveragePct = Math.round((coverage?.avg_ratio ?? 0) * 100)
  const recentFails = data ? failTotals(data.runs) : 0
  const hasTrend = Boolean(
    data &&
      (trendTotal(data.runs.functional) || trendTotal(data.runs.api) || trendTotal(data.runs.ui)),
  )
  const trendRows = mergeTrendRows(
    data?.runs ?? {
      functional: { points: [], totals: {} },
      api: { points: [], totals: {} },
      ui: { points: [], totals: {} },
    },
    DAYS,
  )
  const covered = Math.max(0, (coverage?.requirement_total ?? 0) - (coverage?.blind_count ?? 0))
  const pieData =
    coverage && coverage.requirement_total > 0
      ? [
          { name: '已覆盖', value: covered },
          { name: '盲区', value: coverage.blind_count },
        ]
      : []

  const recentColumns: ColumnsType<RecentRun> = [
    {
      title: '类型',
      dataIndex: 'kind',
      width: 80,
      render: (kind: RecentRun['kind']) => KIND_LABEL[kind],
    },
    {
      title: 'ID',
      dataIndex: 'id',
      width: 80,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      ellipsis: true,
      render: (v: string) => formatBeijing(v),
    },
    {
      title: '操作',
      width: 80,
      render: (_, row) => (
        <Link to={`/projects/${pid}/${KIND_PATH[row.kind]}/${row.id}`}>详情</Link>
      ),
    },
  ]

  return (
    <div className="dashboard-page">
      <PageBanner title="项目概览" />
      <Spin spinning={query.isLoading}>
        {query.isError ? (
          <p className="muted">加载失败，请稍后重试。</p>
        ) : (
          <>
            <Row gutter={[12, 12]}>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/requirements`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic title="需求" value={assets?.requirements.total ?? 0} />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/test-cases`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic title="功能用例" value={assets?.test_cases.total ?? 0} />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/defects`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic
                      title="开放缺陷"
                      value={openDefects}
                      valueStyle={openDefects ? { color: '#cf1322' } : undefined}
                    />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/knowledge`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic title="知识文档" value={assets?.knowledge_documents.total ?? 0} />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/api-cases`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic title="接口用例" value={assets?.api_cases.total ?? 0} />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/ui-cases`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic title="UI 用例" value={assets?.ui_cases.total ?? 0} />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Link to={`/projects/${pid}/coverage`} className="dashboard-kpi-link">
                  <Card size="small" hoverable>
                    <Statistic title="需求覆盖率" value={coveragePct} suffix="%" />
                  </Card>
                </Link>
              </Col>
              <Col xs={12} sm={8} lg={6}>
                <Card size="small">
                  <Statistic
                    title="近14天失败"
                    value={recentFails}
                    valueStyle={recentFails ? { color: '#cf1322' } : undefined}
                  />
                </Card>
              </Col>
            </Row>

            <Row gutter={[12, 12]} style={{ marginTop: 16 }}>
              <Col xs={24} lg={16}>
                <Card size="small" title="近14天执行趋势">
                  {!hasTrend && <p className="muted" style={{ marginBottom: 8 }}>暂无执行记录</p>}
                  <div className="dashboard-chart">
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={trendRows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="day" />
                        <YAxis allowDecimals={false} domain={hasTrend ? [0, 'auto'] : [0, 1]} />
                        <Tooltip />
                        <Legend />
                        <Line type="monotone" dataKey="functionalPassed" name="功能通过" stroke="#3f8600" dot={false} />
                        <Line type="monotone" dataKey="apiPassed" name="接口通过" stroke="#2f5aa8" dot={false} />
                        <Line type="monotone" dataKey="uiPassed" name="UI 通过" stroke="#13c2c2" dot={false} />
                        <Line type="monotone" dataKey="functionalFailed" name="功能失败" stroke="#cf1322" dot={false} />
                        <Line type="monotone" dataKey="apiFailed" name="接口失败" stroke="#d46b08" dot={false} />
                        <Line type="monotone" dataKey="uiFailed" name="UI 失败" stroke="#c41d7f" dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </Card>
              </Col>
              <Col xs={24} lg={8}>
                <Card size="small" title="需求覆盖">
                  {pieData.length > 0 ? (
                    <div className="dashboard-chart">
                      <ResponsiveContainer width="100%" height={280}>
                        <PieChart>
                          <Pie
                            data={pieData}
                            dataKey="value"
                            nameKey="name"
                            cx="50%"
                            cy="50%"
                            innerRadius={56}
                            outerRadius={88}
                            paddingAngle={2}
                          >
                            {pieData.map((entry) => (
                              <Cell key={entry.name} fill={entry.name === '已覆盖' ? PIE_COLORS[0] : PIE_COLORS[1]} />
                            ))}
                          </Pie>
                          <Tooltip />
                          <Legend />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <p className="muted">暂无需求</p>
                  )}
                </Card>
              </Col>
            </Row>

            <Card size="small" title="最近执行" style={{ marginTop: 16 }}>
              <Table<RecentRun>
                rowKey={(row) => `${row.kind}-${row.id}`}
                columns={recentColumns}
                dataSource={data?.recent_runs ?? []}
                pagination={false}
                size="small"
                locale={{ emptyText: '暂无执行记录' }}
              />
            </Card>
          </>
        )}
      </Spin>
    </div>
  )
}
