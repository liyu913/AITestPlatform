import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import { useTableScrollY } from '../hooks/useTableScrollY'
import PageBanner from './PageBanner'

type AppDevice = {
  id: number
  name: string
  platform: string
  udid: string
  device_name: string
  platform_version: string
  appium_server_url: string
  automation_name: string
  tags: string[]
  status: string
  worker_id: string
  last_heartbeat_at: string | null
  current_run_id: number | null
  notes: string
}

type PageRes<T> = { items: T[]; total: number }

type DeviceForm = {
  name: string
  platform: string
  udid: string
  device_name: string
  platform_version: string
  appium_server_url: string
  automation_name: string
  tags_text: string
  status: string
  worker_id: string
  notes: string
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string; detail?: string }
    if (b.detail && typeof b.detail === 'string') {
      return b.message ? `${b.message}：${b.detail}` : b.detail
    }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function statusColor(s: string): string {
  if (s === 'online') return 'success'
  if (s === 'busy') return 'processing'
  if (s === 'disabled') return 'default'
  return 'error'
}

export default function AppDeviceList() {
  const { containerRef, scrollY } = useTableScrollY()

  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AppDevice | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [form] = Form.useForm<DeviceForm>()

  const list = useQuery({
    queryKey: ['app-devices', pid, search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (search.trim()) qs.set('search', search.trim())
      return apiJson<PageRes<AppDevice>>(`/api/v1/projects/${pid}/app-devices/?${qs}`)
    },
    enabled: Number.isFinite(pid),
    refetchInterval: 5000,
  })

  const save = useMutation({
    mutationFn: async (values: DeviceForm) => {
      const payload = {
        name: values.name.trim(),
        platform: values.platform,
        udid: values.udid || '',
        device_name: values.device_name || '',
        platform_version: values.platform_version || '',
        appium_server_url: values.appium_server_url || '',
        automation_name: values.automation_name || '',
        tags: (values.tags_text || '')
          .split(/[,，\s]+/)
          .map((t) => t.trim())
          .filter(Boolean),
        status: values.status,
        worker_id: values.worker_id || '',
        notes: values.notes || '',
      }
      if (editing) {
        return apiJson(`/api/v1/projects/${pid}/app-devices/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      return apiJson(`/api/v1/projects/${pid}/app-devices/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-devices', pid] })
      setOpen(false)
      message.success('已保存')
    },
    onError: (e) => message.error(errText(e)),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/app-devices/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-devices', pid] })
      message.success('已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const heartbeat = useMutation({
    mutationFn: async (id: number) =>
      apiJson(`/api/v1/projects/${pid}/app-devices/${id}/heartbeat`, {
        method: 'POST',
        body: JSON.stringify({ status: 'online', worker_id: 'ui' }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-devices', pid] })
      message.success('已上线')
    },
    onError: (e) => message.error(errText(e)),
  })

  const release = useMutation({
    mutationFn: async (id: number) =>
      apiJson(`/api/v1/projects/${pid}/app-devices/${id}/release`, { method: 'POST', body: '{}' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['app-devices', pid] })
      message.success('已释放')
    },
    onError: (e) => message.error(errText(e)),
  })

  const scan = useMutation({
    mutationFn: async () =>
      apiJson<{
        scanned: number
        ready: number
        created: number
        updated: number
        marked_offline: number
      }>(`/api/v1/projects/${pid}/app-devices/scan`, {
        method: 'POST',
        body: JSON.stringify({ mark_missing_offline: true }),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['app-devices', pid] })
      message.success(
        `扫描完成：发现 ${data.ready}/${data.scanned} 台可用，新建 ${data.created}，更新 ${data.updated}` +
          (data.marked_offline ? `，离线 ${data.marked_offline}` : ''),
      )
    },
    onError: (e) => message.error(errText(e)),
  })

  const columns: ColumnsType<AppDevice> = [
    { title: '名称', dataIndex: 'name', width: 140 },
    { title: '平台', dataIndex: 'platform', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: 'UDID', dataIndex: 'udid', ellipsis: true },
    { title: '设备名', dataIndex: 'device_name', ellipsis: true },
    {
      title: '标签',
      dataIndex: 'tags',
      width: 160,
      render: (tags: string[]) =>
        tags?.length ? tags.map((t) => <Tag key={t}>{t}</Tag>) : '—',
    },
    { title: 'Worker', dataIndex: 'worker_id', width: 100, ellipsis: true },
    {
      title: '心跳',
      dataIndex: 'last_heartbeat_at',
      width: 160,
      ellipsis: true,
      render: (v: string | null) => formatBeijing(v),
    },
    {
      title: '操作',
      width: 260,
      render: (_, row) => (
        <Space wrap>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setEditing(row)
              form.setFieldsValue({
                name: row.name,
                platform: row.platform,
                udid: row.udid,
                device_name: row.device_name,
                platform_version: row.platform_version,
                appium_server_url: row.appium_server_url,
                automation_name: row.automation_name,
                tags_text: (row.tags || []).join(','),
                status: row.status === 'busy' ? 'online' : row.status,
                worker_id: row.worker_id,
                notes: row.notes,
              })
              setOpen(true)
            }}
          >
            编辑
          </Button>
          <Button type="link" size="small" onClick={() => heartbeat.mutate(row.id)}>
            上线
          </Button>
          {row.status === 'busy' && (
            <Button type="link" size="small" onClick={() => release.mutate(row.id)}>
              释放
            </Button>
          )}
          <Popconfirm title="删除设备？" onConfirm={() => remove.mutate(row.id)}>
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
      <PageBanner title="APP 设备池" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button
          type="primary"
          onClick={() => {
            setEditing(null)
            form.setFieldsValue({
              name: '',
              platform: 'android',
              udid: '',
              device_name: '',
              platform_version: '',
              appium_server_url: 'http://127.0.0.1:4723',
              automation_name: 'UiAutomator2',
              tags_text: '',
              status: 'offline',
              worker_id: '',
              notes: '',
            })
            setOpen(true)
          }}
        >
          注册设备
        </Button>
        <Button loading={scan.isPending} onClick={() => scan.mutate()}>
          扫描本地设备
        </Button>
        <Input.Search
          allowClear
          placeholder="搜索名称/UDID"
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
        title={editing ? '编辑设备' : '注册设备'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        width={640}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'android', label: 'Android' },
                { value: 'ios', label: 'iOS' },
              ]}
            />
          </Form.Item>
          <Form.Item name="udid" label="UDID">
            <Input placeholder="adb devices / idevice_id" />
          </Form.Item>
          <Form.Item name="device_name" label="deviceName">
            <Input />
          </Form.Item>
          <Form.Item name="platform_version" label="系统版本">
            <Input />
          </Form.Item>
          <Form.Item name="appium_server_url" label="Appium URL（可覆盖环境）">
            <Input placeholder="留空则用环境配置" />
          </Form.Item>
          <Form.Item name="automation_name" label="Automation">
            <Input />
          </Form.Item>
          <Form.Item name="tags_text" label="标签（逗号分隔）">
            <Input placeholder="smoke, pixel6" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { value: 'online', label: 'online' },
                { value: 'offline', label: 'offline' },
                { value: 'disabled', label: 'disabled' },
              ]}
            />
          </Form.Item>
          <Form.Item name="worker_id" label="Worker ID">
            <Input />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
