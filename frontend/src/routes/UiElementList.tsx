import { App, Button, Form, Input, Modal, Popconfirm, Space, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type UiElement = {
  id: number
  page_name: string
  name: string
  selector: string
  description: string
}
type PageRes<T> = { items: T[]; total: number }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function UiElementList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<UiElement | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<UiElement>()

  const list = useQuery({
    queryKey: ['ui-elements', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<UiElement>>(`/api/v1/projects/${pid}/ui-elements/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const save = useMutation({
    mutationFn: async (values: UiElement) => {
      const payload = {
        page_name: values.page_name?.trim() || '',
        name: values.name.trim(),
        selector: values.selector.trim(),
        description: values.description || '',
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/ui-elements/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/ui-elements/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-elements', pid] })
      setOpen(false)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/ui-elements/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-elements', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<UiElement> = [
    { title: '页面', dataIndex: 'page_name', width: 140 },
    { title: '名称', dataIndex: 'name', width: 140 },
    {
      title: '引用',
      width: 180,
      render: (_, r) => (
        <code>
          {r.page_name ? `${r.page_name}.${r.name}` : r.name}
        </code>
      ),
    },
    { title: '选择器', dataIndex: 'selector', ellipsis: true },
    {
      title: '操作',
      width: 140,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            onClick={() => {
              setEditing(row)
              form.setFieldsValue(row)
              setOpen(true)
            }}
          >
            编辑
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
      <PageBanner title="元素仓库" />
      <p style={{ color: '#666', marginBottom: 12 }}>
        步骤中可用 <code>element: &quot;LoginPage.username&quot;</code> 或选择器{' '}
        <code>@LoginPage.username</code> 引用。
      </p>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            form.resetFields()
            setOpen(true)
          }}
        >
          新建元素
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索名称/选择器"
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
       scroll={{ y: scrollY }} />
      </div>
      <Modal title={editing ? '编辑元素' : '新建元素'} open={open} onCancel={() => setOpen(false)} footer={null} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v as UiElement)}>
          <Form.Item name="page_name" label="页面名">
            <Input placeholder="LoginPage" />
          </Form.Item>
          <Form.Item name="name" label="元素名" rules={[{ required: true }]}>
            <Input placeholder="username" />
          </Form.Item>
          <Form.Item name="selector" label="选择器" rules={[{ required: true }]}>
            <Input placeholder="#username 或 [data-testid=user]" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
