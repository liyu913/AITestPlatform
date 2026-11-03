import { Empty, Input, Tree, Typography } from 'antd'
import type { DataNode } from 'antd/es/tree'
import { useEffect, useMemo, useState, type CSSProperties, type Key } from 'react'
import {
  apiDefKey,
  buildApiDefinitionTreeData,
  collectTreeKeys,
  filterDefinitionsForTree,
  parseApiDefKey,
  type DefForTree,
} from '../utils/apiDefinitionTree'

export type ApiDefinitionTreeCheckProps = {
  definitions: DefForTree[]
  value: number[]
  onChange: (next: number[]) => void
  height?: number
  loading?: boolean
  disabled?: boolean
  title?: string
  bordered?: boolean
  className?: string
}

export default function ApiDefinitionTreeCheck({
  definitions,
  value,
  onChange,
  height = 280,
  loading,
  disabled,
  title = '可选接口',
  bordered = true,
  className,
}: ApiDefinitionTreeCheckProps) {
  const [search, setSearch] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])

  const filtered = useMemo(
    () => filterDefinitionsForTree(definitions, search),
    [definitions, search],
  )

  const treeData: DataNode[] = useMemo(
    () =>
      buildApiDefinitionTreeData(filtered, {
        pruneEmpty: Boolean(search.trim()),
      }),
    [filtered, search],
  )

  useEffect(() => {
    if (search.trim()) {
      setExpandedKeys(collectTreeKeys(treeData))
    }
  }, [search, treeData])

  const checkedKeys = useMemo(() => value.map(apiDefKey), [value])

  function onCheck(checked: Key[] | { checked: Key[]; halfChecked: Key[] }) {
    if (disabled) return
    const keys = Array.isArray(checked) ? checked : checked.checked
    const apiIds = keys.map(parseApiDefKey).filter((n): n is number => n != null)
    const selected = new Set(apiIds)
    const kept = value.filter((id) => selected.has(id))
    const keptSet = new Set(kept)
    const added = apiIds.filter((id) => !keptSet.has(id))
    onChange([...kept, ...added])
  }

  const rootStyle: CSSProperties = bordered
    ? {
        border: '1px solid var(--ant-color-border, #e6e6e6)',
        borderRadius: 6,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        height,
      }
    : {
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        height,
      }

  return (
    <div className={className} style={rootStyle}>
      {title ? (
        <div
          style={{
            padding: '8px 10px',
            borderBottom: bordered ? '1px solid var(--ant-color-border, #e6e6e6)' : undefined,
            fontWeight: 600,
          }}
        >
          {title}
          <Typography.Text type="secondary" style={{ marginLeft: 6, fontWeight: 400 }}>
            {filtered.length} 项
          </Typography.Text>
        </div>
      ) : null}
      <div style={{ padding: '8px 10px' }}>
        <Input.Search
          allowClear
          placeholder="搜索名称 / path / method"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          disabled={disabled}
        />
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px 8px' }}>
        {loading ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : treeData.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无匹配接口" />
        ) : (
          <Tree
            checkable
            selectable={false}
            disabled={disabled}
            treeData={treeData}
            checkedKeys={checkedKeys}
            expandedKeys={expandedKeys}
            onExpand={setExpandedKeys}
            onCheck={onCheck}
          />
        )}
      </div>
    </div>
  )
}
