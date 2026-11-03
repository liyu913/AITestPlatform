import {
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Transfer,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type CaseItem = { case_id: number; weight: number }
type Sla = { max_p95_ms?: number | null; max_error_rate?: number | null }

type PerfScenario = {
  id: number
  name: string
  description: string
  env_id: number | null
  case_items: CaseItem[]
  users: number
  spawn_rate: number
  run_time_sec: number
  think_time_ms: number
  sla: Sla
}

type ApiCase = { id: number; name: string; method: string; path: string }
type ApiEnv = { id: number; name: string; is_default: boolean }
type PageRes<T> = { items: T[]; total: number }
type PerfRun = { id: number; status: string }

type ScenarioForm = {
  name: string
  description: string
  env_id?: number
  users: number
  spawn_rate: number
  run_time_sec: number
  think_time_ms: number
  max_p95_ms?: number | null
  max_error_rate?: number | null
}

type RunForm = {
  users: number
  spawn_rate: number
  run_time_sec: number
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function PerfScenarioList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<PerfScenario | null>(null)
  const [targetKeys, setTargetKeys] = useState<string[]>([])
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [runOpen, setRunOpen] = useState(false)
  const [running, setRunning] = useState<PerfScenario | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<ScenarioForm>()
  const [runForm] = Form.useForm<RunForm>()

  const scenarios = useQuery({
    queryKey: ['perf-scenarios', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<PerfScenario>>(`/api/v1/projects/${pid}/perf-scenarios/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const cases = useQuery({
    queryKey: ['api-cases', pid, 'transfer'],
    queryFn: () => apiJson<PageRes<ApiCase>>(`/api/v1/projects/${pid}/api-cases/?page=1&page_size=100`),
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

  const transferData = useMemo(
    () =>
      (cases.data?.items ?? []).map((c) => ({
        key: String(c.id),
        title: `${c.method} ${c.name}`,
        description: c.path,
      })),
    [cases.data],
  )

  function onTransferChange(next: string[]) {
    setTargetKeys(next)
    setWeights((prev) => {
      const out: Record<string, number> = {}
      for (const k of next) out[k] = prev[k] ?? 1
      return out
    })
  }

  const save = useMutation({
    mutationFn: async (values: ScenarioForm) => {
      const payload = {
        name: values.name.trim(),
        description: values.description || '',
        env_id: values.env_id ?? null,
        case_items: targetKeys.map((k) => ({
          case_id: Number(k),
          weight: Math.max(1, Math.min(1000, Number(weights[k] ?? 1) || 1)),
        })),
        users: values.users,
        spawn_rate: values.spawn_rate,
        run_time_sec: values.run_time_sec,
        think_time_ms: values.think_time_ms,
        sla: {
          max_p95_ms: values.max_p95_ms ?? null,
          max_error_rate: values.max_error_rate ?? null,
        },
      }
      if (editing) {
        return apiJson<PerfScenario>(`/api/v1/projects/${pid}/perf-scenarios/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson<PerfScenario>(`/api/v1/projects/${pid}/perf-scenarios/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['perf-scenarios', pid] })
      setOpen(false)
      setEditing(null)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/perf-scenarios/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['perf-scenarios', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const run = useMutation({
    mutationFn: (payload: { scenarioId: number; users: number; spawn_rate: number; run_time_sec: number }) =>
      apiJson<PerfRun>(`/api/v1/projects/${pid}/perf-scenarios/${payload.scenarioId}/run`, {
        method: 'POST',
        body: JSON.stringify({
          sync: false,
          users: payload.users,
          spawn_rate: payload.spawn_rate,
          run_time_sec: payload.run_time_sec,
        }),
      }),
    onSuccess: (data) => {
      setRunOpen(false)
      setRunning(null)
      message.success(`压测已提交：#${data.id}`)
      nav(`/projects/${pid}/perf-runs/${data.id}`)
    },
    onError: (e) => message.error(errText(e)),
  })

  function openCreate() {
    setEditing(null)
    setTargetKeys([])
    setWeights({})
    form.setFieldsValue({
      name: '',
      description: '',
      env_id: undefined,
      users: 10,
      spawn_rate: 2,
      run_time_sec: 60,
      think_time_ms: 0,
      max_p95_ms: undefined,
      max_error_rate: undefined,
    })
    setOpen(true)
  }

  function openEdit(row: PerfScenario) {
    setEditing(row)
    const keys = row.case_items.map((i) => String(i.case_id))
    setTargetKeys(keys)
    const w: Record<string, number> = {}
    for (const i of row.case_items) w[String(i.case_id)] = i.weight || 1
    setWeights(w)
    form.setFieldsValue({
      name: row.name,
      description: row.description,
      env_id: row.env_id ?? undefined,
      users: row.users,
      spawn_rate: row.spawn_rate,
      run_time_sec: row.run_time_sec,
      think_time_ms: row.think_time_ms,
      max_p95_ms: row.sla?.max_p95_ms ?? undefined,
      max_error_rate: row.sla?.max_error_rate ?? undefined,
    })
    setOpen(true)
  }

  function openRun(row: PerfScenario) {
    setRunning(row)
    runForm.setFieldsValue({
      users: row.users,
      spawn_rate: row.spawn_rate,
      run_time_sec: row.run_time_sec,
    })
    setRunOpen(true)
  }

  const columns: ColumnsType<PerfScenario> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '用例',
      render: (_, row) =>
        row.case_items
          .map((i) => {
            const name = caseMap.get(i.case_id)?.name || `#${i.case_id}`
            return i.weight !== 1 ? `${name}×${i.weight}` : name
          })
          .join(', ') || '—',
      ellipsis: true,
    },
    { title: '并发', dataIndex: 'users', width: 70 },
    { title: '时长(s)', dataIndex: 'run_time_sec', width: 80 },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space>
          <Button type="link" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Button type="link" onClick={() => openRun(row)}>
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
      <PageBanner title="压测场景" />
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
        <Table<PerfScenario>
        rowKey="id"
        loading={scenarios.isLoading}
        columns={columns}
        dataSource={scenarios.data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: scenarios.data?.total ?? 0,
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
        title={editing ? '编辑压测场景' : '新建压测场景'}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={780}
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input maxLength={512} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="env_id" label="接口环境">
            <Select
              allowClear
              options={(envs.data?.items ?? []).map((e) => ({
                value: e.id,
                label: e.is_default ? `${e.name}（默认）` : e.name,
              }))}
            />
          </Form.Item>
          <Space wrap style={{ width: '100%' }}>
            <Form.Item name="users" label="并发用户" rules={[{ required: true }]}>
              <InputNumber min={1} max={200} />
            </Form.Item>
            <Form.Item name="spawn_rate" label="加压速率/s" rules={[{ required: true }]}>
              <InputNumber min={0.1} max={50} step={0.5} />
            </Form.Item>
            <Form.Item name="run_time_sec" label="时长(秒)" rules={[{ required: true }]}>
              <InputNumber min={1} max={600} />
            </Form.Item>
            <Form.Item name="think_time_ms" label="思考时间(ms)">
              <InputNumber min={0} max={60000} />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="max_p95_ms" label="SLA P95(ms)">
              <InputNumber min={0} placeholder="可选" />
            </Form.Item>
            <Form.Item name="max_error_rate" label="SLA 错误率(0-1)">
              <InputNumber min={0} max={1} step={0.01} placeholder="可选" />
            </Form.Item>
          </Space>
          <Form.Item label="压测接口（右侧为选中）" required>
            <Transfer
              dataSource={transferData}
              titles={['可选用例', '压测接口']}
              targetKeys={targetKeys}
              onChange={(next) => onTransferChange(next as string[])}
              render={(item) => item.title}
              listStyle={{ width: 300, height: 280 }}
              oneWay={false}
            />
          </Form.Item>
          {targetKeys.length > 0 && (
            <Form.Item label="流量权重（越大请求占比越高）">
              <Space direction="vertical" style={{ width: '100%' }}>
                {targetKeys.map((k) => {
                  const c = caseMap.get(Number(k))
                  return (
                    <Space key={k}>
                      <span style={{ minWidth: 220, display: 'inline-block' }}>
                        {c ? `${c.method} ${c.name}` : `#${k}`}
                      </span>
                      <InputNumber
                        min={1}
                        max={1000}
                        value={weights[k] ?? 1}
                        onChange={(v) =>
                          setWeights((prev) => ({ ...prev, [k]: Math.max(1, Number(v) || 1) }))
                        }
                      />
                    </Space>
                  )
                })}
              </Space>
            </Form.Item>
          )}
          <Button type="primary" htmlType="submit" loading={save.isPending} disabled={!targetKeys.length}>
            保存
          </Button>
        </Form>
      </Modal>

      <Modal
        title={running ? `执行压测：${running.name}` : '执行压测'}
        open={runOpen}
        onCancel={() => {
          setRunOpen(false)
          setRunning(null)
        }}
        footer={null}
        destroyOnHidden
      >
        <Form
          form={runForm}
          layout="vertical"
          onFinish={(v) => {
            if (!running) return
            run.mutate({
              scenarioId: running.id,
              users: v.users,
              spawn_rate: v.spawn_rate,
              run_time_sec: v.run_time_sec,
            })
          }}
        >
          <Form.Item name="users" label="并发用户" rules={[{ required: true }]}>
            <InputNumber min={1} max={200} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="spawn_rate" label="加压速率/s" rules={[{ required: true }]}>
            <InputNumber min={0.1} max={50} step={0.5} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="run_time_sec" label="时长(秒)" rules={[{ required: true }]}>
            <InputNumber min={1} max={600} style={{ width: '100%' }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={run.isPending} block>
            开始压测
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
