import { Navigate, useParams, useSearchParams } from 'react-router-dom'

/** Legacy detail URL → ApiPost-style workspace with query params. */
export default function ApiDefinitionDetail() {
  const { projectId, apiId } = useParams()
  const [searchParams] = useSearchParams()
  const qs = new URLSearchParams()
  if (apiId) qs.set('apiId', apiId)
  const tab = searchParams.get('tab')
  if (tab) qs.set('tab', tab)
  const q = qs.toString()
  return <Navigate to={`/projects/${projectId}/api-definitions${q ? `?${q}` : ''}`} replace />
}
