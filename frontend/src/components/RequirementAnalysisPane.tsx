import { formatBeijing } from '../utils/datetime'
import MarkdownPane from './MarkdownPane'

export type RequirementAnalysisFeatureItem = { name: string; detail: string }
export type RequirementAnalysisFeatureModule = {
  module: string
  items: RequirementAnalysisFeatureItem[]
}
export type RequirementAnalysisUiPage = {
  name: string
  description: string
  areas?: string[]
}
export type RequirementAnalysisData = {
  features?: RequirementAnalysisFeatureModule[] | RequirementAnalysisFeatureItem[]
  ui?: { summary?: string; pages?: RequirementAnalysisUiPage[] }
  unclear?: { item: string; reason: string }[]
  image_count?: number
  /** @deprecated legacy sections ignored in new schema */
  logic_issues?: unknown
  points?: unknown
  boundaries?: unknown
}

const SECTIONS = [
  { id: 'features', title: '功能清单' },
  { id: 'ui', title: '界面描述' },
  { id: 'unclear', title: '待澄清项' },
] as const

function mdEscape(text: string): string {
  return text.replace(/([\\`*_[\]#])/g, '\\$1')
}

function mdUnescape(text: string): string {
  return text.replace(/\\([\\`*_[\]#])/g, '$1')
}

function listOrEmpty(lines: string[]): string {
  return lines.length > 0 ? lines.join('\n') : '_无_'
}

function normalizeFeatures(
  features: RequirementAnalysisData['features'],
): RequirementAnalysisFeatureModule[] {
  if (!features || features.length === 0) return []
  const first = features[0] as Record<string, unknown>
  if ('module' in first) {
    return features as RequirementAnalysisFeatureModule[]
  }
  const items = (features as RequirementAnalysisFeatureItem[]).map((f) => ({
    name: f.name || '未命名',
    detail: f.detail || '',
  }))
  return items.length ? [{ module: '未分组', items }] : []
}

function hideModuleHeader(modules: RequirementAnalysisFeatureModule[]): boolean {
  return modules.length === 1 && (modules[0].module || '').trim() === '未分组'
}

export function analysisToMarkdown(analysis: RequirementAnalysisData): string {
  const features = normalizeFeatures(analysis.features)
  const pages = analysis.ui?.pages ?? []
  const summary = (analysis.ui?.summary ?? '').trim()
  const unclear = analysis.unclear ?? []
  const skipModuleHead = hideModuleHeader(features)

  const featureLines: string[] = []
  for (const mod of features) {
    const moduleName = mdEscape(mod.module || '未命名模块')
    if (!skipModuleHead) featureLines.push(`### ${moduleName}`)
    for (const item of mod.items ?? []) {
      const name = mdEscape(item.name || '未命名')
      const detail = (item.detail || '').trim()
      featureLines.push(detail ? `- **${name}**：${mdEscape(detail)}` : `- **${name}**`)
    }
  }

  const uiLines: string[] = []
  if (summary) uiLines.push(mdEscape(summary))
  for (const pg of pages) {
    const name = mdEscape(pg.name || '未命名')
    const desc = (pg.description || '').trim()
    const areas = (pg.areas ?? []).map((a) => a.trim()).filter(Boolean)
    const areaText = areas.length ? `（${areas.map(mdEscape).join('、')}）` : ''
    uiLines.push(
      desc ? `- **${name}**：${mdEscape(desc)}${areaText}` : `- **${name}**${areaText}`,
    )
  }

  const unclearLines = unclear.map((u) => {
    const item = mdEscape(u.item || '未命名')
    const reason = (u.reason || '').trim()
    return reason ? `- **${item}**：${mdEscape(reason)}` : `- **${item}**`
  })

  return [
    `## ${SECTIONS[0].title}`,
    listOrEmpty(featureLines),
    '',
    `## ${SECTIONS[1].title}`,
    listOrEmpty(uiLines),
    '',
    `## ${SECTIONS[2].title}`,
    listOrEmpty(unclearLines),
  ].join('\n')
}

const BULLET_RE = /^-\s+\*\*(.+?)\*\*(?:：([\s\S]*))?$/
const AREA_TAIL_RE = /（([^）]*)）\s*$/

function isEmptyPlaceholder(body: string): boolean {
  return body.trim() === '_无_' || body.trim() === ''
}

function splitSections(md: string): Record<(typeof SECTIONS)[number]['id'], string> {
  const out: Record<(typeof SECTIONS)[number]['id'], string> = {
    features: '',
    ui: '',
    unclear: '',
  }
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  let current: (typeof SECTIONS)[number]['id'] | null = null
  const buf: string[] = []

  function flush() {
    if (current) out[current] = buf.join('\n').trim()
    buf.length = 0
  }

  for (const line of lines) {
    const h2 = line.match(/^##\s+(.+?)\s*$/)
    if (h2) {
      const title = h2[1].trim()
      const found = SECTIONS.find((s) => s.title === title)
      flush()
      current = found?.id ?? null
      continue
    }
    if (current) buf.push(line)
  }
  flush()
  return out
}

function parseBoldBullet(line: string): { name: string; rest: string } | null {
  const m = line.trim().match(BULLET_RE)
  if (!m) return null
  return { name: mdUnescape(m[1].trim()) || '未命名', rest: mdUnescape((m[2] ?? '').trim()) }
}

function parseFeatureBody(body: string): RequirementAnalysisFeatureModule[] {
  if (isEmptyPlaceholder(body)) return []
  const modules: RequirementAnalysisFeatureModule[] = []
  let current: RequirementAnalysisFeatureModule | null = null

  function ensureModule(name: string) {
    current = { module: name, items: [] }
    modules.push(current)
  }

  for (const raw of body.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const h3 = line.match(/^###\s+(.+?)\s*$/)
    if (h3) {
      ensureModule(mdUnescape(h3[1].trim()) || '未命名模块')
      continue
    }
    const bullet = parseBoldBullet(line)
    if (!bullet) continue
    if (!current) ensureModule('未分组')
    current!.items.push({ name: bullet.name, detail: bullet.rest })
  }
  return modules
}

function parseUiBody(body: string): NonNullable<RequirementAnalysisData['ui']> {
  if (isEmptyPlaceholder(body)) return { summary: '', pages: [] }
  const summaryParts: string[] = []
  const pages: RequirementAnalysisUiPage[] = []

  for (const raw of body.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const bullet = parseBoldBullet(line)
    if (bullet) {
      let description = bullet.rest
      let areas: string[] = []
      const areaMatch = description.match(AREA_TAIL_RE)
      if (areaMatch) {
        areas = areaMatch[1]
          .split('、')
          .map((a) => mdUnescape(a.trim()))
          .filter(Boolean)
        description = description.slice(0, areaMatch.index).trim()
      }
      pages.push({ name: bullet.name, description, areas })
      continue
    }
    if (pages.length === 0) summaryParts.push(mdUnescape(line))
  }

  return { summary: summaryParts.join('\n').trim(), pages }
}

function parseUnclearBody(body: string): NonNullable<RequirementAnalysisData['unclear']> {
  if (isEmptyPlaceholder(body)) return []
  const unclear: { item: string; reason: string }[] = []
  for (const raw of body.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const bullet = parseBoldBullet(line)
    if (!bullet) continue
    unclear.push({ item: bullet.name, reason: bullet.rest })
  }
  return unclear
}

export function markdownToAnalysis(
  md: string,
  opts?: { image_count?: number },
): RequirementAnalysisData {
  const sections = splitSections(md)
  return {
    features: parseFeatureBody(sections.features),
    ui: parseUiBody(sections.ui),
    unclear: parseUnclearBody(sections.unclear),
    image_count: opts?.image_count ?? 0,
  }
}

export default function RequirementAnalysisPane({
  value,
  onChange,
  analysis,
  pending = null,
  analyzedAt,
}: {
  value?: string
  onChange?: (next: string) => void
  analysis: RequirementAnalysisData
  pending?: RequirementAnalysisData | null
  analyzedAt?: string | null
}) {
  const display = pending ?? analysis
  const md = value ?? analysisToMarkdown(display)
  const imageCount = display.image_count ?? analysis.image_count ?? 0
  const metaPills: { key: string; label: string; tone?: 'pending' }[] = []
  if (analyzedAt) {
    metaPills.push({ key: 'time', label: formatBeijing(analyzedAt) })
  }
  if (imageCount > 0) {
    metaPills.push({ key: 'images', label: `${imageCount} 张图片` })
  }
  if (pending) {
    metaPills.push({ key: 'pending', label: '待确认修改', tone: 'pending' })
  }

  return (
    <div className="req-analysis-pane">
      {metaPills.length > 0 ? (
        <div className="req-analysis-meta">
          {metaPills.map((p) => (
            <span
              key={p.key}
              className={
                p.tone === 'pending'
                  ? 'req-analysis-meta-pill req-analysis-meta-pill--pending'
                  : 'req-analysis-meta-pill'
              }
            >
              {p.label}
            </span>
          ))}
        </div>
      ) : null}
      <MarkdownPane
        withOutline
        className="plan-draft-md req-analysis-md"
        value={md}
        onChange={onChange}
      />
    </div>
  )
}
