import { Alert, Button, Card, Empty, List, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { labelForCaseResult, labelForLifecycleStatus } from '../constants/workflow'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type WorkbenchTask = {
  key: string
  kind: 'manual_run' | 'review' | 'regression' | 'run'
  title: string
  subtitle: string | null
  status: string
  priority: string | null
  target_type: string
  target_id: number
  link: string
  updated_at: string | null
}

type WorkbenchBucket = {
  key: string
  label: string
  count: number
  items: WorkbenchTask[]
  empty_hint: string
}

type WorkbenchRecentRun = {
  key: string
  kind: 'functional' | 'api' | 'ui'
  kind_label: string
  id: number
  status: string
  link: string
  created_at: string
}

type WorkbenchData = {
  project_id: number
  generated_at: string
  buckets: WorkbenchBucket[]
  recent_runs: WorkbenchRecentRun[]
}

const BUCKET_ORDER = ['pending_run', 'pending_review', 'pending_regression']

function runStatusColor(status: string): string {
  if (status === 'passed') return 'success'
  if (status === 'failed' || status === 'error') return 'error'
  if (status === 'running' || status === 'pending') return 'processing'
  if (status === 'blocked') return 'warning'
  return 'default'
}

/** 任务状态标签：执行类看结果，评审看生命周期，回归看缺陷状态。 */
function taskStatusLabel(task: WorkbenchTask): string {
  if (task.kind === 'review') return labelForLifecycleStatus(task.status)
  if (task.kind === 'manual_run') return labelForCaseResult(task.status)
  return task.status
}

export default function Workbench() {
  const { projectId } = useParams()
  const pid = Number(projectId)

  const query = useQuery({
    queryKey: ['workbench', pid],
    queryFn: () => apiJson<WorkbenchData>(`/api/v1/projects/${pid}/workbench`),
    enabled: Number.isFinite(pid),
  })

  const buckets = [...(query.data?.buckets ?? [])].sort(
    (a, b) => BUCKET_ORDER.indexOf(a.key) - BUCKET_ORDER.indexOf(b.key),
  )

  const recentColumns: ColumnsType<WorkbenchRecentRun> = [
    {
      title: '类型',
      dataIndex: 'kind_label',
      width: 80,
    },
    {
      title: 'ID',
      dataIndex: 'id',
      width: 80,
      render: (id: number, row) => (
        <Link to={`/projects/${pid}/${row.link}`}>#{id}</Link>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string) => <Tag color={runStatusColor(status)}>{status}</Tag>,
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      render: (value: string) => formatBeijing(value),
    },
    {
      title: '操作',
      width: 80,
      render: (_: unknown, row) => (
        <Link to={`/projects/${pid}/${row.link}`}>详情</Link>
      ),
    },
  ]

  return (
    <div className="dashboard-page">
      <PageBanner title="工作台" />
      {query.isError ? (
        <Alert type="error" showIcon message="工作台加载失败，请稍后重试。" style={{ marginBottom: 12 }} />
      ) : null}
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div
          style={{
            display: 'grid',
            gap: 12,
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          }}
        >
          {buckets.map((bucket) => (
            <Card
              key={bucket.key}
              size="small"
              loading={query.isLoading}
              title={
                <Space>
                  <span>{bucket.label}</span>
                  <Tag color={bucket.count > 0 ? 'blue' : 'default'}>{bucket.count}</Tag>
                </Space>
              }
            >
              {bucket.items.length === 0 ? (
                <Empty description={bucket.empty_hint} image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <List
                  size="small"
                  dataSource={bucket.items}
                  renderItem={(task) => (
                    <List.Item
                      actions={[
                        <Link key="open" to={`/projects/${pid}/${task.link}`}>
                          处理
                        </Link>,
                      ]}
                    >
                      <List.Item.Meta
                        title={
                          <Link to={`/projects/${pid}/${task.link}`}>{task.title}</Link>
                        }
                        description={
                          <Space size={8} wrap>
                            <Tag>{taskStatusLabel(task)}</Tag>
                            {task.priority ? <span>{task.priority}</span> : null}
                            {task.subtitle ? (
                              <span className="muted">{task.subtitle}</span>
                            ) : null}
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              )}
              {bucket.count > bucket.items.length ? (
                <div style={{ marginTop: 8 }}>
                  <Link to={`/projects/${pid}/${bucketLink(bucket.key)}`}>
                    <Button type="link" size="small">
                      查看全部 {bucket.count} 项
                    </Button>
                  </Link>
                </div>
              ) : null}
            </Card>
          ))}
        </div>

        <Card
          size="small"
          title="近期执行"
          extra={<Link to={`/projects/${pid}/reports`}>查看测试报告</Link>}
        >
          <Table<WorkbenchRecentRun>
            rowKey="key"
            loading={query.isLoading}
            columns={recentColumns}
            dataSource={query.data?.recent_runs ?? []}
            pagination={false}
            size="small"
            locale={{ emptyText: '暂无执行记录' }}
          />
        </Card>
        <p className="muted" style={{ margin: 0 }}>
          数据生成时间：
          {query.data ? formatBeijing(query.data.generated_at) : '—'}
          。待办均来自可跳转的业务记录，处理完成后自动消失。
        </p>
      </Space>
    </div>
  )
}

function bucketLink(key: string): string {
  if (key === 'pending_review') return 'test-cases?status=pending_review'
  if (key === 'pending_regression') return 'defects'
  return 'test-plans'
}
