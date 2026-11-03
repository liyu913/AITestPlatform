import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Form, Input, Space, Tag } from 'antd'
import type { NamePath } from 'antd/es/form/interface'

export type Kv = { key: string; value: string }

export function dictToRows(obj: Record<string, string> | undefined | null): Kv[] {
  const entries = Object.entries(obj || {})
  if (!entries.length) return [{ key: '', value: '' }]
  return entries.map(([key, value]) => ({ key, value }))
}

export function rowsToDict(rows: Kv[] | undefined): Record<string, string> {
  const out: Record<string, string> = {}
  for (const r of rows || []) {
    const k = (r.key || '').trim()
    if (!k) continue
    out[k] = r.value ?? ''
  }
  return out
}

/** Best-effort parse free-form test_data into rows for legacy display/edit. */
export function parseTestDataToRows(text: string | null | undefined): Kv[] {
  const raw = (text || '').trim()
  if (!raw) return [{ key: '', value: '' }]
  const parts = raw.split(/[;；\n|/]+/).map((p) => p.trim()).filter(Boolean)
  const rows: Kv[] = []
  for (const part of parts) {
    const m = part.match(
      /^\s*(?:\{\{\s*)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\}\})?\s*[：:=\-]\s*(.+?)\s*$/,
    )
    if (m) rows.push({ key: m[1], value: m[2].trim() })
  }
  return rows.length ? rows : [{ key: '', value: '' }]
}

export function VariablesTags({
  variables,
  fallbackText,
}: {
  variables?: Record<string, string> | null
  fallbackText?: string | null
}) {
  let entries = Object.entries(variables || {}).filter(([k]) => k.trim())
  if (!entries.length && fallbackText?.trim()) {
    const parsed = parseTestDataToRows(fallbackText).filter((r) => r.key.trim())
    if (parsed.length) entries = parsed.map((r) => [r.key, r.value])
  }
  if (entries.length) {
    return (
      <Space size={[8, 8]} wrap>
        {entries.map(([k, v]) => (
          <Tag key={k} style={{ marginInlineEnd: 0 }}>
            <span style={{ color: '#389e0d' }}>{`{{${k}}}`}</span>
            {` = ${v}`}
          </Tag>
        ))}
      </Space>
    )
  }
  const t = (fallbackText || '').trim()
  return <>{t || '—'}</>
}

/** Form.List editor bound to `name` (e.g. variable_rows). */
export function TestDataKvList({
  name = 'variable_rows',
  addLabel = '添加变量',
  keyPlaceholder = '键，如 username',
  valuePlaceholder = '值',
}: {
  name?: NamePath
  addLabel?: string
  keyPlaceholder?: string
  valuePlaceholder?: string
}) {
  return (
    <Form.List name={name}>
      {(fields, { add, remove }) => (
        <div className="kv-form-list">
          {fields.map((field) => (
            <div key={field.key} className="kv-form-row">
              <Form.Item {...field} name={[field.name, 'key']} className="kv-form-key">
                <Input placeholder={keyPlaceholder} />
              </Form.Item>
              <Form.Item {...field} name={[field.name, 'value']} className="kv-form-value">
                <Input placeholder={valuePlaceholder} />
              </Form.Item>
              <Button
                type="text"
                danger
                icon={<MinusCircleOutlined />}
                onClick={() => remove(field.name)}
                aria-label="删除"
              />
            </div>
          ))}
          <Button
            type="dashed"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => add({ key: '', value: '' })}
          >
            {addLabel}
          </Button>
        </div>
      )}
    </Form.List>
  )
}
