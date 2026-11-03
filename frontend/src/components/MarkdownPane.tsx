import { Input, Segmented } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type Mode = 'preview' | 'source'

type OutlineItem = {
  id: string
  level: number
  title: string
  index: number
}

function extractOutline(md: string): OutlineItem[] {
  const items: OutlineItem[] = []
  const re = /^(#{1,3})\s+(.+)$/gm
  let match: RegExpExecArray | null
  let i = 0
  while ((match = re.exec(md)) != null) {
    const title = match[2].replace(/\s+#*\s*$/, '').trim()
    if (!title) continue
    items.push({ id: `md-h-${i}`, level: match[1].length, title, index: i })
    i += 1
  }
  return items
}

function findScrollParent(el: HTMLElement): HTMLElement {
  let node: HTMLElement | null = el
  while (node) {
    const { overflowY } = getComputedStyle(node)
    if (
      (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') &&
      node.scrollHeight > node.clientHeight + 1
    ) {
      return node
    }
    node = node.parentElement
  }
  return el
}

function scrollChildIntoContainer(preferredRoot: HTMLElement, el: HTMLElement) {
  const root =
    preferredRoot.scrollHeight > preferredRoot.clientHeight + 1
      ? preferredRoot
      : findScrollParent(el)
  const rootRect = root.getBoundingClientRect()
  const elRect = el.getBoundingClientRect()
  const nextTop = root.scrollTop + (elRect.top - rootRect.top) - 8
  root.scrollTo({ top: Math.max(0, nextTop), behavior: 'smooth' })
}

function listPreviewHeadings(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>('.md-outline-md h1, .md-outline-md h2, .md-outline-md h3'))
}

export default function MarkdownPane({
  value,
  onChange,
  withOutline = false,
  className,
}: {
  value?: string | null
  onChange?: (next: string) => void
  withOutline?: boolean
  className?: string
}) {
  const md = value ?? ''
  const [mode, setMode] = useState<Mode>('preview')
  const bodyRef = useRef<HTMLDivElement>(null)
  const pendingScrollIndex = useRef<number | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const outline = useMemo(() => (withOutline ? extractOutline(md) : []), [md, withOutline])

  // Stamp stable ids onto rendered headings (avoids ReactMarkdown remount counter drift).
  useEffect(() => {
    if (!withOutline || mode !== 'preview') return
    const root = bodyRef.current
    if (!root) return

    const headings = listPreviewHeadings(root)
    headings.forEach((h, i) => {
      h.id = `md-h-${i}`
    })

    const pending = pendingScrollIndex.current
    if (pending != null) {
      pendingScrollIndex.current = null
      const el = headings[pending]
      if (el) {
        setActiveId(el.id)
        requestAnimationFrame(() => scrollChildIntoContainer(root, el))
      }
    }

    if (headings.length === 0) return
    setActiveId((prev) => prev ?? headings[0].id)

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => (a.boundingClientRect.top ?? 0) - (b.boundingClientRect.top ?? 0))
        const first = visible[0]?.target as HTMLElement | undefined
        if (first?.id) setActiveId(first.id)
      },
      { root, rootMargin: '0px 0px -65% 0px', threshold: [0, 0.1, 1] },
    )
    for (const h of headings) observer.observe(h)
    return () => observer.disconnect()
  }, [withOutline, mode, md])

  function scrollToOutlineItem(item: OutlineItem) {
    setActiveId(item.id)
    if (mode !== 'preview') {
      pendingScrollIndex.current = item.index
      setMode('preview')
      return
    }
    const root = bodyRef.current
    if (!root) return
    const headings = listPreviewHeadings(root)
    headings.forEach((h, i) => {
      h.id = `md-h-${i}`
    })
    const el =
      headings[item.index] ??
      headings.find((h) => (h.textContent || '').trim() === item.title)
    if (el) scrollChildIntoContainer(root, el)
  }

  const toolbar = (
    <Segmented
      value={mode}
      onChange={(next) => setMode(next as Mode)}
      options={[
        { label: '预览', value: 'preview' },
        { label: '源码', value: 'source' },
      ]}
    />
  )

  const preview = (
    <div className="markdown-body md-outline-md">
      {md.trim() ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      ) : (
        <p className="markdown-body-empty">暂无内容</p>
      )}
    </div>
  )

  const source = onChange ? (
    <Input.TextArea
      className="md-outline-source"
      value={md}
      onChange={(e) => onChange(e.target.value)}
      style={{
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        height: withOutline ? '100%' : undefined,
        resize: withOutline ? 'none' : undefined,
      }}
      rows={withOutline ? undefined : 18}
    />
  ) : (
    <pre className="markdown-source">{md}</pre>
  )

  if (!withOutline) {
    return (
      <div className={className}>
        <div style={{ marginBottom: 8 }}>{toolbar}</div>
        {mode === 'preview' ? preview : source}
      </div>
    )
  }

  return (
    <div className={`md-outline-pane ${className ?? ''}`.trim()}>
      <div className="md-outline-pane-toolbar">{toolbar}</div>
      <div className="md-outline-split">
        <nav className="md-outline-toc" aria-label="文档大纲">
          <div className="md-outline-toc-label">大纲</div>
          {outline.length === 0 ? (
            <div className="md-outline-toc-empty">无标题</div>
          ) : (
            outline.map((item) => (
              <button
                key={item.id}
                type="button"
                className={
                  activeId === item.id ? 'md-outline-toc-item is-active' : 'md-outline-toc-item'
                }
                data-level={item.level}
                onClick={() => scrollToOutlineItem(item)}
                title={item.title}
              >
                <span className="md-outline-toc-title">{item.title}</span>
              </button>
            ))
          )}
        </nav>
        <div className="md-outline-body" ref={bodyRef}>
          {mode === 'preview' ? preview : source}
        </div>
      </div>
    </div>
  )
}
