import {
  Alert,
  Button,
  Card,
  Col,
  List,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiJson } from '../api/client'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'
import { pct, type ReportSummary } from './Reports'

type MetricDelta = {
  key: string
  label: string
  current: number | null
  previous: number | null
  delta: number | null
}

type RequirementChange = {
  requirement_id: number
  title: string
  previous_ratio: number | null
  current_ratio: number | null
  previous_case_total: number
  current_case_total: number
}

type CompareResult = {
  cycle_id: number
  current_id: number
  previous_id: number
  current_revision: number
  previous_revision: number
  metrics: MetricDelta[]
  risks_added: string[]
  risks_resolved: string[]
  requirements_changed: RequirementChange[]
}

type RequirementRow = {
  requirement_id: number
  title: string
  coverage_kind: 'case' | 'rule' | 'none'
  case_total: number
  passed: number
  failed: number
  blocked: number
  untested: number
  coverage_ratio: number
  blind: boolean
}

type RetryRow = {
  item_id: number
  title: string
  first_failed_attempt: number
  first_failure: string
  attempts: number
}

type RiskRow = { code: string; message: string; severity: string }

type Snapshot = {
  generated_at: string
  cycle: {
    id: number
    name: string
    plan_name: string | null
    target_build: string | null
    env_snapshot: { name?: string | null; base_url?: string | null } | null
    data_note: string
  }
  metrics: {
    selected_total: number
    passed: number
    failed: number
    blocked: number
    untested: number
    running: number
    skipped: number
    pass_rate: number | null
    completion_rate: number | null
    retried_to_pass: number
    flaky: number
  }
  requirements: RequirementRow[]
  attempts: { retried_to_pass: RetryRow[]; flaky: RetryRow[]; running: number }
  defects: { linked: number; by_status: Record<string, number>; unresolved: { id: number; title: string; status: string }[] }
  risks: RiskRow[]
}

type Report = ReportSummary & { snapshot: Snapshot | null }

const COVERAGE_LABEL: Record<string, string> = {
  case: '用例关联覆盖',
  rule: '规则覆盖',
  none: '无覆盖',
}

function riskColor(severity: string): string {
  if (severity === 'high') return 'error'
  if (severity === 'medium') return 'warning'
  return 'default'
}

export default function ReportDetail() {
  const { projectId, reportId } = useParams()
  const pid = Number(projectId)
  const rid = Number(reportId)
  const nav = useNavigate()

  const query = useQuery({
    queryKey: ['report', pid, rid],
    queryFn: () => apiJson<Report>(`/api/v1/projects/${pid}/reports/${rid}`),
    enabled: Number.isFinite(pid) && Number.isFinite(rid),
  })

  const cycleId = query.data?.cycle_id
  const revisions = useQuery({
    queryKey: ['report-revisions', pid, cycleId],
    queryFn: () => apiJson<ReportSummary[]>(`/api/v1/projects/${pid}/reports/cycles/${cycleId}`),
    enabled: Number.isFinite(pid) && Boolean(cycleId),
  })

  const report = query.data
  const snapshot = report?.snapshot
  const metrics = snapshot?.metrics

  const [compareId, setCompareId] = useState<number | null>(null)
  const compareQuery = useQuery({
    queryKey: ['report-compare', pid, rid, compareId],
    queryFn: () =>
      apiJson<CompareResult>(
        `/api/v1/projects/${pid}/reports/${rid}/compare?against=${compareId}`,
      ),
    enabled: Number.isFinite(pid) && Number.isFinite(rid) && compareId !== null,
  })

  const compareColumns: ColumnsType<MetricDelta> = [
    { title: '指标', dataIndex: 'label', width: 140 },
    { title: '上一版', dataIndex: 'previous', width: 110, render: (v) => (v === null ? '—' : v) },
    { title: '当前', dataIndex: 'current', width: 110, render: (v) => (v === null ? '—' : v) },
    {
      title: '变化',
      dataIndex: 'delta',
      width: 120,
      render: (v: number | null) => {
        if (v === null) return '—'
        if (v === 0) return <span className="muted">0</span>
        return (
          <Tag color={v > 0 ? 'success' : 'error'}>
            {v > 0 ? '+' : ''}
            {v}
          </Tag>
        )
      },
    },
  ]

  const coverageColumns: ColumnsType<RequirementRow> = [
    { title: '需求', dataIndex: 'title', ellipsis: true },
    {
      title: '覆盖方式',
      dataIndex: 'coverage_kind',
      width: 130,
      render: (kind: string) => (
        <Tag color={kind === 'case' ? 'blue' : kind === 'rule' ? 'purple' : 'default'}>
          {COVERAGE_LABEL[kind] ?? kind}
        </Tag>
      ),
    },
    { title: '用例数', dataIndex: 'case_total', width: 80 },
    { title: '通过', dataIndex: 'passed', width: 70 },
    { title: '失败', dataIndex: 'failed', width: 70 },
    { title: '阻塞', dataIndex: 'blocked', width: 70 },
    { title: '未测', dataIndex: 'untested', width: 70 },
    {
      title: '盲区',
      dataIndex: 'blind',
      width: 70,
      render: (blind: boolean) => (blind ? <Tag color="warning">是</Tag> : <Tag>否</Tag>),
    },
  ]

  const retryColumns: ColumnsType<RetryRow> = [
    { title: '执行项', dataIndex: 'title', ellipsis: true },
    { title: '首次失败尝试', dataIndex: 'first_failed_attempt', width: 120 },
    { title: '首次失败', dataIndex: 'first_failure', ellipsis: true },
    { title: '尝试次数', dataIndex: 'attempts', width: 90 },
  ]

  return (
    <div className="dashboard-page">
      <PageBanner title={report ? `报告：${report.cycle_name ?? ''}` : '测试报告'} />
      {query.isError ? (
        <Alert type="error" showIcon message="报告加载失败" />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Card size="small" loading={query.isLoading}>
            <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
              <Space wrap>
                <Tag color="blue">r{report?.revision ?? '—'}</Tag>
                <span>计划：{report?.plan_name || '—'}</span>
                <span>构建：{report?.target_build || '未知'}</span>
                <span>环境：{snapshot?.cycle.env_snapshot?.name || '—'}</span>
                <span className="muted">生成于 {report ? formatBeijing(report.generated_at) : '—'}</span>
              </Space>
              <Space>
                <Select
                  style={{ width: 150 }}
                  value={rid}
                  loading={revisions.isLoading}
                  onChange={(value) => nav(`/projects/${pid}/reports/${value}`)}
                  options={(revisions.data ?? []).map((row) => ({
                    value: row.id,
                    label: `r${row.revision} · ${formatBeijing(row.generated_at)}`,
                  }))}
                />
                <Select
                  allowClear
                  placeholder="对比历史版本"
                  style={{ width: 170 }}
                  value={compareId ?? undefined}
                  loading={revisions.isLoading}
                  options={(revisions.data ?? [])
                    .filter((row) => row.id !== rid)
                    .map((row) => ({
                      value: row.id,
                      label: `对比 r${row.revision}`,
                    }))}
                  onChange={(value) => setCompareId(value ?? null)}
                />
                <Button
                  href={`/api/v1/projects/${pid}/reports/${rid}/export?format=md`}
                  target="_blank"
                >
                  导出 Markdown
                </Button>
                <Button
                  type="primary"
                  href={`/api/v1/projects/${pid}/reports/${rid}/export?format=xlsx`}
                  target="_blank"
                >
                  导出 Excel
                </Button>
                {snapshot?.cycle.id ? (
                  <Link to={`/projects/${pid}/cycles/${snapshot.cycle.id}`}>回到批次</Link>
                ) : null}
              </Space>
            </Space>
          </Card>

          <Row gutter={[12, 12]}>
            {[
              { title: '选定计划项', value: metrics?.selected_total ?? 0 },
              { title: '通过率', value: pct(metrics?.pass_rate), raw: true },
              { title: '完成比例', value: pct(metrics?.completion_rate), raw: true },
              { title: '通过 / 失败', value: `${metrics?.passed ?? 0} / ${metrics?.failed ?? 0}`, raw: true },
              { title: '阻塞', value: metrics?.blocked ?? 0 },
              { title: '未执行', value: metrics?.untested ?? 0 },
              { title: '运行中 / 跳过', value: `${metrics?.running ?? 0} / ${metrics?.skipped ?? 0}`, raw: true },
              { title: '重试后通过', value: metrics?.retried_to_pass ?? 0 },
            ].map((item) => (
              <Col key={item.title} xs={12} sm={8} lg={3}>
                <Card size="small">
                  {item.raw ? (
                    <>
                      <div className="muted" style={{ marginBottom: 4 }}>
                        {item.title}
                      </div>
                      <strong style={{ fontSize: 20 }}>{item.value}</strong>
                    </>
                  ) : (
                    <Statistic title={item.title} value={item.value} />
                  )}
                </Card>
              </Col>
            ))}
          </Row>

          <Card size="small" title={`遗留风险（${snapshot?.risks.length ?? 0}）`}>
            {(snapshot?.risks.length ?? 0) === 0 ? (
              <Typography.Text type="secondary">无遗留风险</Typography.Text>
            ) : (
              <List
                size="small"
                dataSource={snapshot?.risks ?? []}
                renderItem={(risk) => (
                  <List.Item>
                    <Space>
                      <Tag color={riskColor(risk.severity)}>{risk.severity}</Tag>
                      <span>{risk.message}</span>
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </Card>

          <Card size="small" title="需求覆盖">
            <Table<RequirementRow>
              rowKey="requirement_id"
              size="small"
              columns={coverageColumns}
              dataSource={snapshot?.requirements ?? []}
              pagination={false}
              locale={{ emptyText: '暂无需求' }}
            />
          </Card>

          <Card size="small" title="重试与不稳定（首次失败保留）">
            <Table<RetryRow>
              rowKey="item_id"
              size="small"
              columns={retryColumns}
              dataSource={snapshot?.attempts.retried_to_pass ?? []}
              pagination={false}
              locale={{ emptyText: '没有重试后通过的计划项' }}
            />
          </Card>

          <Card size="small" title={`关联缺陷（${snapshot?.defects.linked ?? 0}）`}>
            {(snapshot?.defects.unresolved.length ?? 0) === 0 ? (
              <Typography.Text type="secondary">关联缺陷已全部关闭</Typography.Text>
            ) : (
              <List
                size="small"
                dataSource={snapshot?.defects.unresolved ?? []}
                renderItem={(row) => (
                  <List.Item>
                    <Space>
                      <Tag>{row.status}</Tag>
                      <Link to={`/projects/${pid}/defects`}>{row.title}</Link>
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Space>
      )}

      <Modal
        title={
          compareQuery.data
            ? `版本对比：r${compareQuery.data.previous_revision} → r${compareQuery.data.current_revision}`
            : '版本对比'
        }
        open={compareId !== null}
        onCancel={() => setCompareId(null)}
        footer={<Button onClick={() => setCompareId(null)}>关闭</Button>}
        width={780}
        destroyOnHidden
      >
        {compareQuery.isLoading ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : compareQuery.isError ? (
          <Alert type="error" showIcon message="对比加载失败" />
        ) : compareQuery.data ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Table<MetricDelta>
              rowKey="key"
              size="small"
              pagination={false}
              columns={compareColumns}
              dataSource={compareQuery.data.metrics}
            />
            <Card size="small" title={`新增风险（${compareQuery.data.risks_added.length}）`}>
              {compareQuery.data.risks_added.length === 0 ? (
                <Typography.Text type="secondary">无</Typography.Text>
              ) : (
                <List
                  size="small"
                  dataSource={compareQuery.data.risks_added}
                  renderItem={(item) => <List.Item>{item}</List.Item>}
                />
              )}
            </Card>
            <Card size="small" title={`已消除风险（${compareQuery.data.risks_resolved.length}）`}>
              {compareQuery.data.risks_resolved.length === 0 ? (
                <Typography.Text type="secondary">无</Typography.Text>
              ) : (
                <List
                  size="small"
                  dataSource={compareQuery.data.risks_resolved}
                  renderItem={(item) => <List.Item>{item}</List.Item>}
                />
              )}
            </Card>
            <Card size="small" title={`覆盖变化（${compareQuery.data.requirements_changed.length}）`}>
              {compareQuery.data.requirements_changed.length === 0 ? (
                <Typography.Text type="secondary">无</Typography.Text>
              ) : (
                <Table<RequirementChange>
                  rowKey="requirement_id"
                  size="small"
                  pagination={false}
                  dataSource={compareQuery.data.requirements_changed}
                  columns={[
                    { title: '需求', dataIndex: 'title', ellipsis: true },
                    {
                      title: '用例数',
                      width: 120,
                      render: (_, row) => `${row.previous_case_total} → ${row.current_case_total}`,
                    },
                    {
                      title: '覆盖率',
                      width: 140,
                      render: (_, row) =>
                        `${pct(row.previous_ratio)} → ${pct(row.current_ratio)}`,
                    },
                  ]}
                />
              )}
            </Card>
          </Space>
        ) : null}
      </Modal>
    </div>
  )
}
