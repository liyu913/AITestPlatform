import {
  App,
  Alert,
  Button,
  Empty,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import ApiEnvEditModal, { type ApiEnvRecord } from './ApiEnvEditModal'
import {
  dictToRows,
  rowsToDict,
  TestDataKvList,
  type Kv,
} from './TestDataVariables'
import PrettyJson from './PrettyJson'

type AssertionRow = {
  type: 'status' | 'jsonpath' | 'max_duration_ms' | 'contains' | 'not_contains' | 'regex' | 'header' | 'json_schema'
  op?: 'eq' | 'exists' | 'contains' | 'regex'
  expr?: string
  expected?: string
}
type ExtractorRow = { name: string; expr: string }

type ApiCase = {
  id: number
  name: string
  method: string
  path: string
  query: Record<string, string>
  headers: Record<string, string>
  body_type: string
  body: string
  assertions: Array<Record<string, unknown>>
  extractors: Array<Record<string, unknown>>
  timeout_ms: number
  status: string
  pre_script?: string
  post_script?: string
  api_id?: number | null
}

type ApiEnv = ApiEnvRecord

type PageRes<T> = { items: T[]; total: number }

type DebugResult = {
  status: string
  error_message: string
  request_snapshot: Record<string, unknown> | null
  response_status: number | null
  response_headers: Record<string, unknown> | null
  response_body: string
  duration_ms: number | null
  assertion_results: Array<{ ok?: boolean; detail?: string; type?: string }>
  extract_results: Record<string, string>
}

type CaseForm = {
  name: string
  method: string
  path: string
  query_rows: Kv[]
  header_rows: Kv[]
  body_type: string
  body: string
  assertion_rows: AssertionRow[]
  extractor_rows: ExtractorRow[]
  status: string
  timeout_ms: number
  pre_script: string
  post_script: string
}

/** Serializable request payload for scenario steps / external editors */
export type WorkbenchRequestValue = {
  name: string
  method: string
  path: string
  query: Record<string, string>
  headers: Record<string, string>
  body_type: string
  body: string
  assertions: Array<Record<string, unknown>>
  extractors: Array<{ name: string; type?: string; expr: string; from?: string }>
  timeout_ms: number
}

export type ApiRequestWorkbenchProps = {
  projectId: number
  /** Bound case to edit; null shows empty state until parent ensures a case (case mode) */
  caseId?: number | null
  /** When saving a new case, bind to this definition */
  apiId?: number | null
  showRunArchive?: boolean
  onCaseSaved?: (caseId: number) => void
  className?: string
  /**
   * embedded: edit/debug without ApiCase — parent owns persistence (e.g. scenario step).
   * case: load/save ApiCase by caseId (default).
   */
  mode?: 'case' | 'embedded'
  embeddedValue?: WorkbenchRequestValue | null
  onEmbeddedChange?: (value: WorkbenchRequestValue) => void
  /** When this changes, reload form from embeddedValue (e.g. selected step id) */
  embeddedSyncKey?: string | number | null
  /** Prefill env select (scenario default env) */
  preferredEnvId?: number | null
  showSave?: boolean
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  if (ex instanceof Error && ex.message) return ex.message
  return '请求失败'
}

function assertionsFromApi(list: Array<Record<string, unknown>> | undefined): AssertionRow[] {
  if (!list?.length) return [{ type: 'status', expected: '200' }]
  return list.map((a) => ({
    type: (a.type as AssertionRow['type']) || 'status',
    op: (a.op as AssertionRow['op']) || 'eq',
    expr: a.expr != null ? String(a.expr) : '',
    expected: a.expected != null ? String(a.expected) : '',
  }))
}

function assertionsToApi(rows: AssertionRow[] | undefined) {
  return (rows || [])
    .filter((r) => r.type)
    .map((r) => {
      if (r.type === 'status') {
        const n = Number(r.expected)
        return { type: 'status', expected: Number.isFinite(n) ? n : r.expected }
      }
      if (r.type === 'max_duration_ms') {
        const n = Number(r.expected)
        return { type: 'max_duration_ms', expected: Number.isFinite(n) ? n : 0 }
      }
      const op = r.op || 'eq'
      if (op === 'exists') return { type: 'jsonpath', expr: r.expr || '', op: 'exists' }
      let expected: unknown = r.expected ?? ''
      if (typeof expected === 'string' && /^-?\d+(\.\d+)?$/.test(expected.trim())) {
        expected = Number(expected)
      } else if (expected === 'true') expected = true
      else if (expected === 'false') expected = false
      return { type: 'jsonpath', expr: r.expr || '', op: 'eq', expected }
    })
}

function extractorsFromApi(list: Array<Record<string, unknown>> | undefined): ExtractorRow[] {
  if (!list?.length) return [{ name: '', expr: '' }]
  return list.map((e) => ({ name: String(e.name || ''), expr: String(e.expr || '') }))
}

function extractorsToApi(rows: ExtractorRow[] | undefined) {
  return (rows || [])
    .filter((r) => r.name?.trim() && r.expr?.trim())
    .map((r) => ({ name: r.name.trim(), from: 'jsonpath', expr: r.expr.trim() }))
}

function emptyForm(): CaseForm {
  return {
    name: '未命名请求',
    method: 'GET',
    path: '/',
    query_rows: [{ key: '', value: '' }],
    header_rows: [{ key: '', value: '' }],
    body_type: 'none',
    body: '',
    assertion_rows: [{ type: 'status', expected: '200' }],
    extractor_rows: [{ name: '', expr: '' }],
    status: 'draft',
    timeout_ms: 15000,
    pre_script: '',
    post_script: '',
  }
}

function caseToForm(row: ApiCase): CaseForm {
  return {
    name: row.name,
    method: row.method,
    path: row.path,
    query_rows: dictToRows(row.query),
    header_rows: dictToRows(row.headers),
    body_type: row.body_type || 'none',
    body: row.body || '',
    assertion_rows: assertionsFromApi(row.assertions),
    extractor_rows: extractorsFromApi(row.extractors),
    status: row.status,
    timeout_ms: row.timeout_ms || 15000,
    pre_script: row.pre_script || '',
    post_script: row.post_script || '',
  }
}

export function workbenchValueToForm(row: WorkbenchRequestValue): CaseForm {
  return {
    name: row.name || '未命名步骤',
    method: row.method || 'GET',
    path: row.path || '/',
    query_rows: dictToRows(row.query),
    header_rows: dictToRows(row.headers),
    body_type: row.body_type || 'none',
    body: row.body || '',
    assertion_rows: assertionsFromApi(row.assertions),
    extractor_rows: extractorsFromApi(row.extractors),
    status: 'draft',
    timeout_ms: row.timeout_ms || 15000,
    pre_script: '',
    post_script: '',
  }
}

export function formToWorkbenchValue(
  values: CaseForm,
  fallback?: WorkbenchRequestValue | null,
): WorkbenchRequestValue {
  const queryRows = values.query_rows ?? (fallback ? dictToRows(fallback.query) : undefined)
  const headerRows = values.header_rows ?? (fallback ? dictToRows(fallback.headers) : undefined)
  return {
    name: (values.name || fallback?.name || '').trim(),
    method: values.method || fallback?.method || 'GET',
    path: (values.path || fallback?.path || '/').trim(),
    query: rowsToDict(queryRows),
    headers: rowsToDict(headerRows),
    body_type: values.body_type || fallback?.body_type || 'none',
    body: values.body ?? fallback?.body ?? '',
    assertions:
      values.assertion_rows != null ? assertionsToApi(values.assertion_rows) : fallback?.assertions ?? [],
    extractors:
      values.extractor_rows != null ? extractorsToApi(values.extractor_rows) : fallback?.extractors ?? [],
    timeout_ms: values.timeout_ms || fallback?.timeout_ms || 15000,
  }
}

function statusTag(s: string) {
  const color =
    s === 'passed' ? 'success' : s === 'failed' || s === 'error' ? 'error' : 'processing'
  return <Tag color={color}>{s}</Tag>
}

export default function ApiRequestWorkbench({
  projectId: pid,
  caseId = null,
  apiId,
  showRunArchive = true,
  onCaseSaved,
  className,
  mode = 'case',
  embeddedValue = null,
  onEmbeddedChange,
  embeddedSyncKey = null,
  preferredEnvId = null,
  showSave,
}: ApiRequestWorkbenchProps) {
  const { message } = App.useApp()
  const nav = useNavigate()
  const qc = useQueryClient()
  const embedded = mode === 'embedded'
  const allowSave = showSave ?? !embedded
  const [envId, setEnvId] = useState<number | undefined>()
  const [envModalOpen, setEnvModalOpen] = useState(false)
  const [debugResult, setDebugResult] = useState<DebugResult | null>(null)
  const [dirty, setDirty] = useState(false)
  const [paramTab, setParamTab] = useState('headers')
  const [respTab, setRespTab] = useState('body')
  const [form] = Form.useForm<CaseForm>()

  const caseQ = useQuery({
    queryKey: ['api-case', pid, caseId],
    queryFn: () => apiJson<ApiCase>(`/api/v1/projects/${pid}/api-cases/${caseId}`),
    enabled: !embedded && Number.isFinite(pid) && typeof caseId === 'number',
  })

  const envs = useQuery({
    queryKey: ['api-envs', pid],
    queryFn: () => apiJson<PageRes<ApiEnv>>(`/api/v1/projects/${pid}/api-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const envOptions = envs.data?.items ?? []
  const selectedEnv = useMemo(() => {
    if (envId != null) return envOptions.find((e) => e.id === envId)
    if (preferredEnvId != null) {
      const pref = envOptions.find((e) => e.id === preferredEnvId)
      if (pref) return pref
    }
    return envOptions.find((e) => e.is_default) || envOptions[0]
  }, [envId, envOptions, preferredEnvId])

  const varKeys = useMemo(() => {
    const keys = new Set<string>()
    if (selectedEnv?.variables) Object.keys(selectedEnv.variables).forEach((k) => keys.add(k))
    selectedEnv?.secret_keys?.forEach((k) => keys.add(k))
    return Array.from(keys)
  }, [selectedEnv])

  useEffect(() => {
    setDebugResult(null)
    setDirty(false)
    if (embedded) {
      if (embeddedValue) form.setFieldsValue(workbenchValueToForm(embeddedValue))
      else form.setFieldsValue(emptyForm())
      return
    }
    if (caseQ.data) {
      form.setFieldsValue(caseToForm(caseQ.data))
    } else if (caseId == null) {
      form.setFieldsValue(emptyForm())
    }
    // embedded: only remount form when step identity changes, not on every keystroke sync
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional sync key
  }, [embedded, embeddedSyncKey, caseId, caseQ.data, form])

  function buildPayload(values: CaseForm) {
    return {
      name: values.name.trim(),
      method: values.method,
      path: values.path.trim(),
      query: rowsToDict(values.query_rows),
      headers: rowsToDict(values.header_rows),
      body_type: values.body_type,
      body: values.body || '',
      assertions: assertionsToApi(values.assertion_rows),
      extractors: extractorsToApi(values.extractor_rows),
      timeout_ms: values.timeout_ms || 15000,
      status: values.status,
      pre_script: values.pre_script || '',
      post_script: values.post_script || '',
      ...(apiId != null ? { api_id: apiId } : {}),
    }
  }

  const save = useMutation({
    mutationFn: async (values: CaseForm) => {
      const payload = buildPayload(values)
      if (typeof caseId === 'number') {
        return apiJson<ApiCase>(`/api/v1/projects/${pid}/api-cases/${caseId}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson<ApiCase>(`/api/v1/projects/${pid}/api-cases/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-cases', pid] })
      qc.invalidateQueries({ queryKey: ['api-case', pid, data.id] })
      qc.invalidateQueries({ queryKey: ['api-definition-cases', pid] })
      setDirty(false)
      message.success('已保存')
      onCaseSaved?.(data.id)
    },
    onError: (e) => message.error(errText(e)),
  })

  const debug = useMutation({
    mutationFn: async (values: CaseForm) => {
      if (!selectedEnv) throw new Error('请先创建并选择接口环境')
      const payload = buildPayload(values)
      return apiJson<DebugResult>(`/api/v1/projects/${pid}/api-cases/debug`, {
        method: 'POST',
        body: JSON.stringify({
          env_id: selectedEnv.id,
          method: payload.method,
          path: payload.path,
          query: payload.query,
          headers: payload.headers,
          body_type: payload.body_type,
          body: payload.body,
          assertions: payload.assertions,
          extractors: payload.extractors,
          timeout_ms: payload.timeout_ms,
          pre_script: payload.pre_script,
          post_script: payload.post_script,
        }),
      })
    },
    onSuccess: (data) => {
      setDebugResult(data)
      setRespTab('body')
      if (data.status === 'passed') message.success('调试通过')
      else if (data.status === 'failed') message.warning('断言未通过')
      else message.error(data.error_message || '调试失败')
    },
    onError: (e) => message.error(errText(e)),
  })

  const runSync = useMutation({
    mutationFn: async () => {
      if (typeof caseId !== 'number') throw new Error('请先保存用例')
      return apiJson<{ id: number; status: string }>(`/api/v1/projects/${pid}/api-cases/${caseId}/run`, {
        method: 'POST',
        body: JSON.stringify({ env_id: selectedEnv?.id ?? null, sync: true }),
      })
    },
    onSuccess: (data) => {
      message.success(`执行完成：${data.status}`)
      nav(`/projects/${pid}/api-runs/${data.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  function insertVar(name: string) {
    const path = String(form.getFieldValue('path') || '')
    form.setFieldValue('path', `${path}{{${name}}}`)
    setDirty(true)
  }

  const noEnv = !envs.isLoading && envOptions.length === 0

  if (!embedded && caseId == null && !caseQ.data) {
    return (
      <div className={`api-workbench ${className || ''}`}>
        <Empty description="请选择接口或用例后再调试" style={{ marginTop: 48 }} />
      </div>
    )
  }

  return (
    <div className={`api-workbench ${className || ''}`}>
      {noEnv && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8, flexShrink: 0 }}
          message="还没有接口环境"
          description="请先配置 Base URL，再调试请求。"
          action={
            <Button size="small" type="primary" onClick={() => setEnvModalOpen(true)}>
              配置环境
            </Button>
          }
        />
      )}

      <div className="api-workbench-request">
        <Form
          form={form}
          layout="vertical"
          onValuesChange={() => {
            setDirty(true)
            if (embedded && onEmbeddedChange) {
              const all = form.getFieldsValue(true) as CaseForm
              onEmbeddedChange(formToWorkbenchValue(all, embeddedValue))
            }
          }}
          initialValues={emptyForm()}
        >
          <div className="api-workbench-toolbar">
            <Select
              placeholder="环境"
              style={{ minWidth: 200 }}
              value={selectedEnv?.id}
              onChange={(v) => setEnvId(v)}
              options={envOptions.map((e) => ({
                value: e.id,
                label: `${e.is_default ? '★ ' : ''}${e.name}`,
              }))}
            />
            <Form.Item name="name" noStyle rules={[{ required: true }]}>
              <Input
                placeholder={embedded ? '步骤名称' : '用例名称'}
                style={{ width: 180 }}
                maxLength={512}
              />
            </Form.Item>
            {showRunArchive && !embedded && (
              <Button
                size="small"
                onClick={() => runSync.mutate()}
                loading={runSync.isPending}
                disabled={typeof caseId !== 'number' || noEnv}
              >
                留档执行
              </Button>
            )}
          </div>

          <div className="api-workbench-urlbar">
            <Form.Item name="method" noStyle rules={[{ required: true }]}>
              <Select
                className="api-workbench-method"
                options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map((m) => ({
                  value: m,
                  label: m,
                }))}
              />
            </Form.Item>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Form.Item name="path" noStyle rules={[{ required: true }]}>
                <Input className="api-workbench-path" placeholder="/api/v1/...  可用 {{var}}" />
              </Form.Item>
            </div>
            <Button
              type="primary"
              onClick={() => form.validateFields().then((v) => debug.mutate(v))}
              loading={debug.isPending}
              disabled={noEnv}
            >
              发送
            </Button>
            {allowSave ? (
              <Button onClick={() => form.validateFields().then((v) => save.mutate(v))} loading={save.isPending}>
                保存{dirty ? ' *' : ''}
              </Button>
            ) : embedded ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                改动已写入步骤，点场景「保存」落库
              </Typography.Text>
            ) : null}
          </div>

          {varKeys.length > 0 && (
            <div className="api-workbench-vars">
              <Typography.Text type="secondary">变量：</Typography.Text>
              {varKeys.map((k) => (
                <Button key={k} size="small" type="link" onClick={() => insertVar(k)}>
                  {`{{${k}}}`}
                </Button>
              ))}
            </div>
          )}

          <Tabs
            size="small"
            activeKey={paramTab}
            onChange={setParamTab}
            destroyOnHidden={false}
            className="api-workbench-params"
            items={[
              {
                key: 'headers',
                label: 'Header',
                children: (
                  <TestDataKvList
                    name="header_rows"
                    addLabel="添加"
                    keyPlaceholder="Header"
                    valuePlaceholder="Value"
                  />
                ),
              },
              {
                key: 'query',
                label: 'Query',
                children: (
                  <TestDataKvList
                    name="query_rows"
                    addLabel="添加"
                    keyPlaceholder="参数名"
                    valuePlaceholder="值"
                  />
                ),
              },
              {
                key: 'body',
                label: 'Body',
                children: (
                  <>
                    <Form.Item name="body_type" label="类型" style={{ marginBottom: 8 }}>
                      <Select
                        style={{ width: 140 }}
                        options={[
                          { value: 'none', label: 'none' },
                          { value: 'json', label: 'JSON' },
                          { value: 'text', label: 'text' },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="body" noStyle>
                      <Input.TextArea
                        className="api-workbench-body"
                        rows={8}
                        placeholder='{"username":"{{user}}"}'
                      />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'assert',
                label: '断言',
                children: (
                  <Form.List name="assertion_rows">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map((field) => (
                          <Space key={field.key} style={{ display: 'flex', marginBottom: 8 }} wrap align="start">
                            <Form.Item {...field} name={[field.name, 'type']} style={{ width: 140 }}>
                              <Select
                                options={[
                                  { value: 'status', label: '状态码' },
                                  { value: 'jsonpath', label: 'JSONPath' },
                                  { value: 'contains', label: '包含' },
                                  { value: 'max_duration_ms', label: '耗时' },
                                ]}
                              />
                            </Form.Item>
                            <Form.Item shouldUpdate noStyle>
                              {() => {
                                const t = form.getFieldValue(['assertion_rows', field.name, 'type'])
                                if (t === 'jsonpath') {
                                  return (
                                    <>
                                      <Form.Item {...field} name={[field.name, 'expr']} style={{ width: 140 }}>
                                        <Input placeholder="$.code" />
                                      </Form.Item>
                                      <Form.Item {...field} name={[field.name, 'expected']} style={{ width: 120 }}>
                                        <Input placeholder="期望" />
                                      </Form.Item>
                                    </>
                                  )
                                }
                                return (
                                  <Form.Item {...field} name={[field.name, 'expected']} style={{ width: 160 }}>
                                    <Input placeholder={t === 'max_duration_ms' ? 'ms' : '期望'} />
                                  </Form.Item>
                                )
                              }}
                            </Form.Item>
                            <Button type="link" danger onClick={() => remove(field.name)}>
                              删
                            </Button>
                          </Space>
                        ))}
                        <Button type="dashed" onClick={() => add({ type: 'status', expected: '200' })} size="small">
                          添加断言
                        </Button>
                      </>
                    )}
                  </Form.List>
                ),
              },
              {
                key: 'extract',
                label: '提取',
                children: (
                  <Form.List name="extractor_rows">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map((field) => (
                          <Space key={field.key} style={{ display: 'flex', marginBottom: 8 }} align="baseline">
                            <Form.Item {...field} name={[field.name, 'name']} style={{ width: 140 }}>
                              <Input placeholder="变量名" />
                            </Form.Item>
                            <Form.Item {...field} name={[field.name, 'expr']} style={{ width: 240 }}>
                              <Input placeholder="$.data.token" />
                            </Form.Item>
                            <Button type="link" danger onClick={() => remove(field.name)}>
                              删
                            </Button>
                          </Space>
                        ))}
                        <Button type="dashed" onClick={() => add({ name: '', expr: '' })} size="small">
                          添加提取
                        </Button>
                      </>
                    )}
                  </Form.List>
                ),
              },
              {
                key: 'scripts',
                label: '脚本',
                children: (
                  <>
                    <Form.Item name="pre_script" label="前置脚本">
                      <Input.TextArea rows={3} />
                    </Form.Item>
                    <Form.Item name="post_script" label="后置脚本">
                      <Input.TextArea rows={3} />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'meta',
                label: '其它',
                children: (
                  <Space>
                    <Form.Item name="status" label="状态">
                      <Select
                        style={{ width: 120 }}
                        options={[
                          { value: 'draft', label: '草稿' },
                          { value: 'ready', label: '就绪' },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="timeout_ms" label="超时(ms)">
                      <InputNumber min={1000} max={120000} step={1000} />
                    </Form.Item>
                  </Space>
                ),
              },
            ]}
          />
        </Form>
      </div>

      <div className="api-workbench-response">
        <Tabs
          size="small"
          activeKey={respTab}
          onChange={setRespTab}
          items={[
            {
              key: 'body',
              label: '实时响应',
              children: !debugResult ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="请输入 URL 点击发送按钮获取响应结果"
                  style={{ marginTop: 24 }}
                />
              ) : (
                <div>
                  <Space style={{ marginBottom: 8 }} wrap>
                    {statusTag(debugResult.status)}
                    {debugResult.duration_ms != null && <span>{debugResult.duration_ms} ms</span>}
                    {debugResult.response_status != null && <span>HTTP {debugResult.response_status}</span>}
                  </Space>
                  {debugResult.error_message && (
                    <Alert type="error" message={debugResult.error_message} style={{ marginBottom: 8 }} />
                  )}
                  {(debugResult.assertion_results?.length ?? 0) > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      {debugResult.assertion_results.map((a, i) => (
                        <div key={i} style={{ color: a.ok ? '#389e0d' : '#cf1322' }}>
                          {a.ok ? '✓' : '✗'} {a.detail || a.type}
                        </div>
                      ))}
                    </div>
                  )}
                  <PrettyJson value={debugResult.response_body} />
                </div>
              ),
            },
            {
              key: 'headers',
              label: '响应头',
              children: debugResult?.response_headers ? (
                <PrettyJson value={debugResult.response_headers} />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无响应" />
              ),
            },
            {
              key: 'request',
              label: '实际请求',
              children: debugResult?.request_snapshot ? (
                <PrettyJson value={debugResult.request_snapshot} />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无请求快照" />
              ),
            },
          ]}
        />
      </div>

      <ApiEnvEditModal
        open={envModalOpen}
        onClose={() => setEnvModalOpen(false)}
        projectId={pid}
        env={selectedEnv ?? null}
        defaultAsDefault={!envOptions.some((e) => e.is_default)}
        onSaved={(saved) => setEnvId(saved.id)}
      />
    </div>
  )
}
