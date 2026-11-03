import { App, Button, Descriptions, Drawer, Input, Select, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'

type LogRow = {
  id: number
  created_at: string | null
  kind: string
  scene: string | null
  model: string | null
  status: string
  http_status: number | null
  latency_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  user_id: number | null
  username: string | null
  project_id: number | null
  project_name: string | null
  request_id: string | null
}

type LogDetail = LogRow & {
  error_message: string | null
  request_text: string | null
  response_text: string | null
}

type LogListResponse = {
  items: LogRow[]
  total: number
  page: number
  page_size: number
}

const KIND_LABEL: Record<string, string> = {
  chat: '对话',
  chat_stream: '流式对话',
  embedding: '向量',
}

function formatTime(iso: string | null): string {
  return formatBeijing(iso)
}

function prettyJson(text: string): string | null {
  try {
    const value = JSON.parse(text) as unknown
    if (value === null || typeof value !== 'object') return null
    return JSON.stringify(value, null, 2)
  } catch {
    return null
  }
}

function jsonSpanEnd(text: string, start: number): number {
  const stack: string[] = []
  let inString = false
  let escaped = false
  for (let i = start; i < text.length; i++) {
    const ch = text[i]
    if (inString) {
      if (escaped) {
        escaped = false
        continue
      }
      if (ch === '\\') {
        escaped = true
        continue
      }
      if (ch === '"') inString = false
      continue
    }
    if (ch === '"') {
      inString = true
      continue
    }
    if (ch === '{') stack.push('}')
    else if (ch === '[') stack.push(']')
    else if (ch === '}' || ch === ']') {
      if (stack.length === 0 || stack[stack.length - 1] !== ch) return -1
      stack.pop()
      if (stack.length === 0) return i + 1
    }
  }
  return -1
}

function formatLogText(text: string): string {
  const whole = prettyJson(text.trim())
  if (whole) return whole
  let out = ''
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === '{' || ch === '[') {
      const end = jsonSpanEnd(text, i)
      if (end > i) {
        const pretty = prettyJson(text.slice(i, end))
        if (pretty) {
          out += pretty
          i = end
          continue
        }
      }
    }
    out += ch
    i++
  }
  return out
}

function LogBlock({ title, text }: { title: string; text: string | null }) {
  const { message } = App.useApp()
  const shown = text?.trim() ? formatLogText(text) : ''

  async function copy() {
    try {
      await navigator.clipboard.writeText(shown)
      message.success('已复制')
    } catch {
      message.error('复制失败')
    }
  }

  return (
    <section className="ai-log-block">
      <div className="ai-log-head">
        <h3>{title}</h3>
        <Button size="small" onClick={() => void copy()} disabled={!shown}>
          复制
        </Button>
      </div>
      <pre className="ai-log-pre">{shown || '—'}</pre>
    </section>
  )
}

function tokenText(row: LogRow): string {
  if (row.total_tokens == null && row.prompt_tokens == null && row.completion_tokens == null) {
    return '—'
  }
  const prompt = row.prompt_tokens ?? '—'
  const completion = row.completion_tokens ?? '—'
  const total = row.total_tokens ?? '—'
  return `${prompt} / ${completion} / ${total}`
}

export default function AdminAiCallLogs() {
  const [page, setPage] = useState(1)
  const [kind, setKind] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [scene, setScene] = useState('')
  const [sceneQuery, setSceneQuery] = useState('')
  const [openId, setOpenId] = useState<number | null>(null)
  const pageSize = 20

  const listQuery = useQuery({
    queryKey: ['admin', 'ai-call-logs', page, kind, status, sceneQuery],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (kind) qs.set('kind', kind)
      if (status) qs.set('status', status)
      if (sceneQuery) qs.set('scene', sceneQuery)
      return apiJson<LogListResponse>(`/api/v1/admin/ai-call-logs?${qs}`)
    },
  })

  const detailQuery = useQuery({
    queryKey: ['admin', 'ai-call-logs', 'detail', openId],
    queryFn: () => apiJson<LogDetail>(`/api/v1/admin/ai-call-logs/${openId}`),
    enabled: openId != null,
  })

  const columns: ColumnsType<LogRow> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 180,
      render: (v: string | null) => formatTime(v),
    },
    {
      title: '场景',
      dataIndex: 'scene',
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      render: (v: string) => KIND_LABEL[v] || v,
    },
    {
      title: '模型',
      dataIndex: 'model',
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) =>
        v === 'success' ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>,
    },
    {
      title: '耗时',
      dataIndex: 'latency_ms',
      width: 100,
      render: (v: number | null) => (v == null ? '—' : `${v} ms`),
    },
    {
      title: 'Token',
      key: 'tokens',
      width: 140,
      render: (_: unknown, row: LogRow) => tokenText(row),
    },
    {
      title: '用户',
      dataIndex: 'username',
      width: 120,
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
    {
      title: '项目',
      dataIndex: 'project_name',
      width: 140,
      ellipsis: true,
      render: (v: string | null) => v || '—',
    },
  ]

  const detail = detailQuery.data

  return (
    <div>
      <h1>AI 调用日志</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        记录每次大模型对话和向量调用。请求与响应正文各保留约 8KB，不保存 API Key 和向量本身。
      </p>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="类型"
          style={{ width: 140 }}
          value={kind}
          onChange={(value) => {
            setKind(value)
            setPage(1)
          }}
          options={[
            { value: 'chat', label: '对话' },
            { value: 'chat_stream', label: '流式对话' },
            { value: 'embedding', label: '向量' },
          ]}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={(value) => {
            setStatus(value)
            setPage(1)
          }}
          options={[
            { value: 'success', label: '成功' },
            { value: 'error', label: '失败' },
          ]}
        />
        <Input
          allowClear
          placeholder="场景"
          style={{ width: 200 }}
          value={scene}
          onChange={(e) => setScene(e.target.value)}
          onPressEnter={() => {
            setSceneQuery(scene.trim())
            setPage(1)
          }}
        />
        <Button
          type="primary"
          onClick={() => {
            setSceneQuery(scene.trim())
            setPage(1)
          }}
        >
          查询
        </Button>
      </Space>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={listQuery.data?.items ?? []}
        loading={listQuery.isLoading}
        onRow={(row: LogRow) => ({
          onClick: () => setOpenId(row.id),
          style: { cursor: 'pointer' },
        })}
        pagination={{
          current: page,
          pageSize,
          total: listQuery.data?.total ?? 0,
          showSizeChanger: false,
          onChange: (p) => setPage(p),
        }}
      />
      <Drawer
        title={openId != null ? `调用 #${openId}` : '调用详情'}
        open={openId != null}
        width={840}
        onClose={() => setOpenId(null)}
        destroyOnHidden
      >
        {detailQuery.isLoading && <p className="muted">加载中…</p>}
        {detailQuery.isError && <p className="error">无法加载这条日志。</p>}
        {detail && (
          <>
            <Descriptions column={2} size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="时间">{formatTime(detail.created_at)}</Descriptions.Item>
              <Descriptions.Item label="场景">{detail.scene || '—'}</Descriptions.Item>
              <Descriptions.Item label="类型">{KIND_LABEL[detail.kind] || detail.kind}</Descriptions.Item>
              <Descriptions.Item label="模型">{detail.model || '—'}</Descriptions.Item>
              <Descriptions.Item label="状态">
                {detail.status === 'success' ? '成功' : '失败'}
              </Descriptions.Item>
              <Descriptions.Item label="HTTP">{detail.http_status ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="耗时">
                {detail.latency_ms == null ? '—' : `${detail.latency_ms} ms`}
              </Descriptions.Item>
              <Descriptions.Item label="Token">{tokenText(detail)}</Descriptions.Item>
              <Descriptions.Item label="用户">{detail.username || '—'}</Descriptions.Item>
              <Descriptions.Item label="项目">{detail.project_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="请求号" span={2}>
                {detail.request_id || '—'}
              </Descriptions.Item>
            </Descriptions>
            {detail.error_message && <LogBlock title="错误" text={detail.error_message} />}
            <LogBlock title="请求" text={detail.request_text} />
            <LogBlock title="响应" text={detail.response_text} />
          </>
        )}
      </Drawer>
    </div>
  )
}
