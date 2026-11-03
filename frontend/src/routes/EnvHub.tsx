import { Navigate, useLocation, useParams, useSearchParams } from 'react-router-dom'
import ApiEnvList from './ApiEnvList'
import UiEnvList from './UiEnvList'

type EnvType = 'api' | 'ui'

function typeFromPath(pathname: string): EnvType {
  return pathname.includes('/ui-envs') ? 'ui' : 'api'
}

export default function EnvHub() {
  const { projectId } = useParams()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const type = typeFromPath(location.pathname)

  // Legacy ?type=ui → dedicated path so domain tabs highlight correctly
  if (projectId && searchParams.get('type') === 'ui' && !location.pathname.includes('/ui-envs')) {
    return <Navigate to={`/projects/${projectId}/ui-envs`} replace />
  }

  return (
    <div className="env-hub">
      {type === 'api' ? <ApiEnvList embedded /> : <UiEnvList embedded />}
    </div>
  )
}
