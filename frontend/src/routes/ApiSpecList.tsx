import {
  Alert,
  App,
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type Spec = {
  id: number
  name: string
  source_type: string
  summary_json: { title?: string; endpoint_count?: number; base_url?: string } | null
}

type FieldRow = {
  name: string
  type?: string
  required?: boolean
  description?: string
  example?: unknown
  in?: string
}

type ResponseDoc = {
  status: string
  description?: string
  fields?: FieldRow[]
  sample?: unknown
}

type Endpoint = {
  id: number
  method: string
  path: string
  summary: string | null
  operation_id: string | null
  tags_json: string[] | null
  request_sample_json: {
    body_type?: string
    body?: unknown
    query?: Record<string, string>
    path_params?: Record<string, string>
    headers?: Record<string, string>
    parameters?: FieldRow[]
    request_fields?: FieldRow[]
    responses?: ResponseDoc[]
  } | null
}
type PageRes<T> = { items: T[]; total: number; page?: number; page_size?: number }

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    return (ex.body as { message?: string }).message ?? '请求失败'
  }
  return '请求失败'
}

function methodColor(m: string): string {
  if (m === 'GET') return 'blue'
  if (m === 'POST') return 'green'
  if (m === 'PUT' || m === 'PATCH') return 'orange'
  if (m === 'DELETE') return 'red'
  return 'default'
}

function formatExample(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

const fieldColumns: ColumnsType<FieldRow> = [
  { title: '名称', dataIndex: 'name', width: 140, ellipsis: true },
  { title: '类型', dataIndex: 'type', width: 110, ellipsis: true, render: (v) => v || '—' },
  {
    title: '必填',
    dataIndex: 'required',
    width: 60,
    render: (v: boolean | undefined) => (v ? '是' : '否'),
  },
  { title: '说明', dataIndex: 'description', ellipsis: true, render: (v) => v || '—' },
  {
    title: '示例',
    dataIndex: 'example',
    width: 140,
    ellipsis: true,
    render: (v) => formatExample(v),
  },
]

const paramColumns: ColumnsType<FieldRow> = [
  { title: '位置', dataIndex: 'in', width: 70, render: (v) => v || '—' },
  ...fieldColumns,
]

function emptyHint(text: string) {
  return <Typography.Text type="secondary">{text}</Typography.Text>
}

export default function ApiSpecList() {
  const { containerRef, scrollY } = useTableScrollY()
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [importOpen, setImportOpen] = useState(false)
  const [name, setName] = useState('OpenAPI')
  const [content, setContent] = useState('')
  const [activeSpec, setActiveSpec] = useState<number | null>(null)
  const [selectedEps, setSelectedEps] = useState<number[]>([])
  const [detail, setDetail] = useState<Endpoint | null>(null)

  const [specSearch, setSpecSearch] = useState('')
  const [specSearchInput, setSpecSearchInput] = useState('')
  const [specPage, setSpecPage] = useState(1)
  const [specPageSize, setSpecPageSize] = useState(20)

  const [epSearch, setEpSearch] = useState('')
  const [epSearchInput, setEpSearchInput] = useState('')
  const [epMethod, setEpMethod] = useState<string | undefined>()
  const [epTag, setEpTag] = useState<string | undefined>()
  const [epPage, setEpPage] = useState(1)
  const [epPageSize, setEpPageSize] = useState(20)

  const specs = useQuery({
    queryKey: ['api-specs', pid, specSearch, specPage, specPageSize],
    queryFn: () => {
      const qs = new URLSearchParams({
        page: String(specPage),
        page_size: String(specPageSize),
      })
      if (specSearch.trim()) qs.set('search', specSearch.trim())
      return apiJson<PageRes<Spec>>(`/api/v1/projects/${pid}/api-specs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const endpoints = useQuery({
    queryKey: ['api-spec-eps', pid, activeSpec, epSearch, epMethod, epTag, epPage, epPageSize],
    queryFn: () => {
      const qs = new URLSearchParams({
        page: String(epPage),
        page_size: String(epPageSize),
      })
      if (epSearch.trim()) qs.set('search', epSearch.trim())
      if (epMethod) qs.set('method', epMethod)
      if (epTag) qs.set('tag', epTag)
      return apiJson<PageRes<Endpoint>>(
        `/api/v1/projects/${pid}/api-specs/${activeSpec}/endpoints?${qs}`,
      )
    },
    enabled: Number.isFinite(pid) && activeSpec != null,
  })

  const epTags = useQuery({
    queryKey: ['api-spec-ep-tags', pid, activeSpec],
    queryFn: () =>
      apiJson<string[]>(`/api/v1/projects/${pid}/api-specs/${activeSpec}/endpoint-tags`),
    enabled: Number.isFinite(pid) && activeSpec != null,
  })

  function selectSpec(id: number) {
    setActiveSpec(id)
    setSelectedEps([])
    setDetail(null)
    setEpSearch('')
    setEpSearchInput('')
    setEpMethod(undefined)
    setEpTag(undefined)
    setEpPage(1)
  }

  function resetEpFilters(patch?: {
    search?: string
    method?: string | undefined
    tag?: string | undefined
  }) {
    setSelectedEps([])
    setEpPage(1)
    if (patch?.search !== undefined) setEpSearch(patch.search)
    if (patch && 'method' in patch) setEpMethod(patch.method)
    if (patch && 'tag' in patch) setEpTag(patch.tag)
  }

  const importSpec = useMutation({
    mutationFn: () =>
      apiJson<Spec>(`/api/v1/projects/${pid}/api-specs/import`, {
        method: 'POST',
        body: JSON.stringify({ name, content }),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-specs', pid] })
      setImportOpen(false)
      setContent('')
      selectSpec(data.id)
      message.success('已导入接口文档')
    },
    onError: (e) => message.error(errText(e)),
  })

  const toDefs = useMutation({
    mutationFn: () =>
      apiJson<{ count: number; created: number; updated: number }>(
        `/api/v1/projects/${pid}/api-specs/${activeSpec}/sync-definitions`,
        {
          method: 'POST',
          body: JSON.stringify({ endpoint_ids: selectedEps, status: 'draft' }),
        },
      ),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      setSelectedEps([])
      message.success(`已同步到接口管理：新增 ${data.created} / 更新 ${data.updated}`)
      nav(`/projects/${pid}/api-definitions`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const oneClickImport = useMutation({
    mutationFn: () =>
      apiJson<{ count: number; created: number; updated: number }>(
        `/api/v1/projects/${pid}/api-definitions/sync-from-spec`,
        {
          method: 'POST',
          body: JSON.stringify({ spec_id: activeSpec }),
        },
      ),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-definitions', pid] })
      setSelectedEps([])
      message.success(`已同步到接口管理：新增 ${data.created} / 更新 ${data.updated}`)
      nav(`/projects/${pid}/api-definitions`)
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/api-specs/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-specs', pid] })
      if (activeSpec) setActiveSpec(null)
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const reparse = useMutation({
    mutationFn: (id: number) =>
      apiJson<Spec>(`/api/v1/projects/${pid}/api-specs/${id}/reparse`, { method: 'POST', body: '{}' }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-specs', pid] })
      qc.invalidateQueries({ queryKey: ['api-spec-eps', pid, data.id] })
      qc.invalidateQueries({ queryKey: ['api-spec-ep-tags', pid, data.id] })
      setActiveSpec(data.id)
      setDetail(null)
      message.success('已重新解析接口详情（参数 / 字段表 / 响应）')
    },
    onError: (e) => message.error(errText(e)),
  })

  const specCols: ColumnsType<Spec> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '摘要',
      render: (_, r) =>
        `${r.summary_json?.title || ''} · ${r.summary_json?.endpoint_count ?? 0} endpoints`,
    },
    {
      title: '操作',
      width: 260,
      render: (_, r) => (
        <Space
          style={{ position: 'relative', zIndex: 1 }}
          onClick={(e) => e.stopPropagation()}
        >
          <Button
            type="link"
            onClick={(e) => {
              e.stopPropagation()
              selectSpec(r.id)
            }}
          >
            查看接口
          </Button>
          <Button
            type="link"
            loading={reparse.isPending}
            onClick={(e) => {
              e.stopPropagation()
              reparse.mutate(r.id)
            }}
          >
            刷新详情
          </Button>
          <Button
            type="link"
            danger
            onClick={(e) => {
              e.stopPropagation()
              remove.mutate(r.id)
            }}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ]

  const activeSpecRow = specs.data?.items?.find((s) => s.id === activeSpec)

  const epCols: ColumnsType<Endpoint> = [
    {
      title: '方法',
      dataIndex: 'method',
      width: 90,
      render: (m: string) => <Tag color={methodColor(m)}>{m}</Tag>,
    },
    { title: '路径', dataIndex: 'path' },
    { title: '说明', dataIndex: 'summary', ellipsis: true },
    {
      title: '标签',
      width: 140,
      render: (_, r) =>
        (r.tags_json ?? []).length ? (
          <Space size={[4, 4]} wrap>
            {(r.tags_json ?? []).map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </Space>
        ) : (
          '—'
        ),
    },
    {
      title: '详情',
      width: 80,
      render: (_, r) => (
        <Button type="link" onClick={() => setDetail(r)}>
          查看
        </Button>
      ),
    },
  ]

  const sample = detail?.request_sample_json

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="接口资产库" />
        {activeSpec == null ? (
          <Space style={{ marginBottom: 16 }} wrap>
            <Button type="primary" onClick={() => setImportOpen(true)}>
              导入 OpenAPI / Swagger
            </Button>
            <Input.Search
              allowClear
              placeholder="搜索文档名称"
              value={specSearchInput}
              onChange={(e) => setSpecSearchInput(e.target.value)}
              onSearch={(v) => {
                setSpecSearch(v)
                setSpecPage(1)
              }}
              style={{ width: 240 }}
            />
          </Space>
        ) : (
          <Space style={{ marginBottom: 12 }} wrap>
            <Button
              onClick={() => {
                setActiveSpec(null)
                setSelectedEps([])
                setDetail(null)
              }}
            >
              ← 返回文档列表
            </Button>
            <strong>
              {activeSpecRow?.name || `文档 #${activeSpec}`} · 接口列表
            </strong>
            <Button loading={reparse.isPending} onClick={() => activeSpec != null && reparse.mutate(activeSpec)}>
              刷新详情字段
            </Button>
            <Button
              disabled={!selectedEps.length}
              loading={toDefs.isPending}
              onClick={() => toDefs.mutate()}
            >
              同步到接口管理（{selectedEps.length}）
            </Button>
            <Button
              type="primary"
              loading={oneClickImport.isPending}
              onClick={() => oneClickImport.mutate()}
            >
              一键导入
            </Button>
          </Space>
        )}
      </div>

      <div
        className="list-page-table"
        ref={containerRef}
        style={{ display: activeSpec == null ? undefined : 'none' }}
      >
        <Table
          rowKey="id"
          loading={specs.isLoading}
          columns={specCols}
          dataSource={specs.data?.items ?? []}
          scroll={{ y: scrollY }}
          pagination={{
            current: specPage,
            pageSize: specPageSize,
            total: specs.data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 份文档`,
            onChange: (page, size) => {
              setSpecPage(page)
              setSpecPageSize(size)
            },
          }}
        />
      </div>

      {activeSpec != null && (
        <div className="list-page-table" style={{ overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <Space style={{ marginBottom: 12, flexShrink: 0 }} wrap>
            <Input.Search
              allowClear
              placeholder="搜索 path / 说明 / operationId"
              value={epSearchInput}
              onChange={(e) => setEpSearchInput(e.target.value)}
              onSearch={(v) => resetEpFilters({ search: v })}
              style={{ width: 280 }}
            />
            <Select
              allowClear
              placeholder="方法"
              value={epMethod}
              onChange={(v) => resetEpFilters({ method: v })}
              options={METHODS.map((m) => ({ value: m, label: m }))}
              style={{ width: 120 }}
            />
            <Select
              allowClear
              showSearch
              placeholder="标签"
              value={epTag}
              onChange={(v) => resetEpFilters({ tag: v })}
              options={(epTags.data ?? []).map((t) => ({ value: t, label: t }))}
              style={{ width: 160 }}
              optionFilterProp="label"
            />
          </Space>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <Table
              rowKey="id"
              loading={endpoints.isLoading}
              columns={epCols}
              dataSource={endpoints.data?.items ?? []}
              rowSelection={{
                selectedRowKeys: selectedEps,
                onChange: (keys) => setSelectedEps(keys as number[]),
              }}
              pagination={{
                current: epPage,
                pageSize: epPageSize,
                total: endpoints.data?.total ?? 0,
                showSizeChanger: true,
                showTotal: (t) => `共 ${t} 个接口`,
                onChange: (page, size) => {
                  setSelectedEps([])
                  setEpPage(page)
                  setEpPageSize(size)
                },
              }}
              onRow={(row) => ({
                onDoubleClick: () => setDetail(row),
              })}
            />
          </div>
        </div>
      )}

      <Drawer
        title={detail ? `${detail.method} ${detail.path}` : '接口详情'}
        open={detail != null}
        onClose={() => setDetail(null)}
        width={720}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="说明">{detail.summary || '—'}</Descriptions.Item>
              <Descriptions.Item label="operationId">{detail.operation_id || '—'}</Descriptions.Item>
              <Descriptions.Item label="标签">
                {(detail.tags_json ?? []).length
                  ? (detail.tags_json ?? []).map((t) => <Tag key={t}>{t}</Tag>)
                  : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Body 类型">{sample?.body_type || 'none'}</Descriptions.Item>
            </Descriptions>

            <Typography.Title level={5}>参数</Typography.Title>
            {(sample?.parameters?.length ?? 0) > 0 ? (
              <Table
                size="small"
                rowKey={(r) => `${r.in}-${r.name}`}
                columns={paramColumns}
                dataSource={sample?.parameters ?? []}
                pagination={false}
                style={{ marginBottom: 16 }}
                scroll={{ x: 640 }}
              />
            ) : (
              <div style={{ marginBottom: 16 }}>{emptyHint('无 Path / Query / Header 参数')}</div>
            )}

            <Typography.Title level={5}>请求体字段</Typography.Title>
            {(sample?.request_fields?.length ?? 0) > 0 ? (
              <Table
                size="small"
                rowKey={(r) => r.name}
                columns={fieldColumns}
                dataSource={sample?.request_fields ?? []}
                pagination={false}
                style={{ marginBottom: 12 }}
                scroll={{ x: 560 }}
              />
            ) : (
              <div style={{ marginBottom: 12 }}>{emptyHint('无请求体字段说明')}</div>
            )}
            {sample?.body != null && (
              <Collapse
                style={{ marginBottom: 16 }}
                items={[
                  {
                    key: 'body',
                    label: '请求 Body 示例',
                    children: (
                      <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 280, overflow: 'auto' }}>
                        {typeof sample.body === 'string' ? sample.body : JSON.stringify(sample.body, null, 2)}
                      </pre>
                    ),
                  },
                ]}
              />
            )}

            <Typography.Title level={5}>响应</Typography.Title>
            {(sample?.responses?.length ?? 0) > 0 ? (
              <Tabs
                items={(sample?.responses ?? []).map((resp) => ({
                  key: resp.status,
                  label: resp.status,
                  children: (
                    <div>
                      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                        {resp.description || '无说明'}
                      </Typography.Paragraph>
                      {(resp.fields?.length ?? 0) > 0 ? (
                        <Table
                          size="small"
                          rowKey={(r) => r.name}
                          columns={fieldColumns}
                          dataSource={resp.fields ?? []}
                          pagination={false}
                          style={{ marginBottom: 12 }}
                          scroll={{ x: 560 }}
                        />
                      ) : (
                        <div style={{ marginBottom: 12 }}>{emptyHint('无响应字段说明')}</div>
                      )}
                      {resp.sample != null && (
                        <Collapse
                          items={[
                            {
                              key: 'sample',
                              label: '响应示例',
                              children: (
                                <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}>
                                  {typeof resp.sample === 'string'
                                    ? resp.sample
                                    : JSON.stringify(resp.sample, null, 2)}
                                </pre>
                              ),
                            },
                          ]}
                        />
                      )}
                    </div>
                  ),
                }))}
              />
            ) : (
              <Alert
                type="warning"
                showIcon
                message="暂无响应信息"
                description="点文档操作里的「刷新详情」或「刷新详情字段」，用已保存原文重新解析即可，无需重新上传。"
              />
            )}
          </>
        )}
      </Drawer>

      <Modal
        title="导入 OpenAPI 3 / Swagger 2"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={() => importSpec.mutate()}
        confirmLoading={importSpec.isPending}
        width={720}
      >
        <Input style={{ marginBottom: 12 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="文档名称" />
        <Upload
          beforeUpload={async (file) => {
            const text = await file.text()
            setContent(text)
            if (!name || name === 'OpenAPI') setName(file.name)
            return false
          }}
          maxCount={1}
          accept=".json,.yaml,.yml"
        >
          <Button>选择文件</Button>
        </Upload>
        <Input.TextArea
          style={{ marginTop: 12 }}
          rows={12}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="或粘贴 OpenAPI 3 / Swagger 2 的 JSON / YAML"
        />
      </Modal>
    </div>
  )
}
