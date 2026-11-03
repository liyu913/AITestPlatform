import { App, Button, Descriptions, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { CaseRunShotThumb } from '../components/ExploreShotThumb'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type CaseRunStep = {
  order: number
  action: string
  selector?: string
  value?: string
  status: string
  error?: string
  screenshot_key?: string
}

type CaseRun = {
  id: number
  case_id: number
  case_title: string
  mode: string
  status: string
  summary: string
  error_message: string
  actual_result: string
  session_id: number | null
  env_id: number | null
  summary_json: {
    step_count?: number
    steps?: CaseRunStep[]
    log_lines?: string[]
    screenshot_keys?: string[]
    result?: string
  } | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

const MODE_LABEL: Record<string, string> = {
  agent_once: 'Agent 直跑',
  manual: '手工',
}

function statusColor(s: string): string {
  if (s === 'passed' || s === 'ok') return 'success'
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

export default function TestCaseRunDetail() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const { projectId, runId } = useParams()
  const pid = Number(projectId)
  const rid = Number(runId)
  const [stopping, setStopping] = useState(false)

  const run = useQuery({
    queryKey: ['test-case-run', pid, rid],
    queryFn: () => apiJson<CaseRun>(`/api/v1/projects/${pid}/test-case-runs/${rid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: (q) => {
      if (q.state.error) return false
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  async function stopRun() {
    if (!Number.isFinite(pid) || !Number.isFinite(rid)) return
    setStopping(true)
    try {
      await apiJson<CaseRun>(`/api/v1/projects/${pid}/test-case-runs/${rid}/cancel`, {
        method: 'POST',
      })
      message.info('已停止执行')
      void qc.invalidateQueries({ queryKey: ['test-case-run', pid, rid] })
      void qc.invalidateQueries({ queryKey: ['test-case-runs', pid] })
    } catch (e) {
      message.error(errText(e))
    } finally {
      setStopping(false)
    }
  }

  const row = run.data
  const steps = useMemo(() => {
    const list = row?.summary_json?.steps
    return Array.isArray(list) ? list : []
  }, [row])
  const logLines = useMemo(() => {
    const list = row?.summary_json?.log_lines
    return Array.isArray(list) ? list : []
  }, [row])
  const stepCount = row?.summary_json?.step_count ?? steps.length

  const columns: ColumnsType<CaseRunStep> = [
    { title: '#', dataIndex: 'order', width: 50 },
    { title: '动作', dataIndex: 'action', width: 110 },
    {
      title: '选择器',
      dataIndex: 'selector',
      ellipsis: true,
      render: (v: string | undefined) => v || '—',
    },
    {
      title: '值',
      dataIndex: 'value',
      ellipsis: true,
      width: 160,
      render: (v: string | undefined) => v || '—',
    },
    {
      title: '结果',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (v: string | undefined) =>
        v ? <span className="error">{v}</span> : '—',
    },
    {
      title: '截图',
      width: 100,
      render: (_, s) =>
        s.screenshot_key ? (
          <CaseRunShotThumb projectId={pid} runId={rid} storageKey={s.screenshot_key} width={72} />
        ) : (
          '—'
        ),
    },
  ]

  const canStop = row?.status === 'pending' || row?.status === 'running'

  return (
    <div className="detail-page">
      <PageBanner title={`执行记录 #${rid}`} />
      <p>
        <Space>
          <Link to={`/projects/${pid}/test-case-runs`}>返回列表</Link>
          {canStop ? (
            <Button danger size="small" loading={stopping} onClick={() => void stopRun()}>
              停止
            </Button>
          ) : null}
        </Space>
      </p>
      {row ? (
        <>
          <Descriptions bordered size="small" column={1} className="card">
            <Descriptions.Item label="状态">
              <Tag color={statusColor(row.status)}>{row.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="模式">{MODE_LABEL[row.mode] || row.mode}</Descriptions.Item>
            <Descriptions.Item label="用例">
              <Link to={`/projects/${pid}/test-cases`}>
                {row.case_title || `#${row.case_id}`}
              </Link>
            </Descriptions.Item>
            <Descriptions.Item label="摘要">{row.summary || '—'}</Descriptions.Item>
            <Descriptions.Item label="实际结果">{row.actual_result || '—'}</Descriptions.Item>
            <Descriptions.Item label="错误">
              {row.error_message ? (
                <span className={row.status === 'cancelled' ? undefined : 'error'}>
                  {row.error_message}
                </span>
              ) : (
                '—'
              )}
            </Descriptions.Item>
            <Descriptions.Item label="步骤数">{stepCount || '—'}</Descriptions.Item>
            <Descriptions.Item label="开始">{formatBeijing(row.started_at)}</Descriptions.Item>
            <Descriptions.Item label="结束">{formatBeijing(row.finished_at)}</Descriptions.Item>
            <Descriptions.Item label="创建">{formatBeijing(row.created_at)}</Descriptions.Item>
          </Descriptions>

          <h2 className="card-section-title">执行步骤</h2>
          <Table
            className="card"
            rowKey={(r) => `${r.order}-${r.action}`}
            size="small"
            pagination={false}
            columns={columns}
            dataSource={steps}
            locale={{ emptyText: '暂无步骤快照（较早记录可能未保存）' }}
          />

          <h2 className="card-section-title">执行日志</h2>
          <div className="card testcase-run-log">
            {logLines.length === 0 ? (
              <div className="testcase-automate-log-empty">暂无日志</div>
            ) : (
              logLines.map((line, i) => (
                <div key={`${i}-${line.slice(0, 24)}`} className="testcase-automate-log-line">
                  <span className="testcase-automate-log-body">{line}</span>
                </div>
              ))
            )}
          </div>
        </>
      ) : run.isLoading ? (
        <p>加载中…</p>
      ) : (
        <p className="error">记录不存在</p>
      )}
    </div>
  )
}
