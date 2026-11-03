import {
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiFetch, apiJson, ApiError } from '../api/client'
import PageBanner from './PageBanner'

type ModuleNode = {
  id: number
  parent_id: number | null
  name: string
  description: string
  sort_order: number
  children: ModuleNode[]
}

type TableRow = Omit<ModuleNode, 'children'> & { children?: TableRow[] }

type ModuleForm = {
  name: string
  description?: string
  sort_order?: number
}

function errText(ex: unknown): string {
  if (ex instanceof ApiError && typeof ex.body === 'object' && ex.body !== null) {
    return (ex.body as { message?: string }).message ?? '请求失败'
  }
  return '请求失败'
}

/** Ant Design tree table: empty children must be undefined (not []). */
function toTableData(nodes: ModuleNode[]): TableRow[] {
  return nodes.map((n) => ({
    id: n.id,
    parent_id: n.parent_id,
    name: n.name,
    description: n.description,
    sort_order: n.sort_order,
    children: n.children?.length ? toTableData(n.children) : undefined,
  }))
}

function findNode(nodes: TableRow[], id: number): TableRow | null {
  for (const n of nodes) {
    if (n.id === id) return n
    if (n.children?.length) {
      const hit = findNode(n.children, id)
      if (hit) return hit
    }
  }
  return null
}

function depthOf(nodes: TableRow[], id: number, depth = 1): number | null {
  for (const n of nodes) {
    if (n.id === id) return depth
    if (n.children?.length) {
      const d = depthOf(n.children, id, depth + 1)
      if (d != null) return d
    }
  }
  return null
}

export default function ModuleList() {
  const { message } = App.useApp()
  const { projectId } = useParams()
  const pid = Number(projectId)
  const qc = useQueryClient()

  const treeQuery = useQuery({
    queryKey: ['project-modules-tree', pid],
    queryFn: () => apiJson<ModuleNode[]>(`/api/v1/projects/${pid}/modules/tree`),
    enabled: Number.isFinite(pid),
  })

  const tableData = useMemo(() => toTableData(treeQuery.data || []), [treeQuery.data])

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [createParentId, setCreateParentId] = useState<number | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [createForm] = Form.useForm<ModuleForm>()
  const [editForm] = Form.useForm<ModuleForm>()

  const selected = useMemo(() => {
    if (selectedId == null) return null
    return findNode(tableData, selectedId)
  }, [selectedId, tableData])

  const createMut = useMutation({
    mutationFn: (body: ModuleForm & { parent_id: number | null }) =>
      apiJson(`/api/v1/projects/${pid}/modules/`, {
        method: 'POST',
        body: JSON.stringify({
          name: body.name.trim(),
          parent_id: body.parent_id,
          description: body.description || '',
          sort_order: body.sort_order ?? 0,
        }),
      }),
    onSuccess: () => {
      message.success('已创建模块')
      setCreateOpen(false)
      createForm.resetFields()
      qc.invalidateQueries({ queryKey: ['project-modules-tree', pid] })
      qc.invalidateQueries({ queryKey: ['project-modules-flat', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  const editMut = useMutation({
    mutationFn: (body: ModuleForm) => {
      if (editingId == null) throw new Error('未选择模块')
      return apiJson(`/api/v1/projects/${pid}/modules/${editingId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: body.name.trim(),
          description: body.description || '',
          sort_order: body.sort_order ?? 0,
        }),
      })
    },
    onSuccess: () => {
      message.success('已保存')
      setEditOpen(false)
      setEditingId(null)
      qc.invalidateQueries({ queryKey: ['project-modules-tree', pid] })
      qc.invalidateQueries({ queryKey: ['project-modules-flat', pid] })
    },
    onError: (e) => message.error(errText(e)),
  })

  async function removeModule(force: boolean, id?: number | null) {
    const targetId = id ?? selectedId
    if (targetId == null) return
    try {
      const qs = force ? '?force=true' : ''
      const res = await apiFetch(`/api/v1/projects/${pid}/modules/${targetId}${qs}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const t = await res.text()
        let msg = '删除失败'
        try {
          msg = (JSON.parse(t) as { message?: string }).message ?? msg
        } catch {
          /* ignore */
        }
        message.error(msg)
        return
      }
      message.success(force ? '已强制删除' : '已删除')
      if (selectedId === targetId) setSelectedId(null)
      qc.invalidateQueries({ queryKey: ['project-modules-tree', pid] })
      qc.invalidateQueries({ queryKey: ['project-modules-flat', pid] })
    } catch {
      message.error('删除失败')
    }
  }

  function openCreate(parentId: number | null) {
    setCreateParentId(parentId)
    createForm.setFieldsValue({ name: '', description: '', sort_order: 0 })
    setCreateOpen(true)
  }

  function openEdit(row: TableRow) {
    setSelectedId(row.id)
    setEditingId(row.id)
    editForm.setFieldsValue({
      name: row.name,
      description: row.description,
      sort_order: row.sort_order,
    })
    setEditOpen(true)
  }

  function openEditSelected() {
    if (!selected) return
    openEdit(selected)
  }

  const columns: ColumnsType<TableRow> = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
    },
    {
      title: '说明',
      dataIndex: 'description',
      ellipsis: true,
      render: (v: string) => v?.trim() || <span className="muted">—</span>,
    },
    {
      title: '排序',
      dataIndex: 'sort_order',
      width: 88,
    },
    {
      title: '操作',
      width: 260,
      render: (_: unknown, row: TableRow) => {
        const depth = depthOf(tableData, row.id) ?? 1
        const canAddChild = depth < 3
        return (
          <Space size="small" wrap onClick={(e) => e.stopPropagation()}>
            <Button
              type="link"
              size="small"
              disabled={!canAddChild}
              onClick={() => openCreate(row.id)}
            >
              新建子模块
            </Button>
            <Button type="link" size="small" onClick={() => openEdit(row)}>
              编辑
            </Button>
            <Popconfirm
              title="删除该模块？"
              description="若有子模块将失败，可改用强制删除"
              onConfirm={() => removeModule(false, row.id)}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  return (
    <div className="list-page">
      <div className="list-page-toolbar">
        <PageBanner title="模块管理" />
        <Space style={{ marginBottom: 8 }} wrap>
          <Button type="primary" onClick={() => openCreate(null)}>
            新建根模块
          </Button>
          <Button
            disabled={selectedId == null || (depthOf(tableData, selectedId!) ?? 1) >= 3}
            onClick={() => openCreate(selectedId)}
          >
            新建子模块
          </Button>
          <Button disabled={!selected} onClick={openEditSelected}>
            编辑
          </Button>
          <Popconfirm
            title="删除该模块？"
            description="若有子模块将失败，可改用强制删除"
            disabled={selectedId == null}
            onConfirm={() => removeModule(false)}
          >
            <Button danger disabled={selectedId == null}>
              删除
            </Button>
          </Popconfirm>
          <Popconfirm
            title="强制删除该模块及其子模块？"
            disabled={selectedId == null}
            onConfirm={() => removeModule(true)}
          >
            <Button danger disabled={selectedId == null}>
              强制删除
            </Button>
          </Popconfirm>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          最多 3 级。接口管理中通过模块字段从本树选择，接口列表保持扁平展示。
        </Typography.Paragraph>
      </div>
      <div className="list-page-table">
        <Table<TableRow>
          rowKey="id"
          loading={treeQuery.isLoading}
          columns={columns}
          dataSource={tableData}
          pagination={false}
          defaultExpandAllRows
          locale={{ emptyText: '暂无模块，请先新建根模块' }}
          rowClassName={(row) => (row.id === selectedId ? 'ant-table-row-selected' : '')}
          onRow={(row) => ({
            onClick: () => setSelectedId(row.id),
            style: { cursor: 'pointer' },
          })}
        />
      </div>

      <Modal
        title={createParentId == null ? '新建根模块' : '新建子模块'}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={(v) => createMut.mutate({ ...v, parent_id: createParentId })}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="sort_order" label="排序" initialValue={0}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={createMut.isPending} block>
            创建
          </Button>
        </Form>
      </Modal>

      <Modal
        title="编辑模块"
        open={editOpen}
        onCancel={() => {
          setEditOpen(false)
          setEditingId(null)
        }}
        footer={null}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" onFinish={(v) => editMut.mutate(v)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="sort_order" label="排序">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={editMut.isPending} block>
            保存
          </Button>
        </Form>
      </Modal>
    </div>
  )
}
