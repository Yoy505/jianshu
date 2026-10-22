import { Badge, type BadgeVariant } from '@/components/ui/badge'

/* 杏临汇健康服务状态 → 中文标签 */
const STATUS_LABELS: Record<string, string> = {
  /* 服务包交易域（工作一） */
  draft: '草稿',
  on_shelf: '上架中',
  off_shelf: '已下架',
  pending_payment: '待支付',
  paid: '已支付',
  refund_pending: '退款处理中',
  refund_failed: '退款失败',
  refunded: '已退款',
  processing: '处理中',
  success: '成功',
  failed: '失败',
  preview: '待确认',
  confirmed: '已确认发放',
  active: '已生效',
  rejected: '已拒绝',
  void: '已作废',
  valid: '有效',
  duplicate: '重复',
  format_error: '格式错误',
  over_limit: '超量',
  pending: '待确认',
  completed: '已完成',
  cancelled: '已取消',
  open: '进行中',
  coming: '即将开放',
}

/* 健康域 pending amber / confirmed blue / completed green / cancelled zinc */
const STATUS_VARIANTS: Record<string, BadgeVariant> = {
  draft: 'secondary',
  on_shelf: 'success',
  off_shelf: 'warning',
  pending_payment: 'warning',
  paid: 'success',
  refund_pending: 'info',
  refund_failed: 'warning',
  refunded: 'secondary',
  processing: 'info',
  success: 'success',
  failed: 'warning',
  preview: 'warning',
  active: 'success',
  rejected: 'secondary',
  void: 'secondary',
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
