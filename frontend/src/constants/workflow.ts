/** 与后端自由字符串字段兼容的约定取值；可按项目规范调整。 */

export type LabeledValue = { value: string; label: string }

export const REQUIREMENT_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'archived', label: '已归档' },
]

export const TEST_CASE_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'draft', label: '草稿' },
  { value: 'ready', label: '就绪' },
  { value: 'blocked', label: '阻塞' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'archived', label: '已归档' },
]

export const PRIORITY_OPTIONS: LabeledValue[] = [
  { value: 'P0', label: 'P0（最高）' },
  { value: 'P1', label: 'P1' },
  { value: 'P2', label: 'P2' },
  { value: 'P3', label: 'P3' },
  { value: 'P4', label: 'P4（最低）' },
]

export const CASE_TYPE_OPTIONS: LabeledValue[] = [
  { value: '功能', label: '功能' },
  { value: '边界', label: '边界' },
  { value: '异常', label: '异常' },
  { value: '权限', label: '权限' },
]

export const PLAN_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '进行中' },
  { value: 'closed', label: '已关闭' },
]

export const CYCLE_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '进行中' },
  { value: 'closed', label: '已关闭' },
]

export const EXECUTION_RESULT_OPTIONS: LabeledValue[] = [
  { value: 'untested', label: '未测' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'blocked', label: '阻塞' },
]

export const DEFECT_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'open', label: '待处理' },
  { value: 'in_progress', label: '处理中' },
  { value: 'resolved', label: '已解决' },
  { value: 'closed', label: '已关闭' },
]

export function priorityToForm(p: string | null | undefined): string | undefined {
  if (p === null || p === undefined || p === '') {
    return undefined
  }
  return p
}

export function priorityFromForm(v: string | undefined): string | null {
  if (v === undefined || v === '') {
    return null
  }
  return v
}

export function labelForTestCaseStatus(value: string): string {
  return TEST_CASE_STATUS_OPTIONS.find((o) => o.value === value)?.label ?? value
}

export function labelForRequirementStatus(value: string): string {
  return REQUIREMENT_STATUS_OPTIONS.find((o) => o.value === value)?.label ?? value
}

export function labelForPlanStatus(value: string): string {
  return PLAN_STATUS_OPTIONS.find((o) => o.value === value)?.label ?? value
}

export function labelForCycleStatus(value: string): string {
  return CYCLE_STATUS_OPTIONS.find((o) => o.value === value)?.label ?? value
}

export function labelForExecutionResult(value: string): string {
  return EXECUTION_RESULT_OPTIONS.find((o) => o.value === value)?.label ?? value
}

export function labelForDefectStatus(value: string): string {
  return DEFECT_STATUS_OPTIONS.find((o) => o.value === value)?.label ?? value
}
