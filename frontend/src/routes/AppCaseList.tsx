import {
  Alert,
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import AppStepEditor, { type AppStep } from './AppStepEditor'
import PageBanner from './PageBanner'

type AppCase = {
  id: number
  name: string
  description: string
  steps: AppStep[]
  status: string
}

type AppEnv = {
  id: number
  name: string
  is_default: boolean
  platform: string
  accounts?: Array<{ name: string }>
}

type AppDevice = {
  id: number
  name: string
  platform: string
  status: string
  tags: string[]
}
type PageRes<T> = { items: T[]; total: number }
type AppRun = { id: number; status: string }

type CaseForm = {
  name: string
  description: string
  status: string
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function cleanSteps(steps: AppStep[]): AppStep[] {
  return steps.map((s) => {
    const out: AppStep = { action: s.action }
    if (s.strategy) out.strategy = s.strategy
    if (s.selector) out.selector = s.selector
    if (s.element) out.element = s.element
    if (s.value != null && s.value !== '') out.value = s.value
    if (s.assert != null && s.assert !== '') out.assert = s.assert
    if (s.timeout_ms) out.timeout_ms = s.timeout_ms
    return out
  })
}

export default function AppCaseList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AppCase | null>(null)
  const [steps, setSteps] = useState<AppStep[]>([{ action: 'launch' }])
  const [runEnvId, setRunEnvId] = useState<number | undefined>()
  const [runAccount, setRunAccount] = useState<string | undefined>()
  const [runDeviceId, setRunDeviceId] = useState<number | undefined>()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<CaseForm>()

  const list = useQuery({
    queryKey: ['app-cases', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<AppCase>>(`/api/v1/projects/${pid}/app-cases/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const envs = useQuery({
    queryKey: ['app-envs', pid],
    queryFn: () => apiJson<PageRes<AppEnv>>(`/api/v1/projects/${pid}/app-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const devices = useQuery({
    queryKey: ['app-devices', pid],
    queryFn: () =>
      apiJson<PageRes<AppDevice>>(`/api/v1/projects/${pid}/app-devices/?page=1&page_size=200`),
    enabled: Number.isFinite(pid),
  })

  const defaultEnv = (envs.data?.items ?? []).find((e) => e.is_default) ?? envs.data?.items?.[0]
  const selectedEnv = (envs.data?.items ?? []).find((e) => e.id === runEnvId) ?? defaultEnv
  const accountOptions = selectedEnv?.accounts ?? []
  const deviceOptions = (devices.data?.items ?? []).filter(
    (d) => !selectedEnv?.platform || d.platform === selectedEnv.platform,
  )

  const save = useMutation({
    mutationFn: async (values: CaseForm) => {
      const payload = {
        name: values.name.trim(),
        description: values.description || '',
        steps: cleanSteps(steps),
        status: values.status,
      }
      if (editing) {
        return apiJson<AppCase>(`/api/v1/projects/${pid}/app-cases/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson<AppCase>(`/api/v1/projects/${pid}/app-cases/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-cases', pid] })
      setOpen(false)
      setEditing(null)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/app-cases/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-cases', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const run = useMutation({
    mutationFn: async (caseId: number) =>
      apiJson<AppRun>(`/api/v1/projects/${pid}/app-cases/${caseId}/run`, {
        method: 'POST',
        body: JSON.stringify({
          env_id: runEnvId ?? defaultEnv?.id,
          account_name: runAccount,
          device_id: runDeviceId,
        }),
      }),
    onSuccess: (data) => {
      message.success('已提交执行')
      nav(`/projects/${pid}/app-runs/${data.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<AppCase> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '名称', dataIndex: 'name' },
    { title: '状态', dataIndex: 'status', width: 90 },
    {
      title: '步骤数',
      width: 80,
      render: (_, r) => r.steps?.length ?? 0,
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setEditing(row)
              setSteps(row.steps?.length ? row.steps : [{ action: 'launch' }])
              form.setFieldsValue({
                name: row.name,
                description: row.description,
                status: row.status,
              })
              setOpen(true)
            }}
          >
            编辑
          </Button>
          <Button type="link" size="small" loading={run.isPending} onClick={() => run.mutate(row.id)}>
            执行
          </Button>
          <Popconfirm title="删除用例？" onConfirm={() => remove.mutate(row.id)}>
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
      <PageBanner title="APP 用例" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            setSteps([{ action: 'launch' }])
            form.setFieldsValue({ name: '', description: '', status: 'draft' })
            setOpen(true)
          }}
        >
          新建用例
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
          placeholder="执行环境"
          style={{ width: 180 }}
          value={runEnvId}
          onChange={setRunEnvId}
          options={(envs.data?.items ?? []).map((e) => ({
            value: e.id,
            label: `${e.name}${e.is_default ? ' (默认)' : ''}`,
          }))}
        />
        <Select
          allowClear
          placeholder="账号"
          style={{ width: 140 }}
          value={runAccount}
          onChange={setRunAccount}
          options={accountOptions.map((a) => ({ value: a.name, label: a.name }))}
        />
        <Select
          allowClear
          placeholder="指定设备（可选）"
          style={{ width: 200 }}
          value={runDeviceId}
          onChange={setRunDeviceId}
          options={deviceOptions.map((d) => ({
            value: d.id,
            label: `${d.name} [${d.status}]`,
          }))}
        />
      </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        {list.isError ? (
        <Alert type="error" message={errText(list.error)} />
      ) : !list.data?.items.length && !list.isLoading ? (
        <Empty description="暂无 APP 用例" />
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
        title={editing ? '编辑用例' : '新建用例'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        width={960}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="status" label="状态" initialValue="draft">
            <Select
              options={[
                { value: 'draft', label: '草稿' },
                { value: 'ready', label: '就绪' },
              ]}
            />
          </Form.Item>
          <Form.Item label="步骤">
            <AppStepEditor steps={steps} onChange={setSteps} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
