/** Preset dimensions for AI test-case review (keys must match backend). */

export type CaseReviewDimDef = {
  key: string
  title: string
}

export const CASE_REVIEW_DIMENSIONS: CaseReviewDimDef[] = [
  { key: 'coverage', title: '覆盖完整性' },
  { key: 'correctness', title: '正确性' },
  { key: 'redundancy', title: '冗余去重' },
  { key: 'boundary', title: '边界与异常' },
  { key: 'clarity', title: '步骤清晰度' },
  { key: 'priority', title: '优先级合理性' },
  { key: 'precondition', title: '前置/数据完整性' },
  { key: 'traceability', title: '需求追溯' },
]

export const CASE_REVIEW_DIM_TITLE: Record<string, string> = Object.fromEntries(
  CASE_REVIEW_DIMENSIONS.map((d) => [d.key, d.title]),
)

export const DEFAULT_REVIEW_WEIGHT = 3

export type ReviewDimensionConfig = {
  key: string
  weight: number
}

export function defaultReviewDimensions(): ReviewDimensionConfig[] {
  return CASE_REVIEW_DIMENSIONS.map((d) => ({
    key: d.key,
    weight: DEFAULT_REVIEW_WEIGHT,
  }))
}
