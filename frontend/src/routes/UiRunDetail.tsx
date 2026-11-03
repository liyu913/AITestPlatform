import {
  Alert,
  App,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Image,
  Input,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { UiRunFixPanel, useUiRunFix } from '../components/UiRunFixPanel'
import PageBanner from './PageBanner'

type UiRun = {
  id: number
  project_id: number
  status: string
  trigger_type: string
  browser: string
  headless?: boolean
  account_name: string | null
  summary_json: {
    passed?: number
    failed?: number
    error?: number
    skipped?: number
    log_lines?: string[]
  } | null
  started_at: string | null
  finished_at: string | null
  env_id: number | null
  target_id: number
}

type UiRunStep = {
  id: number
  order: number
  case_id: number | null
  case_name: string | null
  action: string
  selector: string
  status: string
  duration_ms: number | null
  retry_count: number
  error_message: string
  screenshot_key: string | null
  detail_json: Record<string, unknown> | null
}

type UiCaseScript = {
  id: number
  name: string
  script: string
}

function statusColor(s: string): string {
  if (s === 'passed') return 'success'
  if (s === 'failed') return 'error'
  if (s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  return 'default'
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string; detail?: string }
    return b.message ?? b.detail ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

async function downloadExport(pid: number, rid: number, format: 'junit' | 'html') {
  const res = await apiFetch(`/api/v1/projects/${pid}/ui-runs/${rid}/export?format=${format}`)
  if (!res.ok) throw new Error('导出失败')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = format === 'html' ? `ui-run-${rid}.html` : `ui-run-${rid}.xml`
  a.click()
  URL.revokeObjectURL(url)
}

function AuthenticatedShot({
  pid,
  rid,
  stepId,
  width = 280,
}: {
  pid: number
  rid: number
  stepId: number
  width?: number
}) {
  const q = useQuery({
    queryKey: ['ui-shot', pid, rid, stepId],
    queryFn: async () => {
      const res = await apiFetch(`/api/v1/projects/${pid}/ui-runs/${rid}/steps/${stepId}/screenshot`)
      if (!res.ok) throw new Error('截图加载失败')
      const blob = await res.blob()
      return URL.createObjectURL(blob)
    },
  })
  if (q.isLoading) return <span className="explore-shot-loading">加载截图…</span>
  if (!q.data) return <span>—</span>
  return <Image src={q.data} width={width} alt="screenshot" />
}

function UiCaseScriptDrawer({
  projectId,
  caseId,
  caseNameHint,
  open,
  runEnvId,
  runBrowser,
  runHeadless,
  onClose,
  onRerunCreated,
}: {
  projectId: number
  caseId: number | null
  caseNameHint: string | null
  open: boolean
  runEnvId: number | null
  runBrowser: string
  runHeadless: boolean
  onClose: () => void
  onRerunCreated: (runId: number) => void
}) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const [rerunAfterSave, setRerunAfterSave] = useState(true)

  const caseQ = useQuery({
    queryKey: ['ui-case-script', projectId, caseId],
    queryFn: () => apiJson<UiCaseScript>(`/api/v1/projects/${projectId}/ui-cases/${caseId}`),
    enabled: open && caseId != null,
  })

  useEffect(() => {
    if (!open) return
    if (caseQ.data) setDraft(caseQ.data.script ?? '')
  }, [open, caseQ.data])

  const save = useMutation({
    mutationFn: async () => {
      if (caseId == null) throw new Error('缺少用例')
      const updated = await apiJson<UiCaseScript>(`/api/v1/projects/${projectId}/ui-cases/${caseId}`, {
        method: 'PATCH',
        body: JSON.stringify({ script: draft }),
      })
      if (!rerunAfterSave) return { updated, rerunId: null as number | null }
      const run = await apiJson<{ id: number }>(`/api/v1/projects/${projectId}/ui-cases/${caseId}/run`, {
        method: 'POST',
        body: JSON.stringify({
          env_id: runEnvId,
          browser: runBrowser || null,
          headless: runHeadless,
        }),
      })
      return { updated, rerunId: run.id }
    },
    onSuccess: ({ rerunId }) => {
      void qc.invalidateQueries({ queryKey: ['ui-cases', projectId] })
      void qc.invalidateQueries({ queryKey: ['ui-case-script', projectId, caseId] })
      if (rerunId != null) {
        message.success('已保存脚本并开始重跑')
        onClose()
        onRerunCreated(rerunId)
      } else {
        message.success('已写入用例脚本（不影响本次已完成的执行记录）')
      }
    },
    onError: (e) => message.error(errText(e)),
  })

  const titleName = caseQ.data?.name || caseNameHint || (caseId != null ? `#${caseId}` : '')

  return (
    <Drawer
      title={titleName ? `用例脚本 · ${titleName}` : '用例脚本'}
      open={open}
      onClose={onClose}
      width={640}
      destroyOnHidden
      extra={
        <Space>
          <Checkbox
            checked={rerunAfterSave}
            disabled={save.isPending}
            onChange={(e) => setRerunAfterSave(e.target.checked)}
          >
            保存后重跑
          </Checkbox>
          <Button type="primary" loading={save.isPending} disabled={caseId == null} onClick={() => void save.mutate()}>
            保存
          </Button>
        </Space>
      }
    >
      {caseId == null ? (
        <Alert type="warning" showIcon message="该步骤未关联用例，无法打开脚本" />
      ) : caseQ.isLoading ? (
        <Typography.Text type="secondary">加载脚本…</Typography.Text>
      ) : caseQ.isError ? (
        <Alert type="error" showIcon message={errText(caseQ.error)} />
      ) : (
        <>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            编辑并保存将写入用例脚本；本次执行记录不会改变。
          </Typography.Paragraph>
          <Input.TextArea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoSize={{ minRows: 18, maxRows: 36 }}
            style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace', fontSize: 12 }}
            spellCheck={false}
          />
        </>
      )}
    </Drawer>
  )
}

export default function UiRunDetail() {
  const { projectId, runId } = useParams()
  const nav = useNavigate()
  const pid = Number(projectId)
  const rid = Number(runId)
  const tableRef = useRef<HTMLDivElement>(null)
  const fix = useUiRunFix(pid, Number.isFinite(rid) ? rid : null)
  const [scriptCaseId, setScriptCaseId] = useState<number | null>(null)
  const [scriptCaseName, setScriptCaseName] = useState<string | null>(null)
  const [scriptOpen, setScriptOpen] = useState(false)

  const run = useQuery({
    queryKey: ['ui-run', pid, rid],
    queryFn: () => apiJson<UiRun>(`/api/v1/projects/${pid}/ui-runs/${rid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: (q) => {
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const steps = useQuery({
    queryKey: ['ui-run-steps', pid, rid],
    queryFn: () => apiJson<UiRunStep[]>(`/api/v1/projects/${pid}/ui-runs/${rid}/steps`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
    refetchInterval: () => {
      const st = run.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const failed = useMemo(
    () => (steps.data ?? []).find((s) => s.status === 'failed' || s.status === 'error'),
    [steps.data],
  )

  const logLines = useMemo(() => {
    const list = run.data?.summary_json?.log_lines
    return Array.isArray(list) ? list : []
  }, [run.data])

  function openScript(row: UiRunStep) {
    if (row.case_id == null) return
    setScriptCaseId(row.case_id)
    setScriptCaseName(row.case_name)
    setScriptOpen(true)
  }

  const columns: ColumnsType<UiRunStep> = [
    { title: '#', dataIndex: 'order', width: 50 },
    { title: '用例', dataIndex: 'case_name', width: 120, ellipsis: true },
    { title: '动作', dataIndex: 'action', width: 120 },
    { title: '选择器', dataIndex: 'selector', ellipsis: true },
    {
      title: '结果',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '重试', dataIndex: 'retry_count', width: 60 },
    { title: '耗时(ms)', dataIndex: 'duration_ms', width: 100 },
    {
      title: '截图',
      width: 100,
      render: (_, row) =>
        row.screenshot_key ? <AuthenticatedShot pid={pid} rid={rid} stepId={row.id} width={72} /> : '—',
    },
    {
      title: '操作',
      width: 100,
      fixed: 'right',
      render: (_, row) => (
        <Tooltip title={row.case_id == null ? '该步骤未关联用例' : undefined}>
          <Button type="link" size="small" disabled={row.case_id == null} onClick={() => openScript(row)}>
            查看脚本
          </Button>
        </Tooltip>
      ),
    },
  ]

  return (
    <div className="detail-page">
      <PageBanner title={`UI 执行记录 #${rid}`} />
      <Space style={{ marginBottom: 16 }} wrap>
        <Link to={`/projects/${pid}/ui-runs`}>
          <Button>返回列表</Button>
        </Link>
        <Button
          onClick={() => {
            void run.refetch()
            void steps.refetch()
          }}
        >
          刷新
        </Button>
        <Button onClick={() => void downloadExport(pid, rid, 'html')}>导出 HTML</Button>
        <Button onClick={() => void downloadExport(pid, rid, 'junit')}>导出 JUnit</Button>
      </Space>

      {run.data?.status === 'pending' && (
        <Alert
          style={{ marginBottom: 16 }}
          type="info"
          showIcon
          message="执行排队中"
          description="请确认 Celery Worker 已启动，且已执行 playwright install chromium。"
        />
      )}

      {failed && (
        <Alert
          style={{ marginBottom: 16 }}
          type="error"
          showIcon
          message={`失败于步骤 #${failed.order} · ${failed.action}${failed.selector ? ` · ${failed.selector}` : ''}`}
          description={
            <div>
              <Typography.Paragraph style={{ marginBottom: 8 }}>
                {failed.error_message || '无详细错误信息'}
              </Typography.Paragraph>
              {failed.screenshot_key && (
                <AuthenticatedShot pid={pid} rid={rid} stepId={failed.id} width={560} />
              )}
              <div style={{ marginTop: 8 }}>
                <Space>
                  <Button type="primary" size="small" loading={fix.busy} onClick={() => void fix.startFix()}>
                    AI 修复
                  </Button>
                  {failed.case_id != null && (
                    <Button size="small" onClick={() => openScript(failed)}>
                      查看脚本
                    </Button>
                  )}
                  <Button
                    size="small"
                    onClick={() => tableRef.current?.scrollIntoView({ behavior: 'smooth' })}
                  >
                    查看全部步骤
                  </Button>
                </Space>
              </div>
            </div>
          }
        />
      )}

      {run.data && (
        <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }} className="card">
          <Descriptions.Item label="状态">
            <Tag color={statusColor(run.data.status)}>{run.data.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="类型">{run.data.trigger_type}</Descriptions.Item>
          <Descriptions.Item label="目标 ID">{run.data.target_id}</Descriptions.Item>
          <Descriptions.Item label="浏览器">{run.data.browser || 'chromium'}</Descriptions.Item>
          <Descriptions.Item label="模式">{run.data.headless === false ? '有头' : '无头'}</Descriptions.Item>
          <Descriptions.Item label="开始">{formatBeijing(run.data.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束">{formatBeijing(run.data.finished_at)}</Descriptions.Item>
          <Descriptions.Item label="汇总" span={2}>
            {run.data.summary_json
              ? `通过 ${run.data.summary_json.passed ?? 0} / 失败 ${run.data.summary_json.failed ?? 0} / 错误 ${run.data.summary_json.error ?? 0}`
              : '—'}
          </Descriptions.Item>
        </Descriptions>
      )}

      <h2 className="card-section-title">执行步骤</h2>
      <div ref={tableRef}>
        <Table<UiRunStep>
          className="card"
          rowKey="id"
          loading={steps.isLoading}
          columns={columns}
          dataSource={steps.data ?? []}
          pagination={false}
          scroll={{ x: 1100 }}
          style={{ marginBottom: 16 }}
          rowClassName={(row) =>
            row.status === 'failed' || row.status === 'error' ? 'ui-run-step-fail' : ''
          }
          expandable={{
            defaultExpandedRowKeys: failed ? [failed.id] : [],
            expandedRowRender: (row) => (
              <div>
                <Space style={{ marginBottom: 8 }}>
                  <Tooltip title={row.case_id == null ? '该步骤未关联用例' : undefined}>
                    <Button size="small" disabled={row.case_id == null} onClick={() => openScript(row)}>
                      查看脚本
                    </Button>
                  </Tooltip>
                </Space>
                {row.error_message && <p style={{ color: '#cf1322' }}>{row.error_message}</p>}
                <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(row.detail_json, null, 2)}
                </pre>
                {row.screenshot_key && <AuthenticatedShot pid={pid} rid={rid} stepId={row.id} />}
              </div>
            ),
          }}
        />
      </div>

      <h2 className="card-section-title">执行日志</h2>
      <div className="card testcase-run-log" style={{ marginBottom: 16 }}>
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

      <UiRunFixPanel
        projectId={pid}
        open={fix.open}
        busy={fix.busy}
        session={fix.session}
        applyPatch={fix.applyPatch}
        rerun={fix.rerun}
        onApplyPatchChange={fix.setApplyPatch}
        onRerunChange={fix.setRerun}
        onConfirm={(action) =>
          void fix.confirmFix(action, (rerunId) => {
            if (rerunId != null) nav(`/projects/${pid}/ui-runs/${rerunId}`)
          })
        }
        onClose={() => fix.setOpen(false)}
      />

      <UiCaseScriptDrawer
        projectId={pid}
        caseId={scriptCaseId}
        caseNameHint={scriptCaseName}
        open={scriptOpen}
        runEnvId={run.data?.env_id ?? null}
        runBrowser={run.data?.browser || 'chromium'}
        runHeadless={run.data?.headless !== false}
        onClose={() => setScriptOpen(false)}
        onRerunCreated={(newId) => nav(`/projects/${pid}/ui-runs/${newId}`)}
      />
    </div>
  )
}
