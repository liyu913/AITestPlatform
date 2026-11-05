import { Card, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

export type ReportSummary = {
  id: number
  project_id: number
  cycle_id: number
  revision: number
  generated_at: string
  summary_text: string
  cycle_name: string | null
  plan_name: string | null
  target_build: string | null
  pass_rate: number | null
  completion_rate: number | null
  risk_count: number
}

export function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '不适用' : `${Math.round(value * 100)}%`
}

export default function Reports() {
  const { projectId } = useParams()
  const pid = Number(projectId)

  const query = useQuery({
    queryKey: ['reports', pid],
    queryFn: () => apiJson<ReportSummary[]>(`/api/v1/projects/${pid}/reports`),
    enabled: Number.isFinite(pid),
  })

  const columns: ColumnsType<ReportSummary> = [
    {
      title: '批次',
      dataIndex: 'cycle_name',
      ellipsis: true,
      render: (name: string | null, row) =>
        name ? (
          <Space direction="vertical" size={0}>
            <Link to={`/projects/${pid}/reports/${row.id}`}>{name}</Link>
            <span className="muted">{row.plan_name || '—'}</span>
          </Space>
        ) : (
          `批次 #${row.cycle_id}`
        ),
    },
    { title: '版本', dataIndex: 'revision', width: 70, render: (v: number) => `r${v}` },
    { title: '构建', dataIndex: 'target_build', width: 130, render: (v: string | null) => v || '未知' },
    {
      title: '通过率',
      dataIndex: 'pass_rate',
      width: 90,
      render: (v: number | null) => pct(v),
    },
    {
      title: '完成比例',
      dataIndex: 'completion_rate',
      width: 100,
      render: (v: number | null) => pct(v),
    },
    {
      title: '风险',
      dataIndex: 'risk_count',
      width: 80,
      render: (v: number) =>
        v > 0 ? <Tag color="warning">{v} 项</Tag> : <Tag color="success">无</Tag>,
    },
    {
      title: '生成时间',
      dataIndex: 'generated_at',
      width: 170,
      render: (v: string) => formatBeijing(v),
    },
    {
      title: '操作',
      width: 150,
      render: (_: unknown, row) => (
        <Space size="small">
          <Link to={`/projects/${pid}/reports/${row.id}`}>查看</Link>
          <a href={`/api/v1/projects/${pid}/reports/${row.id}/export?format=md`}>Markdown</a>
          <a href={`/api/v1/projects/${pid}/reports/${row.id}/export?format=xlsx`}>Excel</a>
        </Space>
      ),
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="测试报告" />
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          报告是批次结果的快照：生成后即使用例或结果再变化，历史报告仍保持原样。可在「测试计划 → 批次」
          页面为某个批次生成新报告。
        </Typography.Paragraph>
      </div>
      <div className="list-page-body is-table">
        <Card size="small">
          <Table<ReportSummary>
            rowKey="id"
            loading={query.isLoading}
            columns={columns}
            dataSource={query.data ?? []}
            locale={{ emptyText: '暂无报告，请先在批次详情页生成' }}
            pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 份` }}
          />
        </Card>
      </div>
    </div>
  )
}
