import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { toast } from '@/components/common/toast'
import { AsyncList } from '@/components/common/AsyncList'
import { PagePagination } from '@/components/common/PagePagination'
import { StatusBadge } from '@/components/common/StatusBadge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

interface Page<T> { items: T[]; total: number; page: number; page_size: number }
interface Order {
  id: number; instance_id: number; order_type: string; title: string; priority: string
  status: string; physician_name: string | null; care_plan_version: string | null
  due_at: string | null; escalated_at: string | null; transferred_at: string | null
  result: string | null
}
interface Report { id: number; instance_id: number; version: number; report_type: string; title: string; issued_at: string }
interface StaffRow { id: number; name: string; role: string; status: string }

const TYPE_LABELS: Record<string, string> = {
  initial_assessment: '初评', data_quality: '数据质量', physician_review: '医师复核',
  final_review: '期末复评', exception: '异常跟进',
}
const STATUS_LABELS: Record<string, string> = { pending: '待处理', in_progress: '处理中', done: '已完成', cancelled: '已取消' }

export default function StewardProfessional() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canRead = can(admin?.role, 'steward.read')
  const canManage = can(admin?.role, 'steward.manage')
  const [page, setPage] = useState(1)
  const [tab, setTab] = useState<'orders' | 'reports'>('orders')
  const pageSize = 20

  const ordersQuery = useQuery({
    queryKey: ['prof-orders', page],
    queryFn: async () =>
      (await api.get<Page<Order>>('/admin/steward/professional/orders', { params: { page, page_size: pageSize } })).data,
    enabled: canRead && tab === 'orders',
  })
  const reportsQuery = useQuery({
    queryKey: ['prof-reports'],
    queryFn: async () => (await api.get<Report[]>('/admin/steward/reports')).data,
    enabled: canRead && tab === 'reports',
  })
  const staffQuery = useQuery({
    queryKey: ['physicians'],
    queryFn: async () =>
      (await api.get<Page<StaffRow>>('/admin/steward/staff', { params: { role: 'physician', page_size: 100 } })).data,
    enabled: canRead && canManage,
  })

  const onError = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    toast.error(typeof detail === 'string' ? detail : '操作失败')
  }
  const act = useMutation({
    mutationFn: ({ id, path, body }: { id: number; path: string; body?: Record<string, unknown> }) =>
      api.post(`/admin/steward/professional/orders/${id}/${path}`, body ?? {}).then((r) => r.data),
    onSuccess: async () => { toast.success('已处理'); await queryClient.invalidateQueries({ queryKey: ['prof-orders'] }) },
    onError,
  })

  if (!canRead) return <p className="text-sm text-muted-foreground">无权限查看专业工单。</p>
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">专业工单与服务报告</h1>
        <div className="flex gap-2">
          <Button size="sm" variant={tab === 'orders' ? 'default' : 'ghost'} onClick={() => setTab('orders')}>工单</Button>
          <Button size="sm" variant={tab === 'reports' ? 'default' : 'ghost'} onClick={() => setTab('reports')}>报告</Button>
          <Button size="sm" variant="ghost" onClick={() => queryClient.invalidateQueries()}>
            <RefreshCw className="size-4" />
          </Button>
        </div>
      </div>

      {tab === 'orders' && (
        <AsyncList query={ordersQuery}>
          {(data) => (
            <>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>类型</TableHead><TableHead>标题</TableHead><TableHead>优先级</TableHead>
                  <TableHead>状态</TableHead><TableHead>医师</TableHead><TableHead>截止</TableHead>
                  <TableHead>标志</TableHead>{canManage && <TableHead>操作</TableHead>}
                </TableRow></TableHeader>
                <TableBody>
                  {data.items.map((o) => (
                    <TableRow key={o.id}>
                      <TableCell>{TYPE_LABELS[o.order_type] || o.order_type}</TableCell>
                      <TableCell className="max-w-48 truncate">{o.title}</TableCell>
                      <TableCell>{o.priority === 'high' ? <span className="font-medium text-destructive">高</span> : '普通'}</TableCell>
                      <TableCell><StatusBadge status={o.status} label={STATUS_LABELS[o.status] || o.status} /></TableCell>
                      <TableCell>{o.physician_name || '-'}</TableCell>
                      <TableCell className="text-xs">{o.due_at ? formatDateTime(o.due_at) : '-'}</TableCell>
                      <TableCell className="space-x-1 text-xs">
                        {o.escalated_at && <span className="text-amber-600">已升级</span>}
                        {o.transferred_at && <span className="text-blue-600">已转交</span>}
                      </TableCell>
                      {canManage && (
                        <TableCell className="space-x-1 whitespace-nowrap">
                          {o.status === 'pending' && (
                            <Button size="sm" variant="outline" onClick={() => act.mutate({ id: o.id, path: 'start' })}>开始</Button>
                          )}
                          {!['done', 'cancelled'].includes(o.status) && staffQuery.data && (
                            <Button size="sm" variant="outline" onClick={() => {
                              const physicians = staffQuery.data!.items.filter((s) => s.status === 'active')
                              if (!physicians.length) { toast.error('无在职医师'); return }
                              const to = window.prompt(`转交给（输入序号）：\n${physicians.map((s, i) => `${i + 1}. ${s.name}`).join('\n')}`)
                              const idx = Number(to) - 1
                              if (to && physicians[idx]) {
                                const reason = window.prompt('转交依据')
                                if (reason) act.mutate({ id: o.id, path: 'transfer', body: { to_physician_id: physicians[idx].id, reason } })
                              }
                            }}>转交</Button>
                          )}
                          {!['done', 'cancelled'].includes(o.status) && (
                            <Button size="sm" onClick={() => {
                              const result = window.prompt('处理结果（须由绑定该医师的账号录入）')
                              if (result) act.mutate({ id: o.id, path: 'complete', body: { result } })
                            }}>完成</Button>
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
      )}

      {tab === 'reports' && (
        <AsyncList query={reportsQuery}>
          {(data) => (
            <Table>
              <TableHeader><TableRow><TableHead>实例</TableHead><TableHead>版本</TableHead><TableHead>类型</TableHead><TableHead>标题</TableHead><TableHead>签发时间</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>#{r.instance_id}</TableCell>
                    <TableCell>v{r.version}</TableCell>
                    <TableCell>{r.report_type === 'closure' ? '结案' : '更正'}</TableCell>
                    <TableCell className="max-w-64 truncate">{r.title}</TableCell>
                    <TableCell className="text-xs">{formatDateTime(r.issued_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </AsyncList>
      )}
    </div>
  )
}
