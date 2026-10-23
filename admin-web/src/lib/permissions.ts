/**
 * 当前后台页面使用的权限点子集。
 * 前端仅用于 UI 显隐；真正的拦截在后端（403）。
 * 两级评审（方案 B，2026-10-08）：editor 参与形式初审，终审仅 admin。
 * 服务包交易（工作一，2026-10-21）：editor 可维护商品/查询订单/预览名单；
 * 退款、积分调整、批次确认发放仅 admin。
 */

export type Permission =
  | 'audit.read'
  | 'mp.manage'
  | 'occupational.read'
  | 'occupational.initial_review'
  | 'occupational.final_review'
  | 'volunteer.read'
  | 'commerce.product.manage'
  | 'commerce.order.read'
  | 'commerce.order.refund'
  | 'commerce.points.adjust'
  | 'commerce.batch.manage'
  | 'commerce.batch.confirm'
  | 'steward.read'
  | 'steward.manage'
  | 'outreach.read'
  | 'outreach.manage'
  | 'device.read'
  | 'device.manage'
  | 'phone.read'
  | 'phone.manage'

const EDITOR_PERMISSIONS: Permission[] = [
  'mp.manage',
  'steward.read',
  'outreach.read',
  'device.read',
  'phone.read',
  'occupational.read',
  'occupational.initial_review',
  'commerce.product.manage',
  'commerce.order.read',
  'commerce.batch.manage',
]

const ADMIN_PERMISSIONS: Permission[] = [
  'audit.read',
  'mp.manage',
  'occupational.read',
  'occupational.initial_review',
  'occupational.final_review',
  'volunteer.read',
  'commerce.product.manage',
  'commerce.order.read',
  'commerce.order.refund',
  'commerce.points.adjust',
  'commerce.batch.manage',
  'commerce.batch.confirm',
  'steward.read',
  'steward.manage',
  'outreach.read',
  'outreach.manage',
  'device.read',
  'device.manage',
  'phone.read',
  'phone.manage',
]

const ROLE_PERMISSIONS: Record<string, Permission[]> = {
  admin: ADMIN_PERMISSIONS,
  editor: EDITOR_PERMISSIONS,
}

export function can(role: string | undefined | null, permission: Permission): boolean {
  if (!role) return false
  return (ROLE_PERMISSIONS[role] ?? []).includes(permission)
}
