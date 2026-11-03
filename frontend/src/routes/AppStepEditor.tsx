import { Button, Form, Input, InputNumber, Select, Space } from 'antd'

export type AppStep = {
  action: string
  strategy?: string
  selector?: string
  element?: string
  value?: string
  assert?: string
  timeout_ms?: number
}

const ACTIONS = [
  'launch',
  'tap',
  'type',
  'clear',
  'swipe',
  'back',
  'long_press',
  'wait',
  'assert_visible',
  'assert_text',
  'screenshot',
  'hide_keyboard',
]

const STRATEGIES = [
  'id',
  'accessibility_id',
  'xpath',
  'class_name',
  'android_uiautomator',
  'ios_predicate',
  'ios_class_chain',
]

type Props = {
  steps: AppStep[]
  onChange: (steps: AppStep[]) => void
}

export default function AppStepEditor({ steps, onChange }: Props) {
  function update(i: number, patch: Partial<AppStep>) {
    const next = steps.map((s, idx) => (idx === i ? { ...s, ...patch } : s))
    onChange(next)
  }

  function remove(i: number) {
    onChange(steps.filter((_, idx) => idx !== i))
  }

  function add() {
    onChange([...steps, { action: 'tap' }])
  }

  return (
    <div>
      {steps.map((s, i) => (
        <Space key={i} align="start" style={{ display: 'flex', marginBottom: 8 }} wrap>
          <Select
            style={{ width: 140 }}
            value={s.action}
            options={ACTIONS.map((a) => ({ value: a, label: a }))}
            onChange={(v) => update(i, { action: v })}
          />
          <Select
            allowClear
            placeholder="strategy"
            style={{ width: 150 }}
            value={s.strategy}
            options={STRATEGIES.map((a) => ({ value: a, label: a }))}
            onChange={(v) => update(i, { strategy: v })}
          />
          <Input
            placeholder="selector"
            style={{ width: 180 }}
            value={s.selector}
            onChange={(e) => update(i, { selector: e.target.value })}
          />
          <Input
            placeholder="element (Page.name)"
            style={{ width: 160 }}
            value={s.element}
            onChange={(e) => update(i, { element: e.target.value })}
          />
          <Input
            placeholder="value"
            style={{ width: 140 }}
            value={s.value}
            onChange={(e) => update(i, { value: e.target.value })}
          />
          <Input
            placeholder="assert"
            style={{ width: 120 }}
            value={s.assert}
            onChange={(e) => update(i, { assert: e.target.value })}
          />
          <InputNumber
            placeholder="timeout"
            style={{ width: 100 }}
            value={s.timeout_ms}
            onChange={(v) => update(i, { timeout_ms: v ?? undefined })}
          />
          <Button danger size="small" onClick={() => remove(i)}>
            删
          </Button>
        </Space>
      ))}
      <Button type="dashed" onClick={add} block>
        添加步骤
      </Button>
      <Form.Item style={{ marginTop: 8, marginBottom: 0 }}>
        <Input.TextArea
          rows={2}
          readOnly
          value="swipe value: left|right|up|down 或 x1,y1,x2,y2；元素可用 @Page.name"
        />
      </Form.Item>
    </div>
  )
}
