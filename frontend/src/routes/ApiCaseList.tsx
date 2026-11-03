import {
  App,
  Alert,
  Button,
  Drawer,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import ApiCaseEditAgentPanel from '../components/ApiCaseEditAgentPanel'
import ApiRequestWorkbench from '../components/ApiRequestWorkbench'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type ApiCase = {
  id: number
  name: string
  method: string
  path: string
  status: string
}

type ApiEnv = { id: number; name: string; is_default: boolean }
type PageRes<T> = { items: T[]; total: number }
type ApiRun = { id: number; status: string }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    return (ex.body as { message?: string }).message ?? '请求失败'
  }
  return '请求失败'
}

function statusLabel(status: string): string {
  if (status === 'ready') return '就绪'
  if (status === 'draft') return '草稿'
  return status
}

export default function ApiCaseList() {
  const { containerRef, scrollY } = useTableScrollY()
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const qc = useQueryClient()
  const [caseSearch, setCaseSearch] = useState('')
  const [casePage, setCasePage] = useState(1)
  const [casePageSize, setCasePageSize] = useState(20)
  const [postmanHintOpen, setPostmanHintOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [debugId, setDebugId] = useState<number | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [editCaseIds, setEditCaseIds] = useState<number[]>([])
  const [envId, setEnvId] = useState<number | undefined>()
  const [runningId, setRunningId] = useState<number | null>(null)

  const apiIdFilter = searchParams.get('apiId')

  const list = useQuery({
    queryKey: ['api-cases', pid, apiIdFilter, caseSearch, casePage, casePageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(casePage), page_size: String(casePageSize) })
      if (apiIdFilter) qs.set('api_id', apiIdFilter)
      if (caseSearch.trim()) qs.set('search', caseSearch.trim())
      return apiJson<PageRes<ApiCase>>(`/api/v1/projects/${pid}/api-cases/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const envs = useQuery({
    queryKey: ['api-envs', pid, 'select'],
    queryFn: () => apiJson<PageRes<ApiEnv>>(`/api/v1/projects/${pid}/api-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  useEffect(() => {
    if (envId != null) return
    const items = envs.data?.items ?? []
    const preferred = items.find((e) => e.is_default) ?? items[0]
    if (preferred) setEnvId(preferred.id)
  }, [envs.data, envId])

  useEffect(() => {
    const raw = searchParams.get('caseId')
    if (!raw) return
    const id = Number(raw)
    if (!Number.isFinite(id)) return
    setDebugId(id)
    const next = new URLSearchParams(searchParams)
    next.delete('caseId')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/api-cases/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['api-cases', pid] })
      setSelectedIds((prev) => prev.filter((x) => x !== id))
      if (debugId === id) setDebugId(null)
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const runOne = useMutation({
    mutationFn: async (caseId: number) => {
      if (envId == null) throw new Error('请先选择环境')
      setRunningId(caseId)
      return apiJson<ApiRun>(`/api/v1/projects/${pid}/api-cases/${caseId}/run`, {
        method: 'POST',
        body: JSON.stringify({ env_id: envId, sync: true }),
      })
    },
    onSuccess: (data) => {
      message.success(`执行完成：${data.status}`)
      nav(`/projects/${pid}/api-runs/${data.id}`)
    },
    onError: (e) => message.error(e instanceof Error && !(e instanceof ApiError) ? e.message : errText(e)),
    onSettled: () => setRunningId(null),
  })

  const runBatch = useMutation({
    mutationFn: async (ids: number[]) => {
      if (envId == null) throw new Error('请先选择环境')
      if (ids.length === 0) throw new Error('请至少选择一条单接口用例')
      if (ids.length > 50) throw new Error('一次最多执行 50 条单接口用例')
      return apiJson<ApiRun>(`/api/v1/projects/${pid}/api-cases/batch-run`, {
        method: 'POST',
        body: JSON.stringify({ case_ids: ids, env_id: envId, sync: true }),
      })
    },
    onSuccess: (data) => {
      message.success(`批量执行完成：${data.status}`)
      nav(`/projects/${pid}/api-runs/${data.id}`)
    },
    onError: (e) => message.error(e instanceof Error && !(e instanceof ApiError) ? e.message : errText(e)),
  })

  async function createBlank() {
    try {
      const row = await apiJson<ApiCase>(`/api/v1/projects/${pid}/api-cases/`, {
        method: 'POST',
        body: JSON.stringify({
          name: '未命名请求',
          method: 'GET',
          path: '/',
          body_type: 'none',
          body: '',
          status: 'draft',
          ...(apiIdFilter ? { api_id: Number(apiIdFilter) } : {}),
        }),
      })
      qc.invalidateQueries({ queryKey: ['api-cases', pid] })
      setDebugId(row.id)
      message.success('已新建')
    } catch (e) {
      message.error(errText(e))
    }
  }

  const columns: ColumnsType<ApiCase> = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    {
      title: '方法',
      dataIndex: 'method',
      width: 90,
      render: (method: string) => <Tag>{method}</Tag>,
    },
    { title: '路径', dataIndex: 'path', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status: string) => <Tag color={status === 'ready' ? 'success' : 'default'}>{statusLabel(status)}</Tag>,
    },
    {
      title: '操作',
      width: 200,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            loading={runningId === row.id}
            disabled={envId == null || runBatch.isPending}
            onClick={() => runOne.mutate(row.id)}
          >
            执行
          </Button>
          <Button type="link" onClick={() => setDebugId(row.id)}>
            调试
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

  const envOptions = (envs.data?.items ?? []).map((e) => ({
    value: e.id,
    label: e.is_default ? `${e.name}（默认）` : e.name,
  }))

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="单接口用例" />
        {apiIdFilter && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <span>
                仅显示该接口下的单接口用例。 <Link to={`/projects/${pid}/api-cases`}>查看全部</Link>
              </span>
            }
          />
        )}
        <Space style={{ marginBottom: 12 }} wrap>
          <Button type="primary" onClick={() => createBlank()}>
            新建请求
          </Button>
          <Select
            placeholder="选择环境"
            style={{ minWidth: 200 }}
            value={envId}
            onChange={(v) => setEnvId(v)}
            options={envOptions}
            loading={envs.isLoading}
          />
          <Button
            disabled={selectedIds.length === 0 || envId == null}
            loading={runBatch.isPending}
            onClick={() => runBatch.mutate(selectedIds)}
          >
            批量执行{selectedIds.length > 0 ? `（${selectedIds.length}）` : ''}
          </Button>
          <Button
            disabled={selectedIds.length === 0}
            onClick={() => {
              if (selectedIds.length > 20) {
                message.warning('一次最多修订 20 条，请收窄选择')
                return
              }
              setEditCaseIds([...selectedIds])
              setEditOpen(true)
            }}
          >
            AI 批量修改{selectedIds.length > 0 ? `（${selectedIds.length}）` : ''}
          </Button>
          <Upload
            showUploadList={false}
            beforeUpload={async (file) => {
              try {
                const text = await file.text()
                const res = await apiJson<{ count: number }>(`/api/v1/projects/${pid}/api-postman/import`, {
                  method: 'POST',
                  body: JSON.stringify({ content: text, create_cases: true }),
                })
                qc.invalidateQueries({ queryKey: ['api-cases', pid] })
                message.success(`Postman 导入 ${res.count} 条`)
              } catch (e) {
                message.error(errText(e))
              }
              return false
            }}
            accept=".json"
          >
            <Button>导入 Postman</Button>
          </Upload>
          <Button type="link" onClick={() => setPostmanHintOpen((v) => !v)}>
            {postmanHintOpen ? '收起说明' : 'Postman 兼容说明'}
          </Button>
          <Input.Search
            allowClear
            placeholder="搜索名称"
            style={{ width: 220 }}
            onSearch={(v) => {
              setCaseSearch(v)
              setCasePage(1)
            }}
          />
        </Space>
        {postmanHintOpen && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="Postman Collection v2.1 支持范围"
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                <li>支持：文件夹嵌套、method/path/query/header、raw JSON/text、urlencoded body</li>
                <li>不导入：Auth、Tests/Pre-request、环境变量文件、form-data 文件上传</li>
              </ul>
            }
          />
        )}
      </div>
      <div className="list-page-table" ref={containerRef}>
        <Table<ApiCase>
          rowKey="id"
          loading={list.isLoading}
          columns={columns}
          dataSource={list.data?.items ?? []}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys.map((k) => Number(k))),
          }}
          pagination={{
            current: casePage,
            pageSize: casePageSize,
            total: list.data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, s) => {
              setCasePage(p)
              setCasePageSize(s)
            },
          }}
          scroll={{ y: scrollY }}
        />
      </div>
      <Drawer
        title="调试"
        open={debugId != null}
        onClose={() => setDebugId(null)}
        width={960}
        destroyOnHidden
        styles={{ body: { padding: 0, height: 'calc(100vh - 55px)', display: 'flex', flexDirection: 'column' } }}
      >
        {debugId != null && (
          <ApiRequestWorkbench
            projectId={pid}
            caseId={debugId}
            apiId={apiIdFilter ? Number(apiIdFilter) : null}
            preferredEnvId={envId ?? null}
            onCaseSaved={(id) => {
              setDebugId(id)
              qc.invalidateQueries({ queryKey: ['api-cases', pid] })
            }}
          />
        )}
      </Drawer>
      <Modal
        title="AI 批量修改"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        footer={null}
        width="min(1200px, 96vw)"
        destroyOnHidden
        styles={{
          body: {
            padding: 12,
            height: 'calc(100vh - 120px)',
            maxHeight: 820,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          },
        }}
      >
        <ApiCaseEditAgentPanel
          projectId={pid}
          caseIds={editCaseIds}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          onApplied={() => {
            qc.invalidateQueries({ queryKey: ['api-cases', pid] })
          }}
        />
      </Modal>
    </div>
  )
}
