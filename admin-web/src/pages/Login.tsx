import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { toast } from '@/components/common/toast'
import { IcpLink } from '@/components/common/IcpLink'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAdminLogin } from '@/hooks/useQueries'
import { useAuthStore } from '@/stores/auth'

export default function Login() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const setAuth = useAuthStore((s) => s.setAuth)
  const navigate = useNavigate()
  const loginMutation = useAdminLogin()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  if (accessToken) {
    return <Navigate to="/" replace />
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!username.trim() || !password) {
      toast.error('请输入用户名与密码')
      return
    }
    loginMutation.mutate(
      { username: username.trim(), password },
      {
        onSuccess: (data) => {
          setAuth(data)
          toast.success(`欢迎回来，${data.admin.name}`)
          navigate('/', { replace: true })
        },
      },
    )
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-[#f6f7f5] px-5 py-10 sm:px-8">
      <div className="grid w-full max-w-[980px] gap-10 lg:grid-cols-[minmax(0,1fr)_430px] lg:items-center lg:gap-20">
        <div className="max-w-lg">
          <img className="size-12 rounded-full object-cover" src="/brand/xinglinhui-mark.png" alt="杏临汇标识" width="48" height="48" />
          <p className="mt-10 text-xs font-semibold tracking-[0.16em] text-primary">XING LIN HUI · OPERATIONS</p>
          <h1 className="mt-4 text-[32px] font-semibold leading-tight tracking-[-0.04em] text-[#1d332a] sm:text-[42px]">
            杏临汇运营管理平台
          </h1>
          <p className="mt-5 max-w-md text-sm leading-7 text-muted-foreground">
            统一管理职业健康科普作品征集与健康服务相关资料、记录和运营流程。
          </p>
          <div className="mt-10 hidden h-px max-w-sm bg-border lg:block" aria-hidden="true" />
          <p className="mt-5 hidden text-xs text-muted-foreground lg:block">仅供已授权的运营人员使用</p>
        </div>

        <section className="w-full rounded-xl border border-border bg-white p-6 sm:p-9" aria-labelledby="login-heading">
          <p className="text-xs font-semibold tracking-[0.12em] text-primary">账号登录</p>
          <h2 id="login-heading" className="mt-2 text-[23px] font-semibold tracking-[-0.025em] text-foreground">欢迎回来</h2>
          <p className="mt-1 text-sm text-muted-foreground">请使用已开通的后台账号登录。</p>
          <form onSubmit={handleSubmit} className="mt-8 space-y-5">
            <div>
              <label htmlFor="username" className="mb-2 block text-sm font-medium text-foreground">用户名</label>
              <Input
                id="username"
                autoComplete="username"
                placeholder="请输入用户名"
                className="h-11"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="password" className="mb-2 block text-sm font-medium text-foreground">密码</label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="请输入密码"
                className="h-11"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            <Button type="submit" className="mt-2 h-11 w-full" disabled={loginMutation.isPending}>
              {loginMutation.isPending ? <Loader2 className="animate-spin" /> : null}
              登录
            </Button>
          </form>
          <div className="mt-8 flex flex-wrap justify-between gap-2 border-t border-border pt-5 text-xs text-muted-foreground">
            <span>健树征文服务平台 · 运营后台</span>
            <IcpLink className="underline underline-offset-2 hover:text-primary" />
          </div>
        </section>
      </div>
    </div>
  )
}
