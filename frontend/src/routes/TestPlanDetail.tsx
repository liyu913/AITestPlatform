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
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  CYCLE_STATUS_OPTIONS,
  labelForCycleStatus,
  labelForPlanStatus,
  PLAN_STATUS_OPTIONS,
} from '../constants/workflow'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import MarkdownPane from '../components/MarkdownPane'
import PageBanner from './PageBanner'

type TestPlan = {
  id: number
  name: string
  version: string | null
  iteration: string | null
  description: string | null
  status: string
  requirement_id: number | null
  requirement_title: string | null
  scheme_id: number | null
  scheme_version_id: number | null
  scheme_name: string | null
  scheme_version_no: number | null
  body_md: string | null
}

type TestCycle = {
  id: number
  plan_id: number
  name: string
  status: string
  item_count: number
  passed_count: number
  failed_count: number
  blocked_count: number
  untested_count: number
  created_at: string
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function TestPlanDetail() {
  const { message } = App.useApp()
  const { projectId, planId } = useParams()
  const pid = Number(projectId)
  const plid = Number(planId)
  const qc = useQueryClient()
  const [cycleOpen, setCycleOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [contentOpen, setContentOpen] = useState(false)
  const [contentDraft, setContentDraft] = useState('')
  const [cycleForm] = Form.useForm<{ name: string; status: string }>()
  const [editForm] = Form.useForm<{
    name: string
    version?: string
    iteration?: string
    description?: string
    status: string
  }>()

  const plan = useQuery({
    queryKey: ['test-plan', pid, plid],
    queryFn: () => apiJson<TestPlan>(`/api/v1/projects/${pid}/test-plans/${plid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(plid),
  })

  const cycles = useQuery({
    queryKey: ['test-cycles', pid, plid],
    queryFn: () => apiJson<TestCycle[]>(`/api/v1/projects/${pid}/test-plans/${plid}/cycles/`),
    enabled: Number.isFinite(pid) && Number.isFinite(plid),
  })

  const createCycle = useMutation({
    mutationFn: (values: { name: string; status: string }) =>
      apiJson<TestCycle>(`/api/v1/projects/${pid}/test-plans/${plid}/cycles/`, {
        method: 'POST',
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      message.success('轮次已创建')
      setCycleOpen(false)
      cycleForm.resetFields()
      qc.invalidateQueries({ queryKey: ['test-cycles', pid, plid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchPlan = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      apiJson<TestPlan>(`/api/v1/projects/${pid}/test-plans/${plid}`, {
        method: 'PATCH',
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      message.success('计划已更新')
      setEditOpen(false)
      setContentOpen(false)
      qc.invalidateQueries({ queryKey: ['test-plan', pid, plid] })
      qc.invalidateQueries({ queryKey: ['test-plans', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function removeCycle(id: number) {
    try {
      const res = await apiFetch(`/api/v1/projects/${pid}/cycles/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        message.error('删除失败')
        return
      }
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['test-cycles', pid, plid] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  const columns: ColumnsType<TestCycle> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '轮次',
      dataIndex: 'name',
      render: (name: string, row) => (
        <Link to={`/projects/${pid}/cycles/${row.id}`}>{name}</Link>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag>{labelForCycleStatus(s)}</Tag>,
    },
    {
      title: '进度',
      render: (_, row) =>
        `共${row.item_count} · ✓${row.passed_count} · ✗${row.failed_count} · !${row.blocked_count} · —${row.untested_count}`,
    },
    { title: '创建时间', dataIndex: 'created_at', ellipsis: true, render: (v: string) => formatBeijing(v) },
    {
      title: '操作',
      width: 100,
      render: (_, row) => (
        <Popconfirm title="确认删除该轮次？" onConfirm={() => removeCycle(row.id)}>
          <Button type="link" danger size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  const p = plan.data

  return (
    <div>
      <PageBanner title={p ? `计划：${p.name}` : '测试计划'} />
      {p?.requirement_id ? (
        <p className="muted" style={{ marginTop: 0 }}>
          来源需求：
          <Link to={`/projects/${pid}/requirements?requirement=${p.requirement_id}`}>
            {p.requirement_title || `#${p.requirement_id}`}
          </Link>
        </p>
      ) : null}
      {p?.scheme_name ? (
        <p className="muted" style={{ marginTop: 0 }}>
          引用方案：
          <Link to={`/projects/${pid}/schemes`}>
            {p.scheme_name}
            {p.scheme_version_no ? ` · v${p.scheme_version_no}` : ''}
          </Link>
        </p>
      ) : null}
      <Space style={{ marginBottom: 16 }} wrap>
        <Link to={`/projects/${pid}/test-plans`}>
          <Button>返回计划列表</Button>
        </Link>
        <Button
          onClick={() => {
            if (!p) return
            editForm.setFieldsValue({
              name: p.name,
              version: p.version ?? undefined,
              iteration: p.iteration ?? undefined,
              description: p.description ?? undefined,
              status: p.status,
            })
            setEditOpen(true)
          }}
        >
          编辑计划
        </Button>
        <Button
          type="primary"
          onClick={() => {
            cycleForm.setFieldsValue({ status: 'active' })
            setCycleOpen(true)
          }}
        >
          新建轮次
        </Button>
      </Space>
      {p && (
        <div className="muted" style={{ marginBottom: 16 }}>
          版本：{p.version || '—'} · 迭代：{p.iteration || '—'} · 状态：
          {labelForPlanStatus(p.status)}
          {p.requirement_title ? ` · 来源需求：${p.requirement_title}` : ''}
          {p.description ? ` · ${p.description}` : ''}
        </div>
      )}
      <Space style={{ marginBottom: 12 }} align="center">
        <Typography.Title level={5} style={{ margin: 0 }}>
          计划正文
        </Typography.Title>
        <Button
          onClick={() => {
            setContentDraft(p?.body_md ?? '')
            setContentOpen(true)
          }}
        >
          {p?.body_md ? '编辑正文' : '填写正文'}
        </Button>
      </Space>
      {p?.body_md ? (
        <div style={{ marginBottom: 24 }}>
          <MarkdownPane value={p.body_md} />
        </div>
      ) : (
        <Typography.Paragraph type="secondary">尚未填写计划正文</Typography.Paragraph>
      )}
      <Typography.Title level={5}>执行轮次</Typography.Title>
      <Table<TestCycle>
        rowKey="id"
        loading={cycles.isLoading}
        columns={columns}
        dataSource={cycles.data ?? []}
        pagination={false}
      />

      <Modal
        title="新建轮次"
        open={cycleOpen}
        onCancel={() => setCycleOpen(false)}
        onOk={() => cycleForm.submit()}
        confirmLoading={createCycle.isPending}
        destroyOnHidden
      >
        <Form
          form={cycleForm}
          layout="vertical"
          onFinish={(v) => createCycle.mutate(v)}
          initialValues={{ status: 'active' }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：第1轮" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={CYCLE_STATUS_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑计划"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        confirmLoading={patchPlan.isPending}
        destroyOnHidden
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(v) =>
            patchPlan.mutate({
              name: v.name.trim(),
              version: v.version?.trim() || null,
              iteration: v.iteration?.trim() || null,
              description: v.description?.trim() || null,
              status: v.status,
            })
          }
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="version" label="版本">
            <Input />
          </Form.Item>
          <Form.Item name="iteration" label="迭代">
            <Input />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select options={PLAN_STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑计划正文"
        open={contentOpen}
        onCancel={() => setContentOpen(false)}
        onOk={() => {
          patchPlan.mutate({ body_md: contentDraft.trim() })
        }}
        confirmLoading={patchPlan.isPending}
        width={960}
        styles={{ body: { overflow: 'auto', maxHeight: 'calc(100vh - 220px)' } }}
        destroyOnHidden
      >
        <MarkdownPane value={contentDraft} onChange={setContentDraft} />
      </Modal>
    </div>
  )
}
