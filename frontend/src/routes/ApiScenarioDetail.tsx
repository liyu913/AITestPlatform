import { RobotOutlined } from '@ant-design/icons'
import {
  App,
  Alert,
  Button,
  Drawer,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import type { MenuProps } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import PageBanner from './PageBanner'
import ApiEnvEditModal, { type ApiEnvRecord } from '../components/ApiEnvEditModal'
import ApiRequestWorkbench, {
  type WorkbenchRequestValue,
} from '../components/ApiRequestWorkbench'
import { useStickToBottom } from '../hooks/useStickToBottom'

type WhenCond = {
  op?: string
  left?: string
  right?: string
} | null

type SuiteStep = {
  id: string
  case_id?: number | null
  case_name?: string
  definition_id?: number
  definition_name?: string
  name?: string
  method?: string
  path?: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body_type?: string
  body?: string
  assertions?: Array<Record<string, unknown>>
  extractors?: Array<{ name: string; type?: string; expr: string }>
  timeout_ms?: number
  on_pass?: string
  on_fail?: string
  when?: WhenCond
  rationale?: string
  extract_overrides?: Array<{ name: string; type: string; expr: string }>
  assert_overrides?: Array<Record<string, unknown>>
}

type ApiSuite = {
  id: number
  name: string
  description: string
  case_ids: number[]
  steps?: SuiteStep[]
  stop_on_fail: boolean
  env_id: number | null
}

type ApiCase = {
  id: number
  name: string
  method: string
  path: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body?: string
  body_type?: string
  extractors?: Array<{ name: string; expr?: string; from?: string; type?: string }>
  assertions?: Array<Record<string, unknown>>
  timeout_ms?: number
}

type ApiDef = {
  id: number
  name: string
  method: string
  path: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body_type?: string
  body?: string
}

type ApiEnv = ApiEnvRecord

type PageRes<T> = { items: T[]; total: number }
type ApiRun = { id: number; status: string }

const METHOD_COLOR: Record<string, string> = {
  GET: 'green',
  POST: 'blue',
  PUT: 'orange',
  PATCH: 'gold',
  DELETE: 'red',
  HEAD: 'default',
  OPTIONS: 'default',
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  if (ex instanceof Error && ex.message) return ex.message
  return '请求失败'
}

function stepHasInline(s: SuiteStep): boolean {
  return Boolean(s.method && s.path)
}

function stepDisplayName(s: SuiteStep): string {
  return s.name || s.definition_name || s.case_name || s.id
}

function stepToWorkbench(s: SuiteStep): WorkbenchRequestValue {
  return {
    name: stepDisplayName(s),
    method: s.method || 'GET',
    path: s.path || '/',
    query: s.query || {},
    headers: s.headers || {},
    body_type: s.body_type || 'none',
    body: s.body || '',
    assertions: s.assertions?.length ? s.assertions : [{ type: 'status', expected: 200 }],
    extractors: (s.extractors || []).map((e) => ({
      name: e.name,
      type: e.type || 'jsonpath',
      expr: e.expr,
    })),
    timeout_ms: s.timeout_ms || 15000,
  }
}

function applyWorkbench(s: SuiteStep, v: WorkbenchRequestValue): SuiteStep {
  return {
    ...s,
    name: v.name,
    method: v.method,
    path: v.path,
    query: v.query,
    headers: v.headers,
    body_type: v.body_type,
    body: v.body,
    assertions: v.assertions,
    extractors: v.extractors.map((e) => ({
      name: e.name,
      type: e.type || 'jsonpath',
      expr: e.expr,
    })),
    timeout_ms: v.timeout_ms,
    // clear legacy overrides once step owns payload
    extract_overrides: undefined,
    assert_overrides: undefined,
  }
}

function defToStepFields(d: ApiDef): Partial<SuiteStep> {
  return {
    definition_id: d.id,
    definition_name: d.name,
    name: d.name,
    method: d.method,
    path: d.path,
    query: d.query || {},
    headers: d.headers || {},
    body_type: d.body_type || 'none',
    body: d.body || '',
    assertions: [{ type: 'status', expected: 200 }],
    extractors: [],
    timeout_ms: 15000,
    case_id: null,
  }
}

type EditorTurn = { role: 'user' | 'assistant'; content: string }
type EditorBubble = { id: string; role: 'user' | 'assistant' | 'system'; content: string }
type EditorProposal = {
  summary: string
  warnings: string[]
  steps: SuiteStep[]
  baseFingerprint: string
  baseCount: number
  applied: boolean
}

const EDITOR_AGENT_WELCOME =
  '我可以按你的说明修改当前场景的步骤：请求、断言、提取变量、跳转，或用项目里已有接口新增一步。\n\n确认「应用到当前编排」后才会改左侧步骤，仍需点「保存」才会写入场景。'

function stepsToPayload(list: SuiteStep[]) {
  return list.map((s) => ({
    id: s.id,
    case_id: s.case_id ?? null,
    definition_id: s.definition_id,
    definition_name: s.definition_name,
    name: s.name || stepDisplayName(s),
    method: s.method,
    path: s.path,
    query: s.query || {},
    headers: s.headers || {},
    body_type: s.body_type || 'none',
    body: s.body || '',
    assertions: s.assertions || [],
    extractors: (s.extractors || []).filter((e) => e.name?.trim()),
    timeout_ms: s.timeout_ms || 15000,
    on_pass: s.on_pass || 'end',
    on_fail: s.on_fail || 'end',
    when: s.when && s.when.op ? s.when : null,
    rationale: s.rationale,
  }))
}

function stepsFingerprint(list: SuiteStep[]) {
  return JSON.stringify(stepsToPayload(list))
}

function hydrateFromCase(s: SuiteStep, c: ApiCase | undefined): SuiteStep {
  if (stepHasInline(s) || !c) return s
  return {
    ...s,
    name: s.name || c.name,
    method: c.method,
    path: c.path,
    query: c.query || {},
    headers: c.headers || {},
    body_type: c.body_type || 'none',
    body: c.body || '',
    assertions: c.assertions?.length ? c.assertions : [{ type: 'status', expected: 200 }],
    extractors: (c.extractors || [])
      .filter((e) => e.name)
      .map((e) => ({ name: e.name, type: e.type || 'jsonpath', expr: e.expr || '' })),
    timeout_ms: c.timeout_ms || 15000,
  }
}

export default function ApiScenarioDetail() {
  const { message } = App.useApp()
  const { projectId, suiteId } = useParams()
  const pid = Number(projectId)
  const sid = Number(suiteId)
  const nav = useNavigate()
  const qc = useQueryClient()

  const [steps, setSteps] = useState<SuiteStep[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [addDefId, setAddDefId] = useState<number | undefined>()
  const [agentOpen, setAgentOpen] = useState(false)
  const [agentInput, setAgentInput] = useState('')
  const [agentBusy, setAgentBusy] = useState(false)
  const [agentTurns, setAgentTurns] = useState<EditorTurn[]>([])
  const [agentBubbles, setAgentBubbles] = useState<EditorBubble[]>([
    { id: 'welcome', role: 'assistant', content: EDITOR_AGENT_WELCOME },
  ])
  const [proposal, setProposal] = useState<EditorProposal | null>(null)
  const [envModalOpen, setEnvModalOpen] = useState(false)
  const [metaForm] = Form.useForm<{
    name: string
    description: string
    stop_on_fail: boolean
    env_id?: number
  }>()
  const envIdWatch = Form.useWatch('env_id', metaForm)

  const suite = useQuery({
    queryKey: ['api-suite', pid, sid],
    queryFn: () => apiJson<ApiSuite>(`/api/v1/projects/${pid}/api-suites/${sid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(sid),
  })

  const cases = useQuery({
    queryKey: ['api-cases', pid, 'scenario-detail'],
    queryFn: () => apiJson<PageRes<ApiCase>>(`/api/v1/projects/${pid}/api-cases/?page=1&page_size=500`),
    enabled: Number.isFinite(pid),
  })

  const definitions = useQuery({
    queryKey: ['api-definitions', pid, 'scenario-detail'],
    queryFn: () =>
      apiJson<PageRes<ApiDef>>(`/api/v1/projects/${pid}/api-definitions/?page=1&page_size=500`),
    enabled: Number.isFinite(pid),
  })

  const envs = useQuery({
    queryKey: ['api-envs', pid, 'scenario-detail'],
    queryFn: () =>
      apiJson<PageRes<ApiEnv>>(`/api/v1/projects/${pid}/api-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const caseMap = useMemo(() => {
    const m = new Map<number, ApiCase>()
    for (const c of cases.data?.items ?? []) m.set(c.id, c)
    return m
  }, [cases.data])

  useEffect(() => {
    if (!suite.data) return
    const raw = suite.data.steps?.length
      ? suite.data.steps
      : (suite.data.case_ids || []).map((cid, i, arr) => ({
          id: `s${i + 1}`,
          case_id: cid,
          on_pass: i + 1 < arr.length ? `s${i + 2}` : 'end',
          on_fail: suite.data.stop_on_fail ? 'end' : i + 1 < arr.length ? `s${i + 2}` : 'end',
        }))
    setSteps(raw)
    setSelectedId((prev) => (prev && raw.some((s) => s.id === prev) ? prev : raw[0]?.id ?? null))
    metaForm.setFieldsValue({
      name: suite.data.name,
      description: suite.data.description,
      stop_on_fail: suite.data.stop_on_fail,
      env_id: suite.data.env_id ?? undefined,
    })
  }, [suite.data, metaForm])

  // Legacy steps: fill request from bound case once, without clobbering inline edits
  useEffect(() => {
    if (!caseMap.size) return
    setSteps((prev) =>
      prev.map((s) =>
        stepHasInline(s) ? s : hydrateFromCase(s, s.case_id != null ? caseMap.get(s.case_id) : undefined),
      ),
    )
  }, [caseMap])

  const selected = steps.find((s) => s.id === selectedId) ?? null
  const envItems = envs.data?.items ?? []
  const envForModal =
    (envIdWatch != null ? envItems.find((e) => e.id === envIdWatch) : null) ??
    envItems.find((e) => e.is_default) ??
    envItems[0] ??
    null
  const stepOptions = useMemo(
    () => [
      { value: 'end', label: '结束场景' },
      ...steps.map((s, idx) => ({
        value: s.id,
        label: `第 ${idx + 1} 步 · ${stepDisplayName(s)}${s.id === selectedId ? '（当前步骤）' : ''}`,
      })),
    ],
    [steps, selectedId],
  )

  const save = useMutation({
    mutationFn: async () => {
      const values = await metaForm.validateFields()
      return apiJson<ApiSuite>(`/api/v1/projects/${pid}/api-suites/${sid}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: values.name.trim(),
          description: values.description || '',
          stop_on_fail: Boolean(values.stop_on_fail),
          env_id: values.env_id ?? null,
          steps: stepsToPayload(steps),
        }),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-suite', pid, sid] })
      qc.invalidateQueries({ queryKey: ['api-suites', pid] })
      message.success('已保存场景')
    },
    onError: (e) => message.error(errText(e)),
  })

  const run = useMutation({
    mutationFn: () =>
      apiJson<ApiRun>(`/api/v1/projects/${pid}/api-suites/${sid}/run`, {
        method: 'POST',
        body: JSON.stringify({ sync: true }),
      }),
    onSuccess: (data) => {
      message.success(`场景执行完成：${data.status}`)
      nav(`/projects/${pid}/api-runs/${data.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const addStepFromDef = useMutation({
    mutationFn: async (definitionId: number) => {
      const d = definitions.data?.items.find((x) => x.id === definitionId)
      if (d) return d
      return apiJson<ApiDef>(`/api/v1/projects/${pid}/api-definitions/${definitionId}`)
    },
    onSuccess: (d) => {
      const nextId = `s${steps.length + 1}`
      const prev = steps[steps.length - 1]
      const nextSteps = steps.map((s) =>
        prev && s.id === prev.id ? { ...s, on_pass: nextId } : s,
      )
      nextSteps.push({
        id: nextId,
        ...defToStepFields(d),
        on_pass: 'end',
        on_fail: 'end',
        when: null,
      })
      setSteps(nextSteps)
      setSelectedId(nextId)
      setAddOpen(false)
      setAddDefId(undefined)
      message.success('已添加步骤（请求已拷贝进场景，可直接编辑调试）')
    },
    onError: (e) => message.error(errText(e)),
  })

  useEffect(() => {
    setAgentTurns([])
    setAgentBubbles([{ id: 'welcome', role: 'assistant', content: EDITOR_AGENT_WELCOME }])
    setProposal(null)
    setAgentInput('')
  }, [pid, sid])

  const {
    containerRef: agentMessagesRef,
    onScroll: updateAgentStickToBottom,
    pinToBottom: pinAgentToBottom,
  } = useStickToBottom([agentBubbles, proposal, agentBusy])

  function pushAgentBubble(role: EditorBubble['role'], content: string) {
    setAgentBubbles((prev) => [
      ...prev,
      { id: `b-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, role, content },
    ])
  }

  async function sendAgent() {
    const text = agentInput.trim()
    if (!text || agentBusy) return
    const baseFingerprint = stepsFingerprint(steps)
    const baseCount = steps.length
    pinAgentToBottom()
    setAgentBusy(true)
    setAgentInput('')
    pushAgentBubble('user', text)
    try {
      const revised = await apiJson<{
        summary: string
        warnings: string[]
        steps: SuiteStep[]
      }>(`/api/v1/projects/${pid}/api-suites/${sid}/agent/revise`, {
        method: 'POST',
        body: JSON.stringify({
          message: text,
          steps: stepsToPayload(steps),
          messages: agentTurns.slice(-8).map((turn) => ({
            role: turn.role,
            content: turn.content.slice(0, 4000),
          })),
        }),
      })
      const summary = revised.summary || '已修订场景步骤'
      setAgentTurns((prev) => [...prev, { role: 'user', content: text }, { role: 'assistant', content: summary }])
      setProposal({
        summary,
        warnings: revised.warnings || [],
        steps: revised.steps || [],
        baseFingerprint,
        baseCount,
        applied: false,
      })
      pushAgentBubble(
        'assistant',
        `${summary}\n\n步骤 ${baseCount} → ${revised.steps?.length ?? 0}。确认无误后点「应用到当前编排」，再保存场景。`,
      )
    } catch (ex) {
      pushAgentBubble('system', errText(ex))
      message.error(errText(ex))
    } finally {
      setAgentBusy(false)
    }
  }

  function applyProposal() {
    if (!proposal || proposal.applied) return
    if (stepsFingerprint(steps) !== proposal.baseFingerprint) {
      message.warning('编排在这次生成之后又被改过，请重新发送说明后再应用')
      return
    }
    const next = proposal.steps
    setSteps(next)
    setSelectedId((prev) => (prev && next.some((s) => s.id === prev) ? prev : next[0]?.id ?? null))
    setProposal({ ...proposal, applied: true })
    message.success('已应用到当前编排，记得保存')
  }

  function updateSelected(patch: Partial<SuiteStep>) {
    if (!selectedId) return
    setSteps((prev) => prev.map((s) => (s.id === selectedId ? { ...s, ...patch } : s)))
  }

  function rebindDefinition(definitionId: number) {
    const d = definitions.data?.items.find((x) => x.id === definitionId)
    if (!d) {
      message.error('接口未加载')
      return
    }
    updateSelected({ ...defToStepFields(d) })
    message.success('已用该接口模板覆盖本步请求')
  }

  function removeStep(id: string) {
    const next = steps.filter((s) => s.id !== id)
    const remapped = next.map((s, i) => ({ ...s, id: `s${i + 1}` }))
    const linear = remapped.map((s, i) => ({
      ...s,
      on_pass: i + 1 < remapped.length ? remapped[i + 1].id : 'end',
      on_fail: s.on_fail === 'end' || !remapped.some((x) => x.id === s.on_fail) ? 'end' : s.on_fail,
    }))
    setSteps(linear)
    setSelectedId(linear[0]?.id ?? null)
  }

  function moveStep(id: string, dir: -1 | 1) {
    const idx = steps.findIndex((s) => s.id === id)
    const j = idx + dir
    if (idx < 0 || j < 0 || j >= steps.length) return
    const next = [...steps]
    ;[next[idx], next[j]] = [next[j], next[idx]]
    const remapped = next.map((s, i) => ({ ...s, id: `s${i + 1}` }))
    const linear = remapped.map((s, i) => ({
      ...s,
      on_pass: i + 1 < remapped.length ? remapped[i + 1].id : 'end',
      on_fail:
        s.on_fail === 'end' || !remapped.some((x) => x.id === s.on_fail) ? s.on_fail || 'end' : s.on_fail,
    }))
    setSteps(linear)
    setSelectedId(linear[j].id)
  }

  const addMenu: MenuProps = {
    items: [
      { key: 'http', label: 'HTTP 接口', onClick: () => setAddOpen(true) },
      { key: 'hint-flow', label: '条件 / 循环（后续）', disabled: true },
    ],
  }

  if (!Number.isFinite(pid) || !Number.isFinite(sid)) {
    return <p className="error">无效场景</p>
  }

  const whenEnabled = Boolean(selected?.when?.op)

  return (
    <div className="api-scenario-detail">
      <PageBanner title={suite.data?.name ? `场景：${suite.data.name}` : '场景详情'} />
      <Space className="api-scenario-detail-toolbar" wrap>
        <Button onClick={() => nav(`/projects/${pid}/api-suites`)}>返回列表</Button>
        <Button type="primary" loading={save.isPending} onClick={() => save.mutate()}>
          保存
        </Button>
        <Button loading={run.isPending} onClick={() => run.mutate()}>
          执行
        </Button>
        <Dropdown menu={addMenu}>
          <Button type="dashed">+ 添加步骤</Button>
        </Dropdown>
        <Button icon={<RobotOutlined />} onClick={() => setAgentOpen(true)}>
          编排 Agent
        </Button>
      </Space>

      <Form className="api-scenario-detail-meta" form={metaForm} layout="inline">
        <Form.Item name="name" label="名称" rules={[{ required: true }]}>
          <Input style={{ width: 220 }} />
        </Form.Item>
        <Form.Item label="环境">
          <Space.Compact>
            <Form.Item name="env_id" noStyle>
              <Select
                allowClear
                style={{ width: 180 }}
                options={(envs.data?.items ?? []).map((e) => ({
                  value: e.id,
                  label: e.is_default ? `${e.name}（默认）` : e.name,
                }))}
              />
            </Form.Item>
            <Button
              onClick={() => {
                if (envItems.length > 0 && !envForModal) {
                  message.warning(envs.isLoading ? '环境加载中，请稍后再试' : '未找到该环境，请重新选择')
                  return
                }
                setEnvModalOpen(true)
              }}
            >
              {envForModal ? '配置' : '新建'}
            </Button>
          </Space.Compact>
        </Form.Item>
        <Form.Item name="stop_on_fail" label="失败即停" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>

      <div className="api-scenario-detail-body">
        <div className="api-scenario-detail-steps">
          <div className="api-scenario-detail-steps-head">测试步骤（{steps.length}）</div>
          <div className="api-scenario-detail-steps-list">
          {steps.map((s, idx) => {
            const method = s.method || '?'
            const active = s.id === selectedId
            return (
              <div
                key={s.id}
                onClick={() => setSelectedId(s.id)}
                style={{
                  padding: '10px 12px',
                  cursor: 'pointer',
                  background: active ? 'rgba(64,150,255,0.15)' : undefined,
                  borderBottom: '1px solid var(--ant-color-border, #e6e6e6)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                  <Typography.Text strong>
                    {idx + 1}. {s.id}
                  </Typography.Text>
                  <Space size={0} onClick={(e) => e.stopPropagation()}>
                    <Button type="text" size="small" disabled={idx === 0} onClick={() => moveStep(s.id, -1)}>
                      ↑
                    </Button>
                    <Button
                      type="text"
                      size="small"
                      disabled={idx === steps.length - 1}
                      onClick={() => moveStep(s.id, 1)}
                    >
                      ↓
                    </Button>
                    <Popconfirm title="删除该步骤？" onConfirm={() => removeStep(s.id)}>
                      <Button type="link" size="small" danger>
                        删
                      </Button>
                    </Popconfirm>
                  </Space>
                </div>
                <div style={{ marginTop: 4 }}>
                  <Tag color={METHOD_COLOR[method] || 'default'}>{method}</Tag>
                  {stepDisplayName(s)}
                </div>
                <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                  {s.path || ''}
                </Typography.Text>
              </div>
            )
          })}
          {!steps.length && (
            <Typography.Text type="secondary" style={{ display: 'block', padding: 16 }}>
              暂无步骤，点击「+ 添加步骤」
            </Typography.Text>
          )}
          </div>
        </div>

        <div className="api-scenario-detail-editor">
          {!selected ? (
            <Typography.Text type="secondary">选择左侧步骤进行编辑</Typography.Text>
          ) : (
            <>
              <Space style={{ marginBottom: 12 }} wrap>
                <Typography.Text strong>步骤 {selected.id}</Typography.Text>
                {selected.method ? (
                  <Tag color={METHOD_COLOR[selected.method] || 'default'}>{selected.method}</Tag>
                ) : null}
                {selected.definition_id ? (
                  <Link to={`/projects/${pid}/api-definitions?apiId=${selected.definition_id}`}>打开接口</Link>
                ) : null}
              </Space>

              <Form layout="vertical" style={{ marginBottom: 8 }}>
                <Form.Item label="从接口模板覆盖本步（可选）" style={{ marginBottom: 8 }}>
                  <Select
                    showSearch
                    optionFilterProp="label"
                    value={selected.definition_id}
                    placeholder="选择接口以重置请求模板"
                    options={(definitions.data?.items ?? []).map((d) => ({
                      value: d.id,
                      label: `${d.method} ${d.name}  ${d.path}`,
                    }))}
                    onChange={(definitionId) => rebindDefinition(definitionId)}
                  />
                </Form.Item>
              </Form>

              {!stepHasInline(selected) ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="旧步骤缺少内嵌请求"
                  description="请选择上方接口模板覆盖，或等待用例水合完成后再保存。"
                />
              ) : (
                <div style={{ marginBottom: 16 }}>
                  <ApiRequestWorkbench
                    projectId={pid}
                    mode="embedded"
                    embeddedSyncKey={selected.id}
                    embeddedValue={stepToWorkbench(selected)}
                    onEmbeddedChange={(v) => updateSelected(applyWorkbench(selected, v))}
                    preferredEnvId={envIdWatch ?? suite.data?.env_id ?? null}
                    showRunArchive={false}
                  />
                </div>
              )}

              <Typography.Text strong>控制流</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>
                通过或失败后要去哪一步；选「结束场景」就不再继续。
              </Typography.Paragraph>
              <Form layout="vertical" style={{ maxWidth: 640, marginTop: 8 }}>
                <Form.Item label="执行前判断（不满足就跳过，并按「通过后」继续）">
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Switch
                      checked={whenEnabled}
                      checkedChildren="启用"
                      unCheckedChildren="关闭"
                      onChange={(on) =>
                        updateSelected({
                          when: on ? { op: 'eq', left: '{{status}}', right: '200' } : null,
                        })
                      }
                    />
                    {whenEnabled ? (
                      <Space wrap>
                        <Select
                          style={{ width: 120 }}
                          value={selected.when?.op || 'eq'}
                          options={[
                            { value: 'eq', label: '等于' },
                            { value: 'ne', label: '不等于' },
                            { value: 'contains', label: '包含' },
                            { value: 'exists', label: '存在' },
                          ]}
                          onChange={(op) => updateSelected({ when: { ...(selected.when || {}), op } })}
                        />
                        <Input
                          style={{ width: 160 }}
                          placeholder="左值，如 {{token}}"
                          value={selected.when?.left || ''}
                          onChange={(e) =>
                            updateSelected({
                              when: { ...(selected.when || { op: 'eq' }), left: e.target.value },
                            })
                          }
                        />
                        {selected.when?.op !== 'exists' ? (
                          <Input
                            style={{ width: 140 }}
                            placeholder="右值"
                            value={selected.when?.right || ''}
                            onChange={(e) =>
                              updateSelected({
                                when: { ...(selected.when || { op: 'eq' }), right: e.target.value },
                              })
                            }
                          />
                        ) : null}
                      </Space>
                    ) : null}
                  </Space>
                </Form.Item>
                <Space wrap>
                  <Form.Item label="这一步通过后" style={{ minWidth: 280 }}>
                    <Select
                      style={{ width: 280 }}
                      value={selected.on_pass || 'end'}
                      options={stepOptions}
                      onChange={(v) => updateSelected({ on_pass: v })}
                    />
                  </Form.Item>
                  <Form.Item label="这一步失败后" style={{ minWidth: 280 }}>
                    <Select
                      style={{ width: 280 }}
                      value={selected.on_fail || 'end'}
                      options={stepOptions}
                      onChange={(v) => updateSelected({ on_fail: v })}
                    />
                  </Form.Item>
                </Space>
              </Form>
            </>
          )}
        </div>
      </div>

      <Drawer
        title="编排 Agent"
        open={agentOpen}
        onClose={() => setAgentOpen(false)}
        width={440}
        styles={{ body: { display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 16 } }}
      >
        <div className="scenario-editor-agent">
          <div
            className="scenario-editor-agent-messages"
            ref={agentMessagesRef}
            onScroll={updateAgentStickToBottom}
          >
            {agentBubbles.map((bubble) => (
              <div
                key={bubble.id}
                className={`api-scenario-agent-bubble api-scenario-agent-bubble--${bubble.role}`}
              >
                <div className="api-scenario-agent-bubble-role">
                  {bubble.role === 'user' ? '你' : bubble.role === 'system' ? '系统' : 'Agent'}
                </div>
                <div className="api-scenario-agent-bubble-body">{bubble.content}</div>
              </div>
            ))}
          </div>

          {proposal ? (
            <div className="scenario-editor-agent-preview">
              <Typography.Text strong>
                步骤 {proposal.baseCount} → {proposal.steps.length}
              </Typography.Text>
              <Typography.Paragraph type="secondary" style={{ margin: '4px 0 8px' }}>
                {proposal.summary}
              </Typography.Paragraph>
              {proposal.warnings.length ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 8 }}
                  message={proposal.warnings.join('；')}
                />
              ) : null}
              <div className="scenario-editor-agent-preview-steps">
                {proposal.steps.map((s, idx) => (
                  <div key={s.id}>
                    {idx + 1}. <Tag color={METHOD_COLOR[s.method || ''] || 'default'}>{s.method || '?'}</Tag>
                    {stepDisplayName(s)} <Typography.Text type="secondary">{s.path || ''}</Typography.Text>
                  </div>
                ))}
              </div>
              <Button
                type="primary"
                block
                style={{ marginTop: 8 }}
                disabled={proposal.applied}
                onClick={applyProposal}
              >
                {proposal.applied ? '已应用到当前编排' : '应用到当前编排'}
              </Button>
              <Typography.Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                应用后仍需点击页面上的「保存」才会写入场景。
              </Typography.Text>
            </div>
          ) : null}

          <div className="scenario-editor-agent-composer">
            <Input.TextArea
              value={agentInput}
              onChange={(e) => setAgentInput(e.target.value)}
              placeholder="例如：给第二步加上 Authorization，或再接一个查询订单接口"
              autoSize={{ minRows: 2, maxRows: 5 }}
              disabled={agentBusy}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault()
                  void sendAgent()
                }
              }}
            />
            <Button type="primary" loading={agentBusy} disabled={!agentInput.trim()} onClick={() => void sendAgent()}>
              发送
            </Button>
          </div>
        </div>
      </Drawer>

      <Modal
        title="添加 HTTP 接口步骤"
        open={addOpen}
        confirmLoading={addStepFromDef.isPending}
        onCancel={() => setAddOpen(false)}
        onOk={() => {
          if (addDefId == null) {
            message.warning('请选择接口')
            return
          }
          addStepFromDef.mutate(addDefId)
        }}
        destroyOnHidden
      >
        <Select
          style={{ width: '100%' }}
          placeholder="从接口管理选择（请求会拷贝进步骤）"
          value={addDefId}
          onChange={setAddDefId}
          options={(definitions.data?.items ?? []).map((d) => ({
            value: d.id,
            label: `${d.method} ${d.name}  ${d.path}`,
          }))}
          showSearch
          optionFilterProp="label"
        />
      </Modal>

      <ApiEnvEditModal
        open={envModalOpen}
        onClose={() => setEnvModalOpen(false)}
        projectId={pid}
        env={envForModal}
        defaultAsDefault={!envItems.some((e) => e.is_default)}
        onSaved={(saved) => {
          if (envIdWatch == null) {
            metaForm.setFieldValue('env_id', saved.id)
          }
        }}
      />
    </div>
  )
}
