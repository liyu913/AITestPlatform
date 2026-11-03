import {
  Alert,
  App,
  Button,
  Form,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import ListViewToggle, { useListViewMode } from '../components/ListViewToggle'
import {
  dictToRows,
  rowsToDict,
  TestDataKvList,
  type Kv,
} from '../components/TestDataVariables'
import { useTableScrollY } from '../hooks/useTableScrollY'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

const DEFAULT_UI_SCRIPT = `def run(page):
    page.goto("/")
    expect(page.locator("body")).to_be_visible()
`

type UiCase = {
  id: number
  name: string
  description: string
  script: string
  status: string
  auth_mode?: string | null
  variables?: Record<string, string>
  created_at: string
}

type UiEnv = {
  id: number
  name: string
  is_default: boolean
  base_url: string
  browsers?: string[]
  browser?: string
  variables?: Record<string, string>
  secret_keys?: string[]
  inject_headers?: Record<string, string>
  inject_local_storage?: Record<string, string>
  token_inject?: string
  token_storage_key?: string | null
}
type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }
type UiRun = { id: number; status: string; summary_json?: Record<string, unknown> | null }
type Recording = {
  id: string
  status: 'starting' | 'recording' | 'stopped' | 'failed'
  base_url: string
  step_count: number
  script: string
  suggested_name: string
  error_message: string
}

type CaseForm = {
  name: string
  description: string
  status: string
  /** form value: follow | none */
  auth_mode: 'follow' | 'none'
  variable_rows: Kv[]
}

function caseAuthLabel(mode?: string | null): string {
  return mode === 'none' ? '不注入' : '跟随环境注入'
}

function envInjectSummary(env?: UiEnv | null): string {
  if (!env) return '未选环境'
  const headers = Object.keys(env.inject_headers || {})
  const ls = Object.keys(env.inject_local_storage || {})
  if (headers.length || ls.length) {
    const parts: string[] = []
    if (headers.length) parts.push(`请求头 ${headers.join('、')}`)
    if (ls.length) parts.push(`localStorage ${ls.join('、')}`)
    return parts.join('；')
  }
  if (env.token_inject === 'header') return '请求头 Authorization: Bearer（旧配置）'
  if (env.token_inject === 'local_storage') {
    return `localStorage ${env.token_storage_key || 'token'}（旧配置）`
  }
  return '未配置注入'
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function UiCaseList() {
  const { containerRef, scrollY } = useTableScrollY()
  const [viewMode, setViewMode] = useListViewMode('list-view:ui-cases')

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const manualCaseIdRaw = searchParams.get('manual_case_id')
  const manualCaseId = manualCaseIdRaw && /^\d+$/.test(manualCaseIdRaw) ? Number(manualCaseIdRaw) : undefined
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<UiCase | null>(null)
  const [script, setScript] = useState(DEFAULT_UI_SCRIPT)
  const [runEnvId, setRunEnvId] = useState<number | undefined>()
  const [runBrowser, setRunBrowser] = useState<string | undefined>()
  const [runTargetId, setRunTargetId] = useState<number | null>(null)
  const [headed, setHeaded] = useState(false)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<CaseForm>()
  const [liveOpen, setLiveOpen] = useState(false)
  const [live, setLive] = useState<Recording | null>(null)
  const [liveName, setLiveName] = useState('')
  const [liveScript, setLiveScript] = useState('')
  const discardRec = useRef(false)

  useEffect(() => {
    setPage(1)
  }, [manualCaseId])

  const list = useQuery({
    queryKey: ['ui-cases', pid, search, page, pageSize, manualCaseId],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      if (manualCaseId != null) qs.set('manual_case_id', String(manualCaseId))
      return apiJson<PageRes<UiCase>>(`/api/v1/projects/${pid}/ui-cases/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    retry: 1,
  })

  const envs = useQuery({
    queryKey: ['ui-envs', pid],
    queryFn: () => apiJson<PageRes<UiEnv>>(`/api/v1/projects/${pid}/ui-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
    retry: 1,
  })

  const defaultEnv = (envs.data?.items ?? []).find((e) => e.is_default) ?? envs.data?.items?.[0]
  const selectedEnv =
    (envs.data?.items ?? []).find((e) => e.id === runEnvId) ?? defaultEnv
  const browserOptions = selectedEnv?.browsers?.length
    ? selectedEnv.browsers
    : [selectedEnv?.browser || 'chromium']

  const save = useMutation({
    mutationFn: async (values: CaseForm) => {
      const payload = {
        name: values.name.trim(),
        description: values.description || '',
        script,
        status: values.status,
        auth_mode: values.auth_mode === 'none' ? 'none' : null,
        variables: rowsToDict(values.variable_rows),
      }
      if (editing) {
        return apiJson<UiCase>(`/api/v1/projects/${pid}/ui-cases/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson<UiCase>(`/api/v1/projects/${pid}/ui-cases/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-cases', pid] })
      setOpen(false)
      setEditing(null)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e) || (e instanceof Error ? e.message : '失败')),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/ui-cases/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-cases', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const run = useMutation({
    mutationFn: (caseId: number) =>
      apiJson<UiRun>(`/api/v1/projects/${pid}/ui-cases/${caseId}/run`, {
        method: 'POST',
        body: JSON.stringify({
          env_id: runEnvId ?? null,
          browser: runBrowser || null,
          headless: !headed,
        }),
      }),
    onSuccess: (data) => {
      setRunTargetId(null)
      message.success('已开始执行')
      nav(`/projects/${pid}/ui-runs/${data.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  function openRun(row: UiCase) {
    setHeaded(false)
    setRunTargetId(row.id)
  }

  const runTargetCase = (list.data?.items ?? []).find((c) => c.id === runTargetId)

  const startLive = useMutation({
    mutationFn: () =>
      apiJson<Recording>(`/api/v1/projects/${pid}/ui-recordings/`, {
        method: 'POST',
        body: JSON.stringify({ env_id: runEnvId ?? null }),
      }),
    onSuccess: (data) => {
      if (discardRec.current) {
        void apiJson(`/api/v1/projects/${pid}/ui-recordings/${data.id}/stop`, { method: 'POST' }).catch(
          () => undefined,
        )
        return
      }
      setLive(data)
    },
    onError: (e) => {
      setLive({
        id: '',
        status: 'failed',
        base_url: '',
        step_count: 0,
        script: '',
        suggested_name: '',
        error_message: errText(e),
      })
    },
  })

  const livePoll = useQuery({
    queryKey: ['ui-recording', pid, live?.id],
    queryFn: () => apiJson<Recording>(`/api/v1/projects/${pid}/ui-recordings/${live?.id}`),
    enabled: liveOpen && Boolean(live?.id) && live?.status === 'recording',
    refetchInterval: 1000,
  })

  useEffect(() => {
    const data = livePoll.data
    if (!data || data.status === 'recording' || data.status === 'starting') return
    setLive(data)
    if (data.status === 'stopped') {
      setLiveName((name) => name || data.suggested_name || '')
      setLiveScript((scriptText) => scriptText || data.script || '')
    }
  }, [livePoll.data])

  const stopLive = useMutation({
    mutationFn: (id: string) =>
      apiJson<Recording>(`/api/v1/projects/${pid}/ui-recordings/${id}/stop`, { method: 'POST' }),
    onSuccess: (data) => {
      setLive(data)
      if (data.status === 'stopped') {
        setLiveName(data.suggested_name || '')
        setLiveScript(data.script || '')
      } else if (data.error_message) {
        message.error(data.error_message)
      }
    },
    onError: (e) => message.error(errText(e)),
  })

  const saveLive = useMutation({
    mutationFn: () =>
      apiJson<UiCase>(`/api/v1/projects/${pid}/ui-cases/`, {
        method: 'POST',
        body: JSON.stringify({
          name: liveName.trim(),
          description: '由录制生成',
          script: liveScript,
          status: 'draft',
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-cases', pid] })
      setLiveOpen(false)
      message.success('已生成用例')
    },
    onError: (e) => message.error(errText(e)),
  })

  function openLiveRecord() {
    discardRec.current = false
    setLive(null)
    setLiveName('')
    setLiveScript('')
    setLiveOpen(true)
    startLive.mutate()
  }

  function closeLive() {
    discardRec.current = true
    const id = live?.id
    const status = live?.status
    setLiveOpen(false)
    if (id && (status === 'recording' || status === 'starting')) {
      void apiJson(`/api/v1/projects/${pid}/ui-recordings/${id}/stop`, { method: 'POST' }).catch(
        () => undefined,
      )
    }
  }

  function openCreate() {
    setEditing(null)
    setScript(DEFAULT_UI_SCRIPT)
    form.setFieldsValue({
      name: '',
      description: '',
      status: 'draft',
      auth_mode: 'follow',
      variable_rows: [{ key: '', value: '' }],
    })
    setOpen(true)
  }

  function openEdit(row: UiCase) {
    setEditing(row)
    setScript(row.script?.trim() ? row.script : DEFAULT_UI_SCRIPT)
    form.setFieldsValue({
      name: row.name,
      description: row.description,
      status: row.status,
      auth_mode: row.auth_mode === 'none' ? 'none' : 'follow',
      variable_rows: dictToRows(row.variables),
    })
    setOpen(true)
  }

  function caseActions(row: UiCase) {
    return (
      <Space wrap size="small">
        <Button type="link" size="small" onClick={() => openEdit(row)}>
          编辑
        </Button>
        <Button
          type="link"
          size="small"
          disabled={!envs.data?.items?.length}
          onClick={() => openRun(row)}
        >
          执行
        </Button>
        <Popconfirm title="删除该用例？" onConfirm={() => remove.mutate(row.id)}>
          <Button type="link" size="small" danger>
            删除
          </Button>
        </Popconfirm>
      </Space>
    )
  }

  const columns: ColumnsType<UiCase> = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    {
      title: '环境注入',
      width: 130,
      render: (_, row) => caseAuthLabel(row.auth_mode),
    },
    {
      title: '脚本',
      width: 90,
      render: (_, row) => (row.script?.trim() ? '已编写' : '空'),
    },
    { title: '状态', dataIndex: 'status', width: 90 },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => formatBeijing(v),
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => caseActions(row),
    },
  ]

  const noEnvs = !envs.isLoading && !envs.isError && (envs.data?.items?.length ?? 0) === 0
  const items = list.data?.items ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
      <PageBanner title="UI 用例" />
      {(list.isError || envs.isError) && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="加载失败"
          description={
            errText(list.error || envs.error) +
            '（若表不存在，请执行：cd backend && uv run alembic upgrade head）'
          }
          action={
            <Button
              size="small"
              onClick={() => {
                void list.refetch()
                void envs.refetch()
              }}
            >
              重试
            </Button>
          }
        />
      )}

      {noEnvs && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="还没有 UI 环境"
          description={
            <span>
              请先配置被测站点地址，再创建用例与执行。
              <Link to={`/projects/${pid}/ui-envs`}>去创建环境</Link>
            </span>
          }
        />
      )}

      {manualCaseId != null && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`已按功能用例 #${manualCaseId} 过滤`}
          action={
            <Button
              size="small"
              onClick={() => {
                const next = new URLSearchParams(searchParams)
                next.delete('manual_case_id')
                setSearchParams(next, { replace: true })
                setPage(1)
              }}
            >
              清除过滤
            </Button>
          }
        />
      )}

      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }} wrap>
        <Space wrap>
          <Button type="primary" onClick={openCreate} disabled={noEnvs}>
            新建用例
          </Button>
          <Button disabled={noEnvs} loading={startLive.isPending} onClick={openLiveRecord}>
            录制
          </Button>
          <Input.Search
            allowClear
            placeholder="搜索用例名称"
            style={{ width: 220 }}
            onSearch={(v) => {
              setSearch(v)
              setPage(1)
            }}
          />
          <Select
            allowClear
            placeholder="执行环境（默认取默认环境）"
            style={{ minWidth: 240 }}
            value={runEnvId}
            onChange={(v) => {
              setRunEnvId(v)
              setRunBrowser(undefined)
            }}
            options={(envs.data?.items ?? []).map((e) => ({
              value: e.id,
              label: e.is_default ? `${e.name}（默认）` : e.name,
            }))}
          />
          <Select
            allowClear
            placeholder="浏览器"
            style={{ minWidth: 140 }}
            value={runBrowser}
            onChange={setRunBrowser}
            options={browserOptions.map((b) => ({ value: b, label: b }))}
          />
          <Button onClick={() => void list.refetch()}>刷新</Button>
        </Space>
        <ListViewToggle value={viewMode} onChange={setViewMode} />
      </Space>
      </div>

      <div className={`list-page-body is-${viewMode}`}>
        {viewMode === 'table' ? (
          <div className="list-page-table" ref={containerRef}>
            <Table<UiCase>
              rowKey="id"
              loading={list.isLoading}
              columns={columns}
              dataSource={items}
              locale={{ emptyText: '暂无用例' }}
              pagination={{
                current: page,
                pageSize,
                total: list.data?.total ?? 0,
                showSizeChanger: true,
                showTotal: (t) => `共 ${t} 条`,
                onChange: (p, s) => {
                  setPage(p)
                  setPageSize(s)
                },
              }}
              scroll={{ y: scrollY }}
            />
          </div>
        ) : (
          <Spin spinning={list.isLoading}>
            {items.length === 0 && !list.isLoading ? (
              <p className="muted">暂无用例</p>
            ) : (
              <div className="list-card-grid">
                {items.map((row) => (
                  <article key={row.id} className="list-card">
                    <h3 className="list-card-title" title={row.name}>
                      {row.name}
                    </h3>
                    <div className="list-card-meta">
                      <span>{caseAuthLabel(row.auth_mode)}</span>
                      <span>{row.script?.trim() ? '脚本已编写' : '脚本空'}</span>
                      <span>{row.status}</span>
                      <span>{formatBeijing(row.created_at)}</span>
                    </div>
                    <div className="list-card-actions">{caseActions(row)}</div>
                  </article>
                ))}
              </div>
            )}
            <div className="list-card-pagination">
              <Pagination
                current={page}
                pageSize={pageSize}
                total={list.data?.total ?? 0}
                showSizeChanger
                showTotal={(t) => `共 ${t} 条`}
                onChange={(p, s) => {
                  setPage(p)
                  setPageSize(s)
                }}
              />
            </div>
          </Spin>
        )}
      </div>

      <Modal
        title={runTargetCase ? `执行：${runTargetCase.name}` : '执行'}
        open={runTargetId != null}
        onCancel={() => {
          if (!run.isPending) setRunTargetId(null)
        }}
        destroyOnHidden
        width={560}
        okText="开始执行"
        confirmLoading={run.isPending}
        onOk={() => {
          if (runTargetId != null) run.mutate(runTargetId)
        }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Typography.Text type="secondary">当前环境</Typography.Text>
            <div>{selectedEnv ? `${selectedEnv.name} · ${selectedEnv.base_url}` : '默认环境'}</div>
          </div>
          <div>
            <Typography.Text type="secondary">环境注入</Typography.Text>
            <div>{caseAuthLabel(runTargetCase?.auth_mode)}</div>
            {runTargetCase?.auth_mode === 'none' ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                将跳过环境请求头 / localStorage 注入（适合登录用例）。
              </Typography.Text>
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                环境注入：{envInjectSummary(selectedEnv)}
              </Typography.Text>
            )}
          </div>
          {Object.keys(runTargetCase?.variables || {}).length > 0 ? (
            <div>
              <Typography.Text type="secondary">用例变量</Typography.Text>
              <div>{Object.keys(runTargetCase?.variables || {}).join('、')}</div>
            </div>
          ) : null}
          <Space>
            <span>浏览器模式</span>
            <Switch checked={headed} onChange={setHeaded} checkedChildren="有头" unCheckedChildren="无头" />
          </Space>
        </Space>
      </Modal>

      <Modal
        title={editing ? '编辑 UI 用例' : '新建 UI 用例'}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={860}
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input maxLength={512} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="auth_mode"
            label="环境注入"
            initialValue="follow"
            extra="请求头 / localStorage 模板在 UI 环境配置；登录用例请选「不注入」。"
          >
            <Select
              options={[
                { value: 'follow', label: '跟随环境注入' },
                { value: 'none', label: '不注入' },
              ]}
              style={{ width: 240 }}
            />
          </Form.Item>
          <Form.Item
            label="用例变量"
            extra="同名键覆盖环境变量与密钥，供脚本 env.key 与环境注入模板 {{var}} 使用。"
          >
            <TestDataKvList name="variable_rows" addLabel="添加变量" keyPlaceholder="键" />
          </Form.Item>
          <Form.Item label="脚本" required>
            <Input.TextArea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              rows={16}
              spellCheck={false}
              style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
              placeholder={DEFAULT_UI_SCRIPT}
            />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { value: 'draft', label: '草稿' },
                { value: 'ready', label: '就绪' },
              ]}
              style={{ width: 120 }}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>

      <Modal
        title="录制并生成用例"
        open={liveOpen}
        onCancel={closeLive}
        destroyOnHidden
        width={720}
        footer={
          live?.status === 'stopped'
            ? [
                <Button key="cancel" onClick={closeLive}>
                  取消
                </Button>,
                <Button
                  key="save"
                  type="primary"
                  loading={saveLive.isPending}
                  disabled={!liveName.trim() || !liveScript.trim()}
                  onClick={() => saveLive.mutate()}
                >
                  生成用例
                </Button>,
              ]
            : live?.status === 'failed'
              ? [
                  <Button key="close" type="primary" onClick={closeLive}>
                    关闭
                  </Button>,
                ]
              : [
                  <Button
                    key="stop"
                    type="primary"
                    danger
                    loading={stopLive.isPending}
                    disabled={!live?.id || live.status !== 'recording'}
                    onClick={() => live?.id && stopLive.mutate(live.id)}
                  >
                    停止
                  </Button>,
                ]
        }
      >
        {startLive.isPending && !live && <Typography.Text>正在打开浏览器…</Typography.Text>}
        {live?.status === 'failed' && (
          <Alert type="error" showIcon message={live.error_message || '录制失败'} />
        )}
        {live?.status === 'recording' && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Paragraph style={{ marginBottom: 0 }}>
              已在运行后端的这台电脑上打开浏览器。请在页面里操作，右下角或这里都可以点「停止」。
            </Typography.Paragraph>
            <Typography.Text type="secondary">
              已记录 {livePoll.data?.step_count ?? live.step_count} 步
              {live.base_url ? ` · ${live.base_url}` : ''}
            </Typography.Text>
          </Space>
        )}
        {live?.status === 'stopped' && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {!liveScript.trim() && (
              <Alert type="warning" showIcon message="没有录到可执行步骤，请重新录制" />
            )}
            <Input
              placeholder="用例名称"
              value={liveName}
              maxLength={512}
              onChange={(e) => setLiveName(e.target.value)}
            />
            <Input.TextArea
              value={liveScript}
              onChange={(e) => setLiveScript(e.target.value)}
              rows={14}
              spellCheck={false}
              style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
            />
          </Space>
        )}
      </Modal>
    </div>
  )
}
