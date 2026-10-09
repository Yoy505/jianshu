import { Badge, type BadgeVariant } from '@/components/ui/badge'

/* 杏临汇健康服务状态 → 中文标签 */
const STATUS_LABELS: Record<string, string> = {
  closed: '已关闭',
  pending: '待确认',
  confirmed: '已确认',
  completed: '已完成',
  cancelled: '已取消',
  open: '进行中',
  coming: '即将开放',
}

/* 健康域 pending amber / confirmed blue / completed green / cancelled zinc */
const STATUS_VARIANTS: Record<string, BadgeVariant> = {
  closed: 'secondary',
  pending: 'warning',
  confirmed: 'info',
  completed: 'success',
  cancelled: 'secondary',
  open: 'success',
  coming: 'warning',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS_VARIANTS[status] ?? 'secondary'}>{statusLabel(status)}</Badge>
}

/* ---------- 健康域：服务类型标签（线上=蓝 / 上门=绿 / 三方=amber） ---------- */

const SERVICE_TYPE_META: Record<string, { label: string; variant: BadgeVariant }> = {
  online: { label: '线上', variant: 'info' },
  offline: { label: '上门', variant: 'success' },
  third: { label: '三方', variant: 'warning' },
}

export function MpServiceTypeBadge({ serviceType }: { serviceType: string }) {
  const meta = SERVICE_TYPE_META[serviceType]
  if (!meta) return <Badge variant="secondary">{serviceType}</Badge>
  return <Badge variant={meta.variant}>{meta.label}</Badge>
}
