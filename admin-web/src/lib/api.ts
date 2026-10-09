import axios, { AxiosError } from 'axios'
import { toast } from '@/components/common/toast'
import { useAuthStore } from '@/stores/auth'
import type { TokenPair } from '@/types'

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 响应拦截器内部标记：该请求已用新 token 重试过一次 */
    _retried?: boolean
  }
}

const baseURL: string = import.meta.env.VITE_API_BASE || '/api/v1'

export const api = axios.create({
  baseURL,
  timeout: 30000,
})

/** 换发专用实例：绕开 api 的拦截器，避免 401 死循环 */
const refreshClient = axios.create({ timeout: 15000 })

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

function isAuthPath(url: string | undefined): boolean {
  return typeof url === 'string' && (url.includes('/admin/auth/login') || url.includes('/auth/refresh'))
}

function redirectToLogin(): void {
  if (window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
}

/* 单例 promise：并发 401 只触发一次换发 */
let refreshing: Promise<string | null> | null = null

function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      const { refreshToken, updateTokens, logout } = useAuthStore.getState()
      if (!refreshToken) {
        logout()
        redirectToLogin()
        return null
      }
      try {
        const { data } = await refreshClient.post<TokenPair>(
          '/auth/refresh',
          { refresh_token: refreshToken },
          { baseURL },
        )
        updateTokens(data)
        return data.access_token
      } catch {
        logout()
        redirectToLogin()
        return null
      }
    })().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function errorMessage(error: AxiosError): string {
  const data: unknown = error.response?.data
  if (isRecord(data) && typeof data.detail === 'string') {
    return data.detail
  }
  if (!error.response) {
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
    return '网络异常，请检查网络连接'
  }
  const status = error.response.status
  if (status === 401) return '登录已过期，请重新登录'
  if (status === 403) return '没有操作权限'
  if (status === 404) return '请求的资源不存在'
  if (status >= 500) return '服务器异常，请稍后重试'
  return `请求失败（${status}）`
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config
    const status = error.response?.status

    /* 401：尝试用 refresh token 换发后重试一次（登录/刷新接口本身除外） */
    if (status === 401 && original && !original._retried && !isAuthPath(original.url)) {
      const token = await refreshAccessToken()
      if (token) {
        original._retried = true
        original.headers = original.headers ?? {}
        original.headers.Authorization = `Bearer ${token}`
        return api(original)
      }
    }

    /* 重试后仍 401：清理本地会话 */
    if (status === 401 && original?._retried) {
      useAuthStore.getState().logout()
      redirectToLogin()
    }

    toast.error(errorMessage(error))
    return Promise.reject(error)
  },
)
