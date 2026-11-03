import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Upload,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type Key } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  EXECUTION_RESULT_OPTIONS,
  labelForExecutionResult,
} from '../constants/workflow'
import { apiFetch, apiJson, ApiError } from '../api/client'
import PageBanner from './PageBanner'

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
}

type Attachment = {
  id: number
  filename: string
  mime: string
  size_bytes: number
}

type CycleItem = {
  id: number
  cycle_id: number
  case_id: number
  case_title: string | null
  case_priority: string | null
  requirement_id: number | null
  assignee_id: number | null
  result: string
  actual_result: string
  executed_by_id: number | null
  executed_at: string | null
  attachments: Attachment[]
}

type PageRes<T> = { items: T[]; total: number; page: number; page_size: number }

type Member = { user_id: number; username: string; role: string }

type TestCaseLite = { id: number; title: string; priority: string | null; status: string }

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

function resultColor(r: string): string {
  if (r === 'passed') return 'success'
  if (r === 'failed') return 'error'
  if (r === 'blocked') return 'warning'
  return 'default'
}

export default function TestCycleDetail() {
  const { message } = App.useApp()
  const { projectId, cycleId } = useParams()
  const pid = Number(projectId)
  const cid = Number(cycleId)
  const qc = useQueryClient()

  const [resultFilter, setResultFilter] = useState<string | undefined>()
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([])
  const [addOpen, setAddOpen] = useState(false)
  const [execOpen, setExecOpen] = useState(false)
  const [execItem, setExecItem] = useState<CycleItem | null>(null)
  const [execForm] = Form.useForm<{ result: string; actual_result: string }>()
  const [addCaseIds, setAddCaseIds] = useState<number[]>([])

  const cycle = useQuery({
    queryKey: ['test-cycle', pid, cid],
    queryFn: () => apiJson<TestCycle>(`/api/v1/projects/${pid}/cycles/${cid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(cid),
  })

  const itemsUrl = useMemo(() => {
    const sp = new URLSearchParams({ page: '1', page_size: '100' })
    if (resultFilter) sp.set('result', resultFilter)
    return `/api/v1/projects/${pid}/cycles/${cid}/items/?${sp}`
  }, [pid, cid, resultFilter])

  const items = useQuery({
    queryKey: ['cycle-items', pid, cid, resultFilter],
    queryFn: () => apiJson<PageRes<CycleItem>>(itemsUrl),
    enabled: Number.isFinite(pid) && Number.isFinite(cid),
  })

  const members = useQuery({
    queryKey: ['members', pid],
    queryFn: () => apiJson<Member[]>(`/api/v1/projects/${pid}/members`),
    enabled: Number.isFinite(pid),
  })

  const cases = useQuery({
    queryKey: ['test-cases-lite', pid],
    queryFn: () =>
      apiJson<PageRes<TestCaseLite>>(`/api/v1/projects/${pid}/test-cases/?page=1&page_size=100`),
    enabled: Number.isFinite(pid) && addOpen,
  })

  const memberMap = useMemo(() => {
    const m = new Map<number, string>()
    for (const x of members.data ?? []) m.set(x.user_id, x.username)
    return m
  }, [members.data])

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['cycle-items', pid, cid] })
    qc.invalidateQueries({ queryKey: ['test-cycle', pid, cid] })
    if (cycle.data?.plan_id) {
      qc.invalidateQueries({ queryKey: ['test-cycles', pid, cycle.data.plan_id] })
    }
  }

  const addItems = useMutation({
    mutationFn: (case_ids: number[]) =>
      apiJson<CycleItem[]>(`/api/v1/projects/${pid}/cycles/${cid}/items`, {
        method: 'POST',
        body: JSON.stringify({ case_ids }),
      }),
    onSuccess: (rows) => {
      message.success(rows.length ? `已加入 ${rows.length} 条用例` : '所选用例已在轮次中')
      setAddOpen(false)
      setAddCaseIds([])
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const assignMut = useMutation({
    mutationFn: (p: { item_ids: number[]; assignee_id: number | null }) =>
      apiJson<CycleItem[]>(`/api/v1/projects/${pid}/cycles/${cid}/items/assign`, {
        method: 'POST',
        body: JSON.stringify(p),
      }),
    onSuccess: () => {
      message.success('已指派')
      setSelectedRowKeys([])
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchItem = useMutation({
    mutationFn: (p: { itemId: number; result: string; actual_result: string }) =>
      apiJson<CycleItem>(`/api/v1/projects/${pid}/cycles/${cid}/items/${p.itemId}`, {
        method: 'PATCH',
        body: JSON.stringify({ result: p.result, actual_result: p.actual_result }),
      }),
    onSuccess: () => {
      message.success('执行结果已保存')
      setExecOpen(false)
      setExecItem(null)
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const createDefect = useMutation({
    mutationFn: (itemId: number) =>
      apiJson(`/api/v1/projects/${pid}/defects/from-cycle-item/${itemId}`, { method: 'POST' }),
    onSuccess: () => {
      message.success('缺陷已创建')
      qc.invalidateQueries({ queryKey: ['defects', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function uploadAttachment(itemId: number, file: File) {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await apiFetch(
        `/api/v1/projects/${pid}/cycles/${cid}/items/${itemId}/attachments`,
        { method: 'POST', body: fd },
      )
      if (!res.ok) {
        let msg = '上传失败'
        try {
          const j = (await res.json()) as { message?: string }
          msg = j.message ?? msg
        } catch {
          /* ignore */
        }
        message.error(msg)
        return
      }
      message.success('截图已上传')
      invalidateAll()
    } catch (e) {
      message.error(errText(e))
    }
  }

  const existingCaseIds = new Set((items.data?.items ?? []).map((i) => i.case_id))

  const columns: ColumnsType<CycleItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '用例',
      render: (_, row) => (
        <span>
          #{row.case_id} {row.case_title || ''}
        </span>
      ),
    },
    {
      title: '优先级',
      dataIndex: 'case_priority',
      width: 80,
      render: (v) => v || '—',
    },
    {
      title: '结果',
      dataIndex: 'result',
      width: 90,
      render: (r: string) => <Tag color={resultColor(r)}>{labelForExecutionResult(r)}</Tag>,
    },
    {
      title: '执行人',
      dataIndex: 'assignee_id',
      width: 110,
      render: (id: number | null) => (id ? memberMap.get(id) ?? `#${id}` : '—'),
    },
    {
      title: '实际结果',
      dataIndex: 'actual_result',
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: '截图',
      width: 80,
      render: (_, row) => row.attachments?.length ?? 0,
    },
    {
      title: '操作',
      width: 260,
      render: (_, row) => (
        <Space size="small" wrap>
          <Button
            type="link"
            size="small"
            onClick={() => {
              setExecItem(row)
              execForm.setFieldsValue({
                result: row.result === 'untested' ? 'passed' : row.result,
                actual_result: row.actual_result || '',
              })
              setExecOpen(true)
            }}
          >
            登记结果
          </Button>
          <Upload
            showUploadList={false}
            accept="image/png,image/jpeg,image/gif,image/webp"
            beforeUpload={(file) => {
              void uploadAttachment(row.id, file)
              return false
            }}
          >
            <Button type="link" size="small">
              上传截图
            </Button>
          </Upload>
          {row.result === 'failed' && (
            <Button
              type="link"
              size="small"
              danger
              loading={createDefect.isPending}
              onClick={() => createDefect.mutate(row.id)}
            >
              建缺陷
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const c = cycle.data

  return (
    <div>
      <PageBanner title={c ? `轮次：${c.name}` : '测试执行'} />
      <Space style={{ marginBottom: 16 }} wrap>
        {c && (
          <Link to={`/projects/${pid}/test-plans/${c.plan_id}`}>
            <Button>返回计划</Button>
          </Link>
        )}
        <Button type="primary" onClick={() => setAddOpen(true)}>
          加入用例
        </Button>
        <Select
          allowClear
          placeholder="按结果筛选"
          style={{ width: 140 }}
          options={EXECUTION_RESULT_OPTIONS}
          value={resultFilter}
          onChange={(v) => setResultFilter(v)}
        />
        <Select
          placeholder="批量指派给…"
          style={{ width: 180 }}
          disabled={!selectedRowKeys.length}
          options={(members.data ?? []).map((m) => ({
            value: m.user_id,
            label: m.username,
          }))}
          onChange={(uid: number) =>
            assignMut.mutate({
              item_ids: selectedRowKeys.map(Number),
              assignee_id: uid,
            })
          }
        />
      </Space>
      {c && (
        <div className="muted" style={{ marginBottom: 12 }}>
          共 {c.item_count} · 通过 {c.passed_count} · 失败 {c.failed_count} · 阻塞{' '}
          {c.blocked_count} · 未测 {c.untested_count}
        </div>
      )}
      <Table<CycleItem>
        rowKey="id"
        loading={items.isLoading}
        columns={columns}
        dataSource={items.data?.items ?? []}
        pagination={false}
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
        }}
      />

      <Modal
        title="加入用例"
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={() => {
          if (!addCaseIds.length) {
            message.warning('请选择用例')
            return
          }
          addItems.mutate(addCaseIds)
        }}
        confirmLoading={addItems.isPending}
        destroyOnHidden
        width={640}
      >
        <Select
          mode="multiple"
          style={{ width: '100%' }}
          placeholder="选择用例"
          value={addCaseIds}
          onChange={setAddCaseIds}
          optionFilterProp="label"
          options={(cases.data?.items ?? [])
            .filter((x) => !existingCaseIds.has(x.id))
            .map((x) => ({
              value: x.id,
              label: `#${x.id} ${x.title}`,
            }))}
        />
      </Modal>

      <Modal
        title={execItem ? `登记结果 · #${execItem.case_id}` : '登记结果'}
        open={execOpen}
        onCancel={() => setExecOpen(false)}
        onOk={() => execForm.submit()}
        confirmLoading={patchItem.isPending}
        destroyOnHidden
      >
        <Form
          form={execForm}
          layout="vertical"
          onFinish={(v) => {
            if (!execItem) return
            patchItem.mutate({
              itemId: execItem.id,
              result: v.result,
              actual_result: v.actual_result ?? '',
            })
          }}
        >
          <Form.Item name="result" label="结果" rules={[{ required: true }]}>
            <Select options={EXECUTION_RESULT_OPTIONS.filter((o) => o.value !== 'untested')} />
          </Form.Item>
          <Form.Item name="actual_result" label="实际结果">
            <Input.TextArea rows={4} placeholder="记录实际现象" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
