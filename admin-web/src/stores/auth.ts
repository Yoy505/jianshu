import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Admin, AdminLoginResult, TokenPair } from '@/types'

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  admin: Admin | null
  setAuth: (payload: AdminLoginResult) => void
  updateTokens: (payload: TokenPair) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      admin: null,
      setAuth: ({ access_token, refresh_token, admin }) =>
        set({ accessToken: access_token, refreshToken: refresh_token, admin }),
      updateTokens: ({ access_token, refresh_token }) =>
        set({ accessToken: access_token, refreshToken: refresh_token }),
      logout: () => set({ accessToken: null, refreshToken: null, admin: null }),
    }),
    {
      name: 'jianshu-admin-auth',
    },
  ),
)
