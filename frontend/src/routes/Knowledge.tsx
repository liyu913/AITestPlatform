import { DeleteOutlined, EditOutlined } from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import type { UploadFile } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import { DEFAULT_KNOWLEDGE_CATEGORIES } from '../constants/knowledgeCategories'
import { formatBeijing } from '../utils/datetime'
import PageBanner from './PageBanner'

const DOC_ACCEPT =
  '.md,.txt,.pdf,.docx,text/markdown,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document'

type KnowledgeLibrary = {
  id: number
  project_id: number
  name: string
  description: string | null
  document_count: number
  ready_count: number
  failed_count: number
  parsing_count: number
  chunk_mode: 'window' | 'heading'
  chunk_window: number
  chunk_overlap: number
  reindex_required: boolean
  created_at: string
  updated_at: string
}

type Doc = {
  id: number
  library_id: number
  filename: string
  category: string | null
  parse_status: string
  error_message: string | null
  size_bytes: number
  updated_at: string
}

type CategoryFilter = string | '__none__' | null

type IndexChunk = {
  id: number
  chunk_index: number
  text: string
  index_status: string
}

type RagHit = {
  chunk_id: number
  text: string
  title_resolved: string
  score: number
}

type LibraryForm = {
  name: string
  description?: string
}

type SettingsForm = {
  name: string
  description?: string
  chunk_mode: 'window' | 'heading'
  chunk_window: number
  chunk_overlap: number
}

type Me = {
  is_superuser: boolean
}

const CHUNK_PRESETS: Array<{
  label: string
  chunk_mode: 'window' | 'heading'
  chunk_window: number
  chunk_overlap: number
}> = [
  { label: '精细', chunk_mode: 'window', chunk_window: 300, chunk_overlap: 60 },
  { label: '标准', chunk_mode: 'window', chunk_window: 400, chunk_overlap: 80 },
  { label: '按标题', chunk_mode: 'heading', chunk_window: 400, chunk_overlap: 80 },
]

const RAG_TOP_K_OPTIONS = [
  { value: 5, label: '5 条' },
  { value: 8, label: '8 条' },
  { value: 12, label: '12 条' },
  { value: 20, label: '20 条' },
]

const RAG_HIT_PREVIEW_CHARS = 280

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    const b = ex.body as { message?: string }
    return b.message ?? `请求失败 (${ex.status})`
  }
  if (ex instanceof Error && ex.message) return ex.message
  return '请求失败'
}

function docStatusMeta(status: string): { label: string; color: string } {
  if (status === 'pending') return { label: '排队中', color: 'default' }
  if (status === 'parsing') return { label: '解析中', color: 'processing' }
  if (status === 'ready') return { label: '可用', color: 'success' }
  if (status === 'failed') return { label: '失败', color: 'error' }
  return { label: status, color: 'default' }
}

function chunkStatusMeta(status: string): { label: string; color: string } {
  if (status === 'ok') return { label: '已写入', color: 'success' }
  if (status === 'pending') return { label: '索引中', color: 'processing' }
  if (status === 'failed') return { label: '失败', color: 'error' }
  return { label: status, color: 'default' }
}

function isBusyStatus(status: string): boolean {
  return status === 'pending' || status === 'parsing'
}

function libraryHealth(lib: KnowledgeLibrary): string {
  const parts: string[] = []
  if (lib.ready_count > 0) parts.push(`${lib.ready_count} 篇可用`)
  if (lib.failed_count > 0) parts.push(`${lib.failed_count} 篇失败`)
  if (lib.parsing_count > 0) parts.push(`${lib.parsing_count} 篇解析中`)
  if (parts.length) return parts.join(' · ')
  return lib.document_count > 0 ? `${lib.document_count} 篇` : '暂无文档'
}

function formatTime(iso: string): string {
  return formatBeijing(iso)
}

function relevanceLabel(score: number, best: number): string {
  const gap = score - best
  if (gap <= Math.max(0.05, Math.abs(best) * 0.15)) return '高'
  if (gap <= Math.max(0.25, Math.abs(best) * 0.5)) return '中'
  return '低'
}

function ChunkList({ projectId, documentId }: { projectId: number; documentId: number }) {
  const chunksQuery = useQuery({
    queryKey: ['knowledge-chunks', projectId, documentId],
    queryFn: () =>
      apiJson<IndexChunk[]>(
        `/api/v1/projects/${projectId}/knowledge/chunks?document_id=${documentId}`,
      ),
  })

  if (chunksQuery.isLoading) return <p className="muted">正在加载分块…</p>
  if (chunksQuery.isError) return <p className="error">分块加载失败</p>
  const rows = chunksQuery.data ?? []
  if (!rows.length) return <p className="muted">暂无分块。解析完成或重建索引后会出现在这里。</p>

  return (
    <div>
      <p className="muted" style={{ marginBottom: 8 }}>
        共 {rows.length} 个分块
      </p>
      <Collapse
        size="small"
        bordered={false}
        defaultActiveKey={[]}
        items={rows.map((row) => {
          const meta = chunkStatusMeta(row.index_status)
          const preview = row.text.replace(/\s+/g, ' ').trim().slice(0, 80)
          return {
            key: String(row.id),
            label: (
              <Space size="small" wrap>
                <span className="muted">#{row.chunk_index}</span>
                <Tag color={meta.color}>{meta.label}</Tag>
                {preview ? (
                  <span className="muted">
                    {preview}
                    {row.text.trim().length > 80 ? '…' : ''}
                  </span>
                ) : null}
              </Space>
            ),
            children: (
              <Typography.Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                {row.text}
              </Typography.Paragraph>
            ),
          }
        })}
      />
    </div>
  )
}

export default function Knowledge() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()
  const [pickedLibraryId, setPickedLibraryId] = useState<number | null>(null)
  const [settingsLibraryId, setSettingsLibraryId] = useState<number | null>(null)
  const [tab, setTab] = useState('docs')
  const [createOpen, setCreateOpen] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameTarget, setRenameTarget] = useState<KnowledgeLibrary | null>(null)
  const [chunksDoc, setChunksDoc] = useState<Doc | null>(null)
  const [createForm] = Form.useForm<LibraryForm>()
  const [renameForm] = Form.useForm<{ name: string }>()
  const [settingsForm] = Form.useForm<SettingsForm>()
  const [ragQuery, setRagQuery] = useState('')
  const [ragTopK, setRagTopK] = useState(8)
  const [ragDocId, setRagDocId] = useState<number | null>(null)
  const [ragHits, setRagHits] = useState<RagHit[] | null>(null)
  const [ragErr, setRagErr] = useState<string | null>(null)
  const [ragSearching, setRagSearching] = useState(false)
  const [expandedHitIds, setExpandedHitIds] = useState<number[]>([])
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>(null)
  const [knownCategories, setKnownCategories] = useState<string[]>([])
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadCategory, setUploadCategory] = useState<string | null>(null)
  const [uploadName, setUploadName] = useState('')
  const [uploadFileList, setUploadFileList] = useState<UploadFile[]>([])
  const [uploading, setUploading] = useState(false)
  const [categoryEditDoc, setCategoryEditDoc] = useState<Doc | null>(null)
  const [categoryEditValue, setCategoryEditValue] = useState<string | null>(null)
  const [addCategoryOpen, setAddCategoryOpen] = useState(false)
  const [addCategoryName, setAddCategoryName] = useState('')
  const [addCategoryFor, setAddCategoryFor] = useState<'upload' | 'edit' | null>(null)

  const librariesQuery = useQuery({
    queryKey: ['knowledge-libraries', pid],
    queryFn: () => apiJson<KnowledgeLibrary[]>(`/api/v1/projects/${pid}/knowledge/libraries`),
    enabled: Number.isFinite(pid),
    refetchInterval: (query) => {
      const rows = query.state.data
      if (rows?.some((lib) => lib.parsing_count > 0)) return 2000
      return false
    },
  })

  const indexStatusQuery = useQuery({
    queryKey: ['knowledge-index-status', pid],
    queryFn: () =>
      apiJson<{ embedding_ready: boolean }>(`/api/v1/projects/${pid}/knowledge/index-status`),
    enabled: Number.isFinite(pid),
  })

  const meQuery = useQuery({
    queryKey: ['users', 'me'],
    queryFn: () => apiJson<Me>('/api/v1/users/me'),
  })

  const libs = librariesQuery.data ?? []
  const activeLibraryId =
    pickedLibraryId != null && libs.some((lib) => lib.id === pickedLibraryId)
      ? pickedLibraryId
      : (libs[0]?.id ?? null)
  const selected = libs.find((lib) => lib.id === activeLibraryId) ?? null

  if (selected && settingsLibraryId !== selected.id) {
    setSettingsLibraryId(selected.id)
    settingsForm.setFieldsValue({
      name: selected.name,
      description: selected.description ?? '',
      chunk_mode: selected.chunk_mode ?? 'window',
      chunk_window: selected.chunk_window ?? 400,
      chunk_overlap: selected.chunk_overlap ?? 80,
    })
  }

  const docsQuery = useQuery({
    queryKey: ['knowledge-docs', pid, activeLibraryId, categoryFilter],
    queryFn: () => {
      const params = new URLSearchParams()
      if (categoryFilter === '__none__') params.set('uncategorized', 'true')
      else if (categoryFilter) params.set('category', categoryFilter)
      const qs = params.toString()
      const suffix = qs ? `?${qs}` : ''
      return apiJson<Doc[]>(
        `/api/v1/projects/${pid}/knowledge/libraries/${activeLibraryId}/documents${suffix}`,
      )
    },
    enabled: Number.isFinite(pid) && activeLibraryId != null,
    refetchInterval: (query) => {
      const rows = query.state.data
      if (rows?.some((doc) => isBusyStatus(doc.parse_status))) return 2000
      return false
    },
  })

  const docs = docsQuery.data ?? []
  const categoryOptions = Array.from(
    new Set([
      ...DEFAULT_KNOWLEDGE_CATEGORIES,
      ...knownCategories,
      ...docs.map((d) => d.category).filter((c): c is string => Boolean(c)),
    ]),
  ).sort((a, b) => a.localeCompare(b, 'zh-CN'))

  useEffect(() => {
    if (!docs.length) return
    setKnownCategories((prev) => {
      const next = Array.from(
        new Set([...prev, ...docs.map((d) => d.category).filter((c): c is string => Boolean(c))]),
      )
      if (next.length === prev.length && next.every((c) => prev.includes(c))) return prev
      return next
    })
  }, [docs])

  const chunkMode = Form.useWatch('chunk_mode', settingsForm)
  const chunkWindow = Form.useWatch('chunk_window', settingsForm)
  const chunkOverlap = Form.useWatch('chunk_overlap', settingsForm)
  const formReady = chunkMode != null && chunkWindow != null && chunkOverlap != null
  const chunkDirty =
    selected != null &&
    settingsLibraryId === selected.id &&
    formReady &&
    (chunkMode !== selected.chunk_mode ||
      Number(chunkWindow) !== selected.chunk_window ||
      Number(chunkOverlap) !== selected.chunk_overlap)
  const showStale = selected != null && selected.document_count > 0 && (selected.reindex_required || chunkDirty)

  function selectLibrary(id: number) {
    if (id === activeLibraryId) return
    setPickedLibraryId(id)
    setRagHits(null)
    setRagErr(null)
    setRagDocId(null)
    setExpandedHitIds([])
    setCategoryFilter(null)
    setKnownCategories([])
  }

  const createLib = useMutation({
    mutationFn: (values: LibraryForm) =>
      apiJson<KnowledgeLibrary>(`/api/v1/projects/${pid}/knowledge/libraries`, {
        method: 'POST',
        body: JSON.stringify({
          name: values.name.trim(),
          description: values.description?.trim() || null,
        }),
      }),
    onSuccess: (lib) => {
      selectLibrary(lib.id)
      setTab('docs')
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      setCreateOpen(false)
      createForm.resetFields()
      message.success('知识库已创建，可以上传文档')
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchLib = useMutation({
    mutationFn: (values: SettingsForm & { id: number }) =>
      apiJson<KnowledgeLibrary>(`/api/v1/projects/${pid}/knowledge/libraries/${values.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: values.name.trim(),
          description: values.description?.trim() || null,
          chunk_mode: values.chunk_mode,
          chunk_window: values.chunk_window,
          chunk_overlap: values.chunk_overlap,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const reindexLibrary = useMutation({
    mutationFn: (libraryId: number) =>
      apiJson<{ queued: number }>(`/api/v1/projects/${pid}/knowledge/libraries/${libraryId}/reindex`, {
        method: 'POST',
      }),
    onSuccess: (data, libraryId) => {
      qc.invalidateQueries({ queryKey: ['knowledge-docs', pid, libraryId] })
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      qc.invalidateQueries({ queryKey: ['knowledge-chunks', pid] })
      message.success(data.queued ? `已提交 ${data.queued} 个文档重建索引` : '该库暂无文档')
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteLib = useMutation({
    mutationFn: (libraryId: number) =>
      apiFetch(`/api/v1/projects/${pid}/knowledge/libraries/${libraryId}`, { method: 'DELETE' }).then(
        async (res) => {
          if (!res.ok) {
            const text = await res.text()
            throw new Error(text || '删除失败')
          }
        },
      ),
    onSuccess: (_data, libraryId) => {
      if (activeLibraryId === libraryId) {
        setPickedLibraryId(null)
        setRagHits(null)
        setRagErr(null)
      }
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      qc.invalidateQueries({ queryKey: ['knowledge-chunks', pid] })
      message.success('知识库已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const deleteDoc = useMutation({
    mutationFn: async ({ docId }: { docId: number; libraryId: number }) => {
      const res = await apiFetch(`/api/v1/projects/${pid}/knowledge/${docId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('删除文档失败')
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['knowledge-docs', pid, variables.libraryId] })
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      qc.invalidateQueries({ queryKey: ['knowledge-chunks', pid, variables.docId] })
      message.success('文档已删除')
    },
    onError: (e) => message.error(errText(e)),
  })

  const patchDoc = useMutation({
    mutationFn: ({ docId, category }: { docId: number; libraryId: number; category: string | null }) =>
      apiJson<Doc>(`/api/v1/projects/${pid}/knowledge/${docId}`, {
        method: 'PATCH',
        body: JSON.stringify({ category }),
      }),
    onSuccess: (doc, variables) => {
      if (doc.category) {
        setKnownCategories((prev) => (prev.includes(doc.category!) ? prev : [...prev, doc.category!]))
      }
      qc.invalidateQueries({ queryKey: ['knowledge-docs', pid, variables.libraryId] })
      message.success('分类已更新')
    },
    onError: (e) => message.error(errText(e)),
  })

  async function uploadFile(
    file: File,
    libraryId: number,
    category: string | null,
    displayName?: string | null,
  ) {
    const fd = new FormData()
    fd.append('file', file)
    const cat = category?.trim()
    if (cat) fd.append('category', cat)
    const name = displayName?.trim()
    if (name) fd.append('name', name)
    const res = await apiFetch(`/api/v1/projects/${pid}/knowledge/libraries/${libraryId}/upload`, {
      method: 'POST',
      body: fd,
    })
    if (!res.ok) {
      const text = await res.text()
      let msg = text || '上传失败'
      try {
        const parsed = JSON.parse(text) as { message?: string }
        if (parsed.message) msg = parsed.message
      } catch {
        /* 保留原始错误文本 */
      }
      throw new Error(msg)
    }
    return res.json() as Promise<Doc>
  }

  function resetUploadModal() {
    setUploadOpen(false)
    setUploadCategory(null)
    setUploadName('')
    setUploadFileList([])
  }

  function onUploadFileListChange(fileList: UploadFile[]) {
    setUploadFileList(fileList)
    if (fileList.length === 1) {
      const only = fileList[0]
      const nextName = only.name || only.originFileObj?.name || ''
      setUploadName((prev) => (prev.trim() ? prev : nextName))
    } else {
      setUploadName('')
    }
  }

  function openAddCategory(target: 'upload' | 'edit') {
    setAddCategoryFor(target)
    setAddCategoryName('')
    setAddCategoryOpen(true)
  }

  function confirmAddCategory() {
    const cleaned = addCategoryName.trim()
    if (!cleaned) {
      message.warning('请输入分类名称')
      return
    }
    if (cleaned.length > 128) {
      message.warning('分类名称不能超过 128 字')
      return
    }
    setKnownCategories((prev) => (prev.includes(cleaned) ? prev : [...prev, cleaned]))
    if (addCategoryFor === 'upload') setUploadCategory(cleaned)
    if (addCategoryFor === 'edit') setCategoryEditValue(cleaned)
    setAddCategoryOpen(false)
    setAddCategoryName('')
    setAddCategoryFor(null)
  }

  async function submitUpload() {
    const libraryId = activeLibraryId
    if (libraryId == null) {
      message.error('请先选择知识库')
      return
    }
    const files = uploadFileList
      .map((item) => item.originFileObj)
      .filter((f): f is NonNullable<typeof f> => f != null)
    if (!files.length) {
      message.warning('请先选择文件')
      return
    }
    setUploading(true)
    let ok = 0
    const singleDisplayName = files.length === 1 ? uploadName.trim() : ''
    try {
      for (const file of files) {
        const doc = await uploadFile(
          file,
          libraryId,
          uploadCategory,
          files.length === 1 ? singleDisplayName || null : null,
        )
        if (doc.category) {
          setKnownCategories((prev) => (prev.includes(doc.category!) ? prev : [...prev, doc.category!]))
        }
        ok += 1
      }
      qc.invalidateQueries({ queryKey: ['knowledge-docs', pid, libraryId] })
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      const label = singleDisplayName || files[0]?.name || '文件'
      message.success(ok === 1 ? `${label} 已上传，正在解析` : `已上传 ${ok} 个文件，正在解析`)
      resetUploadModal()
    } catch (e) {
      message.error(errText(e))
      if (ok > 0) {
        qc.invalidateQueries({ queryKey: ['knowledge-docs', pid, libraryId] })
        qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      }
    } finally {
      setUploading(false)
    }
  }

  function openCategoryEdit(doc: Doc) {
    setCategoryEditDoc(doc)
    setCategoryEditValue(doc.category)
  }

  function submitCategoryEdit() {
    if (!categoryEditDoc) return
    const next = categoryEditValue?.trim() || null
    if ((next ?? null) === (categoryEditDoc.category ?? null)) {
      setCategoryEditDoc(null)
      return
    }
    patchDoc.mutate(
      { docId: categoryEditDoc.id, libraryId: categoryEditDoc.library_id, category: next },
      {
        onSuccess: () => setCategoryEditDoc(null),
      },
    )
  }

  async function reindexDoc(docId: number, libraryId: number) {
    try {
      await apiJson(`/api/v1/projects/${pid}/knowledge/${docId}/reindex`, { method: 'POST' })
      message.success('已提交重建索引')
      qc.invalidateQueries({ queryKey: ['knowledge-docs', pid, libraryId] })
      qc.invalidateQueries({ queryKey: ['knowledge-libraries', pid] })
      qc.invalidateQueries({ queryKey: ['knowledge-chunks', pid, docId] })
    } catch (e) {
      message.error(errText(e))
    }
  }

  function saveSettings(andReindex: boolean) {
    if (selected == null) return
    void settingsForm.validateFields().then((values) => {
      patchLib.mutate(
        { ...values, id: selected.id },
        {
          onSuccess: () => {
            if (andReindex) {
              reindexLibrary.mutate(selected.id)
              return
            }
            message.success(
              chunkDirty || selected.reindex_required ? '已保存。分段变更需重建索引后生效。' : '已保存',
            )
          },
        },
      )
    })
  }

  function submitRename() {
    if (renameTarget == null) return
    void renameForm.validateFields().then((values) => {
      const name = values.name.trim()
      const target = renameTarget
      patchLib.mutate(
        {
          id: target.id,
          name,
          description: target.description ?? '',
          chunk_mode: target.chunk_mode ?? 'window',
          chunk_window: target.chunk_window ?? 400,
          chunk_overlap: target.chunk_overlap ?? 80,
        },
        {
          onSuccess: () => {
            if (selected?.id === target.id) {
              settingsForm.setFieldsValue({ name })
            }
            setRenameOpen(false)
            setRenameTarget(null)
            renameForm.resetFields()
            message.success('已重命名')
          },
        },
      )
    })
  }

  function applyPreset(preset: (typeof CHUNK_PRESETS)[number]) {
    settingsForm.setFieldsValue({
      chunk_mode: preset.chunk_mode,
      chunk_window: preset.chunk_window,
      chunk_overlap: preset.chunk_overlap,
    })
  }

  function searchBlockReason(): string | null {
    if (docsQuery.isLoading) return '正在加载文档'
    if (!docs.length) return '这个库还没有文档'
    const hasReady = docs.some((doc) => doc.parse_status === 'ready')
    const hasBusy = docs.some((doc) => isBusyStatus(doc.parse_status))
    if (!hasReady && hasBusy) return '文档仍在解析，完成后再检索'
    if (!hasReady) return '没有可用文档，请先处理解析失败的文件'
    return null
  }

  async function runSearch() {
    if (selected == null) return
    const blocked = searchBlockReason()
    if (blocked) {
      setRagErr(blocked)
      setRagHits(null)
      return
    }
    if (!ragQuery.trim()) {
      setRagErr('请输入查询语句')
      setRagHits(null)
      return
    }
    setRagErr(null)
    setRagSearching(true)
    setExpandedHitIds([])
    try {
      const scopedDocId =
        ragDocId != null && docs.some((doc) => doc.id === ragDocId && doc.parse_status === 'ready')
          ? ragDocId
          : null
      const body: Record<string, unknown> = {
        query: ragQuery.trim(),
        top_k: ragTopK,
      }
      if (scopedDocId != null) {
        body.source_type = 'upload'
        body.source_id = scopedDocId
      } else {
        body.knowledge_library_id = selected.id
      }
      const hits = await apiJson<RagHit[]>(`/api/v1/projects/${pid}/rag/search`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setRagHits(hits)
    } catch (e) {
      setRagHits(null)
      setRagErr(errText(e) || '检索失败（需配置向量模型，且 Milvus 可用）')
    } finally {
      setRagSearching(false)
    }
  }

  const docColumns: ColumnsType<Doc> = [
      { title: '文件名', dataIndex: 'filename', key: 'filename', ellipsis: true },
      {
        title: '分类',
        dataIndex: 'category',
        key: 'category',
        width: 180,
        render: (value: string | null, doc: Doc) => (
          <Space size="small">
            {value ? <Tag>{value}</Tag> : <span className="muted">未分类</span>}
            <Button type="link" size="small" onClick={() => openCategoryEdit(doc)}>
              设置
            </Button>
          </Space>
        ),
      },
      {
        title: '状态',
        dataIndex: 'parse_status',
        key: 'parse_status',
        width: 100,
        render: (status: string) => {
          const meta = docStatusMeta(status)
          return <Tag color={meta.color}>{meta.label}</Tag>
        },
      },
      {
        title: '大小',
        key: 'size',
        width: 90,
        render: (_: unknown, doc: Doc) => `${(doc.size_bytes / 1024).toFixed(1)} KB`,
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        key: 'updated_at',
        width: 180,
        render: (value: string) => formatTime(value),
      },
      {
        title: '说明',
        dataIndex: 'error_message',
        key: 'error_message',
        ellipsis: true,
        render: (msg: string | null, doc: Doc) => {
          if (!msg) return <span className="muted">—</span>
          return <span style={{ color: doc.parse_status === 'failed' ? '#ff4d4f' : '#faad14' }}>{msg}</span>
        },
      },
      {
        title: '操作',
        key: 'actions',
        width: 220,
        render: (_: unknown, doc: Doc) => (
          <Space size="small">
            <Button type="link" size="small" onClick={() => setChunksDoc(doc)}>
              查看分块
            </Button>
            <Button type="link" size="small" onClick={() => void reindexDoc(doc.id, doc.library_id)}>
              重建索引
            </Button>
            <Popconfirm
              title={`确定删除文档「${doc.filename}」？将同时清理分块索引。`}
              onConfirm={() => deleteDoc.mutate({ docId: doc.id, libraryId: doc.library_id })}
              okButtonProps={{ loading: deleteDoc.isPending }}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ]

  if (!Number.isFinite(pid)) {
    return <p className="error">无效项目</p>
  }

  const embeddingReady = indexStatusQuery.data?.embedding_ready !== false
  const bestScore = ragHits?.length ? Math.min(...ragHits.map((hit) => hit.score)) : 0
  const blockedSearch = selected ? searchBlockReason() : null
  const readyDocs = docs.filter((doc) => doc.parse_status === 'ready')
  const ragDocStillValid = ragDocId == null || readyDocs.some((doc) => doc.id === ragDocId)

  return (
    <div className="knowledge-page list-page">
      <div className="list-page-toolbar">
        <PageBanner title="知识库" />
        {indexStatusQuery.data && !indexStatusQuery.data.embedding_ready && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="尚未配置向量模型，文档上传后无法写入向量索引。"
            description={
              meQuery.data?.is_superuser ? (
                <Link to="/admin/ai-settings">前往系统 AI 配置</Link>
              ) : (
                '请联系管理员在系统 AI 配置中填写向量模型。'
              )
            }
          />
        )}
      </div>
      <div className="knowledge-workspace">
        <aside className="knowledge-lib-list">
          <div className="knowledge-lib-list-head">
            <Button
              type="primary"
              block
              onClick={() => {
                createForm.setFieldsValue({ name: '', description: '' })
                setCreateOpen(true)
              }}
            >
              {libs.length ? '新建知识库' : '新建并上传第一份文档'}
            </Button>
          </div>
          {librariesQuery.isLoading && <p className="muted knowledge-lib-empty">正在加载…</p>}
          {!librariesQuery.isLoading && !libs.length && (
            <p className="muted knowledge-lib-empty">还没有知识库。新建后即可上传文档。</p>
          )}
          <ul className="knowledge-lib-items">
            {libs.map((lib) => (
              <li key={lib.id}>
                <div
                  role="button"
                  tabIndex={0}
                  className={`knowledge-lib-item${lib.id === activeLibraryId ? ' is-active' : ''}`}
                  onClick={() => selectLibrary(lib.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      selectLibrary(lib.id)
                    }
                  }}
                >
                  <div className="knowledge-lib-item-row">
                    <span className="knowledge-lib-name">{lib.name}</span>
                    <span
                      className="knowledge-lib-actions"
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                    >
                      <Tooltip title="重命名">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          aria-label={`重命名 ${lib.name}`}
                          onClick={(e) => {
                            e.stopPropagation()
                            setRenameTarget(lib)
                            renameForm.setFieldsValue({ name: lib.name })
                            setRenameOpen(true)
                          }}
                        />
                      </Tooltip>
                      <Popconfirm
                        title={
                          lib.document_count > 0
                            ? `将删除 ${lib.document_count} 篇文档及其索引，确定删除知识库「${lib.name}」？`
                            : `确定删除知识库「${lib.name}」？`
                        }
                        onConfirm={(e) => {
                          e?.stopPropagation()
                          deleteLib.mutate(lib.id)
                        }}
                        onCancel={(e) => e?.stopPropagation()}
                        okButtonProps={{ danger: true, loading: deleteLib.isPending }}
                      >
                        <Tooltip title="删除">
                          <Button
                            type="text"
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            aria-label={`删除 ${lib.name}`}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </Tooltip>
                      </Popconfirm>
                    </span>
                  </div>
                  <span className="knowledge-lib-meta">{libraryHealth(lib)}</span>
                  {lib.description?.trim() ? (
                    <span className="knowledge-lib-desc">{lib.description}</span>
                  ) : null}
                  <span className="knowledge-lib-meta">{formatTime(lib.updated_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        </aside>

        <section className="knowledge-main card">
          {!selected && (
            <p className="muted">{librariesQuery.isLoading ? '正在加载知识库…' : '选择或新建一个知识库。'}</p>
          )}
          {selected && (
            <>
              <div className="knowledge-main-head">
                <div>
                  <h2 style={{ margin: 0 }}>{selected.name}</h2>
                  <p className="muted">{selected.description?.trim() || '未填写说明'}</p>
                </div>
              </div>
              <Tabs
                activeKey={tab}
                onChange={setTab}
                items={[
                  {
                    key: 'docs',
                    label: '文档',
                    children: (
                      <>
                        <Space wrap style={{ marginBottom: 12, width: '100%' }} size="middle">
                          <Button
                            type="primary"
                            onClick={() => {
                              setUploadCategory(null)
                              setUploadFileList([])
                              setUploadOpen(true)
                            }}
                          >
                            上传文档
                          </Button>
                          <Select
                            style={{ width: 180 }}
                            allowClear
                            placeholder="筛选分类"
                            value={categoryFilter}
                            options={[
                              { value: '__none__', label: '未分类' },
                              ...categoryOptions.map((c) => ({ value: c, label: c })),
                            ]}
                            onChange={(v: CategoryFilter) => setCategoryFilter(v ?? null)}
                          />
                        </Space>
                        <Table<Doc>
                          rowKey="id"
                          size="small"
                          loading={docsQuery.isLoading}
                          columns={docColumns}
                          dataSource={docs}
                          pagination={false}
                          locale={{ emptyText: '该库暂无文件' }}
                        />
                      </>
                    ),
                  },
                  {
                    key: 'search',
                    label: '试检索',
                    children: (
                      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                        <p className="field-hint">只检索当前知识库「{selected.name}」。用于检查召回质量，不是业务搜索台。</p>
                        <Space.Compact style={{ width: '100%' }}>
                          <Input
                            placeholder="输入查询语句，回车搜索"
                            value={ragQuery}
                            onChange={(e) => setRagQuery(e.target.value)}
                            onPressEnter={() => void runSearch()}
                            disabled={blockedSearch != null}
                          />
                          <Button
                            type="primary"
                            loading={ragSearching}
                            disabled={blockedSearch != null}
                            onClick={() => void runSearch()}
                          >
                            搜索
                          </Button>
                        </Space.Compact>
                        <Space wrap size="middle" className="knowledge-search-opts">
                          <Space size="small">
                            <span className="muted">返回</span>
                            <Select
                              value={ragTopK}
                              options={RAG_TOP_K_OPTIONS}
                              style={{ width: 96 }}
                              disabled={blockedSearch != null}
                              onChange={(value) => setRagTopK(value)}
                            />
                          </Space>
                          <Space size="small">
                            <span className="muted">文档</span>
                            <Select
                              allowClear
                              placeholder="全部文档"
                              value={ragDocStillValid ? ragDocId : null}
                              style={{ minWidth: 220 }}
                              disabled={blockedSearch != null || readyDocs.length === 0}
                              options={readyDocs.map((doc) => ({
                                value: doc.id,
                                label: doc.filename,
                              }))}
                              onChange={(value) => setRagDocId(value ?? null)}
                            />
                          </Space>
                        </Space>
                        {blockedSearch && <p className="muted">{blockedSearch}</p>}
                        {ragErr && !blockedSearch && <p className="error">{ragErr}</p>}
                        {ragHits !== null && ragHits.length > 0 && (
                          <>
                            <p className="knowledge-hit-summary muted">共返回 {ragHits.length} 条</p>
                            <ul className="knowledge-hit-list">
                              {ragHits.map((hit, index) => {
                                const level = relevanceLabel(hit.score, bestScore)
                                const color = level === '高' ? 'success' : level === '中' ? 'warning' : 'default'
                                const expanded = expandedHitIds.includes(hit.chunk_id)
                                const long = hit.text.length > RAG_HIT_PREVIEW_CHARS
                                const preview =
                                  !expanded && long
                                    ? `${hit.text.slice(0, RAG_HIT_PREVIEW_CHARS).trimEnd()}…`
                                    : hit.text
                                return (
                                  <li key={hit.chunk_id} className="knowledge-hit">
                                    <div className="knowledge-hit-head">
                                      <Space size="small" wrap>
                                        <span className="muted">{index + 1}</span>
                                        <span className="knowledge-hit-title">
                                          {hit.title_resolved || '未命名文档'}
                                        </span>
                                        <Tooltip title={`距离 ${hit.score.toFixed(4)}（越小越相关）`}>
                                          <Tag color={color}>相关度{level}</Tag>
                                        </Tooltip>
                                      </Space>
                                    </div>
                                    <pre className="body-preview knowledge-hit-text">{preview}</pre>
                                    {long && (
                                      <Button
                                        type="link"
                                        size="small"
                                        className="knowledge-hit-toggle"
                                        onClick={() =>
                                          setExpandedHitIds((prev) =>
                                            expanded
                                              ? prev.filter((id) => id !== hit.chunk_id)
                                              : [...prev, hit.chunk_id],
                                          )
                                        }
                                      >
                                        {expanded ? '收起' : '展开全文'}
                                      </Button>
                                    )}
                                  </li>
                                )
                              })}
                            </ul>
                          </>
                        )}
                        {ragHits !== null && ragHits.length === 0 && !ragErr && !ragSearching && (
                          <p className="muted">没有命中</p>
                        )}
                        {ragHits === null && !ragErr && !ragSearching && !blockedSearch && embeddingReady && (
                          <p className="muted">输入查询语句后点击搜索</p>
                        )}
                      </Space>
                    ),
                  },
                  {
                    key: 'settings',
                    label: '设置',
                    children: (
                      <Form form={settingsForm} layout="vertical" style={{ maxWidth: 520 }}>
                        {showStale && (
                          <Alert
                            type="warning"
                            showIcon
                            style={{ marginBottom: 16 }}
                            message="索引仍按旧参数"
                            description="分段方式已变化。保存并重建索引后，检索才会使用新的分块。"
                          />
                        )}
                        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入知识库名称' }]}>
                          <Input maxLength={255} />
                        </Form.Item>
                        <Form.Item name="description" label="说明">
                          <Input.TextArea rows={3} placeholder="简要描述该知识库用途" />
                        </Form.Item>
                        <Collapse
                          items={[
                            {
                              key: 'chunk',
                              label: '高级：分段',
                              children: (
                                <>
                                  <Space wrap style={{ marginBottom: 12 }}>
                                    {CHUNK_PRESETS.map((preset) => {
                                      const active =
                                        chunkMode === preset.chunk_mode &&
                                        Number(chunkWindow) === preset.chunk_window &&
                                        Number(chunkOverlap) === preset.chunk_overlap
                                      return (
                                        <Button
                                          key={preset.label}
                                          type={active ? 'primary' : 'default'}
                                          onClick={() => applyPreset(preset)}
                                        >
                                          {preset.label}
                                        </Button>
                                      )
                                    })}
                                  </Space>
                                  <Form.Item name="chunk_mode" label="分段方式" rules={[{ required: true }]}>
                                    <Select
                                      options={[
                                        { value: 'window', label: '固定窗口（按字符长度滑窗）' },
                                        { value: 'heading', label: '按标题分段（Markdown # 标题，过长再滑窗）' },
                                      ]}
                                    />
                                  </Form.Item>
                                  <Form.Item
                                    name="chunk_window"
                                    label="分块大小"
                                    extra="建议 400–1500。过小检索碎片化，过大上下文噪声多。"
                                    rules={[{ required: true }]}
                                  >
                                    <InputNumber min={100} max={8000} step={50} style={{ width: '100%' }} />
                                  </Form.Item>
                                  <Form.Item
                                    name="chunk_overlap"
                                    label="分块重叠"
                                    extra="须小于分块大小，用于保留跨段语义。"
                                    rules={[
                                      { required: true },
                                      ({ getFieldValue }) => ({
                                        validator(_, value) {
                                          const windowSize = getFieldValue('chunk_window')
                                          if (value == null || windowSize == null || Number(value) < Number(windowSize)) {
                                            return Promise.resolve()
                                          }
                                          return Promise.reject(new Error('分块重叠必须小于分块大小'))
                                        },
                                      }),
                                    ]}
                                  >
                                    <InputNumber min={0} max={4000} step={10} style={{ width: '100%' }} />
                                  </Form.Item>
                                </>
                              ),
                            },
                          ]}
                        />
                        <Space style={{ marginTop: 16 }} wrap>
                          <Button
                            type={showStale ? 'default' : 'primary'}
                            loading={patchLib.isPending && !reindexLibrary.isPending}
                            onClick={() => saveSettings(false)}
                          >
                            保存
                          </Button>
                          {selected.document_count > 0 && (
                            <Button
                              type={showStale ? 'primary' : 'default'}
                              loading={patchLib.isPending || reindexLibrary.isPending}
                              onClick={() => saveSettings(true)}
                            >
                              保存并重建索引
                            </Button>
                          )}
                          <Popconfirm
                            title={
                              selected.document_count > 0
                                ? `将删除 ${selected.document_count} 篇文档及其索引，确定删除知识库「${selected.name}」？`
                                : `确定删除知识库「${selected.name}」？`
                            }
                            onConfirm={() => deleteLib.mutate(selected.id)}
                            okButtonProps={{ danger: true, loading: deleteLib.isPending }}
                          >
                            <Button danger>删除知识库</Button>
                          </Popconfirm>
                        </Space>
                      </Form>
                    ),
                  },
                ]}
              />
            </>
          )}
        </section>
      </div>

      <Modal
        title={chunksDoc ? `分块：${chunksDoc.filename}` : '查看分块'}
        open={chunksDoc != null}
        onCancel={() => setChunksDoc(null)}
        footer={null}
        width={720}
        destroyOnHidden
      >
        {chunksDoc ? (
          <div style={{ maxHeight: '60vh', overflow: 'auto' }}>
            <ChunkList projectId={pid} documentId={chunksDoc.id} />
          </div>
        ) : null}
      </Modal>

      <Modal
        title="新建知识库"
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false)
          createForm.resetFields()
        }}
        onOk={() => {
          void createForm.validateFields().then((values) => createLib.mutate(values))
        }}
        confirmLoading={createLib.isPending}
        destroyOnHidden
      >
        <Form form={createForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入知识库名称' }]}>
            <Input maxLength={255} placeholder="例如：接口文档库" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} placeholder="可选，简要描述该知识库用途" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="重命名知识库"
        open={renameOpen}
        onCancel={() => {
          setRenameOpen(false)
          setRenameTarget(null)
          renameForm.resetFields()
        }}
        onOk={() => submitRename()}
        confirmLoading={patchLib.isPending}
        destroyOnHidden
      >
        <Form form={renameForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入知识库名称' }]}>
            <Input maxLength={255} autoFocus />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="上传文档"
        open={uploadOpen}
        onCancel={() => {
          if (!uploading) resetUploadModal()
        }}
        onOk={() => void submitUpload()}
        okText="开始上传"
        confirmLoading={uploading}
        destroyOnHidden
      >
        <Form layout="vertical">
          <Form.Item label="分类" extra="可选。选择已有分类，或新增自定义分类。">
            <Space.Compact style={{ width: '100%' }}>
              <Select
                allowClear
                placeholder="选择分类（可选）"
                value={uploadCategory}
                style={{ flex: 1 }}
                options={categoryOptions.map((c) => ({ value: c, label: c }))}
                onChange={(v: string | null) => setUploadCategory(v ?? null)}
              />
              <Button onClick={() => openAddCategory('upload')}>新增分类</Button>
            </Space.Compact>
          </Form.Item>
          <Form.Item label="文件" required extra="支持 .md / .txt / .pdf / .docx，可一次选择多个文件">
            <Upload
              multiple
              accept={DOC_ACCEPT}
              fileList={uploadFileList}
              beforeUpload={() => false}
              onChange={({ fileList }) => onUploadFileListChange(fileList)}
            >
              <Button>选择文件</Button>
            </Upload>
          </Form.Item>
          {uploadFileList.length === 1 ? (
            <Form.Item label="名称" extra="可选。留空则使用原文件名；未写扩展名时会自动补上。">
              <Input
                value={uploadName}
                placeholder="文档显示名称"
                maxLength={512}
                onChange={(e) => setUploadName(e.target.value)}
              />
            </Form.Item>
          ) : uploadFileList.length > 1 ? (
            <p className="field-hint">多文件上传时使用各文件原名；如需自定义名称，请一次上传一个文件。</p>
          ) : null}
        </Form>
      </Modal>

      <Modal
        title={categoryEditDoc ? `设置分类 · ${categoryEditDoc.filename}` : '设置分类'}
        open={categoryEditDoc != null}
        onCancel={() => setCategoryEditDoc(null)}
        onOk={submitCategoryEdit}
        confirmLoading={patchDoc.isPending}
        destroyOnHidden
      >
        <Form layout="vertical">
          <Form.Item label="分类">
            <Space.Compact style={{ width: '100%' }}>
              <Select
                allowClear
                placeholder="未分类"
                value={categoryEditValue}
                style={{ flex: 1 }}
                options={categoryOptions.map((c) => ({ value: c, label: c }))}
                onChange={(v: string | null) => setCategoryEditValue(v ?? null)}
              />
              <Button onClick={() => openAddCategory('edit')}>新增分类</Button>
            </Space.Compact>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新增分类"
        open={addCategoryOpen}
        onCancel={() => {
          setAddCategoryOpen(false)
          setAddCategoryName('')
          setAddCategoryFor(null)
        }}
        onOk={confirmAddCategory}
        destroyOnHidden
      >
        <Form layout="vertical">
          <Form.Item label="分类名称" required>
            <Input
              maxLength={128}
              placeholder="例如：测试数据"
              value={addCategoryName}
              onChange={(e) => setAddCategoryName(e.target.value)}
              onPressEnter={confirmAddCategory}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
