import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tabs,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'

type UserRow = {
  id: number
  username: string
  full_name: string | null
  is_active: boolean
  is_superuser: boolean
  approval_status: 'pending' | 'approved' | 'rejected'
  created_at: string | null
}

type UserListResponse = {
  items: UserRow[]
  total: number
  page: number
  page_size: number
}

type TabKey = 'pending' | 'approved' | 'rejected' | 'all'

type ResetPasswordForm = {
  new_password: string
  confirm_password: string
}

const TAB_STATUS: Record<TabKey, string | undefined> = {
  pending: 'pending',
  approved: 'approved',
  rejected: 'rejected',
  all: undefined,
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function statusTag(user: UserRow) {
  if (user.approval_status === 'pending') {
    return <Tag color="gold">待审批</Tag>
  }
  if (user.approval_status === 'rejected') {
    return <Tag color="red">已拒绝</Tag>
  }
  if (!user.is_active) {
    return <Tag color="default">已禁用</Tag>
  }
  return <Tag color="green">已通过</Tag>
}

function formatTime(iso: string | null): string {
  return formatBeijing(iso)
}

export default function AdminUserList() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [tab, setTab] = useState<TabKey>('pending')
  const [page, setPage] = useState(1)
  const pageSize = 20
  const [resetTarget, setResetTarget] = useState<UserRow | null>(null)
  const [resetForm] = Form.useForm<ResetPasswordForm>()

  const listQuery = useQuery({
    queryKey: ['admin', 'users', tab, page],
    queryFn: () => {
      const status = TAB_STATUS[tab]
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (status) qs.set('status', status)
      return apiJson<UserListResponse>(`/api/v1/admin/users?${qs}`)
    },
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['admin', 'users'] })
  }

  const approveMut = useMutation({
    mutationFn: (userId: number) =>
      apiJson<UserRow>(`/api/v1/admin/users/${userId}/approve`, { method: 'PATCH' }),
    onSuccess: () => {
      message.success('已通过审批')
      invalidate()
    },
    onError: (e) => message.error(errText(e)),
  })

  const rejectMut = useMutation({
    mutationFn: (userId: number) =>
      apiJson<UserRow>(`/api/v1/admin/users/${userId}/reject`, { method: 'PATCH' }),
    onSuccess: () => {
      message.success('已拒绝')
      invalidate()
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchMut = useMutation({
    mutationFn: (p: { userId: number; is_active: boolean }) =>
      apiJson<UserRow>(`/api/v1/admin/users/${p.userId}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: p.is_active }),
      }),
    onSuccess: (_d, vars) => {
      message.success(vars.is_active ? '已启用' : '已禁用')
      invalidate()
    },
    onError: (e) => message.error(errText(e)),
  })

  const resetMut = useMutation({
    mutationFn: (p: { userId: number; new_password: string }) =>
      apiJson(`/api/v1/admin/users/${p.userId}/reset-password`, {
        method: 'POST',
        body: JSON.stringify({ new_password: p.new_password }),
      }),
    onSuccess: () => {
      message.success('密码已重置')
      setResetTarget(null)
      resetForm.resetFields()
    },
    onError: (e) => message.error(errText(e)),
  })

  function openResetModal(row: UserRow) {
    resetForm.resetFields()
    setResetTarget(row)
  }

  function submitResetPassword(values: ResetPasswordForm) {
    if (!resetTarget) return
    if (values.new_password !== values.confirm_password) {
      message.error('两次输入的新密码不一致')
      return
    }
    resetMut.mutate({ userId: resetTarget.id, new_password: values.new_password })
  }

  function resetPasswordButton(row: UserRow) {
    return (
      <Button type="link" size="small" onClick={() => openResetModal(row)}>
        重置密码
      </Button>
    )
  }

  const columns: ColumnsType<UserRow> = [
    { title: '账号', dataIndex: 'username', key: 'username' },
    {
      title: '姓名',
      dataIndex: 'full_name',
      key: 'full_name',
      render: (v: string | null) => v || '—',
    },
    {
      title: '状态',
      key: 'status',
      render: (_, row) => statusTag(row),
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: string | null) => formatTime(v),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, row) => {
        if (row.is_superuser) {
          return <span className="muted">超级管理员</span>
        }
        if (row.approval_status === 'pending') {
          return (
            <Space>
              <Button
                type="link"
                size="small"
                loading={approveMut.isPending}
                onClick={() => approveMut.mutate(row.id)}
              >
                通过
              </Button>
              <Popconfirm title="确定拒绝该用户？" onConfirm={() => rejectMut.mutate(row.id)}>
                <Button type="link" size="small" danger loading={rejectMut.isPending}>
                  拒绝
                </Button>
              </Popconfirm>
              {resetPasswordButton(row)}
            </Space>
          )
        }
        if (row.approval_status === 'approved') {
          return (
            <Space>
              {row.is_active ? (
                <Popconfirm
                  title="确定禁用该用户？"
                  onConfirm={() => patchMut.mutate({ userId: row.id, is_active: false })}
                >
                  <Button type="link" size="small" danger loading={patchMut.isPending}>
                    禁用
                  </Button>
                </Popconfirm>
              ) : (
                <Button
                  type="link"
                  size="small"
                  loading={patchMut.isPending}
                  onClick={() => patchMut.mutate({ userId: row.id, is_active: true })}
                >
                  启用
                </Button>
              )}
              {resetPasswordButton(row)}
            </Space>
          )
        }
        return <Space>{resetPasswordButton(row)}</Space>
      },
    },
  ]

  return (
    <div>
      <h1>用户管理</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        管理平台账号注册审批与启用状态。审批通过后，用户仍需被加入项目才能使用项目功能。
      </p>
      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k as TabKey)
          setPage(1)
        }}
        items={[
          { key: 'pending', label: '待审批' },
          { key: 'approved', label: '已通过' },
          { key: 'rejected', label: '已拒绝' },
          { key: 'all', label: '全部' },
        ]}
      />
      <Table
        rowKey="id"
        columns={columns}
        dataSource={listQuery.data?.items ?? []}
        loading={listQuery.isLoading}
        pagination={{
          current: page,
          pageSize,
          total: listQuery.data?.total ?? 0,
          showSizeChanger: false,
          onChange: (p) => setPage(p),
        }}
      />
      <Modal
        title={resetTarget ? `重置密码：${resetTarget.username}` : '重置密码'}
        open={resetTarget !== null}
        onCancel={() => {
          setResetTarget(null)
          resetForm.resetFields()
        }}
        footer={null}
        destroyOnHidden
      >
        <Form
          form={resetForm}
          layout="vertical"
          onFinish={(values) => submitResetPassword(values)}
        >
          <Form.Item
            label="新密码"
            name="new_password"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '新密码至少 8 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            label="确认新密码"
            name="confirm_password"
            rules={[{ required: true, message: '请再次输入新密码' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={resetMut.isPending} block>
              确认重置
            </Button>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
