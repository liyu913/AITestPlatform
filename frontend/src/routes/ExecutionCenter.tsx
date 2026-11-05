import { Card, Segmented, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type ExecutionKind = 'manual' | 'api' | 'ui'

type ExecutionRow = {
  key: string
  kind: ExecutionKind
  id: number
  title: string
  status: string
  batch: string | null
  cycle_id: number | null
  cycle_item_id: number | null
  attempt_no: number
  target_type: string | null
  target_id: number | null
  link: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

type ExecutionPage = {
  items: ExecutionRow[]
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
}

const KIND_LABEL: Record<ExecutionKind, string> = {
  manual: '手工',
  api: '接口',
  ui: 'UI',
}

const STATUS_OPTIONS = [
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'error', label: '错误' },
  { value: 'blocked', label: '阻塞' },
  { value: 'running', label: '运行中' },
  { value: 'pending', label: '待运行' },
  { value: 'untested', label: '未测' },
  { value: 'cancelled', label: '已取消' },
]

function statusColor(status: string): string {
  if (status === 'passed') return 'success'
  if (status === 'failed' || status === 'error') return 'error'
  if (status === 'running' || status === 'pending') return 'processing'
  if (status === 'blocked') return 'warning'
  return 'default'
}

export default function ExecutionCenter() {
  const { projectId } = useParams()
  const pid = Number(projectId)
  const [params, setParams] = useSearchParams()

  const kind = (params.get('kind') ?? 'all') as 'all' | ExecutionKind
  const status = params.get('status') ?? undefined
  const page = Math.max(1, Number(params.get('page') ?? 1) || 1)
  const pageSize = Math.min(100, Math.max(1, Number(params.get('page_size') ?? 20) || 20))

  function update(next: Record<string, string | undefined>) {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined || value === '') merged.delete(key)
      else merged.set(key, value)
    }
    if (!('page' in next)) merged.delete('page')
    setParams(merged, { replace: true })
  }

  const query = useQuery({
    queryKey: ['executions', pid, kind, status, page, pageSize],
    queryFn: () => {
      const sp = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (kind !== 'all') sp.set('kind', kind)
      if (status) sp.set('status', status)
      return apiJson<ExecutionPage>(`/api/v1/projects/${pid}/executions?${sp.toString()}`)
    },
    enabled: Number.isFinite(pid),
  })

  const counts = query.data?.counts ?? {}
  const columns: ColumnsType<ExecutionRow> = [
    {
      title: '类型',
      dataIndex: 'kind',
      width: 80,
      render: (value: ExecutionKind) => KIND_LABEL[value] ?? value,
    },
    {
      title: '执行对象',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string, row) => <Link to={`/projects/${pid}/${row.link}`}>{title}</Link>,
    },
    {
      title: '批次',
      dataIndex: 'batch',
      width: 200,
      ellipsis: true,
      render: (batch: string | null, row) =>
        batch && row.cycle_id ? (
          <Link to={`/projects/${pid}/cycles/${row.cycle_id}`}>{batch}</Link>
        ) : (
          <span className="muted">独立运行</span>
        ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
    },
    {
      title: '尝试',
      dataIndex: 'attempt_no',
      width: 70,
      render: (value: number) => (value > 0 ? `#${value}` : '—'),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 170,
      render: (value: string) => formatBeijing(value),
    },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, row) => <Link to={`/projects/${pid}/${row.link}`}>详情</Link>,
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="执行中心" />
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Segmented
              value={kind}
              onChange={(value) => update({ kind: String(value) })}
              options={[
                { value: 'all', label: `全部 (${query.data?.total ?? 0})` },
                { value: 'manual', label: `手工 (${counts.manual ?? 0})` },
                { value: 'api', label: `接口 (${counts.api ?? 0})` },
                { value: 'ui', label: `UI (${counts.ui ?? 0})` },
              ]}
            />
            <Select
              allowClear
              placeholder="全部状态"
              style={{ minWidth: 130 }}
              value={status}
              options={STATUS_OPTIONS}
              onChange={(value) => update({ status: value ?? undefined })}
            />
          </Space>
          <Typography.Text type="secondary">
            同一批次的手工、接口、UI 执行都汇到这里；重试记录进入批次项详情。
          </Typography.Text>
        </Space>
      </div>
      <div className="list-page-body is-table">
        <Card size="small">
          <Table<ExecutionRow>
            rowKey="key"
            loading={query.isLoading}
            columns={columns}
            dataSource={query.data?.items ?? []}
            locale={{ emptyText: '暂无执行记录' }}
            pagination={{
              current: page,
              pageSize,
              total: query.data?.total ?? 0,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50, 100],
              showTotal: (total) => `共 ${total} 条`,
              onChange: (nextPage, nextSize) =>
                update({ page: String(nextPage), page_size: String(nextSize) }),
            }}
          />
        </Card>
      </div>
    </div>
  )
}
