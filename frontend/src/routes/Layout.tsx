import {
  ApiOutlined,
  AppstoreOutlined,
  BookOutlined,
  BugOutlined,
  CheckOutlined,
  CloudServerOutlined,
  CompassOutlined,
  DashboardOutlined,
  DesktopOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MobileOutlined,
  PieChartOutlined,
  PlayCircleOutlined,
  ProjectOutlined,
  ScheduleOutlined,
  SettingOutlined,
  SwapOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { App, Button, Dropdown, Form, Input, Modal } from 'antd'
import type { MenuProps } from 'antd'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, matchPath, useLocation, useNavigate } from 'react-router-dom'
import { REMEMBER_LOGIN_KEY, SAVED_PASSWORD_KEY, useAuth } from '../auth/AuthContext'
import { ApiError, apiJson } from '../api/client'
import ProjectSearch from '../components/ProjectSearch'
import { activeDomain, activeTab, DOMAIN_SECTIONS, DOMAINS, projectPath } from './domainNav'

const DOMAIN_ICONS: Record<string, ReactNode> = {
  workbench: <CompassOutlined />,
  dashboard: <DashboardOutlined />,
  requirements: <FileTextOutlined />,
  functional: <ExperimentOutlined />,
  knowledge: <BookOutlined />,
  envs: <CloudServerOutlined />,
  api: <ApiOutlined />,
  ui: <DesktopOutlined />,
  app: <MobileOutlined />,
  perf: <ThunderboltOutlined />,
  testplans: <ScheduleOutlined />,
  execution: <PlayCircleOutlined />,
  reports: <FileTextOutlined />,
  defects: <BugOutlined />,
  coverage: <PieChartOutlined />,
  modules: <AppstoreOutlined />,
  members: <TeamOutlined />,
}

type Project = {
  id: number
  name: string
  description: string | null
}

type Me = {
  id: number
  username: string
  full_name: string | null
  is_active: boolean
  is_superuser: boolean
}

type ChangePasswordForm = {
  current_password: string
  new_password: string
  confirm_password: string
}

const LAST_PROJECT_ID_KEY = 'raina.lastProjectId'

function loadLastProjectId(): string | null {
  try {
    const raw = localStorage.getItem(LAST_PROJECT_ID_KEY)
    if (!raw) return null
    const id = Number(raw)
    return Number.isFinite(id) && id > 0 ? String(id) : null
  } catch {
    return null
  }
}

function saveLastProjectId(projectId: string) {
  localStorage.setItem(LAST_PROJECT_ID_KEY, projectId)
}

function clearLastProjectId() {
  localStorage.removeItem(LAST_PROJECT_ID_KEY)
}

export default function Layout() {
  const { message } = App.useApp()
  const { logout } = useAuth()
  const nav = useNavigate()
  const location = useLocation()
  const [passwordModalOpen, setPasswordModalOpen] = useState(false)
  const [passwordSubmitting, setPasswordSubmitting] = useState(false)
  const [passwordForm] = Form.useForm<ChangePasswordForm>()
  const [lastProjectId, setLastProjectId] = useState<string | null>(loadLastProjectId)

  const projectMatch = matchPath({ path: '/projects/:projectId/*', end: false }, location.pathname)
  const projectId = projectMatch?.params.projectId
  const sidebarProjectId = projectId ?? lastProjectId

  useEffect(() => {
    if (!projectId) return
    saveLastProjectId(projectId)
    setLastProjectId(projectId)
  }, [projectId])

  const meQuery = useQuery({
    queryKey: ['users', 'me'],
    queryFn: () => apiJson<Me>('/api/v1/users/me'),
  })

  const projectDetail = useQuery({
    queryKey: ['project', sidebarProjectId],
    queryFn: () => apiJson<Project>(`/api/v1/projects/${sidebarProjectId}`),
    enabled: Boolean(sidebarProjectId),
  })

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => apiJson<Project[]>('/api/v1/projects/'),
  })

  useEffect(() => {
    if (!sidebarProjectId || !projectDetail.isError) return
    clearLastProjectId()
    setLastProjectId(null)
  }, [sidebarProjectId, projectDetail.isError])

  const showProjectSidebar = Boolean(sidebarProjectId) && !projectDetail.isError
  const currentDomain = sidebarProjectId ? activeDomain(location.pathname, sidebarProjectId) : null
  const currentName = projectDetail.data?.name
  const meLabel = meQuery.data?.full_name?.trim() || meQuery.data?.username

  function switchToProject(nextId: string) {
    if (projectId && nextId === projectId) return
    if (projectId) {
      const domain = activeDomain(location.pathname, projectId)
      const tab = domain ? activeTab(domain, location.pathname, projectId) : null
      nav(projectPath(nextId, tab?.to ?? domain?.to ?? 'dashboard'))
      return
    }
    nav(projectPath(nextId, 'dashboard'))
  }

  const projectMenu: MenuProps = (() => {
    if (projectsQuery.isLoading) {
      return { items: [{ key: 'loading', label: '加载中…', disabled: true }] }
    }
    if (projectsQuery.isError) {
      return { items: [{ key: 'error', label: '加载失败', disabled: true }] }
    }
    const rows = projectsQuery.data ?? []
    if (rows.length === 0) {
      return { items: [{ key: 'empty', label: '暂无项目', disabled: true }] }
    }
    return {
      items: rows.map((project) => {
        const current = projectId === String(project.id)
        return {
          key: String(project.id),
          disabled: current,
          label: (
            <span className="topbar-switch-item">
              <span className="topbar-switch-name">{project.name}</span>
              {current && <CheckOutlined />}
            </span>
          ),
        }
      }),
      onClick: ({ key }) => switchToProject(key),
    }
  })()

  function openPasswordModal() {
    passwordForm.resetFields()
    setPasswordModalOpen(true)
  }

  async function submitChangePassword(values: ChangePasswordForm) {
    if (values.new_password !== values.confirm_password) {
      message.warning('两次输入的新密码不一致')
      return
    }
    setPasswordSubmitting(true)
    try {
      await apiJson('/api/v1/users/me/password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: values.current_password,
          new_password: values.new_password,
        }),
      })
      if (localStorage.getItem(REMEMBER_LOGIN_KEY) === '1') {
        localStorage.setItem(SAVED_PASSWORD_KEY, values.new_password)
      }
      message.success('密码已修改')
      setPasswordModalOpen(false)
      passwordForm.resetFields()
    } catch (ex) {
      if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
        const m = ex.body as { message?: string }
        message.error(m.message ?? '修改密码失败')
      } else {
        message.error('修改密码失败')
      }
    } finally {
      setPasswordSubmitting(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-brand-group">
          <Link to="/projects" className="brand">
            LY测试平台
          </Link>
          <Dropdown menu={projectMenu} trigger={['click']} placement="bottomLeft">
            <button
              type="button"
              className={`topbar-switch${currentName ? ' has-context' : ''}`}
              aria-label="切换项目"
            >
              <span className="topbar-switch-current">{currentName ?? '切换项目'}</span>
              <SwapOutlined />
            </button>
          </Dropdown>
        </div>
        <div className="spacer" />
        <div className="topbar-actions">
          {projectId ? <ProjectSearch projectId={projectId} /> : null}
          {meLabel && <span className="topbar-user muted">{meLabel}</span>}
          <button type="button" className="btn ghost" onClick={openPasswordModal}>
            修改密码
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              logout()
              nav('/login')
            }}
          >
            退出
          </button>
        </div>
      </header>
      <div className="body-row">
        <aside className="sidebar">
          <div className="sidebar-section">
            <div className="sidebar-label">工作台</div>
            <NavLink to="/projects" end className="sidebar-link">
              <ProjectOutlined className="sidebar-link-icon" />
              项目管理
            </NavLink>
            {meQuery.data?.is_superuser && (
              <>
                <NavLink to="/admin/ai-settings" className="sidebar-link">
                  <SettingOutlined className="sidebar-link-icon" />
                  系统 AI 配置
                </NavLink>
                <NavLink to="/admin/ai-logs" className="sidebar-link">
                  <HistoryOutlined className="sidebar-link-icon" />
                  AI 调用日志
                </NavLink>
                <NavLink to="/admin/users" className="sidebar-link">
                  <UserOutlined className="sidebar-link-icon" />
                  用户管理
                </NavLink>
              </>
            )}
          </div>

          {showProjectSidebar && sidebarProjectId && (
            <div className="sidebar-section">
              <div className="sidebar-label">当前项目</div>
              <nav className="sidebar-nav">
                {DOMAIN_SECTIONS.map((section) => (
                  <div key={section} className="sidebar-domain-block">
                    <div className="sidebar-label">{section}</div>
                    {DOMAINS.filter((domain) => domain.section === section && !domain.hidden).map((domain) => {
                      const selected = currentDomain?.key === domain.key
                      return (
                        <Link
                          key={domain.key}
                          to={projectPath(sidebarProjectId, domain.to)}
                          className={`sidebar-link${selected ? ' active' : ''}`}
                          aria-current={selected ? 'page' : undefined}
                        >
                          <span className="sidebar-link-icon">{DOMAIN_ICONS[domain.key]}</span>
                          {domain.label}
                        </Link>
                      )
                    })}
                  </div>
                ))}
              </nav>
            </div>
          )}
        </aside>
        <main className="content-main">
          <Outlet />
        </main>
      </div>

      <Modal
        title="修改密码"
        open={passwordModalOpen}
        onCancel={() => setPasswordModalOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Form
          form={passwordForm}
          layout="vertical"
          size="middle"
          onFinish={(values) => void submitChangePassword(values)}
        >
          <Form.Item
            label="当前密码"
            name="current_password"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            label="新密码"
            name="new_password"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '新密码至少 8 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            label="确认新密码"
            name="confirm_password"
            rules={[{ required: true, message: '请再次输入新密码' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={passwordSubmitting} block>
              确认修改
            </Button>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
