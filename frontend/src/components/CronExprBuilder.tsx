import { Button, Input, InputNumber, Select, Space, Typography } from 'antd'
import { useEffect, useMemo, useState, type ReactNode } from 'react'

type Freq = 'interval' | 'hourly' | 'daily' | 'weekly' | 'monthly' | 'custom'

type Model = {
  freq: Freq
  everyMin: number
  minute: number
  localHour: number
  localMinute: number
  weekday: number
  monthDay: number
  custom: string
}

const WEEKDAYS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 0, label: '周日' },
]

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local'

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function localWallToUtc(hour: number, minute: number, weekday?: number, monthDay?: number) {
  const d = new Date()
  if (monthDay != null) {
    d.setDate(Math.min(28, Math.max(1, monthDay)))
  }
  if (weekday != null) {
    const delta = (weekday - d.getDay() + 7) % 7
    d.setDate(d.getDate() + delta)
  }
  d.setHours(hour, minute, 0, 0)
  return {
    utcMinute: d.getUTCMinutes(),
    utcHour: d.getUTCHours(),
    utcWeekday: d.getUTCDay(),
    utcMonthDay: d.getUTCDate(),
  }
}

function utcWallToLocal(utcHour: number, utcMinute: number, utcWeekday?: number, utcMonthDay?: number) {
  const d = new Date()
  if (utcMonthDay != null) {
    d.setUTCDate(Math.min(28, Math.max(1, utcMonthDay)))
  }
  if (utcWeekday != null) {
    const delta = (utcWeekday - d.getUTCDay() + 7) % 7
    d.setUTCDate(d.getUTCDate() + delta)
  }
  d.setUTCHours(utcHour, utcMinute, 0, 0)
  return {
    hour: d.getHours(),
    minute: d.getMinutes(),
    weekday: d.getDay(),
    monthDay: d.getDate(),
  }
}

function buildCron(m: Model): string {
  if (m.freq === 'custom') return m.custom.trim()
  if (m.freq === 'interval') return `*/${m.everyMin} * * * *`
  if (m.freq === 'hourly') return `${m.minute} * * * *`
  const utc = localWallToUtc(
    m.localHour,
    m.localMinute,
    m.freq === 'weekly' ? m.weekday : undefined,
    m.freq === 'monthly' ? m.monthDay : undefined,
  )
  if (m.freq === 'daily') return `${utc.utcMinute} ${utc.utcHour} * * *`
  if (m.freq === 'weekly') return `${utc.utcMinute} ${utc.utcHour} * * ${utc.utcWeekday}`
  return `${utc.utcMinute} ${utc.utcHour} ${utc.utcMonthDay} * *`
}

function parseCron(expr: string | undefined): Model {
  const fallback: Model = {
    freq: 'hourly',
    everyMin: 15,
    minute: 0,
    localHour: 9,
    localMinute: 0,
    weekday: 1,
    monthDay: 1,
    custom: expr?.trim() || '0 * * * *',
  }
  const raw = (expr || '').trim()
  if (!raw) return fallback
  const parts = raw.split(/\s+/)
  if (parts.length !== 5) return { ...fallback, freq: 'custom', custom: raw }

  const [mi, ho, dom, mon, dow] = parts
  if (mon !== '*') return { ...fallback, freq: 'custom', custom: raw }

  const starMin = mi.match(/^\*\/(\d+)$/)
  if (starMin && ho === '*' && dom === '*' && dow === '*') {
    const n = Number(starMin[1])
    if ([5, 10, 15, 30].includes(n)) {
      return { ...fallback, freq: 'interval', everyMin: n, custom: raw }
    }
  }

  if (/^\d+$/.test(mi) && ho === '*' && dom === '*' && dow === '*') {
    const minute = Math.min(59, Math.max(0, Number(mi)))
    return { ...fallback, freq: 'hourly', minute, custom: raw }
  }

  if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && dom === '*' && dow === '*') {
    const loc = utcWallToLocal(Number(ho), Number(mi))
    return { ...fallback, freq: 'daily', localHour: loc.hour, localMinute: loc.minute, custom: raw }
  }

  if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && dom === '*' && /^\d+$/.test(dow)) {
    const loc = utcWallToLocal(Number(ho), Number(mi), Number(dow))
    return {
      ...fallback,
      freq: 'weekly',
      localHour: loc.hour,
      localMinute: loc.minute,
      weekday: loc.weekday,
      custom: raw,
    }
  }

  if (/^\d+$/.test(mi) && /^\d+$/.test(ho) && /^\d+$/.test(dom) && dow === '*') {
    const loc = utcWallToLocal(Number(ho), Number(mi), undefined, Number(dom))
    return {
      ...fallback,
      freq: 'monthly',
      localHour: loc.hour,
      localMinute: loc.minute,
      monthDay: Math.min(28, loc.monthDay),
      custom: raw,
    }
  }

  return { ...fallback, freq: 'custom', custom: raw }
}

export function describeCronExpr(expr: string | undefined): string {
  const m = parseCron(expr)
  if (m.freq === 'interval') return `每 ${m.everyMin} 分钟`
  if (m.freq === 'hourly') return `每小时的第 ${m.minute} 分`
  const t = `${pad(m.localHour)}:${pad(m.localMinute)}`
  if (m.freq === 'daily') return `每天 ${t}`
  if (m.freq === 'weekly') {
    const w = WEEKDAYS.find((d) => d.value === m.weekday)?.label || '每周'
    return `${w} ${t}`
  }
  if (m.freq === 'monthly') return `每月 ${m.monthDay} 日 ${t}`
  return expr?.trim() || '—'
}

type Props = {
  value?: string
  onChange?: (cron: string) => void
}

export default function CronExprBuilder({ value, onChange }: Props) {
  const [model, setModel] = useState<Model>(() => parseCron(value))

  useEffect(() => {
    setModel(parseCron(value))
  }, [value])

  function commit(next: Model) {
    setModel(next)
    onChange?.(buildCron(next))
  }

  const cron = useMemo(() => buildCron(model), [model])
  const hourOptions = Array.from({ length: 24 }, (_, h) => ({ value: h, label: pad(h) }))
  const minuteOptions = Array.from({ length: 60 }, (_, m) => ({ value: m, label: pad(m) }))

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      <Space wrap>
        <PresetBtn
          active={model.freq === 'interval' && model.everyMin === 15}
          onClick={() => commit({ ...model, freq: 'interval', everyMin: 15 })}
        >
          每 15 分钟
        </PresetBtn>
        <PresetBtn
          active={model.freq === 'hourly' && model.minute === 0}
          onClick={() => commit({ ...model, freq: 'hourly', minute: 0 })}
        >
          每小时
        </PresetBtn>
        <PresetBtn
          active={model.freq === 'daily' && model.localHour === 9 && model.localMinute === 0}
          onClick={() => commit({ ...model, freq: 'daily', localHour: 9, localMinute: 0 })}
        >
          每天 09:00
        </PresetBtn>
        <PresetBtn
          active={model.freq === 'weekly' && model.weekday === 1 && model.localHour === 9 && model.localMinute === 0}
          onClick={() => commit({ ...model, freq: 'weekly', weekday: 1, localHour: 9, localMinute: 0 })}
        >
          每周一 09:00
        </PresetBtn>
      </Space>

      <Space wrap>
        <Select
          style={{ width: 140 }}
          value={model.freq}
          onChange={(freq) =>
            commit({
              ...model,
              freq,
              custom: freq === 'custom' && model.freq !== 'custom' ? buildCron(model) : model.custom,
            })
          }
          options={[
            { value: 'interval', label: '每隔几分钟' },
            { value: 'hourly', label: '每小时' },
            { value: 'daily', label: '每天' },
            { value: 'weekly', label: '每周' },
            { value: 'monthly', label: '每月' },
            { value: 'custom', label: '自定义 Cron' },
          ]}
        />
        {model.freq === 'interval' && (
          <Select
            style={{ width: 120 }}
            value={model.everyMin}
            onChange={(everyMin) => commit({ ...model, everyMin })}
            options={[5, 10, 15, 30].map((n) => ({ value: n, label: `每 ${n} 分钟` }))}
          />
        )}
        {model.freq === 'hourly' && (
          <Space>
            <Typography.Text type="secondary">第</Typography.Text>
            <InputNumber
              min={0}
              max={59}
              value={model.minute}
              onChange={(v) => commit({ ...model, minute: Number(v ?? 0) })}
            />
            <Typography.Text type="secondary">分</Typography.Text>
          </Space>
        )}
        {(model.freq === 'daily' || model.freq === 'weekly' || model.freq === 'monthly') && (
          <Space>
            <Select
              style={{ width: 80 }}
              value={model.localHour}
              options={hourOptions}
              onChange={(localHour) => commit({ ...model, localHour })}
            />
            <Typography.Text>:</Typography.Text>
            <Select
              style={{ width: 80 }}
              value={model.localMinute}
              options={minuteOptions}
              onChange={(localMinute) => commit({ ...model, localMinute })}
            />
          </Space>
        )}
        {model.freq === 'weekly' && (
          <Select
            style={{ width: 110 }}
            value={model.weekday}
            onChange={(weekday) => commit({ ...model, weekday })}
            options={WEEKDAYS}
          />
        )}
        {model.freq === 'monthly' && (
          <Space>
            <Typography.Text type="secondary">每月</Typography.Text>
            <InputNumber
              min={1}
              max={28}
              value={model.monthDay}
              onChange={(v) => commit({ ...model, monthDay: Number(v ?? 1) })}
            />
            <Typography.Text type="secondary">日</Typography.Text>
          </Space>
        )}
      </Space>

      {model.freq === 'custom' && (
        <Input
          value={model.custom}
          placeholder="分 时 日 月 周（UTC）"
          onChange={(e) => commit({ ...model, custom: e.target.value })}
        />
      )}

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {describeCronExpr(cron)} · 时区 {TZ} · 写入 Cron `{cron}`（按 UTC 调度，需 Worker + Beat）
      </Typography.Text>
    </Space>
  )
}

function PresetBtn({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Button type={active ? 'primary' : 'default'} size="small" onClick={onClick}>
      {children}
    </Button>
  )
}
