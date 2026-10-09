import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, CalendarCheck, FileText, MessageSquare, Pill, Stethoscope, Store } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useMpStats } from '@/hooks/useQueries'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { useAuthStore } from '@/stores/auth'
import type { MpAdminStats } from '@/types'

const APPOINTMENT_STATES = [
  { key: 'pending', label: '待确认', color: '#b88c45' },
  { key: 'confirmed', label: '已确认', color: '#397f6e' },
  { key: 'completed', label: '已完成', color: '#83aa95' },
  { key: 'cancelled', label: '已取消', color: '#aeb9b0' },
] as const

const WORK_LINKS = [
  { to: '/mp/appointments', title: '健康预约', detail: '查看预约与处理状态', icon: CalendarCheck },
  { to: '/mp/consults', title: '在线咨询', detail: '查看会话记录', icon: MessageSquare },
  { to: '/mp/providers', title: '专业人员', detail: '维护人员资料', icon: Stethoscope },
  { to: '/mp/drugs', title: '药品展厅', detail: '核对来源与发布状态', icon: Pill },
  { to: '/mp/services', title: '服务与商城', detail: '管理服务与公告、预览商品', icon: Store },
]

function StatValue({ value, loading }: { value: number | undefined; loading: boolean }) {
  if (loading) return <Skeleton className="mt-2 h-8 w-16" />
  return <span className="admin-kpi-value block">{value === undefined ? '—' : value.toLocaleString('zh-CN')}</span>
}

function AppointmentChart({ appointments }: { appointments: MpAdminStats['appointments'] }) {
  const total = appointments.total
  if (total === 0) {
    return (
      <div className="admin-state">
        <strong>暂无预约记录</strong>
        <span>有预约记录后，这里会按状态显示分布。</span>
      </div>
    )
  }
  return (
    <div className="space-y-5 py-2" role="img" aria-label={APPOINTMENT_STATES.map(({ key, label }) => `${label} ${appointments[key]} 条`).join('，')}>
      {APPOINTMENT_STATES.map(({ key, label, color }) => (
        <div key={key} className="grid grid-cols-[56px_minmax(0,1fr)_42px] items-center gap-3">
          <span className="text-xs text-muted-foreground">{label}</span>
          <div className="h-2.5 overflow-hidden rounded-sm bg-[#eef2ed]">
            <div className="h-full rounded-sm" style={{ width: `${(appointments[key] / total) * 100}%`, backgroundColor: color }} />
          </div>
          <span className="text-right text-xs font-semibold tabular-nums text-foreground">{appointments[key].toLocaleString('zh-CN')}</span>
        </div>
      ))}
    </div>
  )
}

export default function Dashboard() {
  const admin = useAuthStore((state) => state.admin)
  const healthAllowed = can(admin?.role, 'mp.manage')
  const occupationalAllowed = can(admin?.role, 'occupational.read')
  const statsQuery = useMpStats()
  const pendingSubmissionQuery = useQuery({
    queryKey: ['occupational-submissions', 'dashboard-pending-total'],
    queryFn: async () => (
      await api.get<{ total: number }>('/admin/occupational-submissions', {
        params: { status: 'submitted', page: 1, page_size: 1 },
      })
    ).data.total,
    enabled: occupationalAllowed,
  })
  const stats = statsQuery.data

  return (
    <div className="admin-page">
      <div className="admin-page-header">
        <h2>运营工作台</h2>
        <p>职业健康科普作品征集与杏临汇健康服务的日常管理入口。</p>
      </div>

      {occupationalAllowed && (
        <Card className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
          <div className="flex min-w-0 items-start gap-4">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-[#f8f2e6] text-[#926e35]">
              <FileText className="size-5" strokeWidth={2.2} aria-hidden="true" />
            </span>
            <div>
              <p className="text-base font-semibold text-foreground">职业健康科普作品征集</p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">查看作者投稿、下载材料并记录审核状态。</p>
              <p className="mt-2 text-xs text-muted-foreground">
                待审核投稿：
                <span className="ml-1 font-semibold tabular-nums text-foreground">
                  {pendingSubmissionQuery.isLoading ? '加载中' : pendingSubmissionQuery.isError ? '暂不可用' : (pendingSubmissionQuery.data ?? 0).toLocaleString('zh-CN')}
                </span>
              </p>
            </div>
          </div>
          <Button asChild>
            <Link to="/occupational-submissions">管理投稿<ArrowUpRight aria-hidden="true" /></Link>
          </Button>
        </Card>
      )}

      {healthAllowed && (
        <>
          <section aria-labelledby="health-overview-heading">
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h3 id="health-overview-heading" className="admin-section-title">健康服务概览</h3>
              <p className="text-xs text-muted-foreground">按当前数据库记录统计</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {[
                { label: '待确认预约', value: stats?.appointments.pending, note: '预约单状态 pending' },
                { label: '进行中咨询', value: stats?.consults_open, note: '开放中的会话记录' },
                { label: '专业人员条目', value: stats?.providers_total, note: '资料条目数，非资质核验数' },
                { label: '服务项目', value: stats?.services_total, note: '后台服务条目数' },
              ].map((item) => (
                <Card key={item.label} className="px-5 py-4">
                  <p className="text-xs font-medium text-muted-foreground">{item.label}</p>
                  <StatValue value={item.value} loading={statsQuery.isLoading} />
                  <p className="mt-2 text-[11px] leading-5 text-muted-foreground">{item.note}</p>
                </Card>
              ))}
            </div>
          </section>

          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
            <section className="admin-data-panel" aria-labelledby="appointment-chart-heading">
              <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-5">
                <div>
                  <h3 id="appointment-chart-heading" className="admin-section-title">预约状态分布</h3>
                  <p className="mt-1 text-xs text-muted-foreground">柱长表示各状态占全部预约的比例，无时间趋势推断。</p>
                </div>
                {stats && <span className="whitespace-nowrap text-xs text-muted-foreground">共 {stats.appointments.total.toLocaleString('zh-CN')} 条</span>}
              </div>
              <div className="px-6 py-5">
                {statsQuery.isLoading ? (
                  <div className="space-y-5 py-3">{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-3 w-full" />)}</div>
                ) : statsQuery.isError || !stats ? (
                  <div className="admin-state">
                    <strong>统计暂时无法加载</strong>
                    <span>请检查服务连接，或稍后重试。</span>
                    <Button variant="outline" size="sm" onClick={() => void statsQuery.refetch()}>重试</Button>
                  </div>
                ) : <AppointmentChart appointments={stats.appointments} />}
              </div>
            </section>

            <section className="admin-data-panel" aria-labelledby="quick-actions-heading">
              <div className="border-b border-border px-6 py-5">
                <h3 id="quick-actions-heading" className="admin-section-title">常用工作</h3>
                <p className="mt-1 text-xs text-muted-foreground">选择对应模块继续处理。</p>
              </div>
              <div className="divide-y divide-border">
                {WORK_LINKS.map(({ to, title, detail, icon: Icon }) => (
                  <Link key={to} to={to} className="flex items-center gap-3 px-6 py-3.5 transition-colors hover:bg-[#f8faf7] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary">
                    <Icon className="size-4 shrink-0 text-primary" strokeWidth={2.2} aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-foreground">{title}</span>
                      <span className="block truncate text-xs text-muted-foreground">{detail}</span>
                    </span>
                    <ArrowUpRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  </Link>
                ))}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}
