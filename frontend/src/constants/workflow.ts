/** 与后端自由字符串字段兼容的约定取值；可按项目规范调整。 */

export type LabeledValue = { value: string; label: string }

export const REQUIREMENT_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'archived', label: '已归档' },
]

/**
 * 用例生命周期。执行结果不再写入这里，改由 `case.last_result` 展示。
 */
export const LIFECYCLE_STATUS_OPTIONS: LabeledValue[] = [
  { value: 'draft', label: '草稿' },
  { value: 'pending_review', label: '待评审' },
  { value: 'confirmed', label: '已确认' },
  { value: 'archived', label: '已归档' },
]

/** 用例最近一次执行结果的展示项。 */
export const CASE_RESULT_OPTIONS: LabeledValue[] = [
  { value: 'untested', label: '未执行' },
  { value: 'passed', label: '通过' },
  { value: 'failed', label: '失败' },
  { value: 'blocked', label: '阻塞' },
  { value: 'error', label: '错误' },
]

/** @deprecated 使用 LIFECYCLE_STATUS_OPTIONS（保留兼容旧引用）。 */
export const TEST_CASE_STATUS_OPTIONS: LabeledValue[] = LIFECYCLE_STATUS_OPTIONS

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
  { value: 'resolved', label: '已修复' },
  { value: 'reopened', label: '重新打开' },
  { value: 'closed', label: '已关闭' },
]

/** 缺陷失败分类（B5）。 */
export const DEFECT_CATEGORY_OPTIONS: LabeledValue[] = [
  { value: 'product', label: '产品缺陷' },
  { value: 'environment', label: '环境' },
  { value: 'data', label: '数据' },
  { value: 'script', label: '执行脚本' },
  { value: 'rule_pending', label: '规则待确认' },
  { value: 'ux', label: '体验建议' },
]

export function labelForDefectCategory(value: string | null | undefined): string {
  if (!value) return '产品缺陷'
  return DEFECT_CATEGORY_OPTIONS.find((o) => o.value === value)?.label ?? value
}

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

/** 旧数据的自由状态词映射到生命周期，保证历史记录照常显示。 */
const LEGACY_LIFECYCLE_LABELS: Record<string, string> = {
  ready: '已确认',
  active: '已确认',
  done: '已确认',
  new: '草稿',
  todo: '草稿',
}

export function labelForLifecycleStatus(value: string | null | undefined): string {
  if (!value) return '草稿'
  const hit = LIFECYCLE_STATUS_OPTIONS.find((o) => o.value === value)
  if (hit) return hit.label
  return LEGACY_LIFECYCLE_LABELS[value] ?? value
}

export function labelForCaseResult(value: string | null | undefined): string {
  const key = value || 'untested'
  return CASE_RESULT_OPTIONS.find((o) => o.value === key)?.label ?? key
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
