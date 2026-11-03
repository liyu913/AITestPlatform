import { useEffect, useRef, useState } from 'react'
import { renderAsync } from 'docx-preview'
import { Button, Space, Spin, Typography } from 'antd'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { apiFetch, ApiError } from '../api/client'

const MAX_TEXT_PREVIEW_CHARS = 200 * 1024

type Props = {
  /** Authenticated URL path, e.g. /api/v1/.../attachments/{id}/file */
  sourceUrl?: string
  /** Local File for pending upload preview */
  localFile?: File | null
  mime?: string | null
  filename?: string | null
  height?: number
}

type PreviewMode = 'pdf' | 'image' | 'docx' | 'markdown' | 'text' | 'download'

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `加载失败 (${ex.status})`
  }
  if (ex instanceof Error) return ex.message
  return '加载失败'
}

function isPdf(mime: string | null | undefined, name: string | null | undefined): boolean {
  if (mime?.includes('pdf')) return true
  return (name || '').toLowerCase().endsWith('.pdf')
}

function isDocx(mime: string | null | undefined, name: string | null | undefined): boolean {
  if (mime?.includes('wordprocessingml') || mime?.includes('officedocument')) return true
  return (name || '').toLowerCase().endsWith('.docx')
}

function isImage(mime: string | null | undefined, name: string | null | undefined): boolean {
  if (mime?.startsWith('image/')) return true
  const n = (name || '').toLowerCase()
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp'].some((s) => n.endsWith(s))
}

function isMarkdown(mime: string | null | undefined, name: string | null | undefined): boolean {
  if (mime === 'text/markdown' || mime === 'text/x-markdown') return true
  return (name || '').toLowerCase().endsWith('.md')
}

function isPlainText(mime: string | null | undefined, name: string | null | undefined): boolean {
  if (isMarkdown(mime, name)) return false
  if (
    mime?.startsWith('text/') ||
    mime === 'application/json' ||
    mime === 'application/javascript'
  ) {
    return true
  }
  const n = (name || '').toLowerCase()
  return ['.txt', '.log', '.json'].some((s) => n.endsWith(s))
}

async function loadBlob(
  localFile: File | null | undefined,
  sourceUrl: string | undefined,
): Promise<Blob> {
  if (localFile) return localFile
  if (!sourceUrl) throw new Error('无可预览文件')
  const res = await apiFetch(sourceUrl)
  if (!res.ok) {
    const text = await res.text()
    let data: unknown = text
    try {
      data = JSON.parse(text) as unknown
    } catch {
      /* keep text */
    }
    throw new ApiError(res.status, data)
  }
  return res.blob()
}

function triggerDownload(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name || 'download'
  a.click()
  URL.revokeObjectURL(url)
}

export default function RequirementSourcePreview({
  sourceUrl,
  localFile,
  mime,
  filename,
  height = 420,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [textContent, setTextContent] = useState<string | null>(null)
  const [textTruncated, setTextTruncated] = useState(false)
  const [mode, setMode] = useState<PreviewMode | null>(null)
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resolvedMime = mime || localFile?.type || null
  const resolvedName = filename || localFile?.name || null

  useEffect(() => {
    let revoked: string | null = null
    let cancelled = false

    async function load() {
      setError(null)
      setObjectUrl(null)
      setTextContent(null)
      setTextTruncated(false)
      setMode(null)
      if (hostRef.current) hostRef.current.innerHTML = ''

      const pdf = isPdf(resolvedMime, resolvedName)
      const docx = isDocx(resolvedMime, resolvedName)
      const image = isImage(resolvedMime, resolvedName)
      const markdown = isMarkdown(resolvedMime, resolvedName)
      const plainText = isPlainText(resolvedMime, resolvedName)

      if (!pdf && !docx && !image && !markdown && !plainText) {
        setMode('download')
        return
      }

      setLoading(true)
      try {
        const blob = await loadBlob(localFile, sourceUrl)
        if (cancelled) return

        if (pdf || image) {
          const url = URL.createObjectURL(blob)
          revoked = url
          setObjectUrl(url)
          setMode(pdf ? 'pdf' : 'image')
        } else if (markdown || plainText) {
          const raw = await blob.text()
          if (cancelled) return
          if (raw.length > MAX_TEXT_PREVIEW_CHARS) {
            setTextContent(raw.slice(0, MAX_TEXT_PREVIEW_CHARS))
            setTextTruncated(true)
          } else {
            setTextContent(raw)
            setTextTruncated(false)
          }
          setMode(markdown ? 'markdown' : 'text')
        } else if (docx) {
          setMode('docx')
          if (hostRef.current) {
            await renderAsync(blob, hostRef.current, undefined, {
              className: 'docx-preview-body',
              inWrapper: true,
              ignoreWidth: true,
              ignoreHeight: true,
            })
          }
        }
      } catch (e) {
        if (!cancelled) setError(errText(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void load()
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [sourceUrl, localFile, resolvedMime, resolvedName])

  async function handleDownload() {
    setDownloading(true)
    try {
      const blob = await loadBlob(localFile, sourceUrl)
      triggerDownload(blob, resolvedName || 'download')
    } catch (e) {
      setError(errText(e))
    } finally {
      setDownloading(false)
    }
  }

  if (error) {
    return (
      <Space direction="vertical" size={8}>
        <Typography.Text type="danger">{error}</Typography.Text>
        {(localFile || sourceUrl) && (
          <Button size="small" loading={downloading} onClick={() => void handleDownload()}>
            下载文件
          </Button>
        )}
      </Space>
    )
  }

  if (mode === 'download') {
    return (
      <div
        style={{
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          padding: 24,
          height,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 12,
          background: '#fff',
        }}
      >
        <Typography.Text type="secondary">
          该类型暂不支持在线预览（如 ZIP），请下载后查看
        </Typography.Text>
        <Typography.Text>{resolvedName}</Typography.Text>
        <Button type="primary" loading={downloading} onClick={() => void handleDownload()}>
          下载文件
        </Button>
      </div>
    )
  }

  return (
    <Spin spinning={loading}>
      <style>{`
        .req-source-preview-host .docx-preview-body-wrapper {
          background: transparent !important;
          padding: 0 !important;
          width: 100% !important;
          align-items: stretch !important;
        }
        .req-source-preview-host .docx-preview-body-wrapper > section.docx-preview-body,
        .req-source-preview-host section.docx-preview-body {
          width: 100% !important;
          max-width: 100% !important;
          min-width: 0 !important;
          box-sizing: border-box !important;
          padding: 16px 24px !important;
          margin: 0 !important;
          box-shadow: none !important;
          overflow: visible !important;
          overflow-wrap: anywhere;
          word-break: break-word;
        }
        .req-source-preview-host .docx-preview-body article,
        .req-source-preview-host .docx-preview-body header,
        .req-source-preview-host .docx-preview-body footer {
          width: 100% !important;
          max-width: 100% !important;
          box-sizing: border-box !important;
        }
        .req-source-preview-host .docx-preview-body img,
        .req-source-preview-host .docx-preview-body-wrapper img {
          max-width: 100% !important;
          height: auto !important;
        }
        .req-source-preview-host .docx-preview-body table {
          max-width: 100% !important;
          table-layout: fixed;
        }
      `}</style>
      {textTruncated ? (
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
          文件较大，仅预览前约 200KB；完整内容请下载
          <Button type="link" size="small" loading={downloading} onClick={() => void handleDownload()}>
            下载
          </Button>
        </Typography.Text>
      ) : null}
      <div
        className="req-source-preview-host"
        style={{
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          overflow: 'auto',
          height,
          background: '#fff',
        }}
      >
        {mode === 'pdf' && objectUrl ? (
          <iframe title="PDF 预览" src={objectUrl} style={{ width: '100%', height: '100%', border: 0 }} />
        ) : mode === 'image' && objectUrl ? (
          <img
            src={objectUrl}
            alt={resolvedName || '预览'}
            style={{ maxWidth: '100%', display: 'block', margin: '0 auto' }}
          />
        ) : mode === 'markdown' ? (
          <div className="markdown-body" style={{ padding: 16 }}>
            {(textContent || '').trim() ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{textContent ?? ''}</ReactMarkdown>
            ) : (
              <p className="markdown-body-empty">暂无内容</p>
            )}
          </div>
        ) : mode === 'text' ? (
          <pre
            style={{
              margin: 0,
              padding: 16,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            {textContent ?? ''}
          </pre>
        ) : (
          <div ref={hostRef} style={{ padding: 8, minHeight: height - 16, width: '100%', boxSizing: 'border-box' }} />
        )}
      </div>
    </Spin>
  )
}
