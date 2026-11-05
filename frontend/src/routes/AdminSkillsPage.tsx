import { App, Button, Form, Input, Modal, Select, Space, Switch, Table, Tag, Upload } from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { apiFetch, apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'

type Skill = {
  id: number
  project_id: number | null
  skill_key: string
  name: string
  action: string
  version: number
  description: string | null
  enabled: boolean
  created_at: string
  updated_at: string
}

const ACTIONS = [
  { value: 'case_design', label: 'AI 用例设计' },
  { value: 'scheme', label: '生成测试方案' },
  { value: 'plan', label: '起草测试计划' },
  { value: 'review', label: '用例评审检查' },
  { value: 'defect_analysis', label: '缺陷分析' },
  { value: 'defect_submit', label: '缺陷提单整理' },
  { value: 'regression_report', label: '回归报告叙述' },
]

const ACTION_LABEL: Record<string, string> = Object.fromEntries(
  ACTIONS.map((a) => [a.value, a.label]),
)

export default function AdminSkillsPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [form] = Form.useForm()

  const list = useQuery({
    queryKey: ['admin-skills'],
    queryFn: () => apiJson<Skill[]>('/api/v1/admin/ai-skills/'),
  })

  const importMut = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      if (!file) throw new Error('请选择 SKILL.md 文件')
      const fd = new FormData()
      fd.append('file', file)
      fd.append('action', String(values.action))
      if (values.skill_key) fd.append('skill_key', String(values.skill_key))
      if (values.name) fd.append('name', String(values.name))
      if (values.description) fd.append('description', String(values.description))
      if (values.version) fd.append('version', String(values.version))
      if (values.tools) fd.append('tools', String(values.tools))
      const res = await apiFetch('/api/v1/admin/ai-skills/import', { method: 'POST', body: fd })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { message?: string }).message ?? `导入失败 (${res.status})`)
      }
      return res.json()
    },
    onSuccess: () => {
      message.success('Skill 已导入')
      setOpen(false)
      setFile(null)
      form.resetFields()
      void qc.invalidateQueries({ queryKey: ['admin-skills'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  const toggleMut = useMutation({
    mutationFn: (p: { id: number; enabled: boolean }) =>
      apiJson<Skill>(`/api/v1/admin/ai-skills/${p.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: p.enabled }),
      }),
    onSuccess: () => {
      message.success('已更新')
      void qc.invalidateQueries({ queryKey: ['admin-skills'] })
    },
    onError: (e: Error) => message.error(e.message),
  })

  return (
    <div className="card">
      <Space style={{ justifyContent: 'space-between', width: '100%', marginBottom: 12 }} wrap>
        <div>
          <h1 style={{ marginBottom: 4 }}>Skill 版本</h1>
          <p className="field-hint" style={{ margin: 0 }}>
            内置用例技能随迁移登记；上传 SKILL.md（可带 front matter）会按 skill_key 自动递增版本。
          </p>
        </div>
        <Button type="primary" onClick={() => setOpen(true)}>
          导入 SKILL.md
        </Button>
      </Space>

      <Table<Skill>
        rowKey="id"
        size="middle"
        loading={list.isLoading}
        dataSource={list.data ?? []}
        pagination={false}
        columns={[
          { title: 'Key', dataIndex: 'skill_key', width: 200 },
          { title: '名称', dataIndex: 'name' },
          {
            title: '适用动作',
            dataIndex: 'action',
            width: 150,
            render: (v: string) => ACTION_LABEL[v] ?? v,
          },
          { title: '版本', dataIndex: 'version', width: 80, render: (v: number) => `v${v}` },
          {
            title: '范围',
            dataIndex: 'project_id',
            width: 100,
            render: (v: number | null) => (v ? `项目 #${v}` : <Tag>全局</Tag>),
          },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 90,
            render: (v: boolean, row) => (
              <Switch
                checked={v}
                size="small"
                loading={toggleMut.isPending}
                onChange={(enabled) => toggleMut.mutate({ id: row.id, enabled })}
              />
            ),
          },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            width: 180,
            render: (v: string) => formatBeijing(v),
          },
        ]}
      />

      <Modal
        title="导入 SKILL.md"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => void form.validateFields().then((values) => importMut.mutate(values))}
        confirmLoading={importMut.isPending}
        okText="导入"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item label="SKILL.md 文件" required>
            <Upload
              maxCount={1}
              accept=".md,text/markdown"
              beforeUpload={(f) => {
                setFile(f)
                return false
              }}
              onRemove={() => setFile(null)}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
          </Form.Item>
          <Form.Item
            name="action"
            label="适用动作"
            rules={[{ required: true, message: '请选择动作' }]}
          >
            <Select options={ACTIONS} />
          </Form.Item>
          <Form.Item name="skill_key" label="Skill Key（留空取 front matter/文件名）">
            <Input placeholder="例如 review-checker" />
          </Form.Item>
          <Form.Item name="name" label="名称（可选）">
            <Input />
          </Form.Item>
          <Form.Item name="version" label="版本（留空自动递增）">
            <Input type="number" />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="tools" label="所需工具（逗号分隔，可选）">
            <Input placeholder="例如 http, playwright" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
