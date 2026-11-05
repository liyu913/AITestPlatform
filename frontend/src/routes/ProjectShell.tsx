import { Link, Outlet, useLocation, useParams } from 'react-router-dom'
import { activeDomain, activeTab, projectPath } from './domainNav'

export default function ProjectShell() {
  const { projectId } = useParams()
  const { pathname } = useLocation()
  const domain = projectId ? activeDomain(pathname, projectId) : null
  const tab = domain && projectId ? activeTab(domain, pathname, projectId) : null

  return (
    <div className="project-shell">
      {projectId && domain && domain.tabs.length > 0 && (
        <nav className="domain-tabs" aria-label={domain.label}>
          {domain.tabs.map((item) => {
            const selected = tab?.to === item.to
            return (
              <Link
                key={item.to}
                to={projectPath(projectId, item.to)}
                className={`domain-tab${selected ? ' is-active' : ''}`}
                aria-current={selected ? 'page' : undefined}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>
      )}
      {/*
        Keyed by project: switching projects remounts the page so filters,
        selections and pagination never leak from the previous project.
      */}
      <div key={projectId ?? 'no-project'}>
        <Outlet />
      </div>
    </div>
  )
}
