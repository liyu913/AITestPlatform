import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import Layout from './routes/Layout'
import Login from './routes/Login'
import Register from './routes/Register'
import ProjectShell from './routes/ProjectShell'
import Projects from './routes/Projects'
import RequirementList from './routes/RequirementList'
import TestCaseList from './routes/TestCaseList'
import Knowledge from './routes/Knowledge'
import AiGenerate from './routes/AiGenerate'
import MemberList from './routes/MemberList'
import ModuleList from './routes/ModuleList'
import AdminAiSettings from './routes/AdminAiSettings'
import AdminAiCallLogs from './routes/AdminAiCallLogs'
import AdminSkillsPage from './routes/AdminSkillsPage'
import AdminUserList from './routes/AdminUserList'
import RequireSuperuser from './routes/RequireSuperuser'
import EnvHub from './routes/EnvHub'
import ApiDefinitionWorkspace from './routes/ApiDefinitionWorkspace'
import ApiDefinitionDetail from './routes/ApiDefinitionDetail'
import ApiCaseList from './routes/ApiCaseList'
import ApiSuiteList from './routes/ApiSuiteList'
import ApiScenarioDetail from './routes/ApiScenarioDetail'
import ApiScenarioAgent from './routes/ApiScenarioAgent'
import ApiCaseAgent from './routes/ApiCaseAgent'
import ApiSpecList from './routes/ApiSpecList'
import ApiScheduleList from './routes/ApiScheduleList'
import ApiRunList from './routes/ApiRunList'
import ApiRunDetail from './routes/ApiRunDetail'
import UiCaseList from './routes/UiCaseList'
import UiSuiteList from './routes/UiSuiteList'
import UiScheduleList from './routes/UiScheduleList'
import UiRunList from './routes/UiRunList'
import UiRunDetail from './routes/UiRunDetail'
import UiScenarioAgent from './routes/UiScenarioAgent'
import AppEnvList from './routes/AppEnvList'
import AppCaseList from './routes/AppCaseList'
import AppDeviceList from './routes/AppDeviceList'
import AppRunList from './routes/AppRunList'
import AppRunDetail from './routes/AppRunDetail'
import PerfScenarioList from './routes/PerfScenarioList'
import PerfRunList from './routes/PerfRunList'
import PerfRunDetail from './routes/PerfRunDetail'
import TestPlanList from './routes/TestPlanList'
import TestPlanDetail from './routes/TestPlanDetail'
import TestCycleDetail from './routes/TestCycleDetail'
import TestCaseRunList from './routes/TestCaseRunList'
import TestCaseRunDetail from './routes/TestCaseRunDetail'
import DefectList from './routes/DefectList'
import DefectAnalysisPage from './routes/DefectAnalysisPage'
import EvidencePage from './routes/EvidencePage'
import SchemeList from './routes/SchemeList'
import AiConfigPage from './routes/AiConfigPage'
import ReviewQueue from './routes/ReviewQueue'
import Coverage from './routes/Coverage'
import Dashboard from './routes/Dashboard'
import Workbench from './routes/Workbench'
import ExecutionCenter from './routes/ExecutionCenter'
import Reports from './routes/Reports'
import ReportDetail from './routes/ReportDetail'
import { LEGACY_PROJECT_SEGMENTS } from './routes/domainNav'

const LAST_PROJECT_ID_KEY = 'raina.lastProjectId'

function readLastProjectId(): string | null {
  try {
    const raw = localStorage.getItem(LAST_PROJECT_ID_KEY)
    if (!raw) return null
    const id = Number(raw)
    return Number.isFinite(id) && id > 0 ? String(id) : null
  } catch {
    return null
  }
}

/**
 * 旧链接（/requirements、/api-runs/12 …）进入当前项目的对应页面。
 * 没有上次访问的项目时回到项目列表，而不是报 404。
 */
function LegacyRedirect() {
  const { pathname, search } = useLocation()
  const last = readLastProjectId()
  const rest = pathname.replace(/^\/+/, '').replace(/\/+$/, '')
  const firstSegment = rest.split('/')[0] ?? ''
  const mapped = LEGACY_PROJECT_SEGMENTS[firstSegment]
  if (!mapped) {
    return <Navigate to="/projects" replace />
  }
  const mappedRest = mapped + rest.slice(firstSegment.length)
  if (!last) {
    return <Navigate to="/projects" replace />
  }
  return <Navigate to={`/projects/${last}/${mappedRest}${search}`} replace />
}

function RedirectToKnowledge() {
  const { projectId } = useParams()
  return <Navigate to={`/projects/${projectId}/knowledge`} replace />
}

function RedirectToEnvs({ type }: { type: 'api' | 'ui' }) {
  const { projectId } = useParams()
  return (
    <Navigate
      to={`/projects/${projectId}/${type === 'ui' ? 'ui-envs' : 'envs'}`}
      replace
    />
  )
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/projects" element={<Projects />} />
        <Route
          path="/admin/ai-settings"
          element={
            <RequireSuperuser>
              <AdminAiSettings />
            </RequireSuperuser>
          }
        />
        <Route
          path="/admin/skills"
          element={
            <RequireSuperuser>
              <AdminSkillsPage />
            </RequireSuperuser>
          }
        />
        <Route
          path="/admin/ai-logs"
          element={
            <RequireSuperuser>
              <AdminAiCallLogs />
            </RequireSuperuser>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireSuperuser>
              <AdminUserList />
            </RequireSuperuser>
          }
        />
        <Route path="/projects/:projectId" element={<ProjectShell />}>
          <Route index element={<Navigate to="workbench" replace />} />
          <Route path="workbench" element={<Workbench />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="requirements" element={<RequirementList />} />
          <Route path="test-cases" element={<TestCaseList />} />
          <Route path="test-cases/ai" element={<AiGenerate />} />
          <Route path="test-case-runs" element={<TestCaseRunList />} />
          <Route path="test-case-runs/:runId" element={<TestCaseRunDetail />} />
          <Route path="test-plans" element={<TestPlanList />} />
          <Route path="test-plans/:planId" element={<TestPlanDetail />} />
          <Route path="cycles/:cycleId" element={<TestCycleDetail />} />
          <Route path="executions" element={<ExecutionCenter />} />
          <Route path="reports" element={<Reports />} />
          <Route path="reports/:reportId" element={<ReportDetail />} />
          <Route path="schemes" element={<SchemeList />} />
          <Route path="ai-config" element={<AiConfigPage />} />
          <Route path="reviews" element={<ReviewQueue />} />
          <Route path="defects" element={<DefectList />} />
          <Route path="defect-analysis" element={<DefectAnalysisPage />} />
          <Route path="evidence" element={<EvidencePage />} />
          <Route path="coverage" element={<Coverage />} />
          <Route path="envs" element={<EnvHub />} />
          <Route path="api-envs" element={<RedirectToEnvs type="api" />} />
          <Route path="ui-envs" element={<EnvHub />} />
          <Route path="api-definitions" element={<ApiDefinitionWorkspace />} />
          <Route path="api-definitions/:apiId" element={<ApiDefinitionDetail />} />
          <Route path="api-cases" element={<ApiCaseList />} />
          <Route path="api-suites" element={<ApiSuiteList />} />
          <Route path="api-suites/:suiteId" element={<ApiScenarioDetail />} />
          <Route path="api-agent" element={<ApiScenarioAgent />} />
          <Route path="api-case-agent" element={<ApiCaseAgent />} />
          <Route path="api-specs" element={<ApiSpecList />} />
          <Route path="api-schedules" element={<ApiScheduleList />} />
          <Route path="api-runs" element={<ApiRunList />} />
          <Route path="api-runs/:runId" element={<ApiRunDetail />} />
          <Route path="ui-cases" element={<UiCaseList />} />
          <Route path="ui-suites" element={<UiSuiteList />} />
          <Route path="ui-schedules" element={<UiScheduleList />} />
          <Route path="ui-runs" element={<UiRunList />} />
          <Route path="ui-runs/:runId" element={<UiRunDetail />} />
          <Route path="ui-ai" element={<UiScenarioAgent />} />
          <Route path="app-envs" element={<AppEnvList />} />
          <Route path="app-cases" element={<AppCaseList />} />
          <Route path="app-devices" element={<AppDeviceList />} />
          <Route path="app-runs" element={<AppRunList />} />
          <Route path="app-runs/:runId" element={<AppRunDetail />} />
          <Route path="perf-scenarios" element={<PerfScenarioList />} />
          <Route path="perf-runs" element={<PerfRunList />} />
          <Route path="perf-runs/:runId" element={<PerfRunDetail />} />
          <Route path="knowledge/chunks" element={<RedirectToKnowledge />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="rag" element={<RedirectToKnowledge />} />
          <Route path="modules" element={<ModuleList />} />
          <Route path="members" element={<MemberList />} />
          <Route path="ai" element={<Navigate to="../test-cases/ai" replace />} />
        </Route>
      </Route>
      <Route path="/" element={<Navigate to="/projects" replace />} />
      {/* 旧链接兼容：命中已知分段时进入上次访问的项目，其余回到项目列表 */}
      <Route path="/:legacySegment/*" element={<LegacyRedirect />} />
      <Route path="*" element={<Navigate to="/projects" replace />} />
    </Routes>
  )
}
