import {
  CalendarCheck,
  ChevronLeft,
  FileText,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Pill,
  ScrollText,
  Stethoscope,
  Store,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { toast } from '@/components/common/toast'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { can } from '@/lib/permissions'
import type { Permission } from '@/lib/permissions'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

type NavItem = { to: string; label: string; icon: LucideIcon; permission?: Permission }
type NavGroup = { label: string; items: NavItem[] }

const NAV_GROUPS: NavGroup[] = [
  {
    label: '工作区',
    items: [
      { to: '/', label: '运营工作台', icon: LayoutDashboard },
      { to: '/occupational-submissions', label: '职业健康投稿', icon: FileText, permission: 'occupational.read' },
    ],
  },
  {
    label: '健康服务',
    items: [
      { to: '/mp/appointments', label: '健康预约', icon: CalendarCheck, permission: 'mp.manage' },
      { to: '/mp/consults', label: '在线咨询', icon: MessageSquare, permission: 'mp.manage' },
      { to: '/mp/providers', label: '专业人员', icon: Stethoscope, permission: 'mp.manage' },
      { to: '/mp/drugs', label: '药品展厅', icon: Pill, permission: 'mp.manage' },
      { to: '/mp/services', label: '服务与商城', icon: Store, permission: 'mp.manage' },
    ],
  },
  {
    label: '系统',
    items: [
      { to: '/audit-logs', label: '审计日志', icon: ScrollText, permission: 'audit.read' },
    ],
  },
]

function Navigation({ role, collapsed, onNavigate }: {
  role: string | undefined
  collapsed: boolean
  onNavigate?: () => void
}) {
  return (
    <nav className="space-y-7" aria-label="后台主导航">
      {NAV_GROUPS.map((group: NavGroup) => {
        const items = group.items.filter(
          (item) => !item.permission || can(role, item.permission),
        )
        if (!items.length) return null
        return (
          <div key={group.label}>
            {!collapsed && <p className="mb-2 px-3 text-[11px] font-semibold tracking-[0.12em] text-[#89978f]">{group.label}</p>}
            <div className="space-y-1">
              {items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  onClick={onNavigate}
                  title={item.label}
                  aria-label={collapsed ? item.label : undefined}
                  className={({ isActive }) => cn(
                    'flex min-h-10 items-center gap-3 rounded-lg px-3 text-[13px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#8cc0aa]',
                    collapsed && 'justify-center px-0',
                    isActive
                      ? 'bg-[#e8f0e9] text-[#205949]'
                      : 'text-[#53645a] hover:bg-[#edf2ed] hover:text-[#263b30]',
                  )}
                >
                  <item.icon className="size-[17px] shrink-0" strokeWidth={2.2} aria-hidden="true" />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </NavLink>
              ))}
            </div>
          </div>
        )
      })}
    </nav>
  )
}

export function AdminLayout() {
  const admin = useAuthStore((s) => s.admin)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  const currentItem = NAV_GROUPS.flatMap((group) => group.items)
    .find((item) => item.to === location.pathname)
  const pageTitle = currentItem?.label ?? '运营工作台'

  function handleLogout() {
    logout()
    toast.info('已退出登录')
    setMobileOpen(false)
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-dvh min-h-0 bg-background">
      <aside
        className={cn(
          'hidden shrink-0 flex-col border-r border-border bg-[#f9fbf8] transition-[width] duration-200 md:flex',
          collapsed ? 'w-[76px]' : 'w-[224px]',
        )}
      >
        <div className={cn('flex h-[76px] items-center border-b border-border px-5', collapsed && 'justify-center px-2')}>
          <NavLink to="/" className="flex items-center gap-3" aria-label="杏临汇运营后台首页">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[#244f42] text-lg font-semibold text-white">杏</span>
            {!collapsed && (
              <span className="min-w-0">
                <span className="block whitespace-nowrap text-sm font-semibold tracking-[0.04em] text-[#263b30]">杏临汇</span>
                <span className="block whitespace-nowrap text-[11px] text-[#718077]">运营管理平台</span>
              </span>
            )}
          </NavLink>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-6">
          <Navigation role={admin?.role} collapsed={collapsed} />
        </div>
        <div className="border-t border-border px-3 py-3">
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            className={cn(
              'flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-sm text-[#64756b] hover:bg-[#edf2ed] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              collapsed && 'justify-center px-0',
            )}
            aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
          >
            <ChevronLeft className={cn('size-[17px] transition-transform', collapsed && 'rotate-180')} strokeWidth={2.2} />
            {!collapsed && <span>收起导航</span>}
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[76px] shrink-0 items-center justify-between gap-3 border-b border-border bg-white px-4 sm:px-7">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="icon" className="md:hidden" aria-label="打开导航菜单" onClick={() => setMobileOpen(true)}>
              <Menu />
            </Button>
            <div className="min-w-0">
              <p className="text-[11px] font-medium tracking-[0.08em] text-muted-foreground">杏临汇 / 运营管理</p>
              <p className="truncate text-sm font-semibold text-foreground">{pageTitle}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-[#f3f6f2] text-sm font-semibold text-[#33574b]" aria-hidden="true">
              {(admin?.name || '管').slice(0, 1)}
            </div>
            <div className="hidden min-w-0 sm:block">
              <p className="max-w-36 truncate text-xs font-medium text-foreground">{admin?.name ?? '管理员'}</p>
              <p className="text-[11px] text-muted-foreground">{admin?.role === 'admin' ? '管理员' : '编辑'}</p>
            </div>
            <span className="mx-1 hidden h-5 w-px bg-border sm:block" />
            <Button variant="ghost" size="sm" onClick={handleLogout} aria-label="退出登录">
              <LogOut />
              <span className="hidden sm:inline">退出</span>
            </Button>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-7 sm:py-8">
          <Outlet />
        </main>
      </div>

      <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogContent className="w-[calc(100vw-32px)] max-w-sm p-0">
          <DialogHeader className="border-b border-border px-5 pb-4 pt-5">
            <DialogTitle>杏临汇运营管理</DialogTitle>
            <DialogDescription>选择要进入的工作区</DialogDescription>
          </DialogHeader>
          <div className="max-h-[65vh] overflow-y-auto px-4 py-5">
            <Navigation role={admin?.role} collapsed={false} onNavigate={() => setMobileOpen(false)} />
          </div>
          <div className="border-t border-border px-5 py-3">
            <Button variant="ghost" className="w-full justify-start" onClick={handleLogout}><LogOut />退出登录</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
