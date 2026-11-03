import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiJson } from '../api/client'

type Me = {
  id: number
  username: string
  full_name: string | null
  is_active: boolean
  is_superuser: boolean
}

/**
 * 仅当 /users/me 为超级管理员时渲染子节点，否则重定向到项目管理页。
 * 避免非超管直接打开 /admin/ai-settings 时先请求管理接口再出现误导性「需超管」错误。
 */
export default function RequireSuperuser({ children }: { children: ReactNode }) {
  const me = useQuery({
    queryKey: ['users', 'me'],
    queryFn: () => apiJson<Me>('/api/v1/users/me'),
  })

  if (me.isLoading) {
    return (
      <div className="card">
        <p className="muted">验证权限中…</p>
      </div>
    )
  }

  if (me.isError) {
    return (
      <div className="card">
        <p className="error">无法获取当前用户信息，请重新登录后再试。</p>
      </div>
    )
  }

  if (!me.data?.is_superuser) {
    return <Navigate to="/projects" replace />
  }

  return <>{children}</>
}
