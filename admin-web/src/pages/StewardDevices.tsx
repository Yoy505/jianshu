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
interface Binding {
  id: number; user_nickname: string | null; user_phone_masked: string | null
  vendor_name: string | null; device_model: string; device_sn: string; status: string
  cursor: { status: string; failure_count: number; last_success_at: string | null; last_error: string | null } | null
}
interface SyncLog {
  id: number; binding_id: number; status: string; pulled: number; stored: number
  duplicates: number; revisions: number; invalid: number; late: number; wrong_user: number
  detail: string | null; started_at: string
}
interface Metric {
  id: number; binding_id: number; metric_type: string; value: number; unit: string
  business_date: string; quality: string; revision: number
}

const QUALITY_LABELS: Record<string, string> = {
  valid: '有效', invalid_value: '无效值', late: '晚到', reviewed: '复核通过',
}
const LOG_LABELS: Record<string, string> = { success: '成功', partial: '部分', failed: '失败', blocked: '待配置/阻断' }

export default function StewardDevices() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canRead = can(admin?.role, 'device.read')
  const canManage = can(admin?.role, 'device.manage')
  const [page, setPage] = useState(1)
  const [tab, setTab] = useState<'bindings' | 'logs' | 'late'>('bindings')
  const pageSize = 20

  const bindingsQuery = useQuery({
    queryKey: ['device-bindings', page],
    queryFn: async () =>
      (await api.get<Page<Binding>>('/admin/devices/bindings', { params: { page, page_size: pageSize } })).data,
    enabled: canRead && tab === 'bindings',
  })
  const logsQuery = useQuery({
    queryKey: ['device-logs', page],
    queryFn: async () =>
      (await api.get<Page<SyncLog>>('/admin/devices/sync-logs', { params: { page, page_size: pageSize } })).data,
    enabled: canRead && tab === 'logs',
  })
  const lateQuery = useQuery({
    queryKey: ['device-late', page],
    queryFn: async () =>
      (await api.get<Page<Metric>>('/admin/devices/metrics', { params: { page, page_size: pageSize, quality: 'late' } })).data,
    enabled: canRead && tab === 'late',
  })

  const onError = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    toast.error(typeof detail === 'string' ? detail : '操作失败')
  }
  const act = useMutation({
    mutationFn: ({ id, path, body }: { id: number; path: string; body?: Record<string, unknown> }) =>
      api.post(`/admin/devices/bindings/${id}/${path}`, body ?? {}).then((r) => r.data),
    onSuccess: async () => { toast.success('已执行'); await queryClient.invalidateQueries({ queryKey: ['device-bindings'] }) },
    onError,
  })
  const review = useMutation({
    mutationFn: (id: number) =>
      api.post(`/admin/devices/metrics/${id}/review`, { reason: '后台复核：厂商补传确认' }).then((r) => r.data),
    onSuccess: async () => { toast.success('已复核解除'); await queryClient.invalidateQueries({ queryKey: ['device-late'] }) },
    onError,
  })

  if (!canRead) return <p className="text-sm text-muted-foreground">无权限查看设备数据。</p>
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">设备数据（厂商适配待配置：真实接入后自动同步）</h1>
        <div className="flex gap-2">
          <Button size="sm" variant={tab === 'bindings' ? 'default' : 'ghost'} onClick={() => setTab('bindings')}>绑定</Button>
          <Button size="sm" variant={tab === 'logs' ? 'default' : 'ghost'} onClick={() => setTab('logs')}>同步日志</Button>
          <Button size="sm" variant={tab === 'late' ? 'default' : 'ghost'} onClick={() => setTab('late')}>晚到复核</Button>
          <Button size="sm" variant="ghost" onClick={() => queryClient.invalidateQueries()}>
            <RefreshCw className="size-4" />
          </Button>
        </div>
      </div>

      {tab === 'bindings' && (
        <AsyncList query={bindingsQuery}>
          {(data) => (
            <>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>用户</TableHead><TableHead>厂商 / 型号</TableHead><TableHead>序列号</TableHead>
                  <TableHead>状态</TableHead><TableHead>游标</TableHead><TableHead>最近成功</TableHead>
                  {canManage && <TableHead>操作</TableHead>}
                </TableRow></TableHeader>
                <TableBody>
                  {data.items.map((b) => (
                    <TableRow key={b.id}>
                      <TableCell>{b.user_nickname || '-'}<span className="ml-1 text-xs text-muted-foreground">{b.user_phone_masked}</span></TableCell>
                      <TableCell className="text-xs">{b.vendor_name} / {b.device_model}</TableCell>
                      <TableCell className="font-mono text-xs">{b.device_sn}</TableCell>
                      <TableCell><StatusBadge status={b.status} label={b.status === 'active' ? '绑定中' : '已解绑'} /></TableCell>
                      <TableCell className="text-xs">
                        {b.cursor ? (b.cursor.status === 'blocked' ? <span className="text-destructive">已封锁（失败 {b.cursor.failure_count}）</span> : '正常') : '-'}
                      </TableCell>
                      <TableCell className="text-xs">{b.cursor?.last_success_at ? formatDateTime(b.cursor.last_success_at) : '-'}</TableCell>
                      {canManage && b.status === 'active' && (
                        <TableCell className="space-x-1 whitespace-nowrap">
                          <Button size="sm" variant="outline" onClick={() => act.mutate({ id: b.id, path: 'sync' })}>同步</Button>
                          {b.cursor?.status === 'blocked' && (
                            <Button size="sm" variant="outline" onClick={() => {
                              const reason = window.prompt('重置原因')
                              if (reason) act.mutate({ id: b.id, path: 'cursor/reset', body: { reason, back: true } })
                            }}>重置游标</Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => {
                            const reason = window.prompt('解绑原因')
                            if (reason) act.mutate({ id: b.id, path: 'unbind', body: { reason } })
                          }}>解绑</Button>
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

      {tab === 'logs' && (
        <AsyncList query={logsQuery}>
          {(data) => (
            <>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>绑定</TableHead><TableHead>结果</TableHead><TableHead>拉取/入库</TableHead>
                  <TableHead>重复/修订</TableHead><TableHead>无效/晚到/错人</TableHead><TableHead>说明</TableHead><TableHead>时间</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {data.items.map((l) => (
                    <TableRow key={l.id}>
                      <TableCell>#{l.binding_id}</TableCell>
                      <TableCell><StatusBadge status={l.status} label={LOG_LABELS[l.status] || l.status} /></TableCell>
                      <TableCell>{l.pulled} / {l.stored}</TableCell>
                      <TableCell>{l.duplicates} / {l.revisions}</TableCell>
                      <TableCell>{l.invalid} / {l.late} / {l.wrong_user}</TableCell>
                      <TableCell className="max-w-64 truncate text-xs text-muted-foreground">{l.detail}</TableCell>
                      <TableCell className="text-xs">{formatDateTime(l.started_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PagePagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} />
            </>
          )}
        </AsyncList>
      )}

      {tab === 'late' && (
        <AsyncList query={lateQuery}>
          {(data) => (
            <>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>绑定</TableHead><TableHead>指标</TableHead><TableHead>数值</TableHead>
                  <TableHead>业务日期</TableHead><TableHead>质量</TableHead>{canManage && <TableHead>操作</TableHead>}
                </TableRow></TableHeader>
                <TableBody>
                  {data.items.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell>#{m.binding_id}</TableCell>
                      <TableCell>{m.metric_type}</TableCell>
                      <TableCell>{m.value} {m.unit}</TableCell>
                      <TableCell>{m.business_date}</TableCell>
                      <TableCell>{QUALITY_LABELS[m.quality] || m.quality}</TableCell>
                      {canManage && (
                        <TableCell><Button size="sm" variant="outline" onClick={() => review.mutate(m.id)}>复核解除</Button></TableCell>
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
    </div>
  )
}
