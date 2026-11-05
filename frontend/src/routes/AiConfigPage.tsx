import { App, Button, Select, Space, Spin, Table, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiJson, ApiError } from '../api/client'
import PageBanner from './PageBanner'

type Skill = {
  id: number
  skill_key: string
  name: string
  action: string
  version: number
  enabled: boolean
}

type ModelRef = { id: number; name: string; model: string | null; is_active: boolean }

type Binding = {
  id: number
  project_id: number
  action: string
  mode: string
  skill_version_id: number | null
  model_profile_id: number | null
}

type Overview = {
  actions: string[]
  bindings: Binding[]
  defaults: { project_id: number; default_model_profile_id: number | null }
  skills: Skill[]
  models: ModelRef[]
}

type DesignTask = {
  id: number
  created_at: string
  model: string | null
  status: string
  latency_ms: number | null
  total_tokens: number | null
  config_source: string | null
  skill_version_id: number | null
  resolved_config: Record<string, unknown> | null
}

type Resolved = {
  action: string
  mode: string
  source: string
  skill_name: string | null
  skill_version: number | null
  model_name: string | null
}

type Draft = { mode: string; skill_version_id?: number; model_profile_id?: number }

const ACTION_LABEL: Record<string, string> = {
  case_design: 'AI 用例设计',
  scheme: '生成测试方案',
  plan: '起草测试计划',
  review: '用例评审检查',
  defect_analysis: '缺陷分析',
  defect_submit: '缺陷提单整理',
  regression_report: '回归报告叙述',
}

const SOURCE_LABEL: Record<string, string> = {
  call: '本次指定',
  action: '动作绑定',
  project: '项目默认',
  system: '系统默认',
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

export default function AiConfigPage() {
  const { projectId } = useParams()
  const pid = Number(projectId)
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [defaultModel, setDefaultModel] = useState<number | undefined>()

  const overviewQuery = useQuery({
    queryKey: ['ai-config', pid],
    queryFn: () => apiJson<Overview>(`/api/v1/projects/${pid}/ai-config/`),
    enabled: Number.isFinite(pid),
  })

  const tasksQuery = useQuery({
    queryKey: ['ai-design-tasks', pid],
    queryFn: () =>
      apiJson<DesignTask[]>(`/api/v1/projects/${pid}/ai/design-tasks?limit=20`),
    enabled: Number.isFinite(pid),
  })

  const resolveQuery = useQuery({
    queryKey: ['ai-config-resolve', pid, overviewQuery.dataUpdatedAt],
    queryFn: async () => {
      const data = overviewQuery.data
      if (!data) return {} as Record<string, Resolved>
      const entries = await Promise.all(
        data.actions.map(async (action) => {
          const row = await apiJson<Resolved>(
            `/api/v1/projects/${pid}/ai-config/resolve?action=${action}`,
          ).catch(() => null)
          return [action, row] as const
        }),
      )
      return Object.fromEntries(entries.filter(([, row]) => row !== null)) as Record<
        string,
        Resolved
      >
    },
    enabled: Boolean(overviewQuery.data),
  })

  useEffect(() => {
    const data = overviewQuery.data
    if (!data) return
    const next: Record<string, Draft> = {}
    for (const action of data.actions) {
      const binding = data.bindings.find((b) => b.action === action)
      next[action] = {
        mode: binding?.mode ?? 'model',
        skill_version_id: binding?.skill_version_id ?? undefined,
        model_profile_id: binding?.model_profile_id ?? undefined,
      }
    }
    setDrafts(next)
    setDefaultModel(data.defaults.default_model_profile_id ?? undefined)
  }, [overviewQuery.data])

  function refresh() {
    void qc.invalidateQueries({ queryKey: ['ai-config', pid] })
  }

  const saveBinding = useMutation({
    mutationFn: (p: { action: string; draft: Draft }) =>
      apiJson<Binding>(`/api/v1/projects/${pid}/ai-config/bindings/${p.action}`, {
        method: 'PUT',
        body: JSON.stringify({
          mode: p.draft.mode,
          skill_version_id: p.draft.skill_version_id ?? null,
          model_profile_id: p.draft.model_profile_id ?? null,
        }),
      }),
    onSuccess: (_row, p) => {
      message.success(`${ACTION_LABEL[p.action] ?? p.action} 已保存`)
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const resetBinding = useMutation({
    mutationFn: (action: string) =>
      apiJson<void>(`/api/v1/projects/${pid}/ai-config/bindings/${action}`, { method: 'DELETE' }),
    onSuccess: () => {
      message.success('已恢复继承')
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  const saveDefault = useMutation({
    mutationFn: (modelProfileId: number | undefined) =>
      apiJson<unknown>(`/api/v1/projects/${pid}/ai-config/defaults`, {
        method: 'PUT',
        body: JSON.stringify({ default_model_profile_id: modelProfileId ?? null }),
      }),
    onSuccess: () => {
      message.success('项目默认模型已保存')
      refresh()
    },
    onError: (e) => message.error(errText(e)),
  })

  if (!Number.isFinite(pid)) return <p className="error">无效项目</p>

  const data = overviewQuery.data
  const bindings = new Map((data?.bindings ?? []).map((b) => [b.action, b]))

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="AI 与 Skill" />
        <p className="muted" style={{ marginTop: 0 }}>
          配置按「本次调用 → 动作绑定 → 项目默认 → 系统默认」解析；Skill 模式下仍使用所选模型。
        </p>
        <Space wrap>
          <span>项目默认模型</span>
          <Select
            allowClear
            style={{ width: 260 }}
            placeholder="继承系统默认"
            value={defaultModel}
            options={(data?.models ?? []).map((m) => ({
              value: m.id,
              label: `${m.name}${m.model ? ` · ${m.model}` : ''}${m.is_active ? '（系统启用）' : ''}`,
            }))}
            onChange={setDefaultModel}
          />
          <Button
            type="primary"
            loading={saveDefault.isPending}
            onClick={() => saveDefault.mutate(defaultModel)}
          >
            保存默认
          </Button>
        </Space>
      </div>
      <div className="list-page-body is-grid">
        <Spin spinning={overviewQuery.isLoading}>
          {!data ? null : (
            <div className="stack">
              {data.actions.map((action) => {
                const draft = drafts[action] ?? { mode: 'model' }
                const binding = bindings.get(action)
                const effective = resolveQuery.data?.[action]
                const actionSkills = data.skills.filter((s) => s.action === action)
                return (
                  <div key={action} className="card" style={{ marginBottom: 10 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        flexWrap: 'wrap',
                      }}
                    >
                      <div style={{ width: 170 }}>
                        <div style={{ fontWeight: 600 }}>{ACTION_LABEL[action] ?? action}</div>
                        <div className="muted" style={{ fontSize: 12 }}>
                          {binding ? '已绑定' : '继承默认'} ·{' '}
                          {effective
                            ? `生效：${SOURCE_LABEL[effective.source] ?? effective.source}`
                            : '未解析'}
                          {effective?.skill_name
                            ? ` · ${effective.skill_name} v${effective.skill_version}`
                            : ''}
                        </div>
                      </div>
                      <Select
                        style={{ width: 150 }}
                        value={draft.mode}
                        options={[
                          { value: 'model', label: '直接使用模型' },
                          { value: 'skill', label: '使用 Skill' },
                        ]}
                        onChange={(mode) =>
                          setDrafts((prev) => ({ ...prev, [action]: { ...draft, mode } }))
                        }
                      />
                      {draft.mode === 'skill' ? (
                        <Select
                          allowClear
                          style={{ width: 210 }}
                          placeholder={
                            actionSkills.length ? '选择 Skill 版本' : '该动作暂无 Skill'
                          }
                          value={draft.skill_version_id}
                          options={actionSkills.map((s) => ({
                            value: s.id,
                            label: `${s.name} v${s.version}`,
                          }))}
                          onChange={(id) =>
                            setDrafts((prev) => ({
                              ...prev,
                              [action]: { ...draft, skill_version_id: id },
                            }))
                          }
                        />
                      ) : null}
                      <Select
                        allowClear
                        style={{ width: 240 }}
                        placeholder="模型：继承默认"
                        value={draft.model_profile_id}
                        options={data.models.map((m) => ({
                          value: m.id,
                          label: `${m.name}${m.model ? ` · ${m.model}` : ''}`,
                        }))}
                        onChange={(id) =>
                          setDrafts((prev) => ({
                            ...prev,
                            [action]: { ...draft, model_profile_id: id },
                          }))
                        }
                      />
                      <Button
                        type="primary"
                        size="small"
                        loading={saveBinding.isPending}
                        onClick={() => saveBinding.mutate({ action, draft })}
                      >
                        保存
                      </Button>
                      {binding ? (
                        <Button
                          type="text"
                          size="small"
                          danger
                          onClick={() => resetBinding.mutate(action)}
                        >
                          恢复继承
                        </Button>
                      ) : null}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Spin>
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>最近设计任务</div>
          <Table<DesignTask>
            rowKey="id"
            size="small"
            loading={tasksQuery.isLoading}
            dataSource={tasksQuery.data ?? []}
            pagination={false}
            locale={{ emptyText: '暂无调用记录' }}
            columns={[
              {
                title: '时间',
                dataIndex: 'created_at',
                width: 170,
                render: (v: string) => new Date(v).toLocaleString('zh-CN'),
              },
              { title: '模型', dataIndex: 'model', width: 180, render: (v: string | null) => v || '—' },
              {
                title: '来源',
                dataIndex: 'config_source',
                width: 100,
                render: (v: string | null) => (v ? SOURCE_LABEL[v] ?? v : '—'),
              },
              {
                title: 'Skill',
                dataIndex: 'skill_version_id',
                width: 90,
                render: (v: number | null) => (v ? `#${v}` : '—'),
              },
              { title: '状态', dataIndex: 'status', width: 90 },
              {
                title: '耗时(ms)',
                dataIndex: 'latency_ms',
                width: 100,
                render: (v: number | null) => (v == null ? '—' : v),
              },
              {
                title: 'Tokens',
                dataIndex: 'total_tokens',
                width: 100,
                render: (v: number | null) => (v == null ? '—' : v),
              },
            ]}
          />
        </div>
        {data && data.models.length === 0 ? (
          <Typography.Text type="warning">
            还没有可用的模型配置。请先在「系统 AI 配置」中创建并启用模型。
          </Typography.Text>
        ) : null}
      </div>
    </div>
  )
}
