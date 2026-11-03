import { ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Checkbox, Collapse, Descriptions, Drawer, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PrettyJson from '../components/PrettyJson'
import PageBanner from './PageBanner'

type ApiRun = {
  id: number
  project_id: number
  status: string
  summary_json: (Record<string, number> & { case_ids?: number[] }) | null
  started_at: string | null
  finished_at: string | null
  env_id: number | null
  trigger_type: string
  target_id: number
}

type ApiRunStep = {
  id: number
  order: number
  case_id: number | null
  case_name: string
  status: string
  response_status: number | null
  duration_ms: number | null
  error_message: string
  request_snapshot_json: Record<string, unknown> | null
  response_body_text: string
  assertion_results_json: Array<{ ok?: boolean; detail?: string; type?: string }> | null
  extract_results_json: Record<string, string> | null
}

type TriageSession = {
  id: string
  status: string
  plan: {
    summary?: string
    likely_causes?: string[]
    suggested_patches?: Array<{ target?: string; rationale?: string; patch?: unknown }>
    warnings?: string[]
  } | null
  events: Array<Record<string, unknown>>
  error_message: string
  patch_message?: string
}

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  if (ex instanceof Error && ex.message) return ex.message
  return '请求失败'
}

export default function ApiRunDetail() {
  const { message } = App.useApp()
  const nav = useNavigate()
  const { projectId, runId } = useParams()
  const pid = Number(projectId)
  const rid = Number(runId)
  const [stuckHint, setStuckHint] = useState(false)
  const [selectedStepId, setSelectedStepId] = useState<number | null>(null)
  const seenPendingAt = useRef<number | null>(null)

  const [triageOpen, setTriageOpen] = useState(false)
  const [triageBusy, setTriageBusy] = useState(false)
  const [triage, setTriage] = useState<TriageSession | null>(null)
  const [triageLogs, setTriageLogs] = useState<string[]>([])
  const [applyPatch, setApplyPatch] = useState(false)

  const run = useQuery({
    queryKey: ['api-run', pid, rid],
    queryFn: () => apiJson<ApiRun>(`/api/v1/projects/${pid}/api-runs/${rid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: (q) => {
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const steps = useQuery({
    queryKey: ['api-run-steps', pid, rid],
    queryFn: () => apiJson<ApiRunStep[]>(`/api/v1/projects/${pid}/api-runs/${rid}/steps`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: () => {
      const st = run.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const rerun = useMutation({
    mutationFn: async () => {
      const data = run.data
      if (!data) throw new Error('记录未加载')
      const env_id = data.env_id
      const body = { sync: true as const, env_id }
      if (data.trigger_type === 'suite') {
        return apiJson<ApiRun>(`/api/v1/projects/${pid}/api-suites/${data.target_id}/run`, {
          method: 'POST',
          body: JSON.stringify(body),
        })
      }
      if (data.trigger_type === 'case') {
        return apiJson<ApiRun>(`/api/v1/projects/${pid}/api-cases/${data.target_id}/run`, {
          method: 'POST',
          body: JSON.stringify(body),
        })
      }
      if (data.trigger_type === 'batch') {
        const case_ids = data.summary_json?.case_ids
        if (!Array.isArray(case_ids) || case_ids.length === 0) {
          throw new Error('批量记录缺少用例列表，无法重新执行')
        }
        return apiJson<ApiRun>(`/api/v1/projects/${pid}/api-cases/batch-run`, {
          method: 'POST',
          body: JSON.stringify({ case_ids, sync: true, env_id }),
        })
      }
      throw new Error(`不支持的触发类型：${data.trigger_type}`)
    },
    onSuccess: (data) => {
      message.success(`重新执行完成：${data.status}`)
      nav(`/projects/${pid}/api-runs/${data.id}`)
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

  useEffect(() => {
    const list = steps.data
    if (!list?.length) return
    if (selectedStepId != null && list.some((s) => s.id === selectedStepId)) return
    const failed = list.find((s) => s.status === 'failed' || s.status === 'error')
    setSelectedStepId((failed || list[0]).id)
  }, [steps.data, selectedStepId])

  const selected = useMemo(
    () => steps.data?.find((s) => s.id === selectedStepId) ?? steps.data?.[0],
    [steps.data, selectedStepId],
  )

  const failedAsserts = (selected?.assertion_results_json ?? []).filter((a) => a.ok === false)

  function ingestTriageEvents(events: Array<Record<string, unknown>>) {
    const lines: string[] = []
    for (const ev of events) {
      const t = String(ev.type || '')
      if (t === 'log') lines.push(`[${String(ev.node || '')}] ${String(ev.message || '')}`)
      else if (t === 'plan') lines.push('[plan] 已生成归因计划')
      else if (t === 'interrupted') lines.push(`[wait] ${String(ev.message || '')}`)
      else if (t === 'done') lines.push(`[done] ${String(ev.status || '')}`)
      else if (t === 'error') lines.push(`[error] ${String(ev.message || '')}`)
    }
    setTriageLogs(lines)
  }

  async function startTriage(stepId: number) {
    setTriageBusy(true)
    setTriage(null)
    setTriageLogs([])
    setApplyPatch(false)
    setTriageOpen(true)
    try {
      const session = await apiJson<TriageSession>(
        `/api/v1/projects/${pid}/api-runs/${rid}/steps/${stepId}/ai-triage`,
        { method: 'POST', body: '{}' },
      )
      setTriage(session)
      ingestTriageEvents(session.events || [])
      setApplyPatch((session.plan?.suggested_patches?.length ?? 0) > 0)
    } catch (e) {
      message.error(errText(e))
      setTriageOpen(false)
    } finally {
      setTriageBusy(false)
    }
  }

  async function confirmTriage(action: 'confirm' | 'cancel') {
    if (!triage) return
    setTriageBusy(true)
    try {
      const session = await apiJson<TriageSession>(
        `/api/v1/projects/${pid}/api-runs/${rid}/ai-triage/${triage.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({ action, apply_patch: applyPatch }),
        },
      )
      setTriage(session)
      ingestTriageEvents(session.events || [])
      if (action === 'cancel') message.info('已取消')
      else if (session.status === 'error') message.error(session.error_message || session.patch_message || '写入失败')
      else if (session.patch_message?.startsWith('已写入')) message.success(session.patch_message)
      else if (session.patch_message) message.warning(session.patch_message)
    } catch (e) {
      message.error(errText(e))
    } finally {
      setTriageBusy(false)
    }
  }

  const columns: ColumnsType<ApiRunStep> = [
    { title: '#', dataIndex: 'order', width: 50 },
    { title: '用例', dataIndex: 'case_name' },
    {
      title: '结果',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: 'HTTP', dataIndex: 'response_status', width: 80 },
    { title: '耗时(ms)', dataIndex: 'duration_ms', width: 100 },
    {
      title: '断言',
      render: (_, row) => {
        const arr = row.assertion_results_json ?? []
        if (!arr.length) return '—'
        return arr.map((a, i) => (
          <div key={i} style={{ color: a.ok ? '#389e0d' : '#cf1322', fontWeight: a.ok ? 400 : 600 }}>
            {a.ok ? '✓' : '✗'} {a.detail || a.type}
          </div>
        ))
      },
    },
  ]

  const canConfirmTriage = triage?.status === 'awaiting_confirm' && !triageBusy

  const triggerType = run.data?.trigger_type
  const targetId = run.data?.target_id
  const batchCaseIds = run.data?.summary_json?.case_ids
  const triggerLabel =
    triggerType === 'suite' ? '场景' : triggerType === 'case' ? '单接口' : triggerType === 'batch' ? '单接口批量' : triggerType || '—'
  const targetLabel = triggerType === 'suite' ? '场景 ID' : triggerType === 'case' ? '用例 ID' : '目标 ID'
  const runBusy = run.data?.status === 'pending' || run.data?.status === 'running'
  const canRerun =
    run.data != null &&
    !runBusy &&
    ((triggerType === 'suite' && targetId != null) ||
      (triggerType === 'case' && targetId != null) ||
      (triggerType === 'batch' && Array.isArray(batchCaseIds) && batchCaseIds.length > 0))

  return (
    <div>
      <PageBanner title={`执行记录 #${rid}`} />
      <Space style={{ marginBottom: 16 }} wrap>
        {triggerType === 'suite' && targetId != null && (
          <Link to={`/projects/${pid}/api-suites/${targetId}`}>
            <Button type="primary">返回场景</Button>
          </Link>
        )}
        {triggerType === 'case' && targetId != null && (
          <Link to={`/projects/${pid}/api-cases?caseId=${targetId}`}>
            <Button type="primary">返回单接口</Button>
          </Link>
        )}
        <Link to={`/projects/${pid}/api-runs`}>
          <Button>全部记录</Button>
        </Link>
        <Button
          onClick={() => {
            void run.refetch()
            void steps.refetch()
          }}
        >
          刷新
        </Button>
        <Button
          icon={<ReloadOutlined />}
          loading={rerun.isPending}
          disabled={!canRerun}
          onClick={() => {
            if (triggerType === 'batch' && (!Array.isArray(batchCaseIds) || batchCaseIds.length === 0)) {
              message.warning('批量记录缺少用例列表，无法重新执行')
              return
            }
            rerun.mutate()
          }}
        >
          重新执行
        </Button>
        <Button
          onClick={async () => {
            try {
              const res = await fetch(
                `${import.meta.env.VITE_API_BASE ?? ''}/api/v1/projects/${pid}/api-runs/${rid}/export?format=junit`,
                { headers: { Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` } },
              )
              if (!res.ok) throw new Error('导出失败')
              const blob = await res.blob()
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = `api-run-${rid}.xml`
              a.click()
              URL.revokeObjectURL(url)
            } catch {
              /* ignore */
            }
          }}
        >
          导出 JUnit
        </Button>
        <Button
          onClick={async () => {
            try {
              const res = await fetch(
                `${import.meta.env.VITE_API_BASE ?? ''}/api/v1/projects/${pid}/api-runs/${rid}/export?format=html`,
                { headers: { Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` } },
              )
              if (!res.ok) throw new Error('导出失败')
              const blob = await res.blob()
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = `api-run-${rid}.html`
              a.click()
              URL.revokeObjectURL(url)
            } catch {
              /* ignore */
            }
          }}
        >
          导出 HTML
        </Button>
      </Space>

      {stuckHint && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="执行似乎卡住了"
          description="若长时间处于 pending/running，请确认已启动 Celery Worker：uv run celery -A app.tasks.celery_app worker -l info。定时任务还需 Beat。调试单条请求可在单接口页使用「发送调试」（无需 Worker）。"
        />
      )}

      {run.data && (
        <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
          <Descriptions.Item label="状态">
            <Tag color={statusColor(run.data.status)}>{run.data.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="触发类型">{triggerLabel}</Descriptions.Item>
          {triggerType !== 'batch' && (
            <Descriptions.Item label={targetLabel}>{run.data.target_id}</Descriptions.Item>
          )}
          <Descriptions.Item label="开始">{formatBeijing(run.data.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束">{formatBeijing(run.data.finished_at)}</Descriptions.Item>
          <Descriptions.Item label="汇总" span={2}>
            {run.data.summary_json
              ? `通过 ${run.data.summary_json.passed ?? 0} / 失败 ${run.data.summary_json.failed ?? 0} / 错误 ${run.data.summary_json.error ?? 0}`
              : '—'}
          </Descriptions.Item>
        </Descriptions>
      )}

      <Table<ApiRunStep>
        rowKey="id"
        loading={steps.isLoading}
        columns={columns}
        dataSource={steps.data ?? []}
        pagination={false}
        style={{ marginBottom: 16 }}
        onRow={(row) => ({
          onClick: () => setSelectedStepId(row.id),
          style: {
            cursor: 'pointer',
            background:
              row.id === selected?.id
                ? 'rgba(22, 119, 255, 0.08)'
                : row.status === 'failed' || row.status === 'error'
                  ? 'rgba(255, 77, 79, 0.06)'
                  : undefined,
          },
        })}
      />

      {selected && (
        <div>
          <Typography.Title level={5}>
            步骤详情 · {selected.case_name}
            {selected.case_id != null && (
              <Link to={`/projects/${pid}/api-cases?caseId=${selected.case_id}`} style={{ marginLeft: 12, fontSize: 14 }}>
                去调试
              </Link>
            )}
            {(selected.status === 'failed' || selected.status === 'error') && (
              <Button
                type="link"
                style={{ marginLeft: 8 }}
                loading={triageBusy}
                onClick={() => void startTriage(selected.id)}
              >
                AI 归因
              </Button>
            )}
          </Typography.Title>
          {selected.error_message && (
            <Alert type="error" showIcon message={selected.error_message} style={{ marginBottom: 12 }} />
          )}
          {failedAsserts.length > 0 && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 12 }}
              message="失败断言"
              description={failedAsserts.map((a) => a.detail || a.type).join('；')}
            />
          )}
          {selected.extract_results_json && Object.keys(selected.extract_results_json).length > 0 && (
            <Alert
              type="info"
              style={{ marginBottom: 12 }}
              message={`提取变量：${Object.entries(selected.extract_results_json)
                .map(([k, v]) => `${k}=${v}`)
                .join(', ')}`}
            />
          )}
          <Collapse
            defaultActiveKey={['assert', 'res']}
            items={[
              {
                key: 'assert',
                label: '断言结果',
                children: (selected.assertion_results_json ?? []).length ? (
                  (selected.assertion_results_json ?? []).map((a, i) => (
                    <div key={i} style={{ color: a.ok ? '#389e0d' : '#cf1322', fontWeight: a.ok ? 400 : 600 }}>
                      {a.ok ? '✓' : '✗'} {a.detail || a.type}
                    </div>
                  ))
                ) : (
                  <span>—</span>
                ),
              },
              {
                key: 'req',
                label: '请求快照',
                children: <PrettyJson value={selected.request_snapshot_json} />,
              },
              {
                key: 'res',
                label: '响应 Body',
                children: (
                  <div style={{ maxHeight: 360, overflow: 'auto' }}>
                    <PrettyJson value={selected.response_body_text} />
                  </div>
                ),
              },
            ]}
          />
        </div>
      )}

      <Drawer
        title="AI 失败归因（计划先行）"
        open={triageOpen}
        onClose={() => setTriageOpen(false)}
        width={560}
        destroyOnHidden
      >
        <Card size="small" title="运行日志" style={{ marginBottom: 16 }}>
          <pre style={{ fontSize: 12, maxHeight: 160, overflow: 'auto', margin: 0 }}>
            {triageLogs.length ? triageLogs.join('\n') : '（尚无日志）'}
          </pre>
        </Card>
        <Card size="small" title="归因计划" style={{ marginBottom: 16 }}>
          {triage?.plan?.summary && <Typography.Paragraph>{triage.plan.summary}</Typography.Paragraph>}
          {(triage?.plan?.likely_causes ?? []).length > 0 && (
            <ul>
              {(triage?.plan?.likely_causes ?? []).map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
          {(triage?.plan?.suggested_patches ?? []).map((p, i) => (
            <Alert
              key={i}
              style={{ marginBottom: 8 }}
              type="info"
              message={`${p.target || 'patch'}: ${p.rationale || ''}`}
              description={
                <pre style={{ fontSize: 11, margin: 0, whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(p.patch, null, 2)}
                </pre>
              }
            />
          ))}
          {!triage?.plan && <Typography.Text type="secondary">生成中或暂无计划</Typography.Text>}
        </Card>
        <Space wrap>
          <Checkbox checked={applyPatch} onChange={(e) => setApplyPatch(e.target.checked)} disabled={triageBusy}>
            确认时写入修补
          </Checkbox>
          <Button type="primary" disabled={!canConfirmTriage} loading={triageBusy} onClick={() => void confirmTriage('confirm')}>
            {applyPatch ? '确认并写入' : '确认'}
          </Button>
          <Button disabled={!canConfirmTriage} onClick={() => void confirmTriage('cancel')}>
            取消
          </Button>
        </Space>
      </Drawer>
    </div>
  )
}
