import { AppstoreOutlined, TableOutlined } from '@ant-design/icons'
import { Segmented } from 'antd'
import { useState } from 'react'

export type ListViewMode = 'table' | 'grid'

function readStored(storageKey: string): ListViewMode {
  try {
    const v = localStorage.getItem(storageKey)
    if (v === 'grid' || v === 'table') return v
  } catch {
    /* ignore */
  }
  return 'grid'
}

export function useListViewMode(storageKey: string): [ListViewMode, (mode: ListViewMode) => void] {
  const [mode, setModeState] = useState<ListViewMode>(() => readStored(storageKey))

  const setMode = (next: ListViewMode) => {
    setModeState(next)
    try {
      localStorage.setItem(storageKey, next)
    } catch {
      /* ignore */
    }
  }

  return [mode, setMode]
}

type Props = {
  value: ListViewMode
  onChange: (mode: ListViewMode) => void
}

export default function ListViewToggle({ value, onChange }: Props) {
  return (
    <Segmented<ListViewMode>
      value={value}
      onChange={onChange}
      options={[
        { value: 'table', icon: <TableOutlined />, label: '表格' },
        { value: 'grid', icon: <AppstoreOutlined />, label: '宫格' },
      ]}
    />
  )
}
