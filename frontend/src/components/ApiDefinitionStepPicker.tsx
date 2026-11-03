import { Button, Empty, Space, Tag, Typography } from 'antd'
import { useMemo, type CSSProperties } from 'react'
import { methodColor, type DefForTree } from '../utils/apiDefinitionTree'
import ApiDefinitionTreeCheck from './ApiDefinitionTreeCheck'

export type ApiDefinitionStepPickerProps = {
  definitions: DefForTree[]
  value: number[]
  onChange: (next: number[]) => void
  height?: number
  loading?: boolean
}

export default function ApiDefinitionStepPicker({
  definitions,
  value,
  onChange,
  height = 320,
  loading,
}: ApiDefinitionStepPickerProps) {
  const defMap = useMemo(() => {
    const m = new Map<number, DefForTree>()
    for (const d of definitions) m.set(d.id, d)
    return m
  }, [definitions])

  function move(idx: number, dir: -1 | 1) {
    const j = idx + dir
    if (j < 0 || j >= value.length) return
    const next = [...value]
    ;[next[idx], next[j]] = [next[j], next[idx]]
    onChange(next)
  }

  function removeAt(idx: number) {
    onChange(value.filter((_, i) => i !== idx))
  }

  const rightPaneStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    border: '1px solid var(--ant-color-border, #e6e6e6)',
    borderRadius: 6,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    height,
  }

  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <ApiDefinitionTreeCheck
          definitions={definitions}
          value={value}
          onChange={onChange}
          height={height}
          loading={loading}
          title="可选接口"
        />
      </div>

      <div style={rightPaneStyle}>
        <div
          style={{
            padding: '8px 10px',
            borderBottom: '1px solid var(--ant-color-border, #e6e6e6)',
            fontWeight: 600,
          }}
        >
          场景步骤
          <Typography.Text type="secondary" style={{ marginLeft: 6, fontWeight: 400 }}>
            {value.length} 项
          </Typography.Text>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {!value.length ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="从左侧勾选接口"
              style={{ marginTop: 48 }}
            />
          ) : (
            value.map((id, idx) => {
              const d = defMap.get(id)
              return (
                <div
                  key={id}
                  style={{
                    padding: '8px 10px',
                    borderBottom: '1px solid var(--ant-color-border, #e6e6e6)',
                    display: 'flex',
                    gap: 8,
                    alignItems: 'flex-start',
                  }}
                >
                  <Typography.Text type="secondary" style={{ width: 20, flexShrink: 0 }}>
                    {idx + 1}
                  </Typography.Text>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div>
                      <Tag color={methodColor(d?.method || '?')}>{d?.method || '?'}</Tag>
                      {d?.name || `#${id}`}
                    </div>
                    <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                      {d?.path || ''}
                    </Typography.Text>
                  </div>
                  <Space size={0}>
                    <Button type="text" size="small" disabled={idx === 0} onClick={() => move(idx, -1)}>
                      ↑
                    </Button>
                    <Button
                      type="text"
                      size="small"
                      disabled={idx === value.length - 1}
                      onClick={() => move(idx, 1)}
                    >
                      ↓
                    </Button>
                    <Button type="link" size="small" danger onClick={() => removeAt(idx)}>
                      删
                    </Button>
                  </Space>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
