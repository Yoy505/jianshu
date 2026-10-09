import axios from 'axios';

export const API_BASE: string = import.meta.env.VITE_API_BASE || '/api/v1';

/** 提取后端返回的错误文案。 */
export function apiErrorDetail(error: unknown, fallback = '请求失败，请稍后重试'): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.length > 0) return detail;
    if (error.code === 'ECONNABORTED') return '请求超时，请检查网络后重试';
    if (!error.response) return '网络连接中断，请稍后重试';
  }
  return fallback;
}
