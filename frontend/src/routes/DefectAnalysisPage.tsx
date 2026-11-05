import {
  App,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type Analysis = {
  id: number
  defect_id: number | null
  source_type: string
  source_id: number | null
  title: string
  observed: string
  expected: string
  category: string
  conclusion: string
  status: string
  created_at: string
  updated_at: string
}

type Defect = {
  id: number
  title: string
  category: string
  status: string
  severity: string
  updated_at: string
}

type Submission = {
  id: number
  target: string
  connector: string | null
  request_id: string
  status: string
  external_id: string | null
  message: string
  attempt_no: number
  created_at: string
}

const CATEGORY_LABEL: Record<string, string> = {
  product: '产品',
  environment: '环境',
  data: '数据',
  script: '脚本',
  pending: '待确认',
}

const STATUS_LABEL: Record<string, string> = {
  draft: '待确认',
  confirmed: '已确认',
  dismissed: '已结束',
  converted: '已转缺陷',
}

const STATUS_COLOR: Record<string, string> = {
  draft: 'default',
  confirmed: 'processing',
  dismissed: 'default',
  converted: 'success',
}

const SOURCE_LABEL: Record<string, string> = {
  cycle_item: '执行项',
  defect: '缺陷',
  manual: '手工',
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function DefectAnalysisPage() {
  const { projectId } = useParams()
  const pid = Number(projectId)
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [confirmTarget, setConfirmTarget] = useState<Analysis | null>(null)
  const [dismissTarget, setDismissTarget] = useState<Analysis | null>(null)
  const [submitTarget, setSubmitTarget] = useState<Defect | null>(null)
  const [historyTarget, setHistoryTarget] = useState<Defect | null>(null)
  const [submitTargetKind, setSubmitTargetKind] = useState<'internal' | 'external'>('internal')
  const [connector, setConnector] = useState('')
  const [form] = Form.useForm()
  const [outcomeForm] = Form.useForm()

  const analysesQuery = useQuery({
    queryKey: ['defect-analyses', pid],
    queryFn: () => apiJson<Analysis[]>(`/api/v1/projects/${pid}/defect-analyses/`),
    enabled: Number.isFinite(pid),
  })

  const pendingQuery = useQuery({
    queryKey: ['defects-pending', pid],
    queryFn: () => apiJson<Defect[]>(`/api/v1/projects/${pid}/defects/pending-submission`),
    enabled: Number.isFinite(pid),
  })

  const submissionsQuery = useQuery({
    queryKey: ['defect-submissions', pid, historyTarget?.id],
    queryFn: () =>
      apiJson<Submission[]>(`/api/v1/projects/${pid}/defects/${historyTarget!.id}/submissions`),
    enabled: Boolean(historyTarget?.id),
  })

  function refresh() {
    void qc.invalidateQueries({ queryKey: ['defect-analyses', pid] })
    void qc.invalidateQueries({ queryKey: ['defects-pending', pid] })
  }

  const createMut = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      apiJson<Analysis>(`/api/v1/projects/${pid}/defect-analyses/`, {
        method: 'POST',
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      message.success('分析记录已创建')
      setCreateOpen(false)
      form.resetFields()
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const updateMut = useMutation({
    mutationFn: (p: { id: number; body: Record<string, unknown> }) =>
      apiJson<Analysis>(`/api/v1/projects/${pid}/defect-analyses/${p.id}`, {
        method: 'PATCH',
        body: JSON.stringify(p.body),
      }),
    onSuccess: () => {
      message.success('分析已更新')
      setConfirmTarget(null)
      setDismissTarget(null)
      outcomeForm.resetFields()
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const convertMut = useMutation({
    mutationFn: (analysisId: number) =>
      apiJson<Defect>(`/api/v1/projects/${pid}/defect-analyses/${analysisId}/convert`, {
        method: 'POST',
      }),
    onSuccess: (defect) => {
      message.success(`已创建缺陷 #${defect.id}`)
      refresh()
      void qc.invalidateQueries({ queryKey: ['defects', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const submitMut = useMutation({
    mutationFn: (p: { defectId: number; target: string; connector?: string }) =>
      apiJson<Submission>(`/api/v1/projects/${pid}/defects/${p.defectId}/submissions`, {
        method: 'POST',
        body: JSON.stringify({ target: p.target, connector: p.connector || null }),
      }),
    onSuccess: (row) => {
      if (row.status === 'success') {
        message.success(`提交成功：${row.external_id ?? ''}`)
        setSubmitTarget(null)
      } else {
        message.warning(row.message)
      }
      refresh()
      if (historyTarget) {
        void qc.invalidateQueries({ queryKey: ['defect-submissions', pid, historyTarget.id] })
      }
    },
    onError: (e) => message.error(errText(e)),
  })

  function confirmAnalysis() {
    void outcomeForm.validateFields().then((values) => {
      if (!confirmTarget) return
      updateMut.mutate({
        id: confirmTarget.id,
        body: {
          category: values.category,
          conclusion: values.conclusion,
          status: 'confirmed',
        },
      })
    })
  }

  function dismissAnalysis() {
    void outcomeForm.validateFields().then((values) => {
      if (!dismissTarget) return
      updateMut.mutate({
        id: dismissTarget.id,
        body: { conclusion: values.conclusion, status: 'dismissed' },
      })
    })
  }

  if (!Number.isFinite(pid)) return <p className="error">无效项目</p>

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="缺陷分析" />
        <p className="muted" style={{ marginTop: 0 }}>
          执行失败先进入分析：确认分类与结论后，产品问题才转为正式缺陷；环境/数据/脚本问题可以独立结束。
        </p>
        <Space>
          <Button
            type="primary"
            onClick={() => {
              form.resetFields()
              form.setFieldsValue({ category: 'pending' })
              setCreateOpen(true)
            }}
          >
            新建分析
          </Button>
        </Space>
      </div>
      <div className="list-page-body">
        <Tabs
          items={[
            {
              key: 'analyses',
              label: `分析记录（${analysesQuery.data?.length ?? 0}）`,
              children: (
                <Table<Analysis>
                  rowKey="id"
                  size="small"
                  loading={analysesQuery.isLoading}
                  dataSource={analysesQuery.data ?? []}
                  pagination={false}
                  columns={[
                    { title: '标题', dataIndex: 'title', ellipsis: true },
                    {
                      title: '来源',
                      dataIndex: 'source_type',
                      width: 110,
                      render: (v: string, row) =>
                        `${SOURCE_LABEL[v] ?? v}${row.source_id ? ` #${row.source_id}` : ''}`,
                    },
                    {
                      title: '分类',
                      dataIndex: 'category',
                      width: 90,
                      render: (v: string) => CATEGORY_LABEL[v] ?? v,
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 100,
                      render: (v: string) => (
                        <Tag color={STATUS_COLOR[v] ?? 'default'}>{STATUS_LABEL[v] ?? v}</Tag>
                      ),
                    },
                    { title: '结论', dataIndex: 'conclusion', ellipsis: true },
                    {
                      title: '操作',
                      width: 300,
                      render: (_, row) => (
                        <Space size={4} wrap>
                          {row.status === 'draft' ? (
                            <Button
                              type="primary"
                              size="small"
                              onClick={() => {
                                setConfirmTarget(row)
                                outcomeForm.setFieldsValue({
                                  category: row.category === 'pending' ? 'product' : row.category,
                                  conclusion: row.conclusion,
                                })
                              }}
                            >
                              确认
                            </Button>
                          ) : null}
                          {row.status === 'draft' && row.category !== 'product' ? (
                            <Button
                              type="text"
                              size="small"
                              danger
                              onClick={() => {
                                setDismissTarget(row)
                                outcomeForm.setFieldsValue({ conclusion: row.conclusion })
                              }}
                            >
                              结束分析
                            </Button>
                          ) : null}
                          {row.status === 'confirmed' && row.category === 'product' ? (
                            <Button
                              size="small"
                              loading={convertMut.isPending}
                              onClick={() => convertMut.mutate(row.id)}
                            >
                              转为缺陷
                            </Button>
                          ) : null}
                          {row.defect_id ? (
                            <Link to={`/projects/${pid}/defects?defect=${row.defect_id}`}>
                              <Button type="text" size="small">
                                查看缺陷 #{row.defect_id}
                              </Button>
                            </Link>
                          ) : null}
                        </Space>
                      ),
                    },
                  ]}
                />
              ),
            },
            {
              key: 'pending',
              label: `待提交（${pendingQuery.data?.length ?? 0}）`,
              children: (
                <Table<Defect>
                  rowKey="id"
                  size="small"
                  loading={pendingQuery.isLoading}
                  dataSource={pendingQuery.data ?? []}
                  pagination={false}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 70 },
                    {
                      title: '标题',
                      dataIndex: 'title',
                      ellipsis: true,
                      render: (v: string, row) => (
                        <Link to={`/projects/${pid}/defects?defect=${row.id}`}>{v}</Link>
                      ),
                    },
                    {
                      title: '分类',
                      dataIndex: 'category',
                      width: 90,
                      render: (v: string) => CATEGORY_LABEL[v] ?? v,
                    },
                    { title: '状态', dataIndex: 'status', width: 100 },
                    {
                      title: '更新时间',
                      dataIndex: 'updated_at',
                      width: 170,
                      render: (v: string) => formatBeijing(v),
                    },
                    {
                      title: '操作',
                      width: 220,
                      render: (_, row) => (
                        <Space size={4}>
                          <Button
                            type="primary"
                            size="small"
                            onClick={() => {
                              setSubmitTarget(row)
                              setSubmitTargetKind('internal')
                              setConnector('')
                            }}
                          >
                            提交
                          </Button>
                          <Button
                            type="text"
                            size="small"
                            onClick={() => setHistoryTarget(row)}
                          >
                            提交记录
                          </Button>
                        </Space>
                      ),
                    },
                  ]}
                />
              ),
            },
          ]}
        />
      </div>

      <Modal
        title="新建分析"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void form.validateFields().then((values) => createMut.mutate(values))}
        confirmLoading={createMut.isPending}
        okText="创建"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input maxLength={512} />
          </Form.Item>
          <Form.Item name="observed" label="实际观察">
            <Input.TextArea rows={2} placeholder="失败现象、日志或报错" />
          </Form.Item>
          <Form.Item name="expected" label="预期结果">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="category" label="初始分类">
            <Select
              options={Object.entries(CATEGORY_LABEL).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item name="conclusion" label="结论（可稍后补）">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="确认分析"
        open={Boolean(confirmTarget)}
        onCancel={() => setConfirmTarget(null)}
        onOk={confirmAnalysis}
        confirmLoading={updateMut.isPending}
        okText="确认"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={outcomeForm} layout="vertical">
          <Form.Item name="category" label="分类" rules={[{ required: true }]}>
            <Select
              options={Object.entries(CATEGORY_LABEL)
                .filter(([value]) => value !== 'pending')
                .map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item
            name="conclusion"
            label="结论"
            rules={[{ required: true, message: '请填写结论' }]}
          >
            <Input.TextArea rows={3} placeholder="判断依据与结论" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="结束分析"
        open={Boolean(dismissTarget)}
        onCancel={() => setDismissTarget(null)}
        onOk={dismissAnalysis}
        confirmLoading={updateMut.isPending}
        okText="结束"
        cancelText="取消"
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">
          环境、数据或脚本问题可以在此结束分析，不创建正式缺陷。
        </Typography.Paragraph>
        <Form form={outcomeForm} layout="vertical">
          <Form.Item
            name="conclusion"
            label="原因"
            rules={[{ required: true, message: '请说明原因' }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`提交缺陷${submitTarget ? ` #${submitTarget.id}` : ''}`}
        open={Boolean(submitTarget)}
        onCancel={() => setSubmitTarget(null)}
        onOk={() => {
          if (!submitTarget) return
          submitMut.mutate({
            defectId: submitTarget.id,
            target: submitTargetKind,
            connector,
          })
        }}
        confirmLoading={submitMut.isPending}
        okText="提交"
        cancelText="取消"
        destroyOnHidden
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <div className="muted" style={{ marginBottom: 4 }}>
              目标
            </div>
            <Select
              style={{ width: '100%' }}
              value={submitTargetKind}
              options={[
                { value: 'internal', label: '平台内部缺陷库' },
                { value: 'external', label: '外部系统（需配置连接器）' },
              ]}
              onChange={setSubmitTargetKind}
            />
          </div>
          {submitTargetKind === 'external' ? (
            <div>
              <div className="muted" style={{ marginBottom: 4 }}>
                连接器
              </div>
              <Input
                value={connector}
                onChange={(e) => setConnector(e.target.value)}
                placeholder="例如 jira / tapd"
              />
            </div>
          ) : null}
        </Space>
      </Modal>

      <Drawer
        title={historyTarget ? `提交记录 · 缺陷 #${historyTarget.id}` : '提交记录'}
        open={Boolean(historyTarget)}
        onClose={() => setHistoryTarget(null)}
        width={640}
        destroyOnHidden
      >
        <Table<Submission>
          rowKey="id"
          size="small"
          loading={submissionsQuery.isLoading}
          dataSource={submissionsQuery.data ?? []}
          pagination={false}
          columns={[
            { title: '尝试', dataIndex: 'attempt_no', width: 60 },
            { title: '目标', dataIndex: 'target', width: 90 },
            { title: '状态', dataIndex: 'status', width: 90 },
            {
              title: '外部编号',
              dataIndex: 'external_id',
              width: 120,
              render: (v: string | null) => v || '—',
            },
            { title: '说明', dataIndex: 'message', ellipsis: true },
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 150,
              render: (v: string) => formatBeijing(v),
            },
          ]}
        />
      </Drawer>
    </div>
  )
}
