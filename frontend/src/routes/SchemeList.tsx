import { App, Button, Dropdown, Form, Input, Modal, Pagination, Select, Space, Spin, Tag } from 'antd'
import type { MenuProps } from 'antd'
import { EllipsisOutlined, PlusOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type Scheme = {
  id: number
  project_id: number
  name: string
  status: string
  owner_id: number | null
  current_version_no: number
  plan_count: number
  latest_snapshot: Record<string, string> | null
  created_at: string
  updated_at: string
}

type SchemeVersion = {
  id: number
  scheme_id: number
  project_id: number
  version_no: number
  snapshot: Record<string, string> | null
  change_note: string | null
  created_by_id: number | null
  created_at: string
}

type SchemeDetail = Scheme & { versions: SchemeVersion[] }
type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

const STATUS_OPTIONS = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '启用' },
  { value: 'archived', label: '归档' },
]

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  active: '启用',
  archived: '归档',
}

const STATUS_COLOR: Record<string, string> = {
  draft: 'default',
  active: 'success',
  archived: 'default',
}

const CONTENT_FIELDS: { name: string; label: string; placeholder: string; rows: number }[] = [
  { name: 'scope', label: '测试范围', placeholder: '覆盖的模块、需求和明确不测的内容', rows: 3 },
  { name: 'strategy', label: '测试策略', placeholder: '分层策略、重点与取舍', rows: 3 },
  { name: 'risks', label: '风险与依赖', placeholder: '业务、环境、数据和第三方依赖风险', rows: 3 },
  { name: 'methods', label: '测试方法', placeholder: '等价类、边界、异常注入、探索测试等', rows: 2 },
  { name: 'exit_criteria', label: '准入准出', placeholder: '进入测试的条件与完成标准', rows: 2 },
]

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function SchemeList() {
  const { projectId } = useParams()
  const pid = Number(projectId)
  const { message, modal } = App.useApp()
  const qc = useQueryClient()

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Scheme | null>(null)
  const [versionsTarget, setVersionsTarget] = useState<Scheme | null>(null)
  const [form] = Form.useForm()

  const listQuery = useQuery({
    queryKey: ['schemes', pid, page, pageSize, search, statusFilter],
    queryFn: () =>
      apiJson<PageRes<Scheme>>(
        `/api/v1/projects/${pid}/schemes/?page=${page}&page_size=${pageSize}` +
          `&search=${encodeURIComponent(search)}` +
          (statusFilter ? `&status=${statusFilter}` : ''),
      ),
    enabled: Number.isFinite(pid),
  })

  const versionsQuery = useQuery({
    queryKey: ['scheme-versions', pid, versionsTarget?.id],
    queryFn: () =>
      apiJson<SchemeVersion[]>(`/api/v1/projects/${pid}/schemes/${versionsTarget!.id}/versions`),
    enabled: Boolean(versionsTarget?.id),
  })

  function refresh() {
    void qc.invalidateQueries({ queryKey: ['schemes', pid] })
  }

  const createMut = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiJson<SchemeDetail>(`/api/v1/projects/${pid}/schemes/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      message.success('方案已创建')
      setDialogOpen(false)
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const updateMut = useMutation({
    mutationFn: (p: { id: number; payload: Record<string, unknown> }) =>
      apiJson<SchemeDetail>(`/api/v1/projects/${pid}/schemes/${p.id}`, {
        method: 'PATCH',
        body: JSON.stringify(p.payload),
      }),
    onSuccess: () => {
      message.success('方案已保存为新版本')
      setDialogOpen(false)
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<void>(`/api/v1/projects/${pid}/schemes/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      message.success('已删除')
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const snapshotMut = useMutation({
    mutationFn: (id: number) =>
      apiJson<SchemeVersion>(`/api/v1/projects/${pid}/schemes/${id}/versions`, {
        method: 'POST',
        body: JSON.stringify({ change_note: '手动留档' }),
      }),
    onSuccess: () => {
      message.success('已生成新版本')
      void qc.invalidateQueries({ queryKey: ['schemes', pid] })
      void qc.invalidateQueries({ queryKey: ['scheme-versions', pid, versionsTarget?.id] })
    },
    onError: (e) => message.error(errText(e)),
  })

  function openCreate() {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ status: 'draft' })
    setDialogOpen(true)
  }

  function openEdit(scheme: Scheme) {
    setEditing(scheme)
    const snapshot = scheme.latest_snapshot ?? {}
    form.setFieldsValue({
      name: scheme.name,
      status: scheme.status,
      scope: snapshot.scope ?? '',
      strategy: snapshot.strategy ?? '',
      risks: snapshot.risks ?? '',
      methods: snapshot.methods ?? '',
      exit_criteria: snapshot.exit_criteria ?? '',
      change_note: '',
    })
    setDialogOpen(true)
  }

  function submit() {
    void form.validateFields().then((values) => {
      if (editing) {
        updateMut.mutate({ id: editing.id, payload: values })
      } else {
        createMut.mutate(values)
      }
    })
  }

  function confirmDelete(scheme: Scheme) {
    modal.confirm({
      title: '删除该测试方案？',
      content: '方案及其版本历史将一并删除，已引用它的计划会保留但失去方案关联。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteMut.mutate(scheme.id),
    })
  }

  function schemeMenu(scheme: Scheme): MenuProps['items'] {
    return [
      {
        key: 'versions',
        label: `版本历史（${scheme.current_version_no}）`,
        onClick: () => setVersionsTarget(scheme),
      },
      {
        key: 'snapshot',
        label: '留档当前版本',
        onClick: () => snapshotMut.mutate(scheme.id),
      },
      { type: 'divider' },
      {
        key: 'delete',
        danger: true,
        label: '删除方案',
        onClick: () => confirmDelete(scheme),
      },
    ]
  }

  if (!Number.isFinite(pid)) return <p className="error">无效项目</p>

  const data = listQuery.data
  const items = data?.items ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="测试方案" />
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索方案名称"
              style={{ width: 260 }}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onSearch={(v) => {
                setPage(1)
                setSearch(v.trim())
              }}
            />
            <Select
              allowClear
              placeholder="全部状态"
              style={{ width: 130 }}
              options={STATUS_OPTIONS}
              value={statusFilter}
              onChange={(v) => {
                setPage(1)
                setStatusFilter(v)
              }}
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建方案
            </Button>
          </Space>
        </Space>
      </div>
      <div className="list-page-body is-grid">
        <Spin spinning={listQuery.isLoading}>
          {items.length === 0 && !listQuery.isLoading ? (
            <p className="muted">暂无测试方案。先定义范围、策略、风险与准入准出，再让计划引用具体版本。</p>
          ) : (
            <div className="list-card-grid">
              {items.map((scheme) => (
                <article key={scheme.id} className="list-card">
                  <h3 className="list-card-title" title={scheme.name}>
                    {scheme.name}
                  </h3>
                  <div className="list-card-meta">
                    <Tag color={STATUS_COLOR[scheme.status] ?? 'default'}>
                      {STATUS_LABEL[scheme.status] ?? scheme.status}
                    </Tag>
                    <span>v{scheme.current_version_no}</span>
                    <span>{scheme.plan_count} 个计划引用</span>
                    <span>{formatBeijing(scheme.updated_at)}</span>
                  </div>
                  {scheme.latest_snapshot?.scope ? (
                    <p className="muted list-card-excerpt">{scheme.latest_snapshot.scope}</p>
                  ) : null}
                  <div className="list-card-actions-row">
                    <Space size={4}>
                      <Button type="primary" size="small" onClick={() => openEdit(scheme)}>
                        编辑
                      </Button>
                      <Button
                        type="text"
                        size="small"
                        onClick={() => setVersionsTarget(scheme)}
                      >
                        版本历史
                      </Button>
                      <Dropdown
                        menu={{ items: schemeMenu(scheme) }}
                        trigger={['click']}
                        placement="bottomRight"
                      >
                        <Button type="text" size="small" aria-label="更多操作">
                          <EllipsisOutlined />
                        </Button>
                      </Dropdown>
                    </Space>
                  </div>
                </article>
              ))}
            </div>
          )}
          <div className="list-card-pagination">
            <Pagination
              current={page}
              pageSize={pageSize}
              total={data?.total ?? 0}
              showSizeChanger
              showTotal={(t) => `共 ${t} 条`}
              pageSizeOptions={[10, 20, 50]}
              onChange={(p, s) => {
                setPage(p)
                setPageSize(s)
              }}
            />
          </div>
        </Spin>
      </div>

      <Modal
        title={editing ? '编辑测试方案' : '新建测试方案'}
        open={dialogOpen}
        onCancel={() => setDialogOpen(false)}
        onOk={submit}
        confirmLoading={createMut.isPending || updateMut.isPending}
        okText="保存"
        cancelText="取消"
        width={720}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="方案名称"
            rules={[{ required: true, message: '请输入方案名称' }]}
          >
            <Input maxLength={512} placeholder="例如：支付模块第一版测试方案" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={STATUS_OPTIONS} />
          </Form.Item>
          {CONTENT_FIELDS.map((field) => (
            <Form.Item key={field.name} name={field.name} label={field.label}>
              <Input.TextArea rows={field.rows} maxLength={65535} placeholder={field.placeholder} />
            </Form.Item>
          ))}
          {editing ? (
            <Form.Item name="change_note" label="本次修改说明">
              <Input maxLength={512} placeholder="保存后自动生成新版本" />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>

      <Modal
        title={versionsTarget ? `版本历史 · ${versionsTarget.name}` : '版本历史'}
        open={Boolean(versionsTarget)}
        onCancel={() => setVersionsTarget(null)}
        footer={
          <Button onClick={() => setVersionsTarget(null)}>关闭</Button>
        }
        width={760}
        destroyOnHidden
      >
        {versionsTarget ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {(versionsQuery.data ?? []).map((version) => (
              <div key={version.id} className="card" style={{ margin: 0 }}>
                <Space wrap>
                  <strong>v{version.version_no}</strong>
                  <span className="muted">{version.change_note || '—'}</span>
                  <span className="muted">{formatBeijing(version.created_at)}</span>
                </Space>
                <div style={{ marginTop: 8 }}>
                  <p className="muted" style={{ margin: 0 }}>
                    范围：{version.snapshot?.scope || '—'}
                  </p>
                  <p className="muted" style={{ margin: 0 }}>
                    准出：{version.snapshot?.exit_criteria || '—'}
                  </p>
                </div>
              </div>
            ))}
          </Space>
        ) : null}
      </Modal>
    </div>
  )
}
