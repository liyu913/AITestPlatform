import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type AppElement = {
  id: number
  page_name: string
  name: string
  strategy: string
  selector: string
  description: string
}
type PageRes<T> = { items: T[]; total: number }

const STRATEGIES = [
  { value: 'id', label: 'id' },
  { value: 'accessibility_id', label: 'accessibility_id' },
  { value: 'xpath', label: 'xpath' },
  { value: 'class_name', label: 'class_name' },
  { value: 'android_uiautomator', label: 'android_uiautomator' },
  { value: 'ios_predicate', label: 'ios_predicate' },
  { value: 'ios_class_chain', label: 'ios_class_chain' },
]

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function AppElementList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AppElement | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<AppElement>()

  const list = useQuery({
    queryKey: ['app-elements', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<AppElement>>(`/api/v1/projects/${pid}/app-elements/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })

  const save = useMutation({
    mutationFn: async (values: AppElement) => {
      const payload = {
        page_name: values.page_name?.trim() || '',
        name: values.name.trim(),
        strategy: values.strategy || 'id',
        selector: values.selector.trim(),
        description: values.description || '',
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/app-elements/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/app-elements/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-elements', pid] })
      setOpen(false)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/app-elements/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-elements', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<AppElement> = [
    { title: '页面', dataIndex: 'page_name', width: 140 },
    { title: '名称', dataIndex: 'name', width: 140 },
    { title: '策略', dataIndex: 'strategy', width: 140 },
    { title: '定位器', dataIndex: 'selector', ellipsis: true },
    {
      title: '操作',
      width: 140,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setEditing(row)
              form.setFieldsValue(row)
              setOpen(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm title="删除？" onConfirm={() => remove.mutate(row.id)}>
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
      <PageBanner title="APP 元素仓库" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            form.setFieldsValue({
              page_name: '',
              name: '',
              strategy: 'id',
              selector: '',
              description: '',
            })
            setOpen(true)
          }}
        >
          新建元素
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索名称/定位器"
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
      </div>
      <Modal
        title={editing ? '编辑元素' : '新建元素'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="page_name" label="页面">
            <Input placeholder="LoginPage" />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="username" />
          </Form.Item>
          <Form.Item name="strategy" label="定位策略" rules={[{ required: true }]}>
            <Select options={STRATEGIES} />
          </Form.Item>
          <Form.Item name="selector" label="定位器" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
