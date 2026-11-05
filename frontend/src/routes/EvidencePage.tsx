import { Input, Select, Space, Table, Tag } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type Evidence = {
  attempt_id: number
  plan_id: number
  plan_name: string | null
  cycle_id: number
  cycle_name: string | null
  item_id: number
  asset_type: string
  case_id: number | null
  case_title: string | null
  attempt_no: number
  execution_type: string
  status: string
  summary: string
  original_run_type: string | null
  original_run_id: number | null
  started_at: string | null
  finished_at: string | null
  evidence: { screenshots?: string[]; attempt_ids?: number[] } | null
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }
type Plan = { id: number; name: string }
type Cycle = { id: number; name: string }

const STATUS_OPTIONS = [
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'error', label: '错误' },
  { value: 'skipped', label: '跳过' },
  { value: 'running', label: '运行中' },
]

const STATUS_COLOR: Record<string, string> = {
  passed: 'success',
  failed: 'error',
  error: 'error',
  skipped: 'default',
  running: 'processing',
}

function runLink(pid: number, row: Evidence): string | null {
  if (!row.original_run_type || row.original_run_id == null) return null
  const prefix =
    row.original_run_type === 'functional'
      ? 'test-case-runs'
      : `${row.original_run_type}-runs`
  return `/projects/${pid}/${prefix}/${row.original_run_id}`
}

export default function EvidencePage() {
  const { projectId } = useParams()
  const pid = Number(projectId)
  const [planId, setPlanId] = useState<number | undefined>()
  const [cycleId, setCycleId] = useState<number | undefined>()
  const [caseInput, setCaseInput] = useState('')
  const [caseId, setCaseId] = useState<number | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const plansQuery = useQuery({
    queryKey: ['test-plans', pid],
    queryFn: () => apiJson<PageRes<Plan>>(`/api/v1/projects/${pid}/test-plans/?page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const cyclesQuery = useQuery({
    queryKey: ['test-cycles', pid, planId],
    queryFn: () => apiJson<Cycle[]>(`/api/v1/projects/${pid}/test-plans/${planId}/cycles/`),
    enabled: Boolean(planId),
  })

  const evidenceQuery = useQuery({
    queryKey: ['evidence', pid, planId, cycleId, caseId, status, page, pageSize],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (planId) params.set('plan_id', String(planId))
      if (cycleId) params.set('cycle_id', String(cycleId))
      if (caseId) params.set('case_id', String(caseId))
      if (status) params.set('status', status)
      return apiJson<PageRes<Evidence>>(`/api/v1/projects/${pid}/evidence/?${params.toString()}`)
    },
    enabled: Number.isFinite(pid),
  })

  if (!Number.isFinite(pid)) return <p className="error">无效项目</p>

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="执行证据" />
        <p className="muted" style={{ marginTop: 0 }}>
          按计划、批次或用例检索每一次执行尝试；证据保留原始运行归属。
        </p>
        <Space wrap>
          <Select
            allowClear
            placeholder="按计划筛选"
            style={{ width: 200 }}
            value={planId}
            options={(plansQuery.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
            onChange={(v) => {
              setPlanId(v)
              setCycleId(undefined)
              setPage(1)
            }}
          />
          <Select
            allowClear
            placeholder="按批次筛选"
            style={{ width: 200 }}
            disabled={!planId}
            value={cycleId}
            options={(cyclesQuery.data ?? []).map((c) => ({ value: c.id, label: c.name }))}
            onChange={(v) => {
              setCycleId(v)
              setPage(1)
            }}
          />
          <Input.Search
            allowClear
            placeholder="用例 ID"
            style={{ width: 140 }}
            value={caseInput}
            onChange={(e) => setCaseInput(e.target.value)}
            onSearch={(v) => {
              const n = Number(v)
              setCaseId(Number.isFinite(n) && n > 0 ? n : undefined)
              setPage(1)
            }}
          />
          <Select
            allowClear
            placeholder="按结果筛选"
            style={{ width: 140 }}
            value={status}
            options={STATUS_OPTIONS}
            onChange={(v) => {
              setStatus(v)
              setPage(1)
            }}
          />
        </Space>
      </div>
      <div className="list-page-body is-table">
        <Table<Evidence>
          rowKey="attempt_id"
          size="small"
          loading={evidenceQuery.isLoading}
          dataSource={evidenceQuery.data?.items ?? []}
          pagination={{
            current: page,
            pageSize,
            total: evidenceQuery.data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, s) => {
              setPage(p)
              setPageSize(s)
            },
          }}
          columns={[
            {
              title: '时间',
              dataIndex: 'finished_at',
              width: 170,
              render: (v: string | null, row) => (v ? formatBeijing(v) : row.started_at ? formatBeijing(row.started_at) : '—'),
            },
            { title: '计划', dataIndex: 'plan_name', width: 160, ellipsis: true },
            {
              title: '批次',
              dataIndex: 'cycle_name',
              width: 140,
              ellipsis: true,
              render: (v: string | null, row) => (
                <Link to={`/projects/${pid}/cycles/${row.cycle_id}`}>{v || `#${row.cycle_id}`}</Link>
              ),
            },
            {
              title: '用例 / 资产',
              dataIndex: 'case_title',
              ellipsis: true,
              render: (v: string | null, row) =>
                v ? (
                  row.case_id ? (
                    <Link to={`/projects/${pid}/test-cases?case=${row.case_id}`}>{v}</Link>
                  ) : (
                    v
                  )
                ) : (
                  `${row.asset_type} #${row.item_id}`
                ),
            },
            { title: '尝试', dataIndex: 'attempt_no', width: 70 },
            {
              title: '结果',
              dataIndex: 'status',
              width: 90,
              render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag>,
            },
            {
              title: '证据',
              dataIndex: 'evidence',
              width: 90,
              render: (v: Evidence['evidence']) => `${v?.screenshots?.length ?? 0} 张截图`,
            },
            {
              title: '运行',
              key: 'run',
              width: 90,
              render: (_, row) => {
                const href = runLink(pid, row)
                return href ? <Link to={href}>查看运行</Link> : '—'
              },
            },
          ]}
        />
      </div>
    </div>
  )
}
