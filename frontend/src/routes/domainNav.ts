export type DomainSection = '概览' | '测试资产' | '执行' | '质量' | '设置'

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

export const DOMAIN_SECTIONS: DomainSection[] = ['概览', '测试资产', '执行', '质量', '设置']

/**
 * 侧栏与页签的组织方式。每个入口都指向真实路由：
 * 工作台 → 资产 → 计划 → 执行 → 缺陷回归 → 报告，旧路由另行兼容跳转。
 */
export const DOMAINS: DomainNav[] = [
  {
    key: 'workbench',
    label: '工作台',
    section: '概览',
    to: 'workbench',
    matches: ['workbench'],
    tabs: [],
  },
  {
    key: 'dashboard',
    label: '项目概览',
    section: '概览',
    to: 'dashboard',
    matches: ['dashboard'],
    tabs: [],
  },
  {
    key: 'schemes',
    label: '测试方案',
    section: '测试资产',
    to: 'schemes',
    matches: ['schemes'],
    tabs: [],
  },
  {
    key: 'requirements',
    label: '需求管理',
    section: '测试资产',
    to: 'requirements',
    matches: ['requirements'],
    tabs: [],
  },
  {
    key: 'functional',
    label: '功能用例',
    section: '测试资产',
    to: 'test-cases',
    matches: ['test-cases'],
    tabs: [
      { label: '用例管理', to: 'test-cases', matches: ['test-cases'] },
      { label: 'AI 生成用例', to: 'test-cases/ai', matches: ['test-cases/ai'] },
      { label: '用例评审', to: 'reviews', matches: ['reviews'] },
    ],
  },
  {
    key: 'api',
    label: '接口自动化',
    section: '测试资产',
    to: 'api-cases',
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
      { label: '单接口用例', to: 'api-cases', matches: ['api-cases'] },
      { label: '接口场景', to: 'api-suites', matches: ['api-suites'] },
      { label: '接口资产', to: 'api-specs', matches: ['api-specs'] },
      { label: '接口管理', to: 'api-definitions', matches: ['api-definitions'] },
      { label: '执行记录', to: 'api-runs', matches: ['api-runs'] },
      { label: '场景 Agent', to: 'api-agent', matches: ['api-agent'] },
      { label: '单接口 Agent', to: 'api-case-agent', matches: ['api-case-agent'] },
    ],
  },
  {
    key: 'ui',
    label: 'UI 自动化',
    section: '测试资产',
    to: 'ui-cases',
    matches: ['ui-ai', 'ui-cases', 'ui-suites', 'ui-runs', 'ui-schedules'],
    tabs: [
      { label: 'UI 用例', to: 'ui-cases', matches: ['ui-cases'] },
      { label: 'UI 套件', to: 'ui-suites', matches: ['ui-suites'] },
      { label: '执行记录', to: 'ui-runs', matches: ['ui-runs'] },
      { label: '自动化 Agent', to: 'ui-ai', matches: ['ui-ai'] },
    ],
  },
  {
    key: 'knowledge',
    label: '知识库',
    section: '测试资产',
    to: 'knowledge',
    matches: ['knowledge', 'rag'],
    tabs: [],
  },
  {
    key: 'evidence',
    label: '执行证据',
    section: '执行',
    to: 'evidence',
    matches: ['evidence'],
    tabs: [],
  },
  {
    key: 'execution',
    label: '执行中心',
    section: '执行',
    to: 'executions',
    matches: ['executions'],
    tabs: [],
  },
  {
    key: 'testplans',
    label: '测试计划与执行',
    section: '执行',
    to: 'test-plans',
    matches: ['test-plans', 'cycles', 'test-case-runs'],
    tabs: [
      { label: '计划列表', to: 'test-plans', matches: ['test-plans', 'cycles'] },
      { label: '功能执行记录', to: 'test-case-runs', matches: ['test-case-runs'] },
    ],
  },
  {
    key: 'defect-analysis',
    label: '缺陷分析',
    section: '质量',
    to: 'defect-analysis',
    matches: ['defect-analysis'],
    tabs: [],
  },
  {
    key: 'defects',
    label: '缺陷与回归',
    section: '质量',
    to: 'defects',
    matches: ['defects'],
    tabs: [],
  },
  {
    key: 'reports',
    label: '测试报告',
    section: '质量',
    to: 'reports',
    matches: ['reports'],
    tabs: [],
  },
  {
    key: 'coverage',
    label: '报告与覆盖',
    section: '质量',
    to: 'coverage',
    matches: ['coverage'],
    tabs: [],
  },
  {
    key: 'ai-config',
    label: 'AI 与 Skill',
    section: '设置',
    to: 'ai-config',
    matches: ['ai-config'],
    tabs: [],
  },
  {
    key: 'envs',
    label: '环境管理',
    section: '设置',
    to: 'envs',
    matches: ['envs', 'api-envs', 'ui-envs'],
    tabs: [
      { label: '接口环境', to: 'envs', matches: ['envs', 'api-envs'] },
      { label: 'UI 环境', to: 'ui-envs', matches: ['ui-envs'] },
    ],
  },
  {
    key: 'modules',
    label: '模块管理',
    section: '设置',
    to: 'modules',
    matches: ['modules'],
    tabs: [],
  },
  {
    key: 'members',
    label: '成员管理',
    section: '设置',
    to: 'members',
    matches: ['members'],
    tabs: [],
  },
  {
    key: 'app',
    label: 'APP 自动化',
    section: '测试资产',
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
    section: '测试资产',
    to: 'perf-scenarios',
    matches: ['perf-scenarios', 'perf-runs'],
    hidden: true,
    tabs: [
      { label: '压测场景', to: 'perf-scenarios', matches: ['perf-scenarios'] },
      { label: '压测记录', to: 'perf-runs', matches: ['perf-runs'] },
    ],
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

/** 旧路由 → 当前分段的兼容映射（项目内）。 */
export const LEGACY_PROJECT_SEGMENTS: Record<string, string> = {
  dashboard: 'dashboard',
  requirements: 'requirements',
  'test-cases': 'test-cases',
  'test-cases/ai': 'test-cases/ai',
  ai: 'test-cases/ai',
  'test-case-runs': 'test-case-runs',
  'test-plans': 'test-plans',
  executions: 'executions',
  reports: 'reports',
  defects: 'defects',
  coverage: 'coverage',
  envs: 'envs',
  'api-envs': 'envs',
  'ui-envs': 'ui-envs',
  'api-cases': 'api-cases',
  'api-suites': 'api-suites',
  'api-agent': 'api-agent',
  'api-case-agent': 'api-case-agent',
  'api-specs': 'api-specs',
  'api-schedules': 'api-schedules',
  'api-runs': 'api-runs',
  'ui-cases': 'ui-cases',
  'ui-suites': 'ui-suites',
  'ui-schedules': 'ui-schedules',
  'ui-runs': 'ui-runs',
  'ui-ai': 'ui-ai',
  knowledge: 'knowledge',
  rag: 'knowledge',
  modules: 'modules',
  members: 'members',
}
