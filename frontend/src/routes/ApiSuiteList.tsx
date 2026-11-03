import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import ApiDefinitionStepPicker from '../components/ApiDefinitionStepPicker'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type SuiteStep = {
  id: string
  case_id?: number
  definition_id?: number
  case_name?: string
  definition_name?: string
  method?: string
  path?: string
  on_pass?: string
  on_fail?: string
  when?: Record<string, unknown> | null
  rationale?: string
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

type ApiCase = { id: number; name: string; method: string; path: string }
type ApiDef = {
  id: number
  name: string
  method: string
  path: string
  module: string
  query?: Record<string, string>
  headers?: Record<string, string>
  body_type?: string
  body?: string
}
type ApiEnv = { id: number; name: string; is_default: boolean }
type PageRes<T> = { items: T[]; total: number }
type ApiRun = { id: number; status: string }

type SuiteForm = {
  name: string
  description: string
  stop_on_fail: boolean
  env_id?: number
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function ApiSuiteList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [selectedDefIds, setSelectedDefIds] = useState<number[]>([])
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<SuiteForm>()

  const suites = useQuery({
    queryKey: ['api-suites', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<ApiSuite>>(`/api/v1/projects/${pid}/api-suites/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const cases = useQuery({
    queryKey: ['api-cases', pid, 'transfer'],
    queryFn: () => apiJson<PageRes<ApiCase>>(`/api/v1/projects/${pid}/api-cases/?page=1&page_size=200`),
    enabled: Number.isFinite(pid),
  })

  const definitions = useQuery({
    queryKey: ['api-definitions', pid, 'suite-create'],
    queryFn: () =>
      apiJson<PageRes<ApiDef>>(`/api/v1/projects/${pid}/api-definitions/?page=1&page_size=500`),
    enabled: Number.isFinite(pid),
  })

  const envs = useQuery({
    queryKey: ['api-envs', pid, 'select'],
    queryFn: () => apiJson<PageRes<ApiEnv>>(`/api/v1/projects/${pid}/api-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const caseMap = useMemo(() => {
    const m = new Map<number, ApiCase>()
    for (const c of cases.data?.items ?? []) m.set(c.id, c)
    return m
  }, [cases.data])

  const defMap = useMemo(() => {
    const m = new Map<number, ApiDef>()
    for (const d of definitions.data?.items ?? []) m.set(d.id, d)
    return m
  }, [definitions.data])

  const save = useMutation({
    mutationFn: async (values: SuiteForm) => {
      if (!selectedDefIds.length) {
        throw new Error('请至少选择一个接口步骤')
      }
      const steps = selectedDefIds.map((defId, i) => {
        const d = defMap.get(defId)
        if (!d) throw new Error(`接口 #${defId} 未加载，请刷新后重试`)
        return {
          id: `s${i + 1}`,
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
          on_pass: i + 1 < selectedDefIds.length ? `s${i + 2}` : 'end',
          on_fail: values.stop_on_fail ? 'end' : i + 1 < selectedDefIds.length ? `s${i + 2}` : 'end',
          when: null,
        }
      })
      return apiJson<ApiSuite>(`/api/v1/projects/${pid}/api-suites/`, {
        method: 'POST',
        body: JSON.stringify({
          name: values.name.trim(),
          description: values.description || '',
          stop_on_fail: Boolean(values.stop_on_fail),
          env_id: values.env_id ?? null,
          case_ids: [],
          steps,
        }),
      })
    },
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: ['api-suites', pid] })
      setOpen(false)
      message.success('已创建，可在编排页编辑请求并调试')
      nav(`/projects/${pid}/api-suites/${row.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/api-suites/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-suites', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const run = useMutation({
    mutationFn: (suiteId: number) =>
      apiJson<ApiRun>(`/api/v1/projects/${pid}/api-suites/${suiteId}/run`, {
        method: 'POST',
        body: JSON.stringify({ sync: true }),
      }),
    onSuccess: (data) => {
      message.success(`场景执行完成：${data.status}`)
      nav(`/projects/${pid}/api-runs/${data.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  function openCreate() {
    setSelectedDefIds([])
    form.setFieldsValue({ name: '', description: '', stop_on_fail: true, env_id: undefined })
    setOpen(true)
  }

  const columns: ColumnsType<ApiSuite> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '步骤',
      render: (_, row) => {
        if (row.steps?.length) {
          return (
            row.steps
              .map(
                (s) =>
                  s.definition_name ||
                  s.case_name ||
                  caseMap.get(s.case_id ?? -1)?.name ||
                  (s.definition_id != null ? `接口#${s.definition_id}` : `#${s.case_id}`),
              )
              .join(' → ') || '—'
          )
        }
        return row.case_ids.map((id) => caseMap.get(id)?.name || `#${id}`).join(' → ') || '—'
      },
      ellipsis: true,
    },
    {
      title: '失败即停',
      dataIndex: 'stop_on_fail',
      width: 90,
      render: (v: boolean) => (v ? '是' : '否'),
    },
    {
      title: '操作',
      width: 240,
      render: (_, row) => (
        <Space>
          <Button type="link" onClick={() => nav(`/projects/${pid}/api-suites/${row.id}`)}>
            编排
          </Button>
          <Button type="link" loading={run.isPending} onClick={() => run.mutate(row.id)}>
            执行
          </Button>
          <Popconfirm title="删除场景？" onConfirm={() => remove.mutate(row.id)}>
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
      <PageBanner title="接口场景" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={openCreate}>
          新建场景
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索场景名称"
          style={{ width: 240 }}
          onSearch={(v) => {
            setSearch(v)
            setPage(1)
          }}
        />
      </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table<ApiSuite>
        rowKey="id"
        loading={suites.isLoading}
        columns={columns}
        dataSource={suites.data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: suites.data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, s) => {
            setPage(p)
            setPageSize(s)
          },
        }}
       scroll={{ y: scrollY }} />
      </div>
      <Modal
        title="新建场景"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={920}
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input maxLength={512} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="env_id" label="默认环境（可选）">
            <Select
              allowClear
              options={(envs.data?.items ?? []).map((e) => ({
                value: e.id,
                label: e.is_default ? `${e.name}（默认）` : e.name,
              }))}
            />
          </Form.Item>
          <Form.Item name="stop_on_fail" label="失败/错误时停止后续步骤" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="初始接口顺序（创建后在编排页直接编辑请求、断言、提取并调试）">
            <ApiDefinitionStepPicker
              definitions={definitions.data?.items ?? []}
              value={selectedDefIds}
              onChange={setSelectedDefIds}
              loading={definitions.isLoading}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            创建并进入编排
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
