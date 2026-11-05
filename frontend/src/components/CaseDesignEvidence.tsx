import { Descriptions } from 'antd'

export type DesignTrace = {
  basis: 'confirmed' | 'inferred' | 'conditional' | 'assumed'
  source_ref: string
  locator: string
  excerpt: string
  method: string
  coverage_target: string
  rationale: string
  readiness: 'ready' | 'needs_data' | 'needs_rule'
  preparation_note?: string | null
  skill_version: string
  source_digest: string
  requirement_id: number | null
  llm_model?: string | null
}

const basisLabels = { confirmed: '明确依据', inferred: '合理推导', conditional: '待确认细则', assumed: '用户指定假设' }
const readinessLabels = { ready: '可准备执行', needs_data: '需要测试数据', needs_rule: '需要确认规则' }

export default function CaseDesignEvidence({ trace }: { trace?: DesignTrace | null }) {
  if (!trace) return null
  return (
    <section aria-label="设计依据" style={{ marginBlock: 16, overflowWrap: 'anywhere' }}>
      <Descriptions title="设计依据" size="small" bordered column={1} items={[
        { key: 'source', label: '依据与位置', children: `${basisLabels[trace.basis]} · ${trace.source_ref} · ${trace.locator}` },
        { key: 'excerpt', label: '依据摘录', children: trace.excerpt },
        { key: 'method', label: '设计方法', children: trace.method },
        { key: 'target', label: '覆盖目标', children: trace.coverage_target },
        { key: 'why', label: '推导理由', children: trace.rationale },
        { key: 'ready', label: '准备状态', children: [readinessLabels[trace.readiness], trace.preparation_note].filter(Boolean).join(' · ') },
        { key: 'version', label: '设计规则版本', children: trace.skill_version },
        {
          key: 'model',
          label: '生成模型',
          children: trace.llm_model?.trim() || '未记录（历史草稿）',
        },
      ]} />
    </section>
  )
}
