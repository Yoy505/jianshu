import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

const pad2 = (n: number): string => String(n).padStart(2, '0')

/** ISO 8601 UTC → 本地 "YYYY-MM-DD HH:mm"，空值显示 "—" */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** ISO 8601 UTC → 本地 "YYYY-MM-DD" */
export function formatDate(iso: string | null | undefined): string {
  const full = formatDateTime(iso)
  return full === '—' ? full : full.slice(0, 10)
}

/** ISO 8601 UTC → input[type="datetime-local"] 所需的 "YYYY-MM-DDTHH:mm" */
export function isoToDateTimeLocal(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** input[type="datetime-local"] 的值 → ISO 8601 UTC 字符串 */
export function dateTimeLocalToIso(value: string): string {
  return new Date(value).toISOString()
}

/** 分 → "¥xx.xx"（API 价格单位为分），空值显示 "—" */
export function fenToYuan(fen: number | null | undefined): string {
  if (fen === null || fen === undefined || Number.isNaN(fen)) return '—'
  return `¥${(fen / 100).toFixed(2)}`
}
