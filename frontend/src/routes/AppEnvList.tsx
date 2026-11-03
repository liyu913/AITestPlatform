import {
  Alert,
  App,
  Button,
  Empty,
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
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type AppAccount = { name: string; variables: Record<string, string> }

type AppEnv = {
  id: number
  name: string
  platform: string
  appium_server_url: string
  device_name: string
  udid: string
  platform_version: string
  automation_name: string
  app_package: string
  app_activity: string
  bundle_id: string
  app_path: string
  no_reset: boolean
  full_reset: boolean
  variables: Record<string, string>
  secret_keys: string[]
  accounts: AppAccount[]
  queue_name: string
  max_concurrency: number
  default_timeout_ms: number
  step_retries: number
  is_default: boolean
  device_tags?: string[]
  use_device_pool?: boolean
}

type PageRes<T> = { items: T[]; total: number }

type EnvForm = {
  name: string
  platform: string
  appium_server_url: string
  device_name: string
  udid: string
  platform_version: string
  automation_name: string
  app_package: string
  app_activity: string
  bundle_id: string
  app_path: string
  no_reset: boolean
  full_reset: boolean
  variables_text: string
  secrets_text: string
  accounts_text: string
  queue_name: string
  max_concurrency: number
  default_timeout_ms: number
  step_retries: number
  is_default: boolean
  device_tags_text: string
  use_device_pool: boolean
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string; detail?: string }
    return b.message ?? b.detail ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function parseKv(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const i = t.indexOf('=')
    if (i <= 0) continue
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim()
  }
  return out
}

function kvToText(obj: Record<string, string> | undefined): string {
  if (!obj) return ''
  return Object.entries(obj)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')
}

function parseAccounts(text: string): AppAccount[] {
  const accounts: AppAccount[] = []
  let current: AppAccount | null = null
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const m = t.match(/^\[(.+)\]$/)
    if (m) {
      current = { name: m[1].trim(), variables: {} }
      accounts.push(current)
      continue
    }
    if (!current) continue
    const i = t.indexOf('=')
    if (i <= 0) continue
    current.variables[t.slice(0, i).trim()] = t.slice(i + 1).trim()
  }
  return accounts
}

function accountsToText(accounts: AppAccount[] | undefined): string {
  if (!accounts?.length) return ''
  return accounts
    .map((a) => `[${a.name}]\n${kvToText(a.variables)}`)
    .join('\n')
}

export default function AppEnvList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AppEnv | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<EnvForm>()

  const list = useQuery({
    queryKey: ['app-envs', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<AppEnv>>(`/api/v1/projects/${pid}/app-envs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const save = useMutation({
    mutationFn: async (values: EnvForm) => {
      const payload = {
        name: values.name.trim(),
        platform: values.platform,
        appium_server_url: values.appium_server_url.trim(),
        device_name: values.device_name || 'Android Emulator',
        udid: values.udid || '',
        platform_version: values.platform_version || '',
        automation_name: values.automation_name || 'UiAutomator2',
        app_package: values.app_package || '',
        app_activity: values.app_activity || '',
        bundle_id: values.bundle_id || '',
        app_path: values.app_path || '',
        no_reset: values.no_reset,
        full_reset: values.full_reset,
        variables: parseKv(values.variables_text || ''),
        secrets: parseKv(values.secrets_text || ''),
        accounts: parseAccounts(values.accounts_text || ''),
        queue_name: values.queue_name || 'app',
        max_concurrency: values.max_concurrency,
        default_timeout_ms: values.default_timeout_ms,
        step_retries: values.step_retries,
        is_default: values.is_default,
        device_tags: (values.device_tags_text || '')
          .split(/[,，\s]+/)
          .map((t) => t.trim())
          .filter(Boolean),
        use_device_pool: values.use_device_pool,
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/app-envs/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/app-envs/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-envs', pid] })
      setOpen(false)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/app-envs/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-envs', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<AppEnv> = [
    { title: '名称', dataIndex: 'name' },
    { title: '平台', dataIndex: 'platform', width: 90 },
    { title: '设备', dataIndex: 'device_name', ellipsis: true },
    { title: '包名/Bundle', render: (_, r) => r.app_package || r.bundle_id || '—' },
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
          <Button
            type="link"
            size="small"
            onClick={() => {
              setEditing(row)
              form.setFieldsValue({
                name: row.name,
                platform: row.platform,
                appium_server_url: row.appium_server_url,
                device_name: row.device_name,
                udid: row.udid,
                platform_version: row.platform_version,
                automation_name: row.automation_name,
                app_package: row.app_package,
                app_activity: row.app_activity,
                bundle_id: row.bundle_id,
                app_path: row.app_path,
                no_reset: row.no_reset,
                full_reset: row.full_reset,
                variables_text: kvToText(row.variables),
                secrets_text: '',
                accounts_text: accountsToText(row.accounts),
                queue_name: row.queue_name,
                max_concurrency: row.max_concurrency,
                default_timeout_ms: row.default_timeout_ms,
                step_retries: row.step_retries,
                is_default: row.is_default,
                device_tags_text: (row.device_tags || []).join(','),
                use_device_pool: row.use_device_pool ?? true,
              })
              setOpen(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm title="删除该环境？" onConfirm={() => remove.mutate(row.id)}>
            <Button type="link" size="small" danger>
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
      <PageBanner title="APP 环境" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            form.setFieldsValue({
              name: '',
              platform: 'android',
              appium_server_url: 'http://127.0.0.1:4723',
              device_name: 'Android Emulator',
              udid: '',
              platform_version: '',
              automation_name: 'UiAutomator2',
              app_package: '',
              app_activity: '',
              bundle_id: '',
              app_path: '',
              no_reset: true,
              full_reset: false,
              variables_text: '',
              secrets_text: '',
              accounts_text: '',
              queue_name: 'app',
              max_concurrency: 1,
              default_timeout_ms: 15000,
              step_retries: 1,
              is_default: false,
              device_tags_text: '',
              use_device_pool: true,
            })
            setOpen(true)
          }}
        >
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
        <Button onClick={() => nav(`/projects/${pid}/app-cases`)}>用例</Button>
      </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        {list.isError ? (
        <Alert type="error" message={errText(list.error)} />
      ) : !list.data?.items.length && !list.isLoading && !search.trim() ? (
        <Empty description="暂无 APP 环境" />
      ) : (
        <Table
          rowKey="id"
          loading={list.isLoading}
          columns={columns}
          dataSource={list.data?.items}
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
         scroll={{ y: scrollY }} />
      )}
      </div>
      <Modal
        title={editing ? '编辑环境' : '新建环境'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'android', label: 'Android' },
                { value: 'ios', label: 'iOS（预留）' },
              ]}
            />
          </Form.Item>
          <Form.Item name="appium_server_url" label="Appium URL" rules={[{ required: true }]}>
            <Input placeholder="http://127.0.0.1:4723" />
          </Form.Item>
          <Space wrap style={{ width: '100%' }}>
            <Form.Item name="device_name" label="设备名">
              <Input style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="udid" label="UDID">
              <Input style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="platform_version" label="系统版本">
              <Input style={{ width: 120 }} />
            </Form.Item>
          </Space>
          <Form.Item name="automation_name" label="Automation">
            <Input placeholder="UiAutomator2 / XCUITest" />
          </Form.Item>
          <Space wrap style={{ width: '100%' }}>
            <Form.Item name="app_package" label="appPackage">
              <Input style={{ width: 220 }} />
            </Form.Item>
            <Form.Item name="app_activity" label="appActivity">
              <Input style={{ width: 220 }} />
            </Form.Item>
          </Space>
          <Form.Item name="bundle_id" label="bundleId（iOS）">
            <Input />
          </Form.Item>
          <Form.Item name="app_path" label="安装包路径（可选）">
            <Input placeholder="/path/to/app.apk" />
          </Form.Item>
          <Space>
            <Form.Item name="no_reset" label="noReset" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="full_reset" label="fullReset" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="is_default" label="默认环境" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="use_device_pool" label="使用设备池" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item name="device_tags_text" label="设备池标签（逗号分隔，领取时匹配）">
            <Input placeholder="smoke, pixel6" />
          </Form.Item>
          <Form.Item name="variables_text" label="变量（key=value 每行）">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="secrets_text" label="密钥（写入时覆盖）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="accounts_text" label="账号（[name] + key=value）">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Space>
            <Form.Item name="queue_name" label="队列">
              <Input style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="max_concurrency" label="并发">
              <InputNumber min={1} max={32} />
            </Form.Item>
            <Form.Item name="default_timeout_ms" label="超时 ms">
              <InputNumber min={1000} max={120000} step={1000} />
            </Form.Item>
            <Form.Item name="step_retries" label="重试">
              <InputNumber min={0} max={5} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
