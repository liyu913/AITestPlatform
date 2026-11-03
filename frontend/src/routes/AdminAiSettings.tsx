import {
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, apiJson } from '../api/client'

type LlmProfile = {
  id: number
  name: string
  api_base: string
  model: string
  allowed_models: string
  api_key: string
  has_api_key: boolean
  is_active: boolean
}

type EmbeddingProfile = {
  id: number
  name: string
  api_base: string
  model: string
  dim: number
  api_key: string
  has_api_key: boolean
  is_active: boolean
  warning?: string | null
}

type MidsceneProfile = {
  id: number
  name: string
  api_base: string
  model: string
  model_family: string
  api_key: string
  has_api_key: boolean
  is_active: boolean
}

type LlmForm = {
  name: string
  api_base: string
  model: string
  allowed_models: string
  api_key: string
  preset?: string
}

type EmbForm = {
  name: string
  api_base: string
  model: string
  dim: number
  api_key: string
  preset?: string
}

type MidsceneForm = {
  name: string
  api_base: string
  model: string
  model_family: string
  api_key: string
  preset?: string
}

const LLM_PRESETS = [
  {
    key: 'dashscope',
    label: '阿里云通义',
    apiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3.7-plus',
    allowedModels: 'qwen3.7-plus,qwen3-max,qwen-plus,qwen-vl-plus',
  },
  {
    key: 'deepseek',
    label: 'DeepSeek',
    apiBase: 'https://api.deepseek.com/v1',
    model: 'deepseek-flash',
    allowedModels: 'deepseek-flash,deepseek-v4-pro',
  },
  {
    key: 'openai',
    label: 'OpenAI',
    apiBase: 'https://api.openai.com/v1',
    model: 'gpt-5.4-mini',
    allowedModels: 'gpt-5.4-mini,gpt-5.4',
  },
  {
    key: 'moonshot',
    label: '月之暗面 Kimi',
    apiBase: 'https://api.moonshot.cn/v1',
    model: 'kimi-k3',
    allowedModels: 'kimi-k3,kimi-k2.6',
  },
  {
    key: 'zhipu',
    label: '智谱 GLM',
    apiBase: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4.7-flash',
    allowedModels: 'glm-4.7-flash,glm-4.6,glm-4.7',
  },
]

const MIDSCENE_PRESETS = [
  {
    key: 'qwen3',
    label: '通义 Qwen3（推荐）',
    apiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3.7-plus',
    modelFamily: 'qwen3',
  },
  {
    key: 'qwen3-vl',
    label: '通义 Qwen3-VL',
    apiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3-vl-plus',
    modelFamily: 'qwen3-vl',
  },
  {
    key: 'qwen25-vl',
    label: '通义 Qwen2.5-VL',
    apiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-vl-plus',
    modelFamily: 'qwen2.5-vl',
  },
  {
    key: 'doubao-seed',
    label: '豆包 Seed',
    apiBase: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-2-1-turbo-260628',
    modelFamily: 'doubao-seed',
  },
  {
    key: 'deepseek',
    label: 'DeepSeek 视觉',
    apiBase: 'https://api.deepseek.com',
    model: 'deepseek-flash',
    modelFamily: 'deepseek',
  },
  {
    key: 'gemini',
    label: 'Google Gemini',
    apiBase: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    model: 'gemini-3.5-flash',
    modelFamily: 'gemini',
  },
  {
    key: 'gpt',
    label: 'OpenAI GPT-5',
    apiBase: 'https://api.openai.com/v1',
    model: 'gpt-5.4',
    modelFamily: 'gpt-5',
  },
  {
    key: 'kimi',
    label: '月之暗面 Kimi',
    apiBase: 'https://api.moonshot.cn/v1',
    model: 'kimi-k2.5',
    modelFamily: 'kimi',
  },
  {
    key: 'glm-v',
    label: '智谱 GLM-V',
    apiBase: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4.6v',
    modelFamily: 'glm-v',
  },
  {
    key: 'ui-tars',
    label: 'UI-TARS（火山）',
    apiBase: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-1.5-ui-tars',
    modelFamily: 'vlm-ui-tars-doubao-1.5',
  },
]

const EMBEDDING_PRESETS = [
  {
    key: 'dashscope',
    label: '阿里云通义',
    apiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'text-embedding-v2',
    dim: 1536,
  },
  {
    key: 'openai',
    label: 'OpenAI',
    apiBase: 'https://api.openai.com/v1',
    model: 'text-embedding-3-small',
    dim: 1536,
  },
  {
    key: 'zhipu',
    label: '智谱 GLM',
    apiBase: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'embedding-3',
    dim: 2048,
  },
  {
    key: 'moonshot',
    label: '月之暗面 Kimi',
    apiBase: 'https://api.moonshot.cn/v1',
    model: 'moonshot-v1-embedding',
    dim: 1024,
  },
]

function errText(ex: unknown): string {
  if (ex instanceof ApiError) {
    if (ex.status === 401) return '登录已失效，请重新登录。'
    if (ex.status === 403) return '无权限访问该接口（需要超级管理员）。'
    if (typeof ex.body === 'object' && ex.body !== null) {
      const m = ex.body as { message?: string }
      if (m.message) return m.message
    }
    if (ex.status >= 500) {
      return `服务端错误（HTTP ${ex.status}）。若为首次部署，请执行 alembic upgrade head。`
    }
    return `请求失败（HTTP ${ex.status}）。`
  }
  return '请求失败，请检查网络与后端是否可达。'
}

export default function AdminAiSettings() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [llmOpen, setLlmOpen] = useState(false)
  const [embOpen, setEmbOpen] = useState(false)
  const [midOpen, setMidOpen] = useState(false)
  const [editingLlm, setEditingLlm] = useState<LlmProfile | null>(null)
  const [editingEmb, setEditingEmb] = useState<EmbeddingProfile | null>(null)
  const [editingMid, setEditingMid] = useState<MidsceneProfile | null>(null)
  const [llmForm] = Form.useForm<LlmForm>()
  const [embForm] = Form.useForm<EmbForm>()
  const [midForm] = Form.useForm<MidsceneForm>()

  const llmQuery = useQuery({
    queryKey: ['admin', 'ai-settings', 'llm-profiles'],
    queryFn: () => apiJson<LlmProfile[]>('/api/v1/admin/ai-settings/llm-profiles'),
  })

  const embQuery = useQuery({
    queryKey: ['admin', 'ai-settings', 'embedding-profiles'],
    queryFn: () => apiJson<EmbeddingProfile[]>('/api/v1/admin/ai-settings/embedding-profiles'),
  })

  const midQuery = useQuery({
    queryKey: ['admin', 'ai-settings', 'midscene-profiles'],
    queryFn: () => apiJson<MidsceneProfile[]>('/api/v1/admin/ai-settings/midscene-profiles'),
  })

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['admin', 'ai-settings'] })
    qc.invalidateQueries({ queryKey: ['ai', 'llm-options'] })
  }

  const saveLlm = useMutation({
    mutationFn: async (values: LlmForm) => {
      const body = {
        name: values.name.trim(),
        api_base: values.api_base,
        model: values.model,
        allowed_models: values.allowed_models,
        api_key: values.api_key?.trim() ?? '',
      }
      if (editingLlm) {
        return apiJson<LlmProfile>(`/api/v1/admin/ai-settings/llm-profiles/${editingLlm.id}`, {
          method: 'PUT',
          body: JSON.stringify(body),
        })
      }
      return apiJson<LlmProfile>('/api/v1/admin/ai-settings/llm-profiles', {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      message.success(editingLlm ? '已更新 LLM 配置' : '已创建 LLM 配置')
      setLlmOpen(false)
      setEditingLlm(null)
      llmForm.resetFields()
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const saveEmb = useMutation({
    mutationFn: async (values: EmbForm) => {
      const body = {
        name: values.name.trim(),
        api_base: values.api_base,
        model: values.model,
        dim: values.dim,
        api_key: values.api_key?.trim() ?? '',
      }
      if (editingEmb) {
        return apiJson<EmbeddingProfile>(
          `/api/v1/admin/ai-settings/embedding-profiles/${editingEmb.id}`,
          {
            method: 'PUT',
            body: JSON.stringify(body),
          },
        )
      }
      return apiJson<EmbeddingProfile>('/api/v1/admin/ai-settings/embedding-profiles', {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: (data) => {
      message.success(editingEmb ? '已更新向量模型配置' : '已创建向量模型配置')
      if (data.warning) message.warning(data.warning)
      setEmbOpen(false)
      setEditingEmb(null)
      embForm.resetFields()
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const activateLlm = useMutation({
    mutationFn: (id: number) =>
      apiJson<LlmProfile>(`/api/v1/admin/ai-settings/llm-profiles/${id}/activate`, {
        method: 'POST',
      }),
    onSuccess: () => {
      message.success('已启用该 LLM 配置')
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const activateEmb = useMutation({
    mutationFn: (id: number) =>
      apiJson<EmbeddingProfile>(`/api/v1/admin/ai-settings/embedding-profiles/${id}/activate`, {
        method: 'POST',
      }),
    onSuccess: (data) => {
      message.success('已启用该向量模型配置')
      if (data.warning) message.warning(data.warning)
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteLlm = useMutation({
    mutationFn: (id: number) =>
      apiJson<void>(`/api/v1/admin/ai-settings/llm-profiles/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      message.success('已删除')
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteEmb = useMutation({
    mutationFn: (id: number) =>
      apiJson<void>(`/api/v1/admin/ai-settings/embedding-profiles/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      message.success('已删除')
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const saveMid = useMutation({
    mutationFn: async (values: MidsceneForm) => {
      const body = {
        name: values.name.trim(),
        api_base: values.api_base,
        model: values.model,
        model_family: values.model_family,
        api_key: values.api_key?.trim() ?? '',
      }
      if (editingMid) {
        return apiJson<MidsceneProfile>(`/api/v1/admin/ai-settings/midscene-profiles/${editingMid.id}`, {
          method: 'PUT',
          body: JSON.stringify(body),
        })
      }
      return apiJson<MidsceneProfile>('/api/v1/admin/ai-settings/midscene-profiles', {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      message.success(editingMid ? '已更新 Midscene 配置' : '已创建 Midscene 配置')
      setMidOpen(false)
      setEditingMid(null)
      midForm.resetFields()
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const activateMid = useMutation({
    mutationFn: (id: number) =>
      apiJson<MidsceneProfile>(`/api/v1/admin/ai-settings/midscene-profiles/${id}/activate`, {
        method: 'POST',
      }),
    onSuccess: () => {
      message.success('已启用该 Midscene 配置')
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteMid = useMutation({
    mutationFn: (id: number) =>
      apiJson<void>(`/api/v1/admin/ai-settings/midscene-profiles/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      message.success('已删除')
      invalidateAll()
    },
    onError: (e) => message.error(errText(e)),
  })

  function openCreateLlm() {
    setEditingLlm(null)
    llmForm.resetFields()
    llmForm.setFieldsValue({
      name: '',
      api_base: '',
      model: '',
      allowed_models: '',
      api_key: '',
    })
    setLlmOpen(true)
  }

  function openEditLlm(row: LlmProfile) {
    setEditingLlm(row)
    llmForm.setFieldsValue({
      name: row.name,
      api_base: row.api_base,
      model: row.model,
      allowed_models: row.allowed_models,
      api_key: row.api_key ?? '',
    })
    setLlmOpen(true)
  }

  function openCreateEmb() {
    setEditingEmb(null)
    embForm.resetFields()
    embForm.setFieldsValue({
      name: '',
      api_base: '',
      model: '',
      dim: 1536,
      api_key: '',
    })
    setEmbOpen(true)
  }

  function openEditEmb(row: EmbeddingProfile) {
    setEditingEmb(row)
    embForm.setFieldsValue({
      name: row.name,
      api_base: row.api_base,
      model: row.model,
      dim: row.dim,
      api_key: row.api_key ?? '',
    })
    setEmbOpen(true)
  }

  function openCreateMid() {
    setEditingMid(null)
    midForm.resetFields()
    midForm.setFieldsValue({
      name: '',
      api_base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      model: 'qwen-vl-plus',
      model_family: 'qwen2.5-vl',
      api_key: '',
    })
    setMidOpen(true)
  }

  function openEditMid(row: MidsceneProfile) {
    setEditingMid(row)
    midForm.setFieldsValue({
      name: row.name,
      api_base: row.api_base,
      model: row.model,
      model_family: row.model_family,
      api_key: row.api_key ?? '',
    })
    setMidOpen(true)
  }

  function applyLlmPreset(key: string | undefined) {
    if (!key) return
    const preset = LLM_PRESETS.find((p) => p.key === key)
    if (!preset) return
    llmForm.setFieldsValue({
      api_base: preset.apiBase,
      model: preset.model,
      allowed_models: preset.allowedModels,
      name: llmForm.getFieldValue('name') || preset.label,
    })
  }

  function applyEmbPreset(key: string | undefined) {
    if (!key) return
    const preset = EMBEDDING_PRESETS.find((p) => p.key === key)
    if (!preset) return
    embForm.setFieldsValue({
      api_base: preset.apiBase,
      model: preset.model,
      dim: preset.dim,
      name: embForm.getFieldValue('name') || preset.label,
    })
  }

  function applyMidPreset(key: string | undefined) {
    if (!key) return
    const preset = MIDSCENE_PRESETS.find((p) => p.key === key)
    if (!preset) return
    midForm.setFieldsValue({
      api_base: preset.apiBase,
      model: preset.model,
      model_family: preset.modelFamily,
      name: midForm.getFieldValue('name') || preset.label,
    })
  }

  const activeLlm = (llmQuery.data ?? []).find((x) => x.is_active)
  const activeEmb = (embQuery.data ?? []).find((x) => x.is_active)
  const activeMid = (midQuery.data ?? []).find((x) => x.is_active)

  const llmColumns: ColumnsType<LlmProfile> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name, row) => (
        <Space>
          <span>{name}</span>
          {row.is_active && <Tag color="green">启用中</Tag>}
        </Space>
      ),
    },
    { title: 'API Base', dataIndex: 'api_base', ellipsis: true },
    { title: '默认模型', dataIndex: 'model' },
    {
      title: '密钥',
      dataIndex: 'has_api_key',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="blue">已配置</Tag> : <Tag>回退 env</Tag>),
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space wrap>
          <Button size="small" onClick={() => openEditLlm(row)}>
            编辑
          </Button>
          {!row.is_active && (
            <Popconfirm
              title="启用该 LLM 配置？"
              description="系统将使用此配置作为全局大模型。"
              onConfirm={() => activateLlm.mutate(row.id)}
            >
              <Button size="small" type="primary">
                启用
              </Button>
            </Popconfirm>
          )}
          {!row.is_active && (
            <Popconfirm title="确认删除该配置？" onConfirm={() => deleteLlm.mutate(row.id)}>
              <Button size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const embColumns: ColumnsType<EmbeddingProfile> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name, row) => (
        <Space>
          <span>{name}</span>
          {row.is_active && <Tag color="green">启用中</Tag>}
        </Space>
      ),
    },
    { title: '模型', dataIndex: 'model' },
    { title: '维度', dataIndex: 'dim', width: 80 },
    { title: 'API Base', dataIndex: 'api_base', ellipsis: true },
    {
      title: '密钥',
      dataIndex: 'has_api_key',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="blue">已配置</Tag> : <Tag>回退 env</Tag>),
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space wrap>
          <Button size="small" onClick={() => openEditEmb(row)}>
            编辑
          </Button>
          {!row.is_active && (
            <Popconfirm
              title="启用该向量模型配置？"
              description="切换维度可能影响已有 Milvus 向量索引。"
              onConfirm={() => activateEmb.mutate(row.id)}
            >
              <Button size="small" type="primary">
                启用
              </Button>
            </Popconfirm>
          )}
          {!row.is_active && (
            <Popconfirm title="确认删除该配置？" onConfirm={() => deleteEmb.mutate(row.id)}>
              <Button size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const midColumns: ColumnsType<MidsceneProfile> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name, row) => (
        <Space>
          <span>{name}</span>
          {row.is_active && <Tag color="green">启用中</Tag>}
        </Space>
      ),
    },
    { title: '模型', dataIndex: 'model' },
    { title: 'Family', dataIndex: 'model_family', width: 120 },
    { title: 'API Base', dataIndex: 'api_base', ellipsis: true },
    {
      title: '密钥',
      dataIndex: 'has_api_key',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="blue">已配置</Tag> : <Tag>回退 env</Tag>),
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space wrap>
          <Button size="small" onClick={() => openEditMid(row)}>
            编辑
          </Button>
          {!row.is_active && (
            <Popconfirm title="启用该 Midscene 配置？" onConfirm={() => activateMid.mutate(row.id)}>
              <Button size="small" type="primary">
                启用
              </Button>
            </Popconfirm>
          )}
          {!row.is_active && (
            <Popconfirm title="确认删除该配置？" onConfirm={() => deleteMid.mutate(row.id)}>
              <Button size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const loadError = llmQuery.isError
    ? errText(llmQuery.error)
    : embQuery.isError
      ? errText(embQuery.error)
      : midQuery.isError
        ? errText(midQuery.error)
        : null

  if (loadError) {
    return (
      <div className="card">
        <h1>系统 AI 配置</h1>
        <p className="error">{loadError}</p>
        <p className="field-hint">
          超级管理员需在数据库 users 表中 is_superuser=1，或通过后端命令创建：
          <code className="body-preview">python -m app.bootstrap</code>
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <h1>系统 AI 配置</h1>
      <p className="field-hint">
        可配置多套大模型 / 向量模型 / Midscene，并分别启用其中一套。Midscene 用于 UI 探索看截图（API Base、模型名、model family、API Key）。未填写密钥且库中无密钥时回退服务器环境变量。修改
        向量模型维度可能影响已有 Milvus 向量索引，请谨慎操作。
      </p>

      <Tabs
        items={[
          {
            key: 'llm',
            label: '大模型管理',
            children: (
              <>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: 12,
                    gap: 12,
                    flexWrap: 'wrap',
                  }}
                >
                  <p className="muted" style={{ margin: 0 }}>
                    当前启用：
                    {activeLlm
                      ? `${activeLlm.name}（${activeLlm.model}）`
                      : '无（回退 .env）'}
                  </p>
                  <Button type="primary" onClick={openCreateLlm}>
                    新建配置
                  </Button>
                </div>
                <Table
                  rowKey="id"
                  size="middle"
                  loading={llmQuery.isLoading}
                  columns={llmColumns}
                  dataSource={llmQuery.data ?? []}
                  pagination={false}
                />
              </>
            ),
          },
          {
            key: 'embedding',
            label: '向量模型配置',
            children: (
              <>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: 12,
                    gap: 12,
                    flexWrap: 'wrap',
                  }}
                >
                  <p className="muted" style={{ margin: 0 }}>
                    当前启用：
                    {activeEmb
                      ? `${activeEmb.name}（${activeEmb.model} / dim=${activeEmb.dim}）`
                      : '无（回退 .env）'}
                  </p>
                  <Button type="primary" onClick={openCreateEmb}>
                    新建配置
                  </Button>
                </div>
                <Table
                  rowKey="id"
                  size="middle"
                  loading={embQuery.isLoading}
                  columns={embColumns}
                  dataSource={embQuery.data ?? []}
                  pagination={false}
                />
              </>
            ),
          },
          {
            key: 'midscene',
            label: 'Midscene',
            children: (
              <>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: 12,
                    gap: 12,
                    flexWrap: 'wrap',
                  }}
                >
                  <p className="muted" style={{ margin: 0 }}>
                    当前启用：
                    {activeMid
                      ? `${activeMid.name}（${activeMid.model} / ${activeMid.model_family}）`
                      : '无（回退 .env 或当前 LLM 密钥）'}
                  </p>
                  <Button type="primary" onClick={openCreateMid}>
                    新建配置
                  </Button>
                </div>
                <Table
                  rowKey="id"
                  size="middle"
                  loading={midQuery.isLoading}
                  columns={midColumns}
                  dataSource={midQuery.data ?? []}
                  pagination={false}
                />
              </>
            ),
          },
        ]}
      />

      <Modal
        title={editingLlm ? '编辑 LLM 配置' : '新建 LLM 配置'}
        open={llmOpen}
        onCancel={() => {
          setLlmOpen(false)
          setEditingLlm(null)
        }}
        onOk={() => llmForm.submit()}
        confirmLoading={saveLlm.isPending}
        destroyOnHidden
        width={560}
      >
        <Form
          form={llmForm}
          layout="vertical"
          onFinish={(values) => saveLlm.mutate(values)}
        >
          <Form.Item label="快速预设" name="preset">
            <Select
              allowClear
              placeholder="选择后填充下方字段，仍可手动修改"
              options={LLM_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
              onChange={(v) => applyLlmPreset(v)}
            />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="API Base" name="api_base">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="默认模型 ID" name="model">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="允许模型列表（逗号分隔，空则仅默认模型）" name="allowed_models">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="API Key（留空保存将清除库内密钥并回退环境变量）"
            name="api_key"
          >
            <Input autoComplete="off" placeholder="未配置" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingEmb ? '编辑向量模型配置' : '新建向量模型配置'}
        open={embOpen}
        onCancel={() => {
          setEmbOpen(false)
          setEditingEmb(null)
        }}
        onOk={() => embForm.submit()}
        confirmLoading={saveEmb.isPending}
        destroyOnHidden
        width={560}
      >
        <Form
          form={embForm}
          layout="vertical"
          onFinish={(values) => saveEmb.mutate(values)}
        >
          <Form.Item label="快速预设" name="preset">
            <Select
              allowClear
              placeholder="选择后填充下方字段，仍可手动修改"
              options={EMBEDDING_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
              onChange={(v) => applyEmbPreset(v)}
            />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="API Base" name="api_base">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="模型 ID" name="model">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="向量维度"
            name="dim"
            rules={[{ required: true, message: '请输入维度' }]}
          >
            <InputNumber min={1} max={32768} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            label="API Key（留空保存将清除库内密钥并回退环境变量）"
            name="api_key"
          >
            <Input autoComplete="off" placeholder="未配置" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingMid ? '编辑 Midscene 配置' : '新建 Midscene 配置'}
        open={midOpen}
        onCancel={() => {
          setMidOpen(false)
          setEditingMid(null)
        }}
        onOk={() => midForm.submit()}
        confirmLoading={saveMid.isPending}
        destroyOnHidden
        width={560}
      >
        <Form form={midForm} layout="vertical" onFinish={(values) => saveMid.mutate(values)}>
          <Form.Item label="快速预设" name="preset">
            <Select
              allowClear
              placeholder="选择后填充下方字段，仍可手动修改"
              options={MIDSCENE_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
              onChange={(v) => applyMidPreset(v)}
            />
          </Form.Item>
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="API Base" name="api_base">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="模型名" name="model">
            <Input autoComplete="off" placeholder="qwen-vl-plus" />
          </Form.Item>
          <Form.Item label="Model family" name="model_family">
            <Input autoComplete="off" placeholder="qwen2.5-vl" />
          </Form.Item>
          <Form.Item label="API Key（留空保存将清除库内密钥并回退环境变量）" name="api_key">
            <Input autoComplete="off" placeholder="未配置" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
