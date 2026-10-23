import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Activity, ClipboardList, HeartPulse, PhoneCall, Watch } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { useAuthStore } from '@/stores/auth'

interface Workbench {
  date: string
  instances: Record<string, number>
  outreach_today: Record<string, number>
  professional: { overdue: number; high_priority_open: number }
  phone: Record<string, number>
  device_sync_failures_24h: number
  pilot_releases_active: number
}

const INSTANCE_LABELS: Record<string, string> = {
  preparing: '准备中', active: '服务中', paused: '已暂停',
  expired: '已到期', closed: '已结案', terminated: '已终止',
}
const OUTREACH_LABELS: Record<string, string> = {
  pending_channel: '待通道配置', pending_manual: '待人工处理', pending_confirm: '待确认',
  resolved: '已办理', failed: '失败', cancelled: '已取消',
}
const PHONE_LABELS: Record<string, string> = {
  pending_review: '待审核', queued: '待拨打', pending_config: '待配置', in_progress: '执行中',
  no_answer: '未接通', busy: '忙线', wrong_number: '错号', refused: '对方拒绝',
  transferred_human: '人工跟进', completed: '已完成', failed: '失败', cancelled: '已停止',
}

function CountRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  )
}

export default function StewardWorkbench() {
  const admin = useAuthStore((s) => s.admin)
  const canRead = can(admin?.role, 'steward.read')
  const query = useQuery({
    queryKey: ['steward-workbench'],
    queryFn: async () => (await api.get<Workbench>('/admin/steward/workbench')).data,
    enabled: canRead,
    refetchInterval: 60_000,
  })

  if (!canRead) {
    return <p className="text-sm text-muted-foreground">无权限查看服务履约工作台。</p>
  }
  if (query.isLoading) return <Skeleton className="h-96 w-full" />
  if (query.isError) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-destructive">工作台加载失败。</p>
        <Button variant="outline" onClick={() => query.refetch()}>重试</Button>
      </div>
    )
  }
  const data = query.data!
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">履约今日工作台（{data.date}）</h1>
        <p className="text-sm text-muted-foreground">
          试点放行生效记录 {data.pilot_releases_active} 条；各明细见左侧菜单。
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><HeartPulse className="size-4" />服务实例</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {Object.keys(INSTANCE_LABELS).map((k) => <CountRow key={k} label={INSTANCE_LABELS[k]} value={data.instances[k] ?? 0} />)}
            <Link to="/mp/steward/instances" className="text-sm text-primary underline-offset-4 hover:underline">进入实例管理 →</Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><Activity className="size-4" />今日触达</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {Object.keys(OUTREACH_LABELS).map((k) => <CountRow key={k} label={OUTREACH_LABELS[k]} value={data.outreach_today[k] ?? 0} />)}
            <Link to="/mp/steward/outreach" className="text-sm text-primary underline-offset-4 hover:underline">进入触达待办 →</Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><ClipboardList className="size-4" />专业工单</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <CountRow label="超时未结" value={data.professional.overdue} />
            <CountRow label="高优先级未结（阻断结案）" value={data.professional.high_priority_open} />
            <Link to="/mp/steward/professional" className="text-sm text-primary underline-offset-4 hover:underline">进入专业工单 →</Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><PhoneCall className="size-4" />电话通路</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {Object.keys(PHONE_LABELS).map((k) => <CountRow key={k} label={PHONE_LABELS[k]} value={data.phone[k] ?? 0} />)}
            <Link to="/mp/steward/phone" className="text-sm text-primary underline-offset-4 hover:underline">进入电话工单 →</Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><Watch className="size-4" />设备同步</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <CountRow label="近 24h 失败/待配置" value={data.device_sync_failures_24h} />
            <Link to="/mp/steward/devices" className="text-sm text-primary underline-offset-4 hover:underline">进入设备管理 →</Link>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}