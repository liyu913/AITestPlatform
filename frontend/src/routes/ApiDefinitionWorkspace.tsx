import {
  App,
  Alert,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  TreeSelect,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { DataNode } from 'antd/es/tree'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import ApiRequestWorkbench from '../components/ApiRequestWorkbench'
import { useTableScrollY } from '../hooks/useTableScrollY'
import { methodColor } from '../utils/apiDefinitionTree'
import PageBanner from './PageBanner'

type ModuleNode = {
  id: number
  name: string
  children: ModuleNode[]
}

type ApiDef = {
  id: number
  name: string
  module?: string
  module_id?: number | null
  description: string
  method: string
  path: string
  query: Record<string, string>
  headers: Record<string, string>
  body_type: string
  body: string
  status: string
  source: string
  case_count: number
}

type ApiCase = {
  id: number
  name: string
  method: string
  path: string
  status: string
}

type PageRes<T> = { items: T[]; total: number }

type DesignForm = {
  name: string
  module_id?: number | null
  description?: string
  method: string
  path: string
  body_type: string
  body?: string
  status: string
}

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']

function moduleTreeSelectData(nodes: ModuleNode[]): DataNode[] {
  return nodes.map((n) => ({
    key: n.id,
    value: n.id,
    title: n.name,
    children: n.children?.length ? moduleTreeSelectData(n.children) : undefined,
  }))
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    return (ex.body as { message?: string }).message ?? '请求失败'
  }
  return '请求失败'
}

type ListProps = {
  items: ApiDef[]
  total: number
  loading: boolean
  error: unknown
  page: number
  pageSize: number
  methodFilter?: string
  moduleFilter?: number | 'ungrouped' | undefined
  moduleTreeOptions: DataNode[]
  onSearch: (value: string) => void
  onMethod: (value: string | undefined) => void
  onModule: (value: number | 'ungrouped' | undefined) => void
  onPage: (page: number, pageSize: number) => void
  onOpen: (id: number, tab?: string) => void
  onCreate: () => void
  onDelete: (id: number) => void
  projectId: number
}

function ApiDefinitionTable({
  items,
  total,
  loading,
  error,
  page,
  pageSize,
  methodFilter,
  moduleFilter,
  moduleTreeOptions,
  onSearch,
  onMethod,
  onModule,
  onPage,
  onOpen,
  onCreate,
  onDelete,
  projectId,
}: ListProps) {
  const { containerRef, scrollY } = useTableScrollY()

  const columns: ColumnsType<ApiDef> = [
    {
      title: '方法',
      dataIndex: 'method',
      width: 90,
      render: (m: string) => <Tag color={methodColor(m)}>{m}</Tag>,
    },
    { title: '路径', dataIndex: 'path', ellipsis: true },
    { title: '名称', dataIndex: 'name', ellipsis: true },
    {
      title: '模块',
      dataIndex: 'module',
      width: 120,
      ellipsis: true,
      render: (m: string | undefined) => m?.trim() || <span className="muted">未分组</span>,
    },
    {
      title: '用例',
      dataIndex: 'case_count',
      width: 70,
      render: (n: number, row: ApiDef) =>
        n > 0 ? (
          <Button
            type="link"
            onClick={(e) => {
              e.stopPropagation()
              onOpen(row.id, 'cases')
            }}
          >
            {n}
          </Button>
        ) : (
          <span className="muted">0</span>
        ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 90,
      render: (s: string) => (s === 'openapi' ? '文档同步' : '手工'),
    },
    {
      title: '操作',
      width: 200,
      render: (_: unknown, row: ApiDef) => (
        <Space onClick={(e) => e.stopPropagation()}>
          <Button type="link" onClick={() => onOpen(row.id)}>
            编辑
          </Button>
          <Button type="link" onClick={() => onOpen(row.id, 'debug')}>
            调试
          </Button>
          <Popconfirm
            title="删除该接口定义？（关联用例会解除绑定）"
            onConfirm={() => onDelete(row.id)}
          >
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
        <PageBanner title="接口管理" />
        {error ? (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="接口列表加载失败"
            description={errText(error)}
          />
        ) : null}
        <Space style={{ marginBottom: 16 }} wrap>
          <Button type="primary" onClick={onCreate}>
            新建接口
          </Button>
          <Input.Search
            allowClear
            placeholder="搜索名称/路径"
            style={{ width: 240 }}
            onSearch={onSearch}
            onChange={(e) => {
              if (!e.target.value) onSearch('')
            }}
          />
          <Select
            allowClear
            placeholder="方法"
            style={{ width: 120 }}
            options={METHODS.map((m) => ({ value: m, label: m }))}
            value={methodFilter}
            onChange={onMethod}
          />
          <TreeSelect
            allowClear
            placeholder="模块"
            style={{ width: 200 }}
            treeData={[
              { value: 'ungrouped', title: '未分组', isLeaf: true },
              ...moduleTreeOptions,
            ]}
            value={moduleFilter}
            onChange={(v) => onModule(v as number | 'ungrouped' | undefined)}
            treeDefaultExpandAll
          />
          <Link to={`/projects/${projectId}/modules`}>模块管理</Link>
        </Space>
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={items}
          onRow={(row: ApiDef) => ({
            onClick: () => onOpen(row.id),
            style: { cursor: 'pointer' },
          })}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: onPage,
          }}
          scroll={{ y: scrollY }}
        />
      </div>
    </div>
  )
}

export default function ApiDefinitionWorkspace() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  const apiIdParam = searchParams.get('apiId')
  const selectedApiId =
    apiIdParam && Number.isFinite(Number(apiIdParam)) ? Number(apiIdParam) : null
  const tab = searchParams.get('tab') || 'design'

  const [search, setSearch] = useState('')
  const [methodFilter, setMethodFilter] = useState<string | undefined>()
  const [moduleFilter, setModuleFilter] = useState<number | 'ungrouped' | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [createOpen, setCreateOpen] = useState(false)
  /** Debug tab only — interface-level ensure-case session */
  const [debugCaseId, setDebugCaseId] = useState<number | null>(null)
  /** Cases-tab drawer — does not overwrite debugCaseId */
  const [drawerCaseId, setDrawerCaseId] = useState<number | null>(null)
  const [drawerCaseName, setDrawerCaseName] = useState<string>('')
  const [stateForApiId, setStateForApiId] = useState(selectedApiId)
  if (stateForApiId !== selectedApiId) {
    setStateForApiId(selectedApiId)
    setDebugCaseId(null)
    setDrawerCaseId(null)
    setDrawerCaseName('')
  }
  const [designForm] = Form.useForm<DesignForm>()
  const [createForm] = Form.useForm<DesignForm>()

  const modulesTree = useQuery({
    queryKey: ['project-modules-tree', pid],
    queryFn: () => apiJson<ModuleNode[]>(`/api/v1/projects/${pid}/modules/tree`),
    enabled: Number.isFinite(pid),
  })

  const moduleTreeOptions = useMemo(
    () => moduleTreeSelectData(modulesTree.data || []),
    [modulesTree.data],
  )

  const list = useQuery({
    queryKey: ['api-definitions', pid, search, methodFilter, moduleFilter, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      if (methodFilter) qs.set('method', methodFilter)
      if (moduleFilter === 'ungrouped') qs.set('ungrouped', 'true')
      else if (typeof moduleFilter === 'number') qs.set('module_id', String(moduleFilter))
      return apiJson<PageRes<ApiDef>>(`/api/v1/projects/${pid}/api-definitions/?${qs}`)
    },
    enabled: Number.isFinite(pid) && selectedApiId == null,
  })

  const detail = useQuery({
    queryKey: ['api-definition', pid, selectedApiId],
    queryFn: () => apiJson<ApiDef>(`/api/v1/projects/${pid}/api-definitions/${selectedApiId}`),
    enabled: Number.isFinite(pid) && selectedApiId != null,
  })

  const selectedDef = detail.data?.id === selectedApiId ? detail.data : undefined

  const cases = useQuery({
    queryKey: ['api-definition-cases', pid, selectedApiId],
    queryFn: () =>
      apiJson<ApiCase[]>(`/api/v1/projects/${pid}/api-definitions/${selectedApiId}/cases`),
    enabled: Number.isFinite(pid) && selectedApiId != null,
  })

  useEffect(() => {
    if (!selectedDef) return
    designForm.setFieldsValue({
      name: selectedDef.name,
      module_id: selectedDef.module_id ?? null,
      description: selectedDef.description,
      method: selectedDef.method,
      path: selectedDef.path,
      body_type: selectedDef.body_type,
      body: selectedDef.body,
      status: selectedDef.status,
    })
  }, [selectedDef, designForm])

  useEffect(() => {
    if (tab !== 'debug' || selectedApiId == null || !Number.isFinite(pid)) {
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const res = await apiJson<{ case_id: number }>(
          `/api/v1/projects/${pid}/api-definitions/${selectedApiId}/ensure-case`,
          { method: 'POST' },
        )
        if (!cancelled) {
          setDebugCaseId(res.case_id)
          qc.invalidateQueries({ queryKey: ['api-definition-cases', pid, selectedApiId] })
        }
      } catch (e) {
        if (!cancelled) message.error(errText(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [tab, selectedApiId, pid, message, qc])

  function openCaseDrawer(caseId: number, name?: string) {
    setDrawerCaseId(caseId)
    setDrawerCaseName(name || `#${caseId}`)
  }

  function selectApi(apiId: number, nextTab?: string) {
    const sp = new URLSearchParams()
    sp.set('apiId', String(apiId))
    if (nextTab && nextTab !== 'design') sp.set('tab', nextTab)
    setSearchParams(sp, { replace: true })
  }

  function backToList() {
    setSearchParams({}, { replace: true })
  }

  function setTab(k: string) {
    const sp = new URLSearchParams(searchParams)
    if (k === 'design') sp.delete('tab')
    else sp.set('tab', k)
    setSearchParams(sp, { replace: true })
  }

  function openCreate() {
    createForm.setFieldsValue({
      name: '',
      module_id: null,
      description: '',
      method: 'GET',
      path: '/',
      body_type: 'none',
      body: '',
      status: 'active',
    })
    setCreateOpen(true)
  }

  const saveDesign = useMutation({
    mutationFn: (v: DesignForm) => {
      if (selectedApiId == null) throw new Error('未选择接口')
      return apiJson<ApiDef>(`/api/v1/projects/${pid}/api-definitions/${selectedApiId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: v.name.trim(),
          module_id: v.module_id ?? null,
          description: v.description || '',
          method: v.method,
          path: v.path.trim(),
          body_type: v.body_type,
          body: v.body || '',
          status: v.status,
        }),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      qc.invalidateQueries({ queryKey: ['api-definition', pid, selectedApiId] })
      message.success('已保存设计')
    },
    onError: (e) => message.error(errText(e)),
  })

  const createDef = useMutation({
    mutationFn: (v: DesignForm) =>
      apiJson<ApiDef>(`/api/v1/projects/${pid}/api-definitions/`, {
        method: 'POST',
        body: JSON.stringify({
          name: v.name.trim(),
          module_id: v.module_id ?? null,
          description: v.description || '',
          method: v.method,
          path: v.path.trim(),
          body_type: v.body_type,
          body: v.body || '',
          status: v.status || 'active',
          query: {},
          headers: {},
        }),
      }),
    onSuccess: (row) => {
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      setCreateOpen(false)
      createForm.resetFields()
      message.success('已创建接口')
      selectApi(row.id, 'design')
    },
    onError: (e) => message.error(errText(e)),
  })

  const createCase = useMutation({
    mutationFn: () => {
      if (selectedApiId == null) throw new Error('未选择接口')
      return apiJson<{ case_id: number }>(
        `/api/v1/projects/${pid}/api-definitions/${selectedApiId}/create-case`,
        { method: 'POST', body: JSON.stringify({ status: 'draft' }) },
      )
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-definition-cases', pid, selectedApiId] })
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      qc.invalidateQueries({ queryKey: ['api-definition', pid, selectedApiId] })
      message.success('已生成用例')
      openCaseDrawer(data.case_id, '新建用例')
    },
    onError: (e) => message.error(errText(e)),
  })

  const generateCases = useMutation({
    mutationFn: () => {
      if (selectedApiId == null) throw new Error('未选择接口')
      return apiJson<{ count: number; case_ids: number[]; names: string[] }>(
        `/api/v1/projects/${pid}/api-definitions/${selectedApiId}/generate-cases`,
        { method: 'POST', body: JSON.stringify({ status: 'draft', max_cases: 12 }) },
      )
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-definition-cases', pid, selectedApiId] })
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      qc.invalidateQueries({ queryKey: ['api-definition', pid, selectedApiId] })
      message.success(`已按设计规则生成 ${data.count} 条用例`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const removeCase = useMutation({
    mutationFn: (caseId: number) =>
      apiFetch(`/api/v1/projects/${pid}/api-cases/${caseId}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-definition-cases', pid, selectedApiId] })
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      qc.invalidateQueries({ queryKey: ['api-definition', pid, selectedApiId] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const removeDef = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/api-definitions/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      qc.invalidateQueries({ queryKey: ['api-definition', pid] })
      setSearchParams({}, { replace: true })
      message.success('已删除接口')
    },
    onError: (e) => message.error(errText(e)),
  })

  const caseCols: ColumnsType<ApiCase> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '名称', dataIndex: 'name', ellipsis: true },
    {
      title: '方法',
      dataIndex: 'method',
      width: 90,
      render: (m: string) => <Tag color={methodColor(m)}>{m}</Tag>,
    },
    { title: '路径', dataIndex: 'path', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 80 },
    {
      title: '操作',
      width: 160,
      render: (_: unknown, row: ApiCase) => (
        <Space>
          <Button type="link" size="small" onClick={() => openCaseDrawer(row.id, row.name)}>
            调试
          </Button>
          <Popconfirm title="删除该用例？" onConfirm={() => removeCase.mutate(row.id)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const createModal = (
    <Modal
      title="新建接口"
      open={createOpen}
      onCancel={() => setCreateOpen(false)}
      footer={null}
      destroyOnHidden
      width={640}
    >
      <Form form={createForm} layout="vertical" onFinish={(v) => createDef.mutate(v)}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="module_id" label="模块">
          <TreeSelect
            allowClear
            placeholder="从模块树选择"
            treeData={moduleTreeOptions}
            treeDefaultExpandAll
            style={{ width: '100%' }}
          />
        </Form.Item>
        <Space style={{ display: 'flex' }} align="start" wrap>
          <Form.Item name="method" label="方法" rules={[{ required: true }]} style={{ width: 120 }}>
            <Select options={METHODS.map((m) => ({ value: m, label: m }))} />
          </Form.Item>
          <Form.Item name="path" label="路径" rules={[{ required: true }]} style={{ flex: 1, minWidth: 320 }}>
            <Input placeholder="/api/users" />
          </Form.Item>
        </Space>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="body_type" label="默认 Body 类型" initialValue="none">
          <Select
            options={[
              { value: 'none', label: 'none' },
              { value: 'json', label: 'json' },
              { value: 'text', label: 'text' },
            ]}
          />
        </Form.Item>
        <Form.Item name="body" label="默认 Body">
          <Input.TextArea rows={4} />
        </Form.Item>
        <Form.Item name="status" initialValue="active" hidden>
          <Input />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={createDef.isPending} block>
          创建
        </Button>
      </Form>
    </Modal>
  )

  if (selectedApiId == null) {
    return (
      <>
        <ApiDefinitionTable
          items={list.data?.items ?? []}
          total={list.data?.total ?? 0}
          loading={list.isLoading}
          error={list.isError ? list.error : null}
          page={page}
          pageSize={pageSize}
          methodFilter={methodFilter}
          moduleFilter={moduleFilter}
          moduleTreeOptions={moduleTreeOptions}
          projectId={pid}
          onSearch={(value) => {
            setSearch(value)
            setPage(1)
          }}
          onMethod={(value) => {
            setMethodFilter(value)
            setPage(1)
          }}
          onModule={(value) => {
            setModuleFilter(value)
            setPage(1)
          }}
          onPage={(p, s) => {
            setPage(p)
            setPageSize(s)
          }}
          onOpen={selectApi}
          onCreate={openCreate}
          onDelete={(id) => removeDef.mutate(id)}
        />
        {createModal}
      </>
    )
  }

  return (
    <div className="api-workspace">
      <div className="api-workspace-banner">
        <PageBanner title="接口管理" />
      </div>
      <div className="api-workspace-body">
        <main className="api-workspace-main">
          {detail.isLoading ? (
            <div className="api-workspace-empty">
              <Spin />
            </div>
          ) : detail.isError || !selectedDef ? (
            <div className="api-workspace-empty" style={{ flexDirection: 'column', gap: 12 }}>
              <Alert
                type="error"
                showIcon
                message="接口加载失败"
                description={detail.isError ? errText(detail.error) : '未找到该接口'}
              />
              <Button onClick={backToList}>返回</Button>
            </div>
          ) : (
            <>
              <div className="api-workspace-header">
                <Space wrap>
                  <Button onClick={backToList}>返回</Button>
                  <Tag color={methodColor(selectedDef.method)}>{selectedDef.method}</Tag>
                  <Typography.Text strong>{selectedDef.name}</Typography.Text>
                  <Typography.Text type="secondary">{selectedDef.path}</Typography.Text>
                </Space>
                <Space>
                  <Popconfirm title="删除该接口定义？" onConfirm={() => removeDef.mutate(selectedDef.id)}>
                    <Button size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              </div>

              <Tabs
                className="api-workspace-tabs"
                activeKey={tab}
                onChange={setTab}
                items={[
                  {
                    key: 'design',
                    label: '设计',
                    children: (
                      <div className="api-workspace-pane">
                        <Form
                          form={designForm}
                          layout="vertical"
                          style={{ maxWidth: 720 }}
                          onFinish={(v) => saveDesign.mutate(v)}
                        >
                          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
                            <Input />
                          </Form.Item>
                          <Form.Item name="module_id" label="模块">
                            <TreeSelect
                              allowClear
                              placeholder="从模块树选择"
                              treeData={moduleTreeOptions}
                              treeDefaultExpandAll
                              style={{ width: '100%' }}
                            />
                          </Form.Item>
                          <Space style={{ display: 'flex' }} align="start" wrap>
                            <Form.Item
                              name="method"
                              label="方法"
                              rules={[{ required: true }]}
                              style={{ width: 120 }}
                            >
                              <Select options={METHODS.map((m) => ({ value: m, label: m }))} />
                            </Form.Item>
                            <Form.Item
                              name="path"
                              label="路径"
                              rules={[{ required: true }]}
                              style={{ flex: 1, minWidth: 320 }}
                            >
                              <Input placeholder="/api/users" />
                            </Form.Item>
                          </Space>
                          <Form.Item name="description" label="说明">
                            <Input.TextArea rows={2} />
                          </Form.Item>
                          <Form.Item name="body_type" label="默认 Body 类型">
                            <Select
                              options={[
                                { value: 'none', label: 'none' },
                                { value: 'json', label: 'json' },
                                { value: 'text', label: 'text' },
                              ]}
                            />
                          </Form.Item>
                          <Form.Item name="body" label="默认 Body">
                            <Input.TextArea rows={8} placeholder="可选，生成用例时作为模板" />
                          </Form.Item>
                          <Form.Item name="status" label="状态">
                            <Select
                              options={[
                                { value: 'active', label: '启用' },
                                { value: 'deprecated', label: '废弃' },
                              ]}
                            />
                          </Form.Item>
                          <Button type="primary" htmlType="submit" loading={saveDesign.isPending}>
                            保存设计
                          </Button>
                        </Form>
                      </div>
                    ),
                  },
                  {
                    key: 'debug',
                    label: '调试',
                    children: (
                      <div className="api-workspace-pane api-workspace-pane-debug">
                        {debugCaseId != null ? (
                          <ApiRequestWorkbench
                            projectId={pid}
                            caseId={debugCaseId}
                            apiId={selectedApiId}
                            onCaseSaved={(id) => setDebugCaseId(id)}
                          />
                        ) : (
                          <Empty description="正在准备调试用例…" style={{ marginTop: 48 }} />
                        )}
                      </div>
                    ),
                  },
                  {
                    key: 'cases',
                    label: `接口用例 (${cases.data?.length ?? selectedDef.case_count})`,
                    children: (
                      <div className="api-workspace-pane">
                        <Space style={{ marginBottom: 12 }} wrap>
                          <Button
                            type="primary"
                            loading={generateCases.isPending}
                            onClick={() => generateCases.mutate()}
                          >
                            一键生成单接口用例
                          </Button>
                          <Button loading={createCase.isPending} onClick={() => createCase.mutate()}>
                            新建空用例
                          </Button>
                          <Button onClick={() => nav(`/projects/${pid}/api-cases?apiId=${selectedApiId}`)}>
                            在单接口中查看
                          </Button>
                        </Space>
                        <Table
                          rowKey="id"
                          size="middle"
                          loading={cases.isLoading}
                          columns={caseCols}
                          dataSource={cases.data ?? []}
                          pagination={false}
                        />
                      </div>
                    ),
                  },
                ]}
              />
            </>
          )}
        </main>
      </div>

      <Drawer
        title={`调试用例：${drawerCaseName || ''}`}
        open={drawerCaseId != null}
        onClose={() => {
          setDrawerCaseId(null)
          setDrawerCaseName('')
        }}
        width="min(920px, 92vw)"
        destroyOnHidden
        styles={{ body: { padding: 0, height: '100%', display: 'flex', flexDirection: 'column' } }}
      >
        {drawerCaseId != null && (
          <div className="api-case-drawer-workbench">
            <ApiRequestWorkbench
              projectId={pid}
              caseId={drawerCaseId}
              apiId={selectedApiId}
              onCaseSaved={(id) => {
                openCaseDrawer(id, drawerCaseName)
                qc.invalidateQueries({ queryKey: ['api-definition-cases', pid, selectedApiId] })
              }}
            />
          </div>
        )}
      </Drawer>
    </div>
  )
}
