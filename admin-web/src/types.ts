/* 严格对齐 docs/api.md（v1）的类型定义 */

/* ---------- 认证 ---------- */

export type AdminRole = 'admin' | 'editor' | 'finance'

export interface Admin {
  id: number
  username: string
  name: string
  role: AdminRole
}

export interface LoginPayload {
  username: string
  password: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
}

export interface AdminLoginResult extends TokenPair {
  admin: Admin
}

/* ---------- 分页 ---------- */

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/* ---------- 审计日志 ---------- */

export type AuditActorType = 'admin' | 'user' | 'system'

export interface AuditLog {
  id: number
  actor_type: AuditActorType
  actor_id: number | null
  actor_name: string
  action: string
  resource_type: string
  resource_id: number | null
  detail: Record<string, unknown> | null
  ip_address: string | null
  created_at: string
}

/* ---------- 健康域（杏临汇 /admin/mp，见 docs/api.md 第三部分） ---------- */

export type MpServiceType = 'online' | 'offline' | 'third'
export type MpAppointmentStatus = 'pending' | 'confirmed' | 'completed' | 'cancelled'
export type MpConsultStatus = 'open' | 'closed'
export type MpDrugTag = 'otc' | 'rx' | 'essential' | 'insurance'
export type MpProductCategory = 'food' | 'fsmp' | 'package' | 'device'
export type MpProductStatus = 'coming'
export type MpGender = 'male' | 'female'

/** GET /admin/mp/stats */
export interface MpAdminStats {
  appointments: {
    pending: number
    confirmed: number
    completed: number
    cancelled: number
    total: number
  }
  consults_open: number
  providers_total: number
  providers_online: number
  services_total: number
  drugs_total: number
  products_total: number
}

/** 预约（列表项含 user/service/provider 摘要与联系人信息） */
export interface MpAppointmentUser {
  id: number
  nickname: string
  /** 后端已掩码，如 138****0001 */
  phone: string
}

export interface MpAppointmentService {
  id: number
  name: string
  service_type: MpServiceType
  /** 单位为分 */
  price: number
  unit: string
}

export interface MpAppointmentProvider {
  id: number
  name: string
  title: string
  avatar: string | null
}

export interface MpAppointment {
  id: number
  user: MpAppointmentUser
  service: MpAppointmentService
  provider: MpAppointmentProvider
  /** 预约日 YYYY-MM-DD */
  appt_date: string
  /** 上午 / 下午 / 晚间 */
  period: string
  address: string | null
  contact_name: string
  /** 完整手机号（供线下服务联络） */
  contact_phone: string
  remark: string | null
  status: MpAppointmentStatus
  created_at: string
}

/** 在线咨询会话（列表项） */
export interface MpConsultUser {
  id: number
  nickname: string
}

export interface MpConsultService {
  id: number
  name: string
  service_type: MpServiceType
}

export interface MpConsultProvider {
  id: number
  name: string
  title: string
  avatar: string | null
  online: boolean
}

export interface MpConsult {
  id: number
  user: MpConsultUser
  service: MpConsultService
  provider: MpConsultProvider
  status: MpConsultStatus
  message_count: number
  last_message: string | null
  last_message_at: string | null
  created_at: string
}

export interface MpConsultMessage {
  id: number
  sender: 'user' | 'provider' | 'system'
  content: string
  created_at: string
}

/** 会话详情（含最近 100 条消息，正序） */
export interface MpConsultDetail extends MpConsult {
  messages: MpConsultMessage[]
}

/** 专业人员（复用 C 端字段） */
export interface MpProvider {
  id: number
  name: string
  gender: MpGender
  title: string
  dept: string
  org: string
  years: number
  skills: string[]
  intro: string
  avatar: string | null
  rating: number
  service_count: number
  online: boolean
}

/** PATCH /admin/mp/providers/{id}（评分/服务次数只读） */
export interface MpProviderUpdate {
  name?: string
  gender?: MpGender
  title?: string
  dept?: string
  org?: string
  years?: number
  skills?: string[]
  intro?: string
  avatar?: string
  online?: boolean
}

/** 服务（price 单位为分） */
export interface MpService {
  id: number
  scenario: string
  scenario_name: string
  service_type: MpServiceType
  name: string
  desc: string
  price: number
  unit: string
  duration_min: number
  sales: number
  provider_count: number
  on_shelf: boolean
}

/** PATCH /admin/mp/services/{id}（price 单位为分） */
export interface MpServiceUpdate {
  name?: string
  desc?: string
  price?: number
  unit?: string
  duration_min?: number
  on_shelf?: boolean
}

/** 首页公告 */
export interface MpNotice {
  id: number
  content: string
  sort: number
  created_at: string
}

/** 药品（列表项） */
export interface MpDrug {
  id: number
  common_name: string
  approval_no: string | null
  specification: string | null
  license_holder: string | null
  source_name: string | null
  source_url: string | null
  source_checked_at: string | null
  publication_status: 'demo' | 'pending' | 'published' | 'offline'
  form: string
  manufacturer: string
  tags: MpDrugTag[]
  indication: string
  usage_note: string
  contraindication: string
  science: string
  video_url: string | null
  brand_name: string | null
}

export interface MpDrugFeedback {
  id: number
  /** 用户掩码昵称 */
  user_name: string
  content: string
  created_at: string
}

/** 药品详情（含完整留言板） */
export interface MpDrugDetail extends MpDrug {
  board: MpDrugFeedback[]
}

export type MpDrugLeafletStatus = 'pending' | 'approved' | 'published' | 'withdrawn'

/** 私有说明书的可审核元数据；不包含原件路径、文件名或下载地址。 */
export interface MpDrugLeaflet {
  id: number
  drug_id: number
  drug_common_name: string
  approval_no: string
  source_name: string
  source_document_id: string
  authorization_ref: string
  identity_check_method: string | null
  document_date: string | null
  document_version: string | null
  source_checked_at: string
  file_size: number
  file_sha256: string
  manifest_sha256: string
  status: MpDrugLeafletStatus
  created_at: string
  updated_at: string
}

/** 商城商品（v1 只读，status 恒为 coming） */
export interface MpProduct {
  id: number
  category: MpProductCategory
  name: string
  desc: string
  /** 单位为分 */
  price: number
  status: MpProductStatus
}
