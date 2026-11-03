import {
  Alert,
  App,
  Button,
  Col,
  Input,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import PageBanner from './PageBanner'
type TestCase = {
  id: number
  title: string
  status: string
  steps?: Array<{ action: string; expected: string }>
}
type UiEnv = {
  id: number
  name: string
  is_default: boolean
  base_url: string
  browsers?: string[]
  browser?: string
}
type PageRes<T> = { items: T[]; total: number }
type ExploreJob = {
  id: number
  testcase_id: number
  env_id: number | null
  browser: string
  status: string
  plan_json: Record<string, unknown> | null
  trace_json: Array<Record<string, unknown>> | null
  result_steps: Array<Record<string, unknown>>
  playwright_code: string
  dry_run_status: string | null
  dry_run_error: string
  error_message: string
  saved_ui_case_id: number | null
}
type UiCase = { id: number; name: string }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function statusColor(s: string): string {
  if (s === 'succeeded' || s === 'passed') return 'success'
  if (s === 'failed' || s === 'error') return 'error'
  if (s === 'running' || s === 'pending') return 'processing'
  if (s === 'cancelled') return 'default'
  return 'default'
}

export default function UiAiAssist() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()

  const [testcaseId, setTestcaseId] = useState<number | undefined>()
  const [envId, setEnvId] = useState<number | undefined>()
  const [browser, setBrowser] = useState<string | undefined>()
  const [jobId, setJobId] = useState<number | null>(null)
  const [nameBound, setNameBound] = useState<{ id: number | null; text: string | null }>({
    id: null,
    text: null,
  })
  const [scriptBound, setScriptBound] = useState<{ id: number | null; code: string; text: string }>({
    id: null,
    code: '',
    text: '',
  })

  const cases = useQuery({
    queryKey: ['test-cases', pid],
    queryFn: () =>
      apiJson<PageRes<TestCase>>(`/api/v1/projects/${pid}/test-cases/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })
  const envs = useQuery({
    queryKey: ['ui-envs', pid],
    queryFn: () => apiJson<PageRes<UiEnv>>(`/api/v1/projects/${pid}/ui-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const selectedEnv =
    (envs.data?.items ?? []).find((e) => e.id === envId) ??
    (envs.data?.items ?? []).find((e) => e.is_default) ??
    envs.data?.items?.[0]
  const browserOptions = selectedEnv?.browsers?.length
    ? selectedEnv.browsers
    : [selectedEnv?.browser || 'chromium']

  const job = useQuery({
    queryKey: ['ui-ai-job', pid, jobId],
    queryFn: () => apiJson<ExploreJob>(`/api/v1/projects/${pid}/ui-ai/explore-jobs/${jobId}`),
    enabled: Number.isFinite(pid) && jobId != null,
    refetchInterval: (q) => {
      const st = q.state.data?.status
      return st === 'pending' || st === 'running' ? 2000 : false
    },
  })

  const suggestedCaseName = useMemo(() => {
    if (job.data?.status !== 'succeeded' && job.data?.status !== 'failed') return ''
    const tc = (cases.data?.items ?? []).find((c) => c.id === job.data?.testcase_id)
    return tc ? `${tc.title} (AI)` : ''
  }, [job.data?.status, job.data?.testcase_id, cases.data])
  const caseName =
    nameBound.id === (job.data?.id ?? null) && nameBound.text !== null
      ? nameBound.text
      : suggestedCaseName

  const serverScript = job.data?.playwright_code || ''
  const script =
    scriptBound.id === (job.data?.id ?? null) && scriptBound.code === serverScript
      ? scriptBound.text
      : serverScript

  const createJob = useMutation({
    mutationFn: () =>
      apiJson<ExploreJob>(`/api/v1/projects/${pid}/ui-ai/explore-jobs`, {
        method: 'POST',
        body: JSON.stringify({
          testcase_id: testcaseId,
          env_id: envId ?? selectedEnv?.id ?? null,
          browser: browser || null,
          max_steps: 25,
        }),
      }),
    onSuccess: (data) => {
      setJobId(data.id)
      setNameBound({ id: data.id, text: null })
      message.success('已开始探索')
      void qc.invalidateQueries({ queryKey: ['ui-ai-job', pid, data.id] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const cancelJob = useMutation({
    mutationFn: () =>
      apiJson<ExploreJob>(`/api/v1/projects/${pid}/ui-ai/explore-jobs/${jobId}/cancel`, {
        method: 'POST',
      }),
    onSuccess: () => {
      message.info('已取消')
      void job.refetch()
    },
    onError: (e) => message.error(errText(e)),
  })

  const saveCase = useMutation({
    mutationFn: () =>
      apiJson<UiCase>(`/api/v1/projects/${pid}/ui-ai/explore-jobs/${jobId}/confirm-save`, {
        method: 'POST',
        body: JSON.stringify({
          name: caseName.trim() || undefined,
          script,
          status: 'ready',
        }),
      }),
    onSuccess: (data) => {
      message.success('已写入 UI 用例')
      void job.refetch()
      nav(`/projects/${pid}/ui-cases`)
      void data
    },
    onError: (e) => message.error(errText(e)),
  })

  const selectedCase = useMemo(
    () => (cases.data?.items ?? []).find((c) => c.id === testcaseId),
    [cases.data, testcaseId],
  )

  const traceColumns: ColumnsType<Record<string, unknown>> = [
    { title: '动作', dataIndex: 'action', width: 100 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '选择器', dataIndex: 'selector', ellipsis: true },
    { title: '说明', dataIndex: 'reason', ellipsis: true },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (v: string) => v || '—',
    },
  ]

  const canSave =
    job.data &&
    (job.data.status === 'succeeded' || job.data.status === 'failed') &&
    script.trim().length > 0 &&
    !job.data.saved_ui_case_id

  const running = job.data?.status === 'pending' || job.data?.status === 'running'

  return (
    <div>
      <PageBanner title="UI AI 辅助" />
      <Row gutter={16}>
        <Col xs={24} lg={8}>
          <Typography.Title level={5}>1. 选择意图</Typography.Title>
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="功能用例"
              style={{ width: '100%' }}
              value={testcaseId}
              onChange={setTestcaseId}
              options={(cases.data?.items ?? []).map((c) => ({
                value: c.id,
                label: c.title,
              }))}
            />
            <Select
              allowClear
              placeholder="UI 环境"
              style={{ width: '100%' }}
              value={envId}
              onChange={(v) => {
                setEnvId(v)
                setBrowser(undefined)
              }}
              options={(envs.data?.items ?? []).map((e) => ({
                value: e.id,
                label: e.is_default ? `${e.name}（默认）` : e.name,
              }))}
            />
            <Select
              allowClear
              placeholder="浏览器"
              style={{ width: '100%' }}
              value={browser}
              onChange={setBrowser}
              options={browserOptions.map((b) => ({ value: b, label: b }))}
            />
            {selectedCase && (
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                步骤数：{selectedCase.steps?.length ?? '—'} · 环境：{selectedEnv?.base_url || '—'}
              </Typography.Paragraph>
            )}
            <Space wrap>
              <Button
                type="primary"
                disabled={!testcaseId || !envs.data?.items?.length}
                loading={createJob.isPending}
                onClick={() => createJob.mutate()}
              >
                开始探索并生成
              </Button>
              {running && (
                <Button danger loading={cancelJob.isPending} onClick={() => cancelJob.mutate()}>
                  取消
                </Button>
              )}
            </Space>
          </Space>
        </Col>

        <Col xs={24} lg={8}>
          <Typography.Title level={5}>2. 探索进度</Typography.Title>
          {!jobId && <Typography.Text type="secondary">尚未开始</Typography.Text>}
          {job.data && (
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>
                任务 #{job.data.id}{' '}
                <Tag color={statusColor(job.data.status)}>{job.data.status}</Tag>
              </div>
              {job.data.error_message && (
                <Alert type="error" showIcon message={job.data.error_message} />
              )}
              {job.data.plan_json && (
                <Typography.Paragraph style={{ fontSize: 12 }}>
                  目标：{String((job.data.plan_json as { goal?: string }).goal || '—')}
                </Typography.Paragraph>
              )}
              <Table
                size="small"
                rowKey={(_, i) => String(i)}
                pagination={false}
                columns={traceColumns}
                dataSource={job.data.trace_json ?? []}
                scroll={{ y: 280 }}
              />
            </Space>
          )}
        </Col>

        <Col xs={24} lg={8}>
          <Typography.Title level={5}>3. 最终结果</Typography.Title>
          {job.data && (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text strong>Python 脚本</Typography.Text>
              <Input.TextArea
                value={script}
                onChange={(e) =>
                  setScriptBound({
                    id: job.data?.id ?? null,
                    code: serverScript,
                    text: e.target.value,
                  })
                }
                rows={16}
                spellCheck={false}
                style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
              />
              <Space wrap>
                <Button
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(script)
                      message.success('已复制')
                    } catch {
                      message.error('复制失败')
                    }
                  }}
                  disabled={!script.trim()}
                >
                  复制代码
                </Button>
              </Space>
              {canSave && (
                <>
                  <Input
                    placeholder="UI 用例名称"
                    value={caseName}
                    onChange={(e) => setNameBound({ id: job.data?.id ?? null, text: e.target.value })}
                  />
                  <Button type="primary" block loading={saveCase.isPending} onClick={() => saveCase.mutate()}>
                    写入 UI 用例
                  </Button>
                </>
              )}
              {job.data.saved_ui_case_id && (
                <Alert
                  type="success"
                  showIcon
                  message={
                    <span>
                      已写入用例 #{job.data.saved_ui_case_id}{' '}
                      <Link to={`/projects/${pid}/ui-cases`}>去查看</Link>
                    </span>
                  }
                />
              )}
            </Space>
          )}
        </Col>
      </Row>
    </div>
  )
}
