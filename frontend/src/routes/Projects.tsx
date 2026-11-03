import { App, Button, Form, Input, Modal, Popconfirm, Space } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useState } from 'react'
import { apiFetch, apiJson, ApiError } from '../api/client'

type Project = {
  id: number
  name: string
  description: string | null
  my_role?: string
}

type CreateForm = {
  name: string
  description?: string
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  if (ex instanceof Error && ex.message) return ex.message
  return '请求失败'
}

export default function Projects() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<CreateForm>()

  const list = useQuery({
    queryKey: ['projects'],
    queryFn: () => apiJson<Project[]>('/api/v1/projects/'),
  })

  const create = useMutation({
    mutationFn: (values: CreateForm) =>
      apiJson<Project>('/api/v1/projects/', {
        method: 'POST',
        body: JSON.stringify({
          name: values.name.trim(),
          description: values.description?.trim() || null,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      setCreateOpen(false)
      createForm.resetFields()
      message.success('项目已创建')
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteProject = useMutation({
    mutationFn: async (projectId: number) => {
      const res = await apiFetch(`/api/v1/projects/${projectId}`, { method: 'DELETE' })
      if (!res.ok) {
        let body: unknown = null
        try {
          body = await res.json()
        } catch {
          body = { message: '删除失败' }
        }
        throw new ApiError(res.status, body)
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      message.success('项目已删除')
    },
    onError: (e) => {
      message.error(errText(e))
    },
  })

  if (list.isLoading) {
    return <p className="muted">加载中…</p>
  }
  if (list.error) {
    return <p className="error">加载失败</p>
  }

  const rows = list.data ?? []

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1 style={{ marginBottom: 4 }}>项目管理</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            创建或进入项目后，可在左侧使用需求、用例、知识库与 AI 功能。
          </p>
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建项目
        </Button>
      </div>

      <h2 style={{ marginTop: '1.5rem' }}>全部项目</h2>
      {rows.length === 0 ? (
        <p className="muted">暂无项目，点击右上角新建。</p>
      ) : (
        <div className="project-grid">
          {rows.map((p) => (
            <article key={p.id} className="project-card">
              <h3>{p.name}</h3>
              {p.description && <p className="muted">{p.description}</p>}
              {!p.description && <p className="muted">无描述</p>}
              <div className="project-card-actions">
                <Space wrap>
                  <Link to={`/projects/${p.id}/dashboard`} className="btn primary">
                    进入项目
                  </Link>
                  {p.my_role === 'admin' && (
                    <Popconfirm
                      title={`确定删除项目「${p.name}」？`}
                      description="将永久删除需求、用例、知识库等全部数据，不可恢复。"
                      onConfirm={() => deleteProject.mutate(p.id)}
                      okButtonProps={{ danger: true, loading: deleteProject.isPending }}
                    >
                      <Button danger>删除</Button>
                    </Popconfirm>
                  )}
                </Space>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal
        title="新建项目"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false)
          createForm.resetFields()
        }}
        onOk={() =>
          void createForm.validateFields().then((values) => create.mutate(values))
        }
        confirmLoading={create.isPending}
        okText="创建"
        destroyOnHidden
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[
              { required: true, message: '请输入项目名称' },
              { whitespace: true, message: '请输入项目名称' },
            ]}
          >
            <Input placeholder="例如：移动端 2.0" maxLength={255} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="一句话说明项目范围（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
