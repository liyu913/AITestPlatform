import { Space, Typography } from 'antd'
import { useMemo } from 'react'

type PrettyJsonProps = {
  value: string | object | null | undefined
  emptyText?: string
  className?: string
}

type TokenKind = 'key' | 'string' | 'number' | 'boolean' | 'null' | 'plain'

type Token = { kind: TokenKind; text: string }

function normalize(value: string | object | null | undefined): {
  text: string
  isJson: boolean
} {
  if (value == null) return { text: '', isJson: false }
  if (typeof value === 'object') {
    try {
      return { text: JSON.stringify(value, null, 2), isJson: true }
    } catch {
      return { text: String(value), isJson: false }
    }
  }
  const raw = value
  if (!raw.trim()) return { text: raw, isJson: false }
  try {
    const parsed = JSON.parse(raw)
    return { text: JSON.stringify(parsed, null, 2), isJson: true }
  } catch {
    return { text: raw, isJson: false }
  }
}

/** Tokenize pretty-printed JSON for lightweight syntax highlighting. */
function tokenizeJson(src: string): Token[] {
  const tokens: Token[] = []
  const re =
    /("(?:\\.|[^"\\])*")\s*:|("(?:\\.|[^"\\])*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false)\b|\b(null)\b/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(src)) !== null) {
    if (m.index > last) {
      tokens.push({ kind: 'plain', text: src.slice(last, m.index) })
    }
    if (m[1] !== undefined) {
      tokens.push({ kind: 'key', text: m[1] })
      const afterKey = src.slice(m.index + m[1].length, m.index + m[0].length)
      if (afterKey) tokens.push({ kind: 'plain', text: afterKey })
    } else if (m[2] !== undefined) {
      tokens.push({ kind: 'string', text: m[2] })
    } else if (m[3] !== undefined) {
      tokens.push({ kind: 'number', text: m[3] })
    } else if (m[4] !== undefined) {
      tokens.push({ kind: 'boolean', text: m[4] })
    } else if (m[5] !== undefined) {
      tokens.push({ kind: 'null', text: m[5] })
    }
    last = m.index + m[0].length
  }
  if (last < src.length) {
    tokens.push({ kind: 'plain', text: src.slice(last) })
  }
  return tokens
}

export default function PrettyJson({ value, emptyText = '—', className }: PrettyJsonProps) {
  const { text, isJson } = useMemo(() => normalize(value), [value])

  const tokens = useMemo(() => (isJson && text ? tokenizeJson(text) : null), [isJson, text])

  if (!text) {
    return <span className="pretty-json-empty">{emptyText}</span>
  }

  return (
    <div className={`pretty-json${className ? ` ${className}` : ''}`}>
      <div className="pretty-json-toolbar">
        <Space size={8}>
          {isJson && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              JSON
            </Typography.Text>
          )}
          <Typography.Text copyable={{ text, tooltips: ['复制', '已复制'] }} style={{ fontSize: 12 }}>
            复制
          </Typography.Text>
        </Space>
      </div>
      <pre className="pretty-json-pre">
        {tokens
          ? tokens.map((t, i) =>
              t.kind === 'plain' ? (
                <span key={i}>{t.text}</span>
              ) : (
                <span key={i} className={`pretty-json-token pretty-json-${t.kind}`}>
                  {t.text}
                </span>
              ),
            )
          : text}
      </pre>
    </div>
  )
}
