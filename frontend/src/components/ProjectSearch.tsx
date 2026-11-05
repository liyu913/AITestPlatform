import { AutoComplete, Input } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiJson } from '../api/client'

type SearchHit = {
  kind: string
  id: number
  title: string
  subtitle: string | null
  link: string
}

type SearchGroup = {
  kind: string
  label: string
  items: SearchHit[]
}

type SearchOut = {
  query: string
  groups: SearchGroup[]
  total: number
}

/** Top-bar jump search: titles across requirements, cases, plans, defects and automation assets. */
export default function ProjectSearch({ projectId }: { projectId: string }) {
  const nav = useNavigate()
  const [input, setInput] = useState('')
  const [term, setTerm] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => setTerm(input.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [input])

  const query = useQuery({
    queryKey: ['project-search', projectId, term],
    queryFn: () =>
      apiJson<SearchOut>(`/api/v1/projects/${projectId}/search?q=${encodeURIComponent(term)}`),
    enabled: term.length > 0,
  })

  const links = useMemo(() => {
    const map = new Map<string, string>()
    for (const group of query.data?.groups ?? []) {
      for (const item of group.items) map.set(`${item.kind}:${item.id}`, item.link)
    }
    return map
  }, [query.data])

  const options = (query.data?.groups ?? []).map((group) => ({
    label: <span className="muted">{group.label}</span>,
    options: group.items.map((item) => ({
      value: `${item.kind}:${item.id}`,
      label: (
        <span className="topbar-search-hit">
          <span className="topbar-search-hit-title">{item.title}</span>
          {item.subtitle ? <span className="muted">{item.subtitle}</span> : null}
        </span>
      ),
    })),
  }))

  return (
    <AutoComplete
      value={input}
      options={options}
      style={{ width: 260 }}
      popupMatchSelectWidth={360}
      notFoundContent={term && !query.isLoading && (query.data?.total ?? 0) === 0 ? '无匹配结果' : null}
      onChange={(value) => setInput(value)}
      onSelect={(value) => {
        const link = links.get(String(value))
        if (link) nav(`/projects/${projectId}/${link}`)
        setInput('')
        setTerm('')
      }}
      onBlur={() => setInput('')}
    >
      <Input.Search
        allowClear
        placeholder="搜索需求 / 用例 / 计划 / 缺陷"
        loading={query.isFetching}
        aria-label="项目内搜索"
      />
    </AutoComplete>
  )
}
