import { App, Button, Form, Input, Modal, Switch } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { apiJson, ApiError } from '../api/client'
import {
  dictToRows,
  rowsToDict,
  TestDataKvList,
  type Kv,
} from './TestDataVariables'

export type ApiEnvRecord = {
  id: number
  project_id?: number
  name: string
  base_url: string
  variables: Record<string, string>
  secret_keys: string[]
  headers: Record<string, string>
  is_default: boolean
}

type EnvForm = {
  name: string
  base_url: string
  variable_rows: Kv[]
  secret_rows: Kv[]
  header_rows: Kv[]
  is_default: boolean
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  return '请求失败'
}

type Props = {
  open: boolean
  onClose: () => void
  projectId: number
  /** null = create */
  env: ApiEnvRecord | null
  /** When creating, whether to default is_default (e.g. no existing default) */
  defaultAsDefault?: boolean
  onSaved?: (env: ApiEnvRecord) => void
}

export default function ApiEnvEditModal({
  open,
  onClose,
  projectId,
  env,
  defaultAsDefault = false,
  onSaved,
}: Props) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [form] = Form.useForm<EnvForm>()
  const editing = env != null

  useEffect(() => {
    if (!open) return
    if (env) {
      form.setFieldsValue({
        name: env.name,
        base_url: env.base_url,
        variable_rows: dictToRows(env.variables ?? {}),
        secret_rows: [{ key: '', value: '' }],
        header_rows: dictToRows(env.headers ?? {}),
        is_default: env.is_default,
      })
    } else {
      form.setFieldsValue({
        name: '',
        base_url: 'https://',
        variable_rows: [{ key: '', value: '' }],
        secret_rows: [{ key: '', value: '' }],
        header_rows: [{ key: '', value: '' }],
        is_default: defaultAsDefault,
      })
    }
  }, [open, env, defaultAsDefault, form])

  const save = useMutation({
    mutationFn: async (values: EnvForm) => {
      const secrets = rowsToDict(values.secret_rows)
      const payload: Record<string, unknown> = {
        name: values.name.trim(),
        base_url: values.base_url.trim(),
        variables: rowsToDict(values.variable_rows),
        headers: rowsToDict(values.header_rows),
        is_default: Boolean(values.is_default),
      }
      if (editing) {
        if (Object.keys(secrets).length > 0) payload.secrets = secrets
        return apiJson<ApiEnvRecord>(`/api/v1/projects/${projectId}/api-envs/${env.id}`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        })
      }
      payload.secrets = secrets
      return apiJson<ApiEnvRecord>(`/api/v1/projects/${projectId}/api-envs/`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ['api-envs', projectId] })
      message.success('已保存')
      onSaved?.(saved)
      onClose()
      form.resetFields()
    },
    onError: (e) => message.error(errText(e)),
  })

  return (
    <Modal
      title={editing ? '编辑环境' : '新建环境'}
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
      width={680}
    >
      <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input maxLength={128} />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="Base URL"
          rules={[{ required: true, message: '请输入 base_url' }]}
          extra="用例 path 会拼在此地址后面"
        >
          <Input placeholder="https://api.example.com" />
        </Form.Item>
        <Form.Item label="变量（可用 {{key}}）">
          <TestDataKvList name="variable_rows" addLabel="添加变量" keyPlaceholder="键" />
        </Form.Item>
        <Form.Item
          label="密钥变量（加密）"
          extra={
            editing
              ? `已有密钥键：${(env.secret_keys ?? []).join(', ') || '无'}；留空则不修改`
              : '写入需 SETTINGS_ENCRYPTION_KEY'
          }
        >
          <TestDataKvList name="secret_rows" addLabel="添加密钥" keyPlaceholder="键" />
        </Form.Item>
        <Form.Item label="默认请求头">
          <TestDataKvList name="header_rows" addLabel="添加 Header" keyPlaceholder="键" />
        </Form.Item>
        <Form.Item name="is_default" label="设为默认环境" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={save.isPending} block>
          保存
        </Button>
      </Form>
    </Modal>
  )
}
