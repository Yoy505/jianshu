import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Search } from 'lucide-react'
import { toast } from '@/components/common/toast'
import { AsyncList } from '@/components/common/AsyncList'
import { PagePagination } from '@/components/common/PagePagination'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

interface Page<T> { items: T[]; total: number; page: number; page_size: number }
interface StaffRef { id: number; name: string; role: string; phone_masked: string | null }
interface PrepCheck { check_type: string; title: string; source: string; status: string; missing_reason: string | null; evidence: string | null }
interface TaskRow { id: number; service_day: number; task_type: string; title: string; status: string; result: string | null }
interface Pause { id: number; cause: string; started_at: string; ended_at: string | null; compensated_seconds: number | null; note: string | null }
interface Timeline { id: number; event_type: string; actor_name: string; created_at: string; detail: Record<string, unknown> | null }
interface Instance {
  id: number; instance_no: string; package_name: string; status: string
  activated_at: string | null; ends_at: string | null; compensated_seconds: number
  current_day: number | null; plan_version: string | null
  butler: StaffRef | null; backup_butler: StaffRef | null; physician: StaffRef | null
  user_nickname: string | null; user_phone_masked: string | null
  termination_reason: string | null; pilot_release_ready: boolean | null
  pilot_release_reason: string | null
}
interface InstanceDetail extends Instance {
  prep_checks: PrepCheck[]; today_tasks: TaskRow[]; pauses: Pause[]
}
interface StaffRow { id: number; name: string; role: string; status: string }

const STATUS_LABELS: Record<string, string> = {
  preparing: '准备中', active: '服务中', paused: '已暂停', expired: '已到期',
  closed: '已结案', terminated: '已终止',
}
const CHECK_LABELS: Record<string, string> = {
  identity_phone: '身份核验', entitlement_valid: '权益有效', delivery: '收货',
  device_paired: '设备配对', device_vendor_consent: '厂商授权', platform_consent: '平台授权',
  first_valid_data: '首条有效数据', physician_plan: '签发计划', staffing_ready: '排班就绪',
}
const EVENT_LABELS: Record<string, string> = {
  instance_created: '实例创建', activated: '已激活', paused: '已暂停', resumed: '已恢复',
  compensated: '补时', expired: '已到期', closed: '已结案', terminated: '已终止',
  prep_check_passed: '门禁通过', prep_check_reset: '门禁回退', staff_assigned: '人员指派',
  task_completed: '任务完成', consent_revoked: '授权撤回', care_plan_bound: '计划绑定',
}

export default function StewardInstances() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canRead = can(admin?.role, 'steward.read')
  const canManage = can(admin?.role, 'steward.manage')
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [keyword, setKeyword] = useState('')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [action, setAction] = useState<'pause' | 'resume' | 'close' | null>(null)
  const [actionText, setActionText] = useState('')
  const pageSize = 20

  const listQuery = useQuery({
    queryKey: ['steward-instances', page, status, keyword],
    queryFn: async () =>
      (await api.get<Page<Instance>>('/admin/steward/instances', {
        params: { page, page_size: pageSize, status: status || undefined, q: keyword || undefined },
      })).data,
    enabled: canRead,
  })
  const detailQuery = useQuery({
    queryKey: ['steward-instance', selectedId],
    queryFn: async () => (await api.get<InstanceDetail>(`/admin/steward/instances/${selectedId}`)).data,
    enabled: canRead && selectedId !== null,
  })
  const timelineQuery = useQuery({
    queryKey: ['steward-timeline', selectedId],
    queryFn: async () => (await api.get<Timeline[]>(`/admin/steward/instances/${selectedId}/timeline`)).data,
    enabled: canRead && selectedId !== null,
  })
  const staffQuery = useQuery({
    queryKey: ['steward-staff'],
    queryFn: async () => (await api.get<Page<StaffRow>>('/admin/steward/staff', { params: { page_size: 100 } })).data,
    enabled: canRead && selectedId !== null,
  })

  const act = useMutation({
    mutationFn: async ({ id, kind, text }: { id: number; kind: 'pause' | 'resume' | 'close'; text: string }) => {
      const body = kind === 'pause'
        ? { cause: 'provider_interruption', reason: text }
        : kind === 'resume' ? { note: text } : { closure_report: text }
      return (await api.post(`/admin/steward/instances/${id}/${kind}`, body)).data
    },
    onSuccess: async () => {
      toast.success('操作成功')
      setAction(null); setActionText('')
      await queryClient.invalidateQueries({ queryKey: ['steward-instance', selectedId] })
      await queryClient.invalidateQueries({ queryKey: ['steward-instances'] })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : '操作失败')
    },
  })
  const assign = useMutation({
    mutationFn: async (payload: Record<string, number>) =>
      (await api.post(`/admin/steward/instances/${selectedId}/assign`, payload)).data,
    onSuccess: async () => {
      toast.success('指派已更新')
      await queryClient.invalidateQueries({ queryKey: ['steward-instance', selectedId] })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(typeof detail === 'string' ? detail : '指派失败')
    },
  })

  if (!canRead) return <p className="text-sm text-muted-foreground">无权限查看服务履约。</p>
  const detail = detailQuery.data
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">服务实例</h1>
      <div className="flex flex-wrap items-center gap-2">
        <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="实例号 / 姓名 / 手机号"
          className="w-64" onKeyDown={(e) => { if (e.key === 'Enter') { setKeyword(search); setPage(1) } }} />
        <Button variant="outline" size="sm" onClick={() => { setKeyword(search); setPage(1) }}>
          <Search className="size-4" />查询
        </Button>
        <select className="h-9 rounded-md border bg-background px-2 text-sm" value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <Button variant="ghost" size="sm" onClick={() => queryClient.invalidateQueries({ queryKey: ['steward-instances'] })}>
          <RefreshCw className="size-4" />
        </Button>
      </div>

      <AsyncList query={listQuery}>
        {(data) => (
          <>
            <Table>
              <TableHeader><TableRow>
                <TableHead>实例号</TableHead><TableHead>用户</TableHead><TableHead>服务包</TableHead>
                <TableHead>状态</TableHead><TableHead>当前服务日</TableHead><TableHead>管家</TableHead><TableHead>结束时间</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {data.items.map((it) => (
                  <TableRow key={it.id} className="cursor-pointer" onClick={() => setSelectedId(it.id)}>
                    <TableCell className="font-mono text-xs">{it.instance_no}</TableCell>
                    <TableCell>{it.user_nickname || '-'}<span className="ml-1 text-xs text-muted-foreground">{it.user_phone_masked}</span></TableCell>
                    <TableCell>{it.package_name}</TableCell>
                    <TableCell><StatusBadge status={it.status} label={STATUS_LABELS[it.status]} /></TableCell>
                    <TableCell>{it.current_day ?? '-'}</TableCell>
                    <TableCell>{it.butler?.name || '-'}</TableCell>
                    <TableCell className="text-xs">{it.ends_at ? formatDateTime(it.ends_at) : '-'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PagePagination page={data.page} pageSize={data.page_size} total={data.total}
              onChange={setPage} />
          </>
        )}
      </AsyncList>

      <Dialog open={selectedId !== null} onOpenChange={(o) => !o && setSelectedId(null)}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          {detail ? (
            <div className="space-y-4">
              <DialogHeader>
                <DialogTitle>{detail.instance_no} · {STATUS_LABELS[detail.status]}</DialogTitle>
              </DialogHeader>
              <div className="grid gap-1 text-sm">
                <div>用户：{detail.user_nickname}（{detail.user_phone_masked}） · 服务包：{detail.package_name}</div>
                <div>管家：{detail.butler?.name || '未指派'} / 替补：{detail.backup_butler?.name || '未指派'} / 医师：{detail.physician?.name || '未指派'}</div>
                <div>激活：{detail.activated_at ? formatDateTime(detail.activated_at) : '-'} · 结束：{detail.ends_at ? formatDateTime(detail.ends_at) : '-'} · 累计补时 {Math.round(detail.compensated_seconds / 60)} 分钟</div>
                {detail.termination_reason && <div className="text-destructive">终止原因：{detail.termination_reason}</div>}
                {detail.pilot_release_ready === false && <div className="text-amber-600">{detail.pilot_release_reason}</div>}
              </div>

              {detail.prep_checks.length > 0 && (
                <section className="space-y-1">
                  <h3 className="text-sm font-semibold">准备门禁</h3>
                  {detail.prep_checks.map((c) => (
                    <div key={c.check_type} className="flex items-center justify-between text-sm">
                      <span>{CHECK_LABELS[c.check_type] || c.check_type}</span>
                      <span className={c.status === 'passed' ? 'text-emerald-600' : 'text-amber-600'}>
                        {c.status === 'passed' ? '通过' : (c.missing_reason || '未通过')}
                      </span>
                    </div>
                  ))}
                </section>
              )}

              {canManage && detail.status === 'preparing' && staffQuery.data && (
                <section className="flex flex-wrap items-end gap-2">
                  {(['butler_id', 'backup_butler_id', 'physician_id'] as const).map((field) => (
                    <div key={field} className="space-y-1">
                      <Label className="text-xs">{field === 'butler_id' ? '负责管家' : field === 'backup_butler_id' ? '替补管家' : '签约医师'}</Label>
                      <select className="h-9 rounded-md border bg-background px-2 text-sm"
                        value={field === 'butler_id' ? detail.butler?.id : field === 'backup_butler_id' ? detail.backup_butler?.id : detail.physician?.id || ''}
                        onChange={(e) => assign.mutate({ [field]: Number(e.target.value) })}>
                        <option value="">未指派</option>
                        {staffQuery.data.items
                          .filter((s) => (field === 'physician_id' ? s.role === 'physician' : s.role === 'butler') && s.status === 'active')
                          .map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </select>
                    </div>
                  ))}
                </section>
              )}

              {detail.pauses.length > 0 && (
                <section className="space-y-1 text-sm">
                  <h3 className="font-semibold">暂停史</h3>
                  {detail.pauses.map((p) => (
                    <div key={p.id} className="text-muted-foreground">
                      {formatDateTime(p.started_at)} 起（{p.cause}）{p.ended_at ? `，${formatDateTime(p.ended_at)} 恢复，补时 ${Math.round((p.compensated_seconds ?? 0) / 60)} 分钟` : '，进行中'}
                    </div>
                  ))}
                </section>
              )}

              {timelineQuery.data && (
                <section className="space-y-1 text-sm">
                  <h3 className="font-semibold">服务时间线（最近）</h3>
                  {timelineQuery.data.slice(0, 10).map((ev) => (
                    <div key={ev.id} className="text-muted-foreground">
                      {formatDateTime(ev.created_at)} · {EVENT_LABELS[ev.event_type] || ev.event_type} · {ev.actor_name}
                    </div>
                  ))}
                </section>
              )}

              {canManage && (detail.status === 'active' || detail.status === 'paused' || detail.status === 'expired') && (
                <section className="flex flex-wrap gap-2">
                  {detail.status === 'active' && <Button size="sm" variant="outline" onClick={() => setAction('pause')}>暂停</Button>}
                  {detail.status === 'paused' && <Button size="sm" variant="outline" onClick={() => setAction('resume')}>恢复</Button>}
                  {detail.status === 'expired' && <Button size="sm" onClick={() => setAction('close')}>结案</Button>}
                </section>
              )}

              {action && (
                <div className="space-y-2 rounded-md border p-3">
                  <Label>{action === 'pause' ? '暂停原因' : action === 'resume' ? '恢复说明' : '结案报告'}</Label>
                  <Textarea value={actionText} onChange={(e) => setActionText(e.target.value)} rows={3} />
                  <div className="flex gap-2">
                    <Button size="sm" disabled={!actionText.trim() || act.isPending}
                      onClick={() => selectedId && act.mutate({ id: selectedId, kind: action, text: actionText })}>
                      确认{action === 'pause' ? '暂停' : action === 'resume' ? '恢复' : '结案'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => { setAction(null); setActionText('') }}>取消</Button>
                  </div>
                </div>
              )}
            </div>
          ) : <p className="text-sm text-muted-foreground">加载中……</p>}
        </DialogContent>
      </Dialog>
    </div>
  )
}
