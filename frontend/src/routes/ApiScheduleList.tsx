import { App, Button, Drawer, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import CronExprBuilder, { describeCronExpr } from '../components/CronExprBuilder'
import { useTableScrollY } from '../hooks/useTableScrollY'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type Schedule = {
  id: number
  name: string
  suite_id: number
  env_id: number | null
  cron_expr: string
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
}
type Suite = { id: number; name: string }
type Env = { id: number; name: string; is_default: boolean }
type PageRes<T> = { items: T[]; total: number }

type ScheduleRun = {
  id: number
  status: string
  started_at: string | null
  finished_at: string | null
}

function statusColor(status: string): string {
  if (status === 'passed') return 'success'
  if (status === 'failed' || status === 'error') return 'error'
  if (status === 'running' || status === 'pending') return 'processing'
  return 'default'
}

type FormVals = {
  name: string
  suite_id: number
  env_id?: number
  cron_expr: string
  enabled: boolean
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    return (ex.body as { message?: string }).message ?? '请求失败'
  }
  return '请求失败'
}

export default function ApiScheduleList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Schedule | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [history, setHistory] = useState<Schedule | null>(null)
  const [historyPage, setHistoryPage] = useState(1)
  const [historyPageSize, setHistoryPageSize] = useState(20)
  const [form] = Form.useForm<FormVals>()

  const list = useQuery({
    queryKey: ['api-schedules', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<Schedule>>(`/api/v1/projects/${pid}/api-schedules/?${qs}`)
    },
    enabled: Number.isFinite(pid),
  })
  const suites = useQuery({
    queryKey: ['api-suites', pid, 'select'],
    queryFn: () => apiJson<PageRes<Suite>>(`/api/v1/projects/${pid}/api-suites/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })
  const historyRuns = useQuery({
    queryKey: ['api-schedule-runs', pid, history?.id, historyPage, historyPageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(historyPage), page_size: String(historyPageSize) })
      return apiJson<PageRes<ScheduleRun>>(
        `/api/v1/projects/${pid}/api-schedules/${history?.id}/runs?${qs}`,
      )
    },
    enabled: Number.isFinite(pid) && history != null,
  })

  const envs = useQuery({
    queryKey: ['api-envs', pid, 'select'],
    queryFn: () => apiJson<PageRes<Env>>(`/api/v1/projects/${pid}/api-envs/?page=1&page_size=100`),
    enabled: Number.isFinite(pid),
  })

  const save = useMutation({
    mutationFn: (values: FormVals) => {
      const payload = {
        name: values.name.trim(),
        suite_id: values.suite_id,
        env_id: values.env_id ?? null,
        cron_expr: values.cron_expr.trim(),
        enabled: Boolean(values.enabled),
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/api-schedules/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/api-schedules/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-schedules', pid] })
      setOpen(false)
      setEditing(null)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/api-schedules/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-schedules', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<Schedule> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '执行周期',
      dataIndex: 'cron_expr',
      width: 200,
      render: (expr: string) => (
        <div>
          <div>{describeCronExpr(expr)}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {expr}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 70,
      render: (v: boolean) => (v ? '是' : '否'),
    },
    { title: '下次执行', dataIndex: 'next_run_at', ellipsis: true, render: (v: string | null) => formatBeijing(v) },
    { title: '上次执行', dataIndex: 'last_run_at', ellipsis: true, render: (v: string | null) => formatBeijing(v) },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space>
          <Button
            type="link"
            onClick={() => {
              setHistory(row)
              setHistoryPage(1)
            }}
          >
            执行记录
          </Button>
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
      <PageBanner title="定时回归" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            form.setFieldsValue({
              name: '',
              suite_id: suites.data?.items[0]?.id,
              cron_expr: '0 * * * *',
              enabled: true,
            })
            setOpen(true)
          }}
        >
          新建定时任务
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

      <Modal
        title={editing ? '编辑定时任务' : '新建定时任务'}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={560}
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="suite_id" label="场景" rules={[{ required: true }]}>
            <Select options={(suites.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))} />
          </Form.Item>
          <Form.Item name="env_id" label="环境（可选）">
            <Select
              allowClear
              options={(envs.data?.items ?? []).map((e) => ({
                value: e.id,
                label: e.is_default ? `${e.name}（默认）` : e.name,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="cron_expr"
            label="执行周期"
            rules={[{ required: true, message: '请选择执行周期' }]}
          >
            <CronExprBuilder />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>

      <Drawer
        title={history ? `${history.name} 的执行记录` : '执行记录'}
        open={history != null}
        onClose={() => setHistory(null)}
        width={720}
      >
        <Table<ScheduleRun>
          rowKey="id"
          loading={historyRuns.isLoading}
          dataSource={historyRuns.data?.items ?? []}
          pagination={{
            current: historyPage,
            pageSize: historyPageSize,
            total: historyRuns.data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, s) => {
              setHistoryPage(p)
              setHistoryPageSize(s)
            },
          }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (status: string) => <Tag color={statusColor(status)}>{status}</Tag>,
            },
            {
              title: '开始',
              dataIndex: 'started_at',
              render: (v: string | null) => formatBeijing(v),
            },
            {
              title: '结束',
              dataIndex: 'finished_at',
              render: (v: string | null) => formatBeijing(v),
            },
            {
              title: '操作',
              width: 80,
              render: (_, row) => <Link to={`/projects/${pid}/api-runs/${row.id}`}>详情</Link>,
            },
          ]}
        />
      </Drawer>
    </div>
  )
}
