import {
  Alert,
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import {
  dictToRows,
  rowsToDict,
  TestDataKvList,
  type Kv,
} from '../components/TestDataVariables'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type UiEnv = {
  id: number
  project_id: number
  name: string
  base_url: string
  browser: string
  browsers: string[]
  viewport: { width: number; height: number }
  variables: Record<string, string>
  secret_keys: string[]
  token_inject: string
  token_storage_key: string | null
  inject_headers?: Record<string, string>
  inject_local_storage?: Record<string, string>
  queue_name: string
  max_concurrency: number
  default_timeout_ms: number
  step_retries: number
  is_default: boolean
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

type EnvForm = {
  name: string
  base_url: string
  browsers: string[]
  variable_rows: Kv[]
  secret_rows: Kv[]
  queue_name: string
  max_concurrency: number
  default_timeout_ms: number
  step_retries: number
  width: number
  height: number
  is_default: boolean
  inject_header_rows: Kv[]
  inject_local_storage_rows: Kv[]
}

function legacyInjectRows(row: UiEnv): { headers: Kv[]; localStorage: Kv[] } {
  const headers = dictToRows(row.inject_headers || {})
  const localStorage = dictToRows(row.inject_local_storage || {})
  if (headers.some((r) => r.key.trim()) || localStorage.some((r) => r.key.trim())) {
    return {
      headers: headers.length ? headers : [{ key: '', value: '' }],
      localStorage: localStorage.length ? localStorage : [{ key: '', value: '' }],
    }
  }
  if (row.token_inject === 'header') {
    return {
      headers: [{ key: 'Authorization', value: 'Bearer {{token}}' }],
      localStorage: [{ key: '', value: '' }],
    }
  }
  if (row.token_inject === 'local_storage') {
    return {
      headers: [{ key: '', value: '' }],
      localStorage: [{ key: row.token_storage_key || 'token', value: '{{token}}' }],
    }
  }
  return {
    headers: [{ key: '', value: '' }],
    localStorage: [{ key: '', value: '' }],
  }
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function hasSecretRows(rows: Kv[] | undefined): boolean {
  return (rows || []).some((r) => (r.key || '').trim())
}

export default function UiEnvList(_props: { embedded?: boolean } = {}) {
  const { containerRef, scrollY } = useTableScrollY()
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<UiEnv | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<EnvForm>()

  const list = useQuery({
    queryKey: ['ui-envs', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<UiEnv>>(`/api/v1/projects/${pid}/ui-envs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    retry: 1,
  })

  const save = useMutation({
    mutationFn: async (values: EnvForm) => {
      const browsers = values.browsers?.length ? values.browsers : ['chromium']
      const secrets = rowsToDict(values.secret_rows)
      const payload = {
        name: values.name.trim(),
        base_url: values.base_url.trim(),
        browser: browsers[0],
        browsers,
        viewport: { width: values.width || 1280, height: values.height || 720 },
        variables: rowsToDict(values.variable_rows),
        accounts: [],
        queue_name: values.queue_name?.trim() || 'ui',
        max_concurrency: values.max_concurrency || 1,
        default_timeout_ms: values.default_timeout_ms || 15000,
        step_retries: values.step_retries ?? 1,
        is_default: Boolean(values.is_default),
        inject_headers: rowsToDict(values.inject_header_rows),
        inject_local_storage: rowsToDict(values.inject_local_storage_rows),
        token_inject: 'none',
        token_storage_key: null,
        ...(hasSecretRows(values.secret_rows)
          ? { secrets }
          : editing
            ? {}
            : { secrets: {} }),
      }
      if (editing) {
        return apiJson<UiEnv>(`/api/v1/projects/${pid}/ui-envs/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson<UiEnv>(`/api/v1/projects/${pid}/ui-envs/`, {
        method: 'POST',
        body: JSON.stringify({ ...payload, secrets }),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-envs', pid] })
      setOpen(false)
      setEditing(null)
      form.resetFields()
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/ui-envs/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        let body: unknown = null
        try {
          body = await res.json()
        } catch {
          body = null
        }
        throw new ApiError(res.status, body)
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-envs', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  function openCreate() {
    setEditing(null)
    form.setFieldsValue({
      name: '',
      base_url: 'https://',
      browsers: ['chromium'],
      variable_rows: [{ key: '', value: '' }],
      secret_rows: [{ key: '', value: '' }],
      queue_name: 'ui',
      max_concurrency: 1,
      default_timeout_ms: 15000,
      step_retries: 1,
      width: 1280,
      height: 720,
      is_default: !(list.data?.items?.length),
      inject_header_rows: [{ key: '', value: '' }],
      inject_local_storage_rows: [{ key: '', value: '' }],
    })
    setOpen(true)
  }

  function openEdit(row: UiEnv) {
    setEditing(row)
    const inject = legacyInjectRows(row)
    form.setFieldsValue({
      name: row.name,
      base_url: row.base_url,
      browsers: row.browsers?.length ? row.browsers : [row.browser || 'chromium'],
      variable_rows: dictToRows(row.variables),
      secret_rows: [{ key: '', value: '' }],
      queue_name: row.queue_name || 'ui',
      max_concurrency: row.max_concurrency || 1,
      default_timeout_ms: row.default_timeout_ms || 15000,
      step_retries: row.step_retries ?? 1,
      width: row.viewport?.width ?? 1280,
      height: row.viewport?.height ?? 720,
      is_default: row.is_default,
      inject_header_rows: inject.headers,
      inject_local_storage_rows: inject.localStorage,
    })
    setOpen(true)
  }

  const columns: ColumnsType<UiEnv> = [
    { title: '名称', dataIndex: 'name' },
    { title: 'Base URL', dataIndex: 'base_url', ellipsis: true },
    {
      title: '浏览器',
      width: 140,
      render: (_, row) => (row.browsers?.length ? row.browsers.join(', ') : row.browser),
    },
    {
      title: '并发/队列',
      width: 120,
      render: (_, row) => `${row.max_concurrency ?? 1} / ${row.queue_name || 'ui'}`,
    },
    {
      title: '默认',
      dataIndex: 'is_default',
      width: 70,
      render: (v: boolean) => (v ? '是' : ''),
    },
    {
      title: '操作',
      width: 160,
      render: (_, row) => (
        <Space>
          <Button type="link" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Popconfirm title="删除该环境？" onConfirm={() => remove.mutate(row.id)}>
            <Button type="link" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="UI 环境" />
        {list.isError && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="加载环境失败"
            description={errText(list.error) + '（若提示表不存在，请在 backend 执行 uv run alembic upgrade head）'}
            action={
              <Button size="small" onClick={() => void list.refetch()}>
                重试
              </Button>
            }
          />
        )}
        <Space style={{ marginBottom: 16 }} wrap>
          <Button type="primary" onClick={openCreate}>
            新建环境
          </Button>
          <Input.Search
            allowClear
            placeholder="搜索环境名称"
            style={{ width: 240 }}
            onSearch={(v) => {
              setSearch(v)
              setPage(1)
            }}
          />
        </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table<UiEnv>
          rowKey="id"
          loading={list.isLoading}
          columns={columns}
          dataSource={list.data?.items ?? []}
          locale={{ emptyText: '暂无环境' }}
          scroll={{ y: scrollY }}
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
        />
      </div>
      <Modal
        title={editing ? '编辑 UI 环境' : '新建 UI 环境'}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={680}
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item
            name="base_url"
            label="Base URL"
            rules={[{ required: true, message: '请输入 base_url' }]}
            extra="用例步骤 goto 使用相对路径，拼接到此地址"
          >
            <Input placeholder="https://app.example.com" />
          </Form.Item>
          <Form.Item name="browsers" label="浏览器矩阵" rules={[{ required: true }]}>
            <Select
              mode="multiple"
              options={[
                { value: 'chromium', label: 'Chromium' },
                { value: 'firefox', label: 'Firefox' },
                { value: 'webkit', label: 'WebKit' },
              ]}
            />
          </Form.Item>
          <Space wrap>
            <Form.Item name="width" label="视口宽度">
              <InputNumber min={320} max={3840} />
            </Form.Item>
            <Form.Item name="height" label="视口高度">
              <InputNumber min={240} max={2160} />
            </Form.Item>
            <Form.Item name="default_timeout_ms" label="默认超时(ms)">
              <InputNumber min={1000} max={120000} step={1000} />
            </Form.Item>
            <Form.Item name="step_retries" label="步骤重试">
              <InputNumber min={0} max={5} />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="queue_name" label="队列名">
              <Input style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="max_concurrency" label="最大并发">
              <InputNumber min={1} max={32} />
            </Form.Item>
          </Space>
          <Form.Item label="变量" extra="步骤中可用 {{key}} 引用">
            <TestDataKvList name="variable_rows" addLabel="添加变量" keyPlaceholder="键" />
          </Form.Item>
          <Form.Item
            label="密钥变量（加密存储）"
            extra={
              editing
                ? `已有密钥键：${editing.secret_keys.join(', ') || '无'}；留空则不修改`
                : '需配置 SETTINGS_ENCRYPTION_KEY'
            }
          >
            <TestDataKvList name="secret_rows" addLabel="添加密钥" keyPlaceholder="键" />
          </Form.Item>
          <Form.Item
            label="请求头注入"
            extra="执行前写入浏览器请求头。值可用 {{变量或密钥键}}，例如 Authorization = Bearer {{access_token}}"
          >
            <TestDataKvList
              name="inject_header_rows"
              addLabel="添加请求头"
              keyPlaceholder="Header 名"
              valuePlaceholder="值或 {{access_token}}"
            />
          </Form.Item>
          <Form.Item
            label="localStorage 注入"
            extra="执行前写入 localStorage。键名须与被测系统一致，例如 access_token = {{access_token}}"
          >
            <TestDataKvList
              name="inject_local_storage_rows"
              addLabel="添加 localStorage"
              keyPlaceholder="键名"
              valuePlaceholder="{{access_token}}"
            />
          </Form.Item>
          <Form.Item name="is_default" label="设为默认环境" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
