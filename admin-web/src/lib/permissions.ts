/**
 * 当前后台页面使用的权限点子集。
 * 前端仅用于 UI 显隐；真正的拦截在后端（403）。
 * 两级评审（方案 B，2026-10-08）：editor 参与形式初审，终审仅 admin。
 */

export type Permission =
  | 'audit.read'
  | 'mp.manage'
  | 'occupational.read'
  | 'occupational.initial_review'
  | 'occupational.final_review'

const EDITOR_PERMISSIONS: Permission[] = [
  'mp.manage',
  'occupational.read',
  'occupational.initial_review',
]

const ADMIN_PERMISSIONS: Permission[] = [
  'audit.read',
  'mp.manage',
  'occupational.read',
  'occupational.initial_review',
  'occupational.final_review',
]

const ROLE_PERMISSIONS: Record<string, Permission[]> = {
  admin: ADMIN_PERMISSIONS,
  editor: EDITOR_PERMISSIONS,
}

export function can(role: string | undefined | null, permission: Permission): boolean {
  if (!role) return false
  return (ROLE_PERMISSIONS[role] ?? []).includes(permission)
}
