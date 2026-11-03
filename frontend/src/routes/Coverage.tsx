import { Button, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import PageBanner from './PageBanner'

type CoverageRow = {
  requirement_id: number
  title: string
  status: string
  priority: string | null
  case_total: number
  passed: number
  failed: number
  blocked: number
  untested: number
  coverage_ratio: number
  blind: boolean
}

export default function Coverage() {
  const { projectId } = useParams()
  const pid = Number(projectId)

  const list = useQuery({
    queryKey: ['coverage', pid],
    queryFn: () =>
      apiJson<CoverageRow[]>(`/api/v1/projects/${pid}/coverage/requirements`),
    enabled: Number.isFinite(pid),
  })

  const columns: ColumnsType<CoverageRow> = [
    { title: 'ID', dataIndex: 'requirement_id', width: 70 },
    {
      title: '需求',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string, row) => (
        <Link to={`/projects/${pid}/test-cases?requirement_id=${row.requirement_id}`}>
          {title}
        </Link>
      ),
    },
    { title: '优先级', dataIndex: 'priority', width: 80, render: (v) => v || '—' },
    { title: '用例数', dataIndex: 'case_total', width: 80 },
    {
      title: '通过',
      dataIndex: 'passed',
      width: 70,
      render: (v: number) => <span style={{ color: '#389e0d' }}>{v}</span>,
    },
    {
      title: '失败',
      dataIndex: 'failed',
      width: 70,
      render: (v: number) => <span style={{ color: v ? '#cf1322' : undefined }}>{v}</span>,
    },
    { title: '阻塞', dataIndex: 'blocked', width: 70 },
    {
      title: '未测',
      dataIndex: 'untested',
      width: 70,
      render: (v: number) => <span style={{ color: v ? '#d48806' : undefined }}>{v}</span>,
    },
    {
      title: '覆盖率',
      dataIndex: 'coverage_ratio',
      width: 90,
      render: (v: number) => `${Math.round(v * 100)}%`,
    },
    {
      title: '盲区',
      dataIndex: 'blind',
      width: 90,
      render: (blind: boolean) =>
        blind ? <Tag color="warning">有盲区</Tag> : <Tag color="success">已覆盖</Tag>,
    },
  ]

  const rows = list.data ?? []
  const blindCount = rows.filter((r) => r.blind).length

  return (
    <div>
      <PageBanner title="需求覆盖率" />
      <Space style={{ marginBottom: 16 }}>
        <Link to={`/projects/${pid}/requirements`}>
          <Button>需求管理</Button>
        </Link>
        <span className="muted">
          共 {rows.length} 条需求 · 盲区 {blindCount} 条（无用例 / 未测 / 失败）
        </span>
      </Space>
      <Table<CoverageRow>
        rowKey="requirement_id"
        loading={list.isLoading}
        columns={columns}
        dataSource={rows}
        pagination={false}
      />
    </div>
  )
}
