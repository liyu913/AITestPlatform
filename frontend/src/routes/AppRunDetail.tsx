import { Alert, Button, Descriptions, Image, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { apiFetch, apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type AppRun = {
  id: number
  project_id: number
  status: string
  trigger_type: string
  platform: string
  account_name: string | null
  device_id?: number | null
  summary_json: Record<string, number> | null
  started_at: string | null
  finished_at: string | null
  env_id: number | null
  target_id: number
}

type AppRunStep = {
  id: number
  order: number
  case_name: string | null
  action: string
  selector: string
  status: string
  duration_ms: number | null
  retry_count: number
  error_message: string
  screenshot_key: string | null
}

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

async function downloadExport(pid: number, rid: number, format: 'junit' | 'html') {
  const res = await apiFetch(`/api/v1/projects/${pid}/app-runs/${rid}/export?format=${format}`)
  if (!res.ok) throw new Error('导出失败')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = format === 'html' ? `app-run-${rid}.html` : `app-run-${rid}.xml`
  a.click()
  URL.revokeObjectURL(url)
}

function AuthenticatedShot({
  pid,
  rid,
  stepId,
  width = 220,
}: {
  pid: number
  rid: number
  stepId: number
  width?: number
}) {
  const q = useQuery({
    queryKey: ['app-shot', pid, rid, stepId],
    queryFn: async () => {
      const res = await apiFetch(`/api/v1/projects/${pid}/app-runs/${rid}/steps/${stepId}/screenshot`)
      if (!res.ok) throw new Error('截图加载失败')
      const blob = await res.blob()
      return URL.createObjectURL(blob)
    },
  })
  if (q.isLoading) return <span>加载截图…</span>
  if (!q.data) return <span>—</span>
  return <Image src={q.data} width={width} alt="screenshot" />
}

export default function AppRunDetail() {
  const { projectId, runId } = useParams()
  const pid = Number(projectId)
  const rid = Number(runId)

  const run = useQuery({
    queryKey: ['app-run', pid, rid],
    queryFn: () => apiJson<AppRun>(`/api/v1/projects/${pid}/app-runs/${rid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: (q) => {
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const steps = useQuery({
    queryKey: ['app-run-steps', pid, rid],
    queryFn: () => apiJson<AppRunStep[]>(`/api/v1/projects/${pid}/app-runs/${rid}/steps`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: () => (run.data?.status === 'pending' || run.data?.status === 'running' ? 2000 : false),
  })

  const columns: ColumnsType<AppRunStep> = [
    { title: '#', dataIndex: 'order', width: 50 },
    { title: '用例', dataIndex: 'case_name', width: 140, ellipsis: true },
    { title: '动作', dataIndex: 'action', width: 110 },
    { title: '定位器', dataIndex: 'selector', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '耗时ms', dataIndex: 'duration_ms', width: 80 },
    { title: '重试', dataIndex: 'retry_count', width: 60 },
    {
      title: '错误',
      dataIndex: 'error_message',
      ellipsis: true,
      render: (t: string) => t || '—',
    },
    {
      title: '截图',
      width: 240,
      render: (_, row) =>
        row.screenshot_key ? <AuthenticatedShot pid={pid} rid={rid} stepId={row.id} /> : '—',
    },
  ]

  if (run.isError) {
    return <Alert type="error" message="加载失败" />
  }

  const r = run.data

  return (
    <div>
      <PageBanner title={`APP 执行 #${rid}`} />
      <Space style={{ marginBottom: 16 }}>
        <Link to={`/projects/${pid}/app-runs`}>
          <Button>返回列表</Button>
        </Link>
        <Button onClick={() => void downloadExport(pid, rid, 'html')}>导出 HTML</Button>
        <Button onClick={() => void downloadExport(pid, rid, 'junit')}>导出 JUnit</Button>
      </Space>
      {r && (
        <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
          <Descriptions.Item label="状态">
            <Tag color={statusColor(r.status)}>{r.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="平台">{r.platform}</Descriptions.Item>
          <Descriptions.Item label="类型">{r.trigger_type}</Descriptions.Item>
          <Descriptions.Item label="目标">{r.target_id}</Descriptions.Item>
          <Descriptions.Item label="环境">{r.env_id ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="设备">{r.device_id ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="账号">{r.account_name ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="开始">{formatBeijing(r.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束">{formatBeijing(r.finished_at)}</Descriptions.Item>
          <Descriptions.Item label="汇总" span={2}>
            <Typography.Text code>{JSON.stringify(r.summary_json)}</Typography.Text>
          </Descriptions.Item>
        </Descriptions>
      )}
      <Table
        rowKey="id"
        loading={steps.isLoading}
        columns={columns}
        dataSource={steps.data}
        pagination={false}
        size="small"
      />
    </div>
  )
}
