import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { toast } from '@/components/common/toast'
import { AsyncList } from '@/components/common/AsyncList'
import { PagePagination } from '@/components/common/PagePagination'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { useAuthStore } from '@/stores/auth'

interface Page<T> { items: T[]; total: number; page: number; page_size: number }
interface Intent {
  id: number; instance_id: number; user_phone_masked: string | null; user_nickname: string | null
  service_date: string; purpose: string; status: string; attempts: number
  assignee_staff_id: number | null; assignee_name: string | null; stop_reason: string | null
  result: string | null
}
interface StaffRow { id: number; name: string; role: string; status: string }
interface SchedIntent {
  id: number; task_id: number; instance_id: number; preferred_date: string
  preferred_period: string; note: string | null; status: string
  handle_note: string | null; created_at: string
}

const STATUS_LABELS: Record<string, string> = {
  pending_channel: '待通道配置', pending_manual: '待人工处理', pending_confirm: '待确认',
  resolved: '已办理', failed: '失败', cancelled: '已取消',
}
const STOP_LABELS: Record<string, string> = {
  instance_expired: '服务到期', instance_terminated: '服务终止', service_paused: '服务暂停',
  unsubscribed: '本人退订', consent_revoked: '授权撤回', manual_cancel: '人工取消',
}

async function call(path: string, body?: Record<string, unknown>) {
  return (await api.post(path, body ?? {})).data
}

export default function StewardOutreach() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canRead = can(admin?.role, 'outreach.read')
  const canManage = can(admin?.role, 'outreach.manage')
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const pageSize = 20

  const listQuery = useQuery({
    queryKey: ['outreach-intents', page, status],
    queryFn: async () =>
      (await api.get<Page<Intent>>('/admin/outreach/intents', {
        params: { page, page_size: pageSize, status: status || undefined },
      })).data,
    enabled: canRead,
  })
  const staffQuery = useQuery({
    queryKey: ['outreach-butlers'],
    queryFn: async () =>
      (await api.get<Page<StaffRow>>('/admin/steward/staff', { params: { role: 'butler', page_size: 100 } })).data,
    enabled: canRead && canManage,
  })

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['outreach-intents'] })
  }
  const onError = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    toast.error(typeof detail === 'string' ? detail : '操作失败')
  }
  const act = useMutation({
    mutationFn: ({ id, path, body }: { id: number; path: string; body?: Record<string, unknown> }) =>
      call(`/admin/outreach/intents/${id}/${path}`, body),
    onSuccess: async () => { toast.success('已处理'); await invalidate() },
    onError,
  })
  const canStewardRead = can(admin?.role, 'steward.read')
  const canStewardManage = can(admin?.role, 'steward.manage')
  const schedQuery = useQuery({
    queryKey: ['steward-schedule-intents'],
    queryFn: async () =>
      (await api.get<SchedIntent[]>('/admin/steward/schedule-intents', { params: { status: 'pending' } })).data,
    enabled: canStewardRead,
    refetchInterval: 60_000,
  })
  const handleSched = useMutation({
    mutationFn: ({ id, note }: { id: number; note: string }) =>
      call(`/admin/steward/schedule-intents/${id}/handle`, { note }),
    onSuccess: async () => {
      toast.success('意向已标记处理')
      await queryClient.invalidateQueries({ queryKey: ['steward-schedule-intents'] })
    },
    onError,
  })
  const handover = useMutation({
    mutationFn: (body: Record<string, number | string>) => call('/admin/outreach/handover', body),
    onSuccess: async (d) => { toast.success(`已交接 ${d.moved} 条`); await invalidate() },
    onError,
  })

  if (!canRead) return <p className="text-sm text-muted-foreground">无权限查看触达待办。</p>
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">触达待办（渠道未决策：仅人工处理，无发送状态）</h1>
        <div className="flex items-center gap-2">
          <select className="h-9 rounded-md border bg-background px-2 text-sm" value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
            <option value="">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <Button variant="ghost" size="sm" onClick={() => queryClient.invalidateQueries({ queryKey: ['outreach-intents'] })}>
            <RefreshCw className="size-4" />
          </Button>
        </div>
      </div>

      {canManage && staffQuery.data && (
        <div className="flex flex-wrap items-end gap-2 rounded-md border p-3 text-sm">
          <div className="space-y-1">
            <span className="text-xs text-muted-foreground">离岗交接：从</span>
            <select id="handover-from" className="h-9 rounded-md border bg-background px-2">
              {staffQuery.data.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <span className="text-xs text-muted-foreground">交接给</span>
            <select id="handover-to" className="h-9 rounded-md border bg-background px-2">
              {staffQuery.data.items.filter((s) => s.status === 'active').map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="flex-1 space-y-1">
            <span className="text-xs text-muted-foreground">原因</span>
            <Input id="handover-reason" placeholder="如：管家休假" />
          </div>
          <Button size="sm" variant="outline"
            onClick={() => {
              const from = Number((document.getElementById('handover-from') as HTMLSelectElement).value)
              const to = Number((document.getElementById('handover-to') as HTMLSelectElement).value)
              const reason = (document.getElementById('handover-reason') as HTMLInputElement).value
              if (!reason.trim()) { toast.error('请填写交接原因'); return }
              handover.mutate({ from_staff_id: from, to_staff_id: to, reason })
            }}>执行交接</Button>
        </div>
      )}

      <AsyncList query={listQuery}>
        {(data) => (
          <>
            <Table>
              <TableHeader><TableRow>
                <TableHead>服务日</TableHead><TableHead>用户</TableHead><TableHead>用途</TableHead>
                <TableHead>状态</TableHead><TableHead>责任人</TableHead><TableHead>结果 / 停止原因</TableHead>
                {canManage && <TableHead>操作</TableHead>}
              </TableRow></TableHeader>
              <TableBody>
                {data.items.map((it) => (
                  <TableRow key={it.id}>
                    <TableCell className="text-xs">{it.service_date}</TableCell>
                    <TableCell>{it.user_nickname || '-'}<span className="ml-1 text-xs text-muted-foreground">{it.user_phone_masked}</span></TableCell>
                    <TableCell className="text-xs">{it.purpose === 'daily_reminder' ? '每日提醒' : '随访跟进'}</TableCell>
                    <TableCell><StatusBadge status={it.status} label={STATUS_LABELS[it.status] || it.status} /></TableCell>
                    <TableCell>{it.assignee_name || '-'}</TableCell>
                    <TableCell className="max-w-56 truncate text-xs text-muted-foreground">
                      {it.stop_reason ? STOP_LABELS[it.stop_reason] || it.stop_reason : (it.result || '-')}
                    </TableCell>
                    {canManage && (
                      <TableCell className="space-x-1 whitespace-nowrap">
                        {it.status === 'pending_channel' && staffQuery.data && (
                          <Button size="sm" variant="outline" onClick={() => {
                            const to = staffQuery.data!.items.find((s) => s.status === 'active')
                            if (to) act.mutate({ id: it.id, path: 'route-manual', body: { assignee_staff_id: to.id, note: '通道未定转人工' } })
                          }}>转人工</Button>
                        )}
                        {it.status === 'pending_manual' && (
                          <>
                            <Button size="sm" variant="outline" onClick={() => {
                              const outcome = window.prompt('联系结果（方式+结果，人工联系不冒充通道成功）')
                              if (outcome) act.mutate({ id: it.id, path: 'submit', body: { contact_method: 'phone', outcome } })
                            }}>提交结果</Button>
                            <Button size="sm" variant="ghost" onClick={() => {
                              const reason = window.prompt('退回原因')
                              if (reason) act.mutate({ id: it.id, path: 'release', body: { reason } })
                            }}>退回</Button>
                          </>
                        )}
                        {it.status === 'pending_confirm' && (
                          <>
                            <Button size="sm" onClick={() => act.mutate({ id: it.id, path: 'confirm' })}>确认</Button>
                            <Button size="sm" variant="ghost" onClick={() => {
                              const reason = window.prompt('驳回原因')
                              if (reason) act.mutate({ id: it.id, path: 'reject', body: { reason } })
                            }}>驳回</Button>
                          </>
                        )}
                        {!['resolved', 'cancelled', 'failed'].includes(it.status) && (
                          <Button size="sm" variant="ghost" onClick={() => {
                            const reason = window.prompt('取消原因')
                            if (reason) act.mutate({ id: it.id, path: 'cancel', body: { reason } })
                          }}>取消</Button>
                        )}
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PagePagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} />
          </>
        )}
      </AsyncList>

      {canStewardRead && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-muted-foreground">
            随访预约意向（用户申报的方便时段；意向非确认，处理留痕）
          </h2>
          <Table>
            <TableHeader><TableRow>
              <TableHead>意向</TableHead><TableHead>实例</TableHead><TableHead>任务</TableHead>
              <TableHead>日期</TableHead><TableHead>时段</TableHead><TableHead>备注</TableHead>
              <TableHead>提交时间</TableHead><TableHead className="text-right">操作</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {(schedQuery.data ?? []).map((it) => (
                <TableRow key={it.id}>
                  <TableCell>#{it.id}</TableCell>
                  <TableCell>{it.instance_id}</TableCell>
                  <TableCell>{it.task_id}</TableCell>
                  <TableCell>{it.preferred_date}</TableCell>
                  <TableCell>{it.preferred_period}</TableCell>
                  <TableCell>{it.note || '—'}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{it.created_at.slice(0, 16).replace('T', ' ')}</TableCell>
                  <TableCell className="text-right">
                    {canStewardManage ? (
                      <Button size="sm" variant="outline" onClick={() => {
                        const note = window.prompt('处理说明（选填）', '')
                        if (note !== null) handleSched.mutate({ id: it.id, note })
                      }}>标记已处理</Button>
                    ) : <span className="text-xs text-muted-foreground">只读</span>}
                  </TableCell>
                </TableRow>
              ))}
              {!schedQuery.isLoading && !(schedQuery.data ?? []).length && (
                <TableRow><TableCell colSpan={8} className="text-center text-sm text-muted-foreground">暂无待处理意向</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </section>
      )}
    </div>
  )
}
