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
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import PageBanner from './PageBanner'

type Member = {
  user_id: number
  username: string
  role: string
}

const ROLE_OPTIONS = [
  { value: 'admin', label: '管理员' },
  { value: 'editor', label: '编辑' },
  { value: 'viewer', label: '查看者' },
]

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function MemberList() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const listQuery = useQuery({
    queryKey: ['members', pid],
    queryFn: () => apiJson<Member[]>(`/api/v1/projects/${pid}/members`),
    enabled: Number.isFinite(pid),
  })

  const [inviteOpen, setInviteOpen] = useState(false)
  const [inviteForm] = Form.useForm<{ username: string; role: string }>()

  const inviteMut = useMutation({
    mutationFn: (values: { username: string; role: string }) =>
      apiJson<Member>(`/api/v1/projects/${pid}/members`, {
        method: 'POST',
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      message.success('已添加成员')
      setInviteOpen(false)
      inviteForm.resetFields()
      qc.invalidateQueries({ queryKey: ['members', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchRoleMut = useMutation({
    mutationFn: (p: { userId: number; role: string }) =>
      apiJson<Member>(`/api/v1/projects/${pid}/members/${p.userId}`, {
        method: 'PATCH',
        body: JSON.stringify({ role: p.role }),
      }),
    onSuccess: () => {
      message.success('角色已更新')
      qc.invalidateQueries({ queryKey: ['members', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function removeMember(userId: number) {
    try {
      const res = await apiFetch(`/api/v1/projects/${pid}/members/${userId}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const t = await res.text()
        let msg = '移除失败'
        try {
          const j = JSON.parse(t) as { message?: string }
          msg = j.message ?? msg
        } catch {
          /* ignore */
        }
        message.error(msg)
        return
      }
      message.success('已移除')
      qc.invalidateQueries({ queryKey: ['members', pid] })
    } catch {
      message.error('移除失败')
    }
  }

  const columns: ColumnsType<Member> = [
    { title: '用户 ID', dataIndex: 'user_id', width: 100 },
    { title: '账号', dataIndex: 'username', ellipsis: true },
    {
      title: '角色',
      dataIndex: 'role',
      width: 200,
      render: (role: string, row) => (
        <Select
          value={role}
          style={{ width: 160 }}
          options={ROLE_OPTIONS}
          onChange={(newRole) => patchRoleMut.mutate({ userId: row.user_id, role: newRole })}
        />
      ),
    },
    {
      title: '操作',
      key: 'act',
      width: 120,
      render: (_: unknown, row) => (
        <Popconfirm title="确定从项目移除该成员？" onConfirm={() => void removeMember(row.user_id)}>
          <Button type="link" size="small" danger>
            移除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  if (!Number.isFinite(pid)) {
    return <p className="error">无效项目</p>
  }

  return (
    <div>
      <PageBanner title="成员管理" />
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={() => setInviteOpen(true)}>
          邀请成员
        </Button>
      </Space>
      <Table<Member>
        rowKey="user_id"
        loading={listQuery.isLoading}
        columns={columns}
        dataSource={listQuery.data ?? []}
        pagination={false}
      />
      <Modal
        title="邀请成员"
        open={inviteOpen}
        onCancel={() => {
          setInviteOpen(false)
          inviteForm.resetFields()
        }}
        onOk={() =>
          void inviteForm.validateFields().then((v) => inviteMut.mutate(v))
        }
        confirmLoading={inviteMut.isPending}
        destroyOnHidden
      >
        <Form form={inviteForm} layout="vertical" initialValues={{ role: 'viewer' }}>
          <Form.Item
            name="username"
            label="账号"
            rules={[
              { required: true, message: '请输入已注册用户的账号' },
              {
                pattern: /^[a-zA-Z][a-zA-Z0-9_]{2,31}$/,
                message: '3–32 位，字母开头，仅字母/数字/下划线',
              },
            ]}
          >
            <Input placeholder="对方需已在平台注册" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
