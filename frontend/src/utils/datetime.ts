const HAS_ZONE = /(?:Z|[+-]\d{2}:\d{2})$/i

/** Parse API datetime: naive = Beijing wall clock; zoned = absolute instant. */
export function parseApiDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const raw = value.trim()
  if (!raw) return null
  const iso = HAS_ZONE.test(raw) ? raw : `${raw}+08:00`
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatBeijing(value: string | null | undefined): string {
  const date = parseApiDate(value)
  if (!date) {
    const raw = value?.trim()
    return raw || '—'
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`
}
