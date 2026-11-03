import { App, Button, Select, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type CaseRun = {
  id: number
  case_id: number
  case_title: string
  mode: string
  status: string
  summary: string
  error_message: string
  session_id: number | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

type PageRes<T> = { items: T[]; total: number }

const STATUS_OPTIONS = [
  { value: 'pending', label: 'pending' },
  { value: 'running', label: 'running' },
  { value: 'passed', label: 'passed' },
  { value: 'failed', label: 'failed' },
  { value: 'error', label: 'error' },
  { value: 'cancelled', label: 'cancelled' },
]

const MODE_LABEL: Record<string, string> = {
  agent_once: 'Agent 直跑',
  manual: '手工',
}

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  if (s === 'cancelled') return 'default'
  return 'default'
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError) return ex.message
  if (ex instanceof Error) return ex.message
  return String(ex)
}

export default function TestCaseRunList() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { containerRef, scrollY } = useTableScrollY()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [stoppingId, setStoppingId] = useState<number | null>(null)

  const list = useQuery({
    queryKey: ['test-case-runs', pid, status, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (status) qs.set('status', status)
      return apiJson<PageRes<CaseRun>>(`/api/v1/projects/${pid}/test-case-runs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    refetchInterval: (q) => {
      if (q.state.error) return false
      const items = q.state.data?.items ?? []
      return items.some((r) => r.status === 'pending' || r.status === 'running') ? 3000 : false
    },
  })

  async function stopRun(runId: number) {
    setStoppingId(runId)
    try {
      await apiJson<CaseRun>(`/api/v1/projects/${pid}/test-case-runs/${runId}/cancel`, {
        method: 'POST',
      })
      message.info('已停止执行')
      void qc.invalidateQueries({ queryKey: ['test-case-runs', pid] })
    } catch (e) {
      message.error(errText(e))
    } finally {
      setStoppingId(null)
    }
  }

  const columns: ColumnsType<CaseRun> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    {
      title: '模式',
      dataIndex: 'mode',
      width: 110,
      render: (m: string) => MODE_LABEL[m] || m,
    },
    {
      title: '用例',
      ellipsis: true,
      render: (_, row) => (
        <Link to={`/projects/${pid}/test-cases`}>{row.case_title || `#${row.case_id}`}</Link>
      ),
    },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    {
      title: '创建',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => formatBeijing(v),
    },
    {
      title: '操作',
      width: 140,
      render: (_, row) => (
        <Space size="small">
          <Link to={`/projects/${pid}/test-case-runs/${row.id}`}>详情</Link>
          {row.status === 'pending' || row.status === 'running' ? (
            <Button
              type="link"
              danger
              size="small"
              loading={stoppingId === row.id}
              onClick={() => void stopRun(row.id)}
            >
              停止
            </Button>
          ) : null}
        </Space>
      ),
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="执行记录" />
        <Space>
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 140 }}
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
          dataSource={list.data?.items ?? []}
          scroll={{ y: scrollY }}
          pagination={{
            current: page,
            pageSize,
            total: list.data?.total ?? 0,
            showSizeChanger: true,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
            },
          }}
        />
      </div>
    </div>
  )
}
