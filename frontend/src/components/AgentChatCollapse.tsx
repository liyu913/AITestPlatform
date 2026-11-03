import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import { useEffect, useState } from 'react'

export function useAgentChatCollapsed(storageKey: string) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(storageKey) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, collapsed ? '1' : '0')
    } catch {
      /* ignore quota / private mode */
    }
  }, [storageKey, collapsed])

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(storageKey) === '1')
    } catch {
      setCollapsed(false)
    }
  }, [storageKey])

  return {
    collapsed,
    setCollapsed,
    toggleCollapsed: () => setCollapsed((v) => !v),
  }
}

type ButtonProps = {
  collapsed: boolean
  onToggle: () => void
}

export function AgentChatCollapseButton({ collapsed, onToggle }: ButtonProps) {
  return (
    <Tooltip title={collapsed ? '展开对话' : '收起对话'}>
      <Button
        type="text"
        size="small"
        icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        onClick={onToggle}
        aria-label={collapsed ? '展开对话' : '收起对话'}
      />
    </Tooltip>
  )
}
