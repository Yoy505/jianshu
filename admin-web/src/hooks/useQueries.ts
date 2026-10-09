import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type {
  AdminLoginResult,
  AuditLog,
  LoginPayload,
  MpAdminStats,
  MpAppointment,
  MpAppointmentStatus,
  MpConsult,
  MpConsultDetail,
  MpConsultStatus,
  MpDrug,
  MpDrugDetail,
  MpDrugLeaflet,
  MpDrugLeafletStatus,
  MpDrugTag,
  MpNotice,
  MpProduct,
  MpProductCategory,
  MpProvider,
  MpProviderUpdate,
  MpService,
  MpServiceType,
  MpServiceUpdate,
  Page,
} from '@/types'

/* ---------- 通用：剔除空串/undefined 的查询参数 ---------- */

type ParamValue = string | number | boolean | undefined
type QueryParams = Record<string, ParamValue>

function cleanParams(params: QueryParams): QueryParams {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  )
}

/* ---------- 认证 ---------- */

export function useAdminLogin() {
  return useMutation({
    mutationFn: async (payload: LoginPayload) =>
      (await api.post<AdminLoginResult>('/admin/auth/login', payload)).data,
  })
}

/* ---------- 审计日志 ---------- */

export interface AuditLogFilters {
  action: string
  resource_type: string
  page: number
  page_size: number
}

export function useAuditLogs(filters: AuditLogFilters) {
  return useQuery({
    queryKey: ['audit-logs', filters],
    queryFn: async () =>
      (
        await api.get<Page<AuditLog>>('/admin/audit-logs', {
          params: cleanParams({
            action: filters.action,
            resource_type: filters.resource_type,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

/* ---------- 健康域 · 筛选条件 ---------- */

export interface MpAppointmentFilters {
  status: MpAppointmentStatus | ''
  /** 预约日 YYYY-MM-DD */
  date: string
  q: string
  page: number
  page_size: number
}

export interface MpConsultFilters {
  status: MpConsultStatus | ''
  q: string
  page: number
  page_size: number
}

export interface MpProviderFilters {
  q: string
  online: boolean | ''
  page: number
  page_size: number
}

export interface MpServiceFilters {
  scenario: string
  service_type: MpServiceType | ''
  page: number
  page_size: number
}

export interface MpDrugFilters {
  q: string
  tag: MpDrugTag | ''
  publication_status: MpDrug['publication_status'] | ''
  page: number
  page_size: number
}

export interface MpDrugLeafletFilters {
  status: MpDrugLeafletStatus | ''
  approval_no: string
  page: number
  page_size: number
}

export interface MpProductFilters {
  category: MpProductCategory | ''
  page: number
  page_size: number
}

/* ---------- 健康域 · 概览 ---------- */

export function useMpStats() {
  return useQuery({
    queryKey: ['mp-stats'],
    queryFn: async () => (await api.get<MpAdminStats>('/admin/mp/stats')).data,
  })
}

/* ---------- 健康域 · 预约管理 ---------- */

export function useMpAppointments(filters: MpAppointmentFilters) {
  return useQuery({
    queryKey: ['mp-appointments', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpAppointment>>('/admin/mp/appointments', {
          params: cleanParams({
            status: filters.status,
            date: filters.date,
            q: filters.q,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

export function useConfirmMpAppointment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      await api.post(`/admin/mp/appointments/${id}/confirm`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-appointments'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

export function useCompleteMpAppointment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      await api.post(`/admin/mp/appointments/${id}/complete`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-appointments'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

export function useCancelMpAppointment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      await api.post(`/admin/mp/appointments/${id}/cancel`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-appointments'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

/* ---------- 健康域 · 咨询管理 ---------- */

export function useMpConsults(filters: MpConsultFilters) {
  return useQuery({
    queryKey: ['mp-consults', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpConsult>>('/admin/mp/consults', {
          params: cleanParams({
            status: filters.status,
            q: filters.q,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

export function useMpConsultDetail(id: number | null) {
  return useQuery({
    queryKey: ['mp-consult', id],
    enabled: id !== null,
    queryFn: async () => (await api.get<MpConsultDetail>(`/admin/mp/consults/${id}`)).data,
  })
}

export function useCloseMpConsult() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      await api.post(`/admin/mp/consults/${id}/close`)
    },
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: ['mp-consults'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-consult', id] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

/* ---------- 健康域 · 专业人员管理 ---------- */

export function useMpProviders(filters: MpProviderFilters) {
  return useQuery({
    queryKey: ['mp-providers', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpProvider>>('/admin/mp/providers', {
          params: cleanParams({
            q: filters.q,
            online: filters.online,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

export function useUpdateMpProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: MpProviderUpdate }) =>
      (await api.patch<MpProvider>(`/admin/mp/providers/${id}`, payload)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-providers'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

/* ---------- 健康域 · 服务与商城管理 ---------- */

export function useMpServices(filters: MpServiceFilters) {
  return useQuery({
    queryKey: ['mp-services', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpService>>('/admin/mp/services', {
          params: cleanParams({
            scenario: filters.scenario,
            service_type: filters.service_type,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

export function useUpdateMpService() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, payload }: { id: number; payload: MpServiceUpdate }) =>
      (await api.patch<MpService>(`/admin/mp/services/${id}`, payload)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-services'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

export function useMpNotices() {
  return useQuery({
    queryKey: ['mp-notices'],
    queryFn: async () => (await api.get<MpNotice[]>('/admin/mp/notices')).data,
  })
}

export function useCreateMpNotice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: { content: string; sort?: number }) =>
      (await api.post<MpNotice>('/admin/mp/notices', payload)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-notices'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

export function useDeleteMpNotice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/admin/mp/notices/${id}`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['mp-notices'] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}

export function useMpProducts(filters: MpProductFilters) {
  return useQuery({
    queryKey: ['mp-products', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpProduct>>('/admin/mp/products', {
          params: cleanParams({
            category: filters.category,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

/* ---------- 健康域 · 药品展厅管理 ---------- */

export function useMpDrugs(filters: MpDrugFilters) {
  return useQuery({
    queryKey: ['mp-drugs', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpDrug>>('/admin/mp/drugs', {
          params: cleanParams({
            q: filters.q,
            tag: filters.tag,
            publication_status: filters.publication_status,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

export function useMpDrugDetail(id: number | null) {
  return useQuery({
    queryKey: ['mp-drug', id],
    enabled: id !== null,
    queryFn: async () => (await api.get<MpDrugDetail>(`/admin/mp/drugs/${id}`)).data,
  })
}

export function useMpDrugLeaflets(filters: MpDrugLeafletFilters) {
  return useQuery({
    queryKey: ['mp-drug-leaflets', filters],
    queryFn: async () =>
      (
        await api.get<Page<MpDrugLeaflet>>('/admin/mp/drug-leaflets', {
          params: cleanParams({
            status: filters.status,
            approval_no: filters.approval_no,
            page: filters.page,
            page_size: filters.page_size,
          }),
        })
      ).data,
  })
}

export function useMpDrugLeafletDetail(id: number | null) {
  return useQuery({
    queryKey: ['mp-drug-leaflet', id],
    enabled: id !== null,
    queryFn: async () => (await api.get<MpDrugLeaflet>(`/admin/mp/drug-leaflets/${id}`)).data,
  })
}

export function useDeleteDrugFeedback() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ feedbackId }: { drugId: number; feedbackId: number }) => {
      await api.delete(`/admin/mp/drugs/feedback/${feedbackId}`)
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['mp-drug', variables.drugId] })
      void queryClient.invalidateQueries({ queryKey: ['mp-stats'] })
    },
  })
}
