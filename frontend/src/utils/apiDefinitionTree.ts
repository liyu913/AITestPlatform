import { Tag, Typography } from 'antd'
import type { DataNode } from 'antd/es/tree'
import type { Key, ReactNode } from 'react'
import { createElement } from 'react'

export type DefForTree = {
  id: number
  name: string
  method: string
  path: string
  module?: string
}

export function methodColor(m: string): string {
  if (m === 'GET') return 'blue'
  if (m === 'POST') return 'green'
  if (m === 'PUT' || m === 'PATCH') return 'orange'
  if (m === 'DELETE') return 'red'
  return 'default'
}

export function apiDefKey(id: number): string {
  return `api-${id}`
}

export function parseApiDefKey(key: Key): number | null {
  const s = String(key)
  if (!s.startsWith('api-')) return null
  const n = Number(s.slice(4))
  return Number.isFinite(n) ? n : null
}

export function filterDefinitionsForTree<T extends DefForTree>(defs: T[], search: string): T[] {
  const q = search.trim().toLowerCase()
  if (!q) return defs
  return defs.filter(
    (d) =>
      d.name.toLowerCase().includes(q) ||
      d.path.toLowerCase().includes(q) ||
      d.method.toLowerCase().includes(q),
  )
}

function defaultLeafTitle(d: DefForTree): ReactNode {
  return createElement(
    'span',
    { className: 'api-ws-leaf' },
    createElement(
      Tag,
      { color: methodColor(d.method), style: { marginInlineEnd: 4, lineHeight: '18px' } },
      d.method,
    ),
    createElement('span', { className: 'api-ws-leaf-name' }, d.name),
  )
}

function moduleTitle(name: string, count: number): ReactNode {
  return createElement(
    'span',
    null,
    name,
    createElement(
      Typography.Text,
      { type: 'secondary', style: { marginLeft: 6, fontSize: 12 } },
      `(${count})`,
    ),
  )
}

function leafNode(d: DefForTree, renderLeaf?: (d: DefForTree) => ReactNode): DataNode {
  return {
    key: apiDefKey(d.id),
    title: renderLeaf ? renderLeaf(d) : defaultLeafTitle(d),
    isLeaf: true,
  }
}

function pruneEmptyModules(nodes: DataNode[]): DataNode[] {
  const out: DataNode[] = []
  for (const n of nodes) {
    if (n.isLeaf) {
      out.push(n)
      continue
    }
    const children = pruneEmptyModules(n.children || [])
    if (!children.length) continue
    out.push({ ...n, children })
  }
  return out
}

export type BuildApiDefinitionTreeOptions = {
  /** Remove module folders that have no matching API leaves (useful when searching). */
  pruneEmpty?: boolean
  renderLeaf?: (d: DefForTree) => ReactNode
}

export function moduleFolderKey(name: string): string {
  return `mod-${encodeURIComponent(name)}`
}

export function parseModuleFolderKey(key: Key): string | null {
  const s = String(key)
  if (!s.startsWith('mod-') || s === 'mod-ungrouped') return null
  try {
    return decodeURIComponent(s.slice(4))
  } catch {
    return s.slice(4)
  }
}

/** Build Ant Design Tree data grouped by the definition's module name. */
export function buildApiDefinitionTreeData(
  definitions: DefForTree[],
  options?: BuildApiDefinitionTreeOptions,
): DataNode[] {
  const byModule = new Map<string, DefForTree[]>()
  const ungrouped: DefForTree[] = []
  for (const d of definitions) {
    const name = (d.module || '').trim()
    if (!name) {
      ungrouped.push(d)
      continue
    }
    const list = byModule.get(name) || []
    list.push(d)
    byModule.set(name, list)
  }

  const renderLeaf = options?.renderLeaf
  const names = [...byModule.keys()].sort((a, b) => a.localeCompare(b, 'zh'))
  const roots: DataNode[] = names.map((name) => {
    const apis = byModule.get(name) || []
    return {
      key: moduleFolderKey(name),
      title: moduleTitle(name, apis.length),
      children: apis.map((d) => leafNode(d, renderLeaf)),
    }
  })
  if (ungrouped.length || !roots.length) {
    roots.push({
      key: 'mod-ungrouped',
      title: moduleTitle('未分组', ungrouped.length),
      children: ungrouped.map((d) => leafNode(d, renderLeaf)),
    })
  }

  return options?.pruneEmpty ? pruneEmptyModules(roots) : roots
}

/** Collect all `api-*` keys under a tree (for expand-all etc.). */
export function collectTreeKeys(nodes: DataNode[], acc: Key[] = []): Key[] {
  for (const n of nodes) {
    acc.push(n.key)
    if (n.children?.length) collectTreeKeys(n.children, acc)
  }
  return acc
}
