import { App, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type UiSchedule = {
  id: number
  name: string
  suite_id: number
  env_id: number | null
  cron_expr: string
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
}
type UiSuite = { id: number; name: string }
type UiEnv = { id: number; name: string }
type PageRes<T> = { items: T[]; total: number }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function UiScheduleList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<UiSchedule | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<{
    name: string
    suite_id: number
    env_id?: number
    cron_expr: string
    enabled: boolean
  }>()

  const list = useQuery({
    queryKey: ['ui-schedules', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<UiSchedule>>(`/api/v1/projects/${pid}/ui-schedules/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })
  const suites = useQuery({
    queryKey: ['ui-suites', pid, 'select'],
    queryFn: () => apiJson<PageRes<UiSuite>>(`/api/v1/projects/${pid}/ui-suites/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })
  const envs = useQuery({
    queryKey: ['ui-envs', pid, 'select'],
    queryFn: () => apiJson<PageRes<UiEnv>>(`/api/v1/projects/${pid}/ui-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const save = useMutation({
    mutationFn: async (values: {
      name: string
      suite_id: number
      env_id?: number
      cron_expr: string
      enabled: boolean
    }) => {
      const payload = {
        name: values.name.trim(),
        suite_id: values.suite_id,
        env_id: values.env_id ?? null,
        cron_expr: values.cron_expr.trim(),
        enabled: Boolean(values.enabled),
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/ui-schedules/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/ui-schedules/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-schedules', pid] })
      setOpen(false)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/ui-schedules/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ui-schedules', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<UiSchedule> = [
    { title: '名称', dataIndex: 'name' },
    { title: '套件 ID', dataIndex: 'suite_id', width: 90 },
    { title: 'Cron', dataIndex: 'cron_expr' },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 70,
      render: (v: boolean) => (v ? '是' : '否'),
    },
    { title: '下次', dataIndex: 'next_run_at', ellipsis: true, render: (v: string | null) => formatBeijing(v) },
    {
      title: '操作',
      width: 140,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            onClick={() => {
              setEditing(row)
              form.setFieldsValue({
                name: row.name,
                suite_id: row.suite_id,
                env_id: row.env_id ?? undefined,
                cron_expr: row.cron_expr,
                enabled: row.enabled,
              })
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
      <PageBanner title="UI 定时任务" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            form.setFieldsValue({
              name: '',
              cron_expr: '0 * * * *',
              enabled: true,
            })
            setOpen(true)
          }}
        >
          新建
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索任务名称"
          style={{ width: 240 }}
          onSearch={(v) => {
            setSearch(v)
            setPage(1)
          }}
        />
        <span style={{ color: '#888', fontSize: 12 }}>需启动 Celery Beat 才会按点触发</span>
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
      <Modal title={editing ? '编辑定时' : '新建定时'} open={open} onCancel={() => setOpen(false)} footer={null} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="suite_id" label="套件" rules={[{ required: true }]}>
            <Select options={(suites.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))} />
          </Form.Item>
          <Form.Item name="env_id" label="环境">
            <Select allowClear options={(envs.data?.items ?? []).map((e) => ({ value: e.id, label: e.name }))} />
          </Form.Item>
          <Form.Item name="cron_expr" label="Cron" rules={[{ required: true }]} extra="例：0 * * * * 每小时">
            <Input />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
