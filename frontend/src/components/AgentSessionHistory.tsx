import { DeleteOutlined, HistoryOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Popconfirm, Popover, Space, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { formatBeijing, parseApiDate } from '../utils/datetime'

export type AgentSessionSummary = {
  id: number
  title: string
  status?: string
  updated_at: string
  message_count?: number
  draft_count?: number
}

type Props = {
  items: AgentSessionSummary[]
  activeId: number | null
  loading?: boolean
  busy?: boolean
  onSelect: (id: number) => void
  onNew: () => void
  onDelete: (id: number) => void
  showStatus?: boolean
}

function statusColor(status: string | undefined): string {
  if (!status) return 'default'
  if (status === 'confirmed' || status === 'done' || status === 'passed') return 'success'
  if (status === 'error' || status === 'failed') return 'error'
  if (status === 'awaiting_confirm' || status === 'running' || status === 'awaiting_human') {
    return 'processing'
  }
  if (status === 'cancelled') return 'default'
  return 'blue'
}

function statusLabel(status: string | undefined): string {
  if (!status) return ''
  const map: Record<string, string> = {
    running: '进行中',
    awaiting_human: '待人工协助',
    awaiting_confirm: '待确认',
    confirmed: '已确认',
    cancelled: '已取消',
    error: '失败',
    done: '完成',
  }
  return map[status] || status
}

function formatUpdatedAt(iso: string): string {
  const d = parseApiDate(iso)
  if (!d) return ''
  const diff = Date.now() - d.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return formatBeijing(iso).slice(0, 10)
}

export default function AgentSessionHistory({
  items,
  activeId,
  loading,
  busy,
  onSelect,
  onNew,
  onDelete,
  showStatus = true,
}: Props) {
  const [open, setOpen] = useState(false)

  const panel = (
    <div className="agent-session-history-panel">
      <div className="agent-session-history-toolbar">
        <Typography.Text strong>会话历史</Typography.Text>
      </div>
      <div className="agent-session-history-list">
        {loading && items.length === 0 ? (
          <Typography.Text type="secondary" className="agent-session-history-empty">
            加载中…
          </Typography.Text>
        ) : items.length === 0 ? (
          <Typography.Text type="secondary" className="agent-session-history-empty">
            暂无历史会话
          </Typography.Text>
        ) : (
          items.map((item) => {
            const active = item.id === activeId
            return (
              <div
                key={item.id}
                className={`agent-session-history-item${active ? ' is-active' : ''}`}
                onClick={() => {
                  if (busy) return
                  if (!active) onSelect(item.id)
                  setOpen(false)
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if ((e.key === 'Enter' || e.key === ' ') && !busy) {
                    e.preventDefault()
                    if (!active) onSelect(item.id)
                    setOpen(false)
                  }
                }}
              >
                <div className="agent-session-history-item-main">
                  <Typography.Text
                    className="agent-session-history-title"
                    ellipsis={{ tooltip: item.title }}
                  >
                    {item.title || '新会话'}
                  </Typography.Text>
                  <Space size={4} wrap>
                    {showStatus && item.status ? (
                      <Tag color={statusColor(item.status)} style={{ marginInlineEnd: 0 }}>
                        {statusLabel(item.status)}
                      </Tag>
                    ) : null}
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {formatUpdatedAt(item.updated_at)}
                    </Typography.Text>
                  </Space>
                </div>
                <Popconfirm
                  title="删除此会话？"
                  okText="删除"
                  cancelText="取消"
                  disabled={busy}
                  onConfirm={(e) => {
                    e?.stopPropagation()
                    onDelete(item.id)
                  }}
                  onCancel={(e) => e?.stopPropagation()}
                >
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={busy}
                    className="agent-session-history-delete"
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>
              </div>
            )
          })
        )}
      </div>
    </div>
  )

  return (
    <Space size={0}>
      <Tooltip title="新会话">
        <Button
          type="text"
          size="small"
          icon={<PlusOutlined />}
          disabled={busy}
          onClick={onNew}
          aria-label="新会话"
        />
      </Tooltip>
      <Popover
        open={open}
        onOpenChange={setOpen}
        trigger="click"
        placement="bottomRight"
        arrow={false}
        overlayClassName="agent-session-history-popover"
        content={panel}
      >
        <Tooltip title="会话历史">
          <Button
            type="text"
            size="small"
            icon={<HistoryOutlined />}
            disabled={busy && !open}
            aria-label="会话历史"
          />
        </Tooltip>
      </Popover>
    </Space>
  )
}
