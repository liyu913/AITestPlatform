import { App, Button, Input, Modal, Space, Spin, Tag } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

type QueueItem = {
  case_id: number
  title: string
  version_no: number
  submitted_at: string | null
  submitted_by_id: number | null
  review_count: number
  assigned_reviewer_id: number | null
  assigned_reviewer_name: string | null
  due_at: string | null
}

type Decision = 'approved' | 'rejected'

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function ReviewQueue() {
  const { projectId } = useParams()
  const pid = Number(projectId)
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [target, setTarget] = useState<{ row: QueueItem; decision: Decision } | null>(null)
  const [comment, setComment] = useState('')

  const queueQuery = useQuery({
    queryKey: ['review-queue', pid],
    queryFn: () => apiJson<QueueItem[]>(`/api/v1/projects/${pid}/test-cases/review-queue`),
    enabled: Number.isFinite(pid),
  })

  const decideMut = useMutation({
    mutationFn: (p: { caseId: number; decision: Decision; comment: string }) =>
      apiJson<unknown>(`/api/v1/projects/${pid}/test-cases/${p.caseId}/review`, {
        method: 'POST',
        body: JSON.stringify({ decision: p.decision, comment: p.comment }),
      }),
    onSuccess: (_d, p) => {
      message.success(p.decision === 'approved' ? '已通过，版本可以发布' : '已驳回，退回草稿')
      setTarget(null)
      setComment('')
      void qc.invalidateQueries({ queryKey: ['review-queue', pid] })
      void qc.invalidateQueries({ queryKey: ['testcases', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  function openDecision(row: QueueItem, decision: Decision) {
    setTarget({ row, decision })
    setComment('')
  }

  function submitDecision() {
    if (!target) return
    if (target.decision === 'rejected' && !comment.trim()) {
      message.warning('驳回需要填写评审意见')
      return
    }
    decideMut.mutate({
      caseId: target.row.case_id,
      decision: target.decision,
      comment: comment.trim(),
    })
  }

  if (!Number.isFinite(pid)) return <p className="error">无效项目</p>

  const items = queueQuery.data ?? []

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="用例评审" />
        <p className="muted" style={{ marginTop: 0 }}>
          待评审版本按提交时间排列。通过后版本可以发布到正式库；驳回会退回草稿并保留意见。
        </p>
      </div>
      <div className="list-page-body is-grid">
        <Spin spinning={queueQuery.isLoading}>
          {items.length === 0 && !queueQuery.isLoading ? (
            <p className="muted">暂无待评审的用例版本。</p>
          ) : (
            <div className="list-card-grid">
              {items.map((row) => (
                <article key={row.case_id} className="list-card">
                  <h3 className="list-card-title" title={row.title}>
                    {row.title}
                  </h3>
                  <div className="list-card-meta">
                    <Tag color="processing">待评审 v{row.version_no}</Tag>
                    <span>{row.submitted_at ? formatBeijing(row.submitted_at) : '—'}</span>
                    <span>已有 {row.review_count} 条评审记录</span>
                    <span>评审人：{row.assigned_reviewer_name ?? '未指派'}</span>
                    {row.due_at ? <span>截止 {formatBeijing(row.due_at)}</span> : null}
                  </div>
                  <div className="list-card-actions-row">
                    <Space size={4} wrap>
                      <Button
                        type="primary"
                        size="small"
                        onClick={() => openDecision(row, 'approved')}
                      >
                        通过
                      </Button>
                      <Button
                        type="text"
                        size="small"
                        danger
                        onClick={() => openDecision(row, 'rejected')}
                      >
                        驳回
                      </Button>
                      <Link to={`/projects/${pid}/test-cases?case=${row.case_id}`}>
                        <Button type="text" size="small">
                          查看用例
                        </Button>
                      </Link>
                    </Space>
                  </div>
                </article>
              ))}
            </div>
          )}
        </Spin>
      </div>

      <Modal
        title={
          target?.decision === 'approved'
            ? `通过评审 · ${target?.row.title}`
            : `驳回评审 · ${target?.row.title}`
        }
        open={Boolean(target)}
        onCancel={() => setTarget(null)}
        onOk={submitDecision}
        confirmLoading={decideMut.isPending}
        okText={target?.decision === 'approved' ? '通过' : '驳回'}
        okButtonProps={{ danger: target?.decision === 'rejected' }}
        cancelText="取消"
        destroyOnHidden
      >
        <p className="muted">
          评审结论绑定版本 v{target?.row.version_no}，后续修改不会改变本次结论。
        </p>
        <Input.TextArea
          rows={4}
          maxLength={65535}
          placeholder={target?.decision === 'rejected' ? '必填：说明需要修改的内容' : '可选：补充评审说明'}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </Modal>
    </div>
  )
}
