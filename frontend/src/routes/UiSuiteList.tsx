import { LeftOutlined, RightOutlined } from '@ant-design/icons'
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
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { TableRowSelection } from 'antd/es/table/interface'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type Key } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

const CASE_STATUS_OPTIONS = [
  { value: 'draft', label: '草稿' },
  { value: 'ready', label: '就绪' },
]

function caseStatusLabel(status: string): string {
  return CASE_STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status
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

type UiSuite = {
  id: number
  name: string
  description: string
  case_ids: number[]
  stop_on_fail: boolean
  env_id: number | null
  browsers: string[]
}
type UiCase = {
  id: number
  name: string
  description?: string
  status?: string
}
type UiEnv = {
  id: number
  name: string
  is_default: boolean
  base_url?: string
  inject_headers?: Record<string, string>
  inject_local_storage?: Record<string, string>
  token_inject?: string
  token_storage_key?: string | null
}
type PageRes<T> = { items: T[]; total: number }
type UiRun = { id: number; status: string }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function UiSuiteList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<UiSuite | null>(null)
  const [selectedCases, setSelectedCases] = useState<UiCase[]>([])
  const [selectedLoading, setSelectedLoading] = useState(false)
  const [leftKeys, setLeftKeys] = useState<Key[]>([])
  const [rightKeys, setRightKeys] = useState<Key[]>([])
  const [caseSearch, setCaseSearch] = useState('')
  const [caseSearchInput, setCaseSearchInput] = useState('')
  const [caseStatus, setCaseStatus] = useState<string | undefined>()
  const [casePage, setCasePage] = useState(1)
  const [casePageSize, setCasePageSize] = useState(20)
  const [rightPage, setRightPage] = useState(1)
  const [rightPageSize, setRightPageSize] = useState(20)
  const [browsers, setBrowsers] = useState<string[]>(['chromium'])
  const [runTargetId, setRunTargetId] = useState<number | null>(null)
  const [headed, setHeaded] = useState(false)
  const [runEnvId, setRunEnvId] = useState<number | undefined>()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<{ name: string; description: string; stop_on_fail: boolean; env_id?: number }>()

  const suites = useQuery({
    queryKey: ['ui-suites', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<UiSuite>>(`/api/v1/projects/${pid}/ui-suites/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })
  const cases = useQuery({
    queryKey: ['ui-cases', pid, 'suite-picker', caseSearch, casePage, casePageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(casePage), page_size: String(casePageSize) })
      if (caseSearch.trim()) qs.set('search', caseSearch.trim())
      return apiJson<PageRes<UiCase>>(`/api/v1/projects/${pid}/ui-cases/?${qs}`)
    },
    enabled: Number.isFinite(pid) && open,
  })
  const envs = useQuery({
    queryKey: ['ui-envs', pid, 'select'],
    queryFn: () => apiJson<PageRes<UiEnv>>(`/api/v1/projects/${pid}/ui-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const selectedIdSet = useMemo(() => new Set(selectedCases.map((c) => c.id)), [selectedCases])

  const leftRows = useMemo(() => {
    const items = cases.data?.items ?? []
    return caseStatus ? items.filter((c) => c.status === caseStatus) : items
  }, [cases.data, caseStatus])

  const caseColumns: ColumnsType<UiCase> = useMemo(
    () => [
      { title: '名称', dataIndex: 'name', ellipsis: true },
      {
        title: '状态',
        dataIndex: 'status',
        width: 72,
        render: (v: string | undefined) => (v ? caseStatusLabel(v) : '—'),
      },
    ],
    [],
  )

  async function resolveSelectedCases(ids: number[]) {
    if (!ids.length) {
      setSelectedCases([])
      return
    }
    setSelectedLoading(true)
    try {
      const rows = await Promise.all(
        ids.map(async (id) => {
          try {
            return await apiJson<UiCase>(`/api/v1/projects/${pid}/ui-cases/${id}`)
          } catch {
            return { id, name: `用例 #${id}`, description: '', status: '' }
          }
        }),
      )
      setSelectedCases(rows)
    } finally {
      setSelectedLoading(false)
    }
  }

  function resetCasePicker() {
    setLeftKeys([])
    setRightKeys([])
    setCaseSearch('')
    setCaseSearchInput('')
    setCaseStatus(undefined)
    setCasePage(1)
    setRightPage(1)
  }

  function moveToRight() {
    const keyed = new Map(leftRows.map((c) => [c.id, c]))
    const toAdd: UiCase[] = []
    for (const key of leftKeys) {
      const id = Number(key)
      if (selectedIdSet.has(id)) continue
      const row = keyed.get(id)
      if (row) toAdd.push(row)
    }
    if (!toAdd.length) return
    setSelectedCases((prev) => [...prev, ...toAdd])
    setLeftKeys([])
  }

  function moveToLeft() {
    if (!rightKeys.length) return
    const drop = new Set(rightKeys.map(Number))
    setSelectedCases((prev) => prev.filter((c) => !drop.has(c.id)))
    setRightKeys([])
    setRightPage(1)
  }

  function moveSelectedCase(id: number, dir: -1 | 1) {
    const idx = selectedCases.findIndex((c) => c.id === id)
    const j = idx + dir
    if (idx < 0 || j < 0 || j >= selectedCases.length) return
    const next = [...selectedCases]
    ;[next[idx], next[j]] = [next[j], next[idx]]
    setSelectedCases(next)
    const targetPage = Math.floor(j / rightPageSize) + 1
    if (targetPage !== rightPage) setRightPage(targetPage)
  }

  const leftRowSelection: TableRowSelection<UiCase> = {
    selectedRowKeys: leftKeys,
    onChange: setLeftKeys,
    getCheckboxProps: (row) => ({ disabled: selectedIdSet.has(row.id) }),
  }

  const rightRowSelection: TableRowSelection<UiCase> = {
    selectedRowKeys: rightKeys,
    onChange: setRightKeys,
  }

  const selectedCaseColumns: ColumnsType<UiCase> = useMemo(
    () => [
      {
        title: '序号',
        width: 56,
        render: (_, row) => {
          const idx = selectedCases.findIndex((c) => c.id === row.id)
          return idx >= 0 ? idx + 1 : '—'
        },
      },
      { title: '名称', dataIndex: 'name', ellipsis: true },
      {
        title: '状态',
        dataIndex: 'status',
        width: 72,
        render: (v: string | undefined) => (v ? caseStatusLabel(v) : '—'),
      },
      {
        title: '顺序',
        width: 72,
        render: (_, row) => {
          const idx = selectedCases.findIndex((c) => c.id === row.id)
          return (
            <Space size={0}>
              <Button
                type="text"
                size="small"
                disabled={idx <= 0}
                onClick={() => moveSelectedCase(row.id, -1)}
              >
                ↑
              </Button>
              <Button
                type="text"
                size="small"
                disabled={idx < 0 || idx >= selectedCases.length - 1}
                onClick={() => moveSelectedCase(row.id, 1)}
              >
                ↓
              </Button>
            </Space>
          )
        },
      },
    ],
    [selectedCases, rightPage, rightPageSize],
  )

  const save = useMutation({
    mutationFn: async (values: { name: string; description: string; stop_on_fail: boolean; env_id?: number }) => {
      const payload = {
        name: values.name.trim(),
        description: values.description || '',
        stop_on_fail: Boolean(values.stop_on_fail),
        env_id: values.env_id ?? null,
        case_ids: selectedCases.map((c) => c.id),
        browsers,
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/ui-suites/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/ui-suites/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-suites', pid] })
      setOpen(false)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const defaultEnv = (envs.data?.items ?? []).find((e) => e.is_default) ?? envs.data?.items?.[0]
  const runSuite = (suites.data?.items ?? []).find((s) => s.id === runTargetId)
  const selectedEnv =
    (envs.data?.items ?? []).find((e) => e.id === (runEnvId ?? runSuite?.env_id ?? undefined)) ??
    defaultEnv

  const run = useMutation({
    mutationFn: (suiteId: number) =>
      apiJson<UiRun[]>(`/api/v1/projects/${pid}/ui-suites/${suiteId}/run`, {
        method: 'POST',
        body: JSON.stringify({
          sync: false,
          headless: !headed,
          env_id: runEnvId ?? runSuite?.env_id ?? null,
        }),
      }),
    onSuccess: (runs) => {
      setRunTargetId(null)
      message.success(`已触发 ${runs.length} 个执行`)
      nav(`/projects/${pid}/ui-runs`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/ui-suites/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-suites', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<UiSuite> = [
    { title: '名称', dataIndex: 'name' },
    { title: '用例数', width: 90, render: (_, r) => r.case_ids?.length ?? 0 },
    { title: '浏览器', render: (_, r) => (r.browsers?.length ? r.browsers.join(', ') : 'chromium') },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            onClick={() => {
              setEditing(row)
              resetCasePicker()
              setBrowsers(row.browsers?.length ? row.browsers : ['chromium'])
              form.setFieldsValue({
                name: row.name,
                description: row.description,
                stop_on_fail: row.stop_on_fail,
                env_id: row.env_id ?? undefined,
              })
              setOpen(true)
              void resolveSelectedCases(row.case_ids || [])
            }}
          >
            编辑
          </Button>
          <Button
            type="link"
            onClick={() => {
              setHeaded(false)
              setRunEnvId(row.env_id ?? undefined)
              setRunTargetId(row.id)
            }}
          >
            执行
          </Button>
          <Popconfirm title="删除？" onConfirm={() => remove.mutate(row.id)}>
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
      <PageBanner title="UI 套件" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            resetCasePicker()
            setSelectedCases([])
            setBrowsers(['chromium'])
            form.setFieldsValue({ name: '', description: '', stop_on_fail: true })
            setOpen(true)
          }}
        >
          新建套件
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索套件名称"
          style={{ width: 240 }}
          onSearch={(v) => {
            setSearch(v)
            setPage(1)
          }}
        />
      </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table
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
        title={runSuite ? `执行套件：${runSuite.name}` : '执行'}
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
            <div style={{ marginBottom: 6 }}>执行环境</div>
            <Select
              allowClear
              style={{ width: '100%' }}
              placeholder="套件默认 / 项目默认环境"
              value={runEnvId}
              onChange={setRunEnvId}
              options={(envs.data?.items ?? []).map((e) => ({
                value: e.id,
                label: e.is_default ? `${e.name}（默认）` : e.name,
              }))}
            />
            {selectedEnv ? (
              <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
                {selectedEnv.base_url || ''} · 注入：{envInjectSummary(selectedEnv)}
              </Typography.Text>
            ) : null}
            <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
              各用例按自身「环境注入 / 用例变量」执行：登录用例可设为不注入，用例变量优先于环境。
            </Typography.Text>
          </div>
          <Space>
            <span>浏览器模式</span>
            <Switch checked={headed} onChange={setHeaded} checkedChildren="有头" unCheckedChildren="无头" />
          </Space>
        </Space>
      </Modal>
      <Modal
        title={editing ? '编辑套件' : '新建套件'}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={980}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="env_id" label="默认环境">
            <Select
              allowClear
              options={(envs.data?.items ?? []).map((e) => ({
                value: e.id,
                label: e.is_default ? `${e.name}（默认）` : e.name,
              }))}
            />
          </Form.Item>
          <Form.Item label="浏览器矩阵">
            <Select
              mode="multiple"
              value={browsers}
              onChange={setBrowsers}
              options={[
                { value: 'chromium', label: 'Chromium' },
                { value: 'firefox', label: 'Firefox' },
                { value: 'webkit', label: 'WebKit' },
              ]}
            />
          </Form.Item>
          <Form.Item name="stop_on_fail" label="失败即停止" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label={`用例（已选 ${selectedCases.length}）`}>
            <Space align="start" style={{ width: '100%' }} size="middle">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ marginBottom: 8, fontWeight: 500 }}>可选</div>
                <Space style={{ marginBottom: 8 }} wrap>
                  <Input.Search
                    allowClear
                    placeholder="搜索用例名称"
                    style={{ width: 200 }}
                    value={caseSearchInput}
                    onChange={(e) => setCaseSearchInput(e.target.value)}
                    onSearch={(v) => {
                      setCaseSearch(v)
                      setCasePage(1)
                      setLeftKeys([])
                    }}
                  />
                  <Select
                    allowClear
                    placeholder="状态"
                    style={{ width: 110 }}
                    value={caseStatus}
                    onChange={(v) => {
                      setCaseStatus(v)
                      setLeftKeys([])
                    }}
                    options={CASE_STATUS_OPTIONS}
                  />
                </Space>
                <Table
                  size="small"
                  rowKey="id"
                  loading={cases.isFetching}
                  columns={caseColumns}
                  dataSource={leftRows}
                  rowSelection={leftRowSelection}
                  pagination={{
                    current: casePage,
                    pageSize: casePageSize,
                    total: cases.data?.total ?? 0,
                    size: 'small',
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t}`,
                    onChange: (p, s) => {
                      setCasePage(p)
                      setCasePageSize(s)
                      setLeftKeys([])
                    },
                  }}
                  scroll={{ y: 260 }}
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 72 }}>
                <Button
                  icon={<RightOutlined />}
                  disabled={!leftKeys.length}
                  onClick={moveToRight}
                />
                <Button
                  icon={<LeftOutlined />}
                  disabled={!rightKeys.length}
                  onClick={moveToLeft}
                />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ marginBottom: 8, fontWeight: 500 }}>已选</div>
                <div style={{ height: 32, marginBottom: 8 }} />
                <Table
                  size="small"
                  rowKey="id"
                  loading={selectedLoading}
                  columns={selectedCaseColumns}
                  dataSource={selectedCases}
                  rowSelection={rightRowSelection}
                  pagination={{
                    current: rightPage,
                    pageSize: rightPageSize,
                    total: selectedCases.length,
                    size: 'small',
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t}`,
                    onChange: (p, s) => {
                      setRightPage(p)
                      setRightPageSize(s)
                    },
                  }}
                  scroll={{ y: 260 }}
                />
              </div>
            </Space>
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
