export type DomainSection = '概览' | '测试' | '自动化' | '项目'

export type DomainTab = {
  label: string
  to: string
  matches: string[]
}

export type DomainNav = {
  key: string
  label: string
  section: DomainSection
  to: string
  matches: string[]
  tabs: DomainTab[]
  /** 临时隐藏侧栏入口，路由仍可直达 */
  hidden?: boolean
}

export const DOMAIN_SECTIONS: DomainSection[] = ['概览', '测试', '自动化', '项目']

export const DOMAINS: DomainNav[] = [
  {
    key: 'dashboard',
    label: '项目概览',
    section: '概览',
    to: 'dashboard',
    matches: ['dashboard'],
    tabs: [],
  },
  {
    key: 'requirements',
    label: '需求管理',
    section: '测试',
    to: 'requirements',
    matches: ['requirements'],
    tabs: [],
  },
  {
    key: 'functional',
    label: '功能测试',
    section: '测试',
    to: 'test-cases/ai',
    matches: ['test-cases', 'test-case-runs', 'test-plans', 'cycles', 'defects', 'coverage'],
    tabs: [
      { label: 'AI 生成用例', to: 'test-cases/ai', matches: ['test-cases/ai'] },
      { label: '用例管理', to: 'test-cases', matches: ['test-cases'] },
      { label: '执行记录', to: 'test-case-runs', matches: ['test-case-runs'] },
      { label: '测试计划', to: 'test-plans', matches: ['test-plans', 'cycles'] },
      { label: '缺陷', to: 'defects', matches: ['defects'] },
      { label: '需求覆盖率', to: 'coverage', matches: ['coverage'] },
    ],
  },
  {
    key: 'knowledge',
    label: '知识库',
    section: '测试',
    to: 'knowledge',
    matches: ['knowledge', 'rag'],
    tabs: [],
  },
  {
    key: 'envs',
    label: '环境管理',
    section: '自动化',
    to: 'envs',
    matches: ['envs', 'api-envs', 'ui-envs'],
    tabs: [
      { label: '接口环境', to: 'envs', matches: ['envs', 'api-envs'] },
      { label: 'UI 环境', to: 'ui-envs', matches: ['ui-envs'] },
    ],
  },
  {
    key: 'api',
    label: '接口自动化',
    section: '自动化',
    to: 'api-agent',
    matches: [
      'api-specs',
      'api-definitions',
      'api-cases',
      'api-suites',
      'api-runs',
      'api-schedules',
      'api-agent',
      'api-case-agent',
    ],
    tabs: [
      { label: '场景 Agent', to: 'api-agent', matches: ['api-agent'] },
      { label: '单接口 Agent', to: 'api-case-agent', matches: ['api-case-agent'] },
      { label: '接口资产', to: 'api-specs', matches: ['api-specs'] },
      { label: '接口管理', to: 'api-definitions', matches: ['api-definitions'] },
      { label: '单接口用例', to: 'api-cases', matches: ['api-cases'] },
      { label: '接口场景', to: 'api-suites', matches: ['api-suites'] },
      { label: '执行记录', to: 'api-runs', matches: ['api-runs'] },
    ],
  },
  {
    key: 'ui',
    label: 'UI 自动化',
    section: '自动化',
    to: 'ui-ai',
    matches: ['ui-ai', 'ui-cases', 'ui-suites', 'ui-runs', 'ui-schedules'],
    tabs: [
      { label: 'UI 自动化 Agent', to: 'ui-ai', matches: ['ui-ai'] },
      { label: 'UI 用例', to: 'ui-cases', matches: ['ui-cases'] },
      { label: 'UI 套件', to: 'ui-suites', matches: ['ui-suites'] },
      { label: '执行记录', to: 'ui-runs', matches: ['ui-runs'] },
    ],
  },
  {
    key: 'app',
    label: 'APP 自动化',
    section: '自动化',
    to: 'app-envs',
    matches: ['app-envs', 'app-devices', 'app-cases', 'app-runs'],
    hidden: true,
    tabs: [
      { label: 'APP 环境', to: 'app-envs', matches: ['app-envs'] },
      { label: '设备池', to: 'app-devices', matches: ['app-devices'] },
      { label: 'APP 用例', to: 'app-cases', matches: ['app-cases'] },
      { label: '执行记录', to: 'app-runs', matches: ['app-runs'] },
    ],
  },
  {
    key: 'perf',
    label: '性能测试',
    section: '自动化',
    to: 'perf-scenarios',
    matches: ['perf-scenarios', 'perf-runs'],
    hidden: true,
    tabs: [
      { label: '压测场景', to: 'perf-scenarios', matches: ['perf-scenarios'] },
      { label: '压测记录', to: 'perf-runs', matches: ['perf-runs'] },
    ],
  },
  {
    key: 'modules',
    label: '模块管理',
    section: '项目',
    to: 'modules',
    matches: ['modules'],
    tabs: [],
  },
  {
    key: 'members',
    label: '成员管理',
    section: '项目',
    to: 'members',
    matches: ['members'],
    tabs: [],
  },
]

export function projectPath(projectId: string, segment: string) {
  return `/projects/${projectId}/${segment}`
}

function restPath(pathname: string, projectId: string) {
  const base = `/projects/${projectId}/`
  if (!pathname.startsWith(base)) return ''
  return pathname.slice(base.length).replace(/\/$/, '')
}

function matchesPrefix(rest: string, prefix: string) {
  return rest === prefix || rest.startsWith(`${prefix}/`)
}

function longestMatch<T extends { matches: string[] }>(items: T[], rest: string): T | null {
  let best: { item: T; len: number } | null = null
  for (const item of items) {
    for (const prefix of item.matches) {
      if (matchesPrefix(rest, prefix) && (!best || prefix.length > best.len)) {
        best = { item, len: prefix.length }
      }
    }
  }
  return best?.item ?? null
}

export function activeDomain(pathname: string, projectId: string): DomainNav | null {
  return longestMatch(DOMAINS, restPath(pathname, projectId))
}

export function activeTab(domain: DomainNav, pathname: string, projectId: string): DomainTab | null {
  return longestMatch(domain.tabs, restPath(pathname, projectId))
}
