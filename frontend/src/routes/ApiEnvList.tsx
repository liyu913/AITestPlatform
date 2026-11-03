import {
  App,
  Button,
  Empty,
  Input,
  Popconfirm,
  Space,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import ApiEnvEditModal, { type ApiEnvRecord } from '../components/ApiEnvEditModal'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function ApiEnvList(_props: { embedded?: boolean } = {}) {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<ApiEnvRecord | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const list = useQuery({
    queryKey: ['api-envs', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<ApiEnvRecord>>(`/api/v1/projects/${pid}/api-envs/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/api-envs/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        throw new ApiError(res.status, await res.json().catch(() => null))
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-envs', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  function openCreate() {
    setEditing(null)
    setOpen(true)
  }

  function openEdit(row: ApiEnvRecord) {
    setEditing(row)
    setOpen(true)
  }

  const columns: ColumnsType<ApiEnvRecord> = [
    { title: '名称', dataIndex: 'name' },
    { title: 'Base URL', dataIndex: 'base_url', ellipsis: true },
    {
      title: '默认',
      dataIndex: 'is_default',
      width: 80,
      render: (v: boolean) => (v ? '是' : ''),
    },
    {
      title: '密钥键',
      dataIndex: 'secret_keys',
      render: (keys: string[]) => (keys?.length ? keys.join(', ') : '—'),
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

  const empty = !list.isLoading && (list.data?.items.length ?? 0) === 0 && !search.trim()

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="接口环境" />
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
        {empty ? (
          <Empty description="暂无环境" />
        ) : (
          <Table<ApiEnvRecord>
            rowKey="id"
            loading={list.isLoading}
            columns={columns}
            dataSource={list.data?.items ?? []}
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
            scroll={{ y: scrollY }}
          />
        )}
      </div>
      <ApiEnvEditModal
        open={open}
        onClose={() => {
          setOpen(false)
          setEditing(null)
        }}
        projectId={pid}
        env={editing}
        defaultAsDefault={!(list.data?.items.some((e) => e.is_default))}
      />
    </div>
  )
}
