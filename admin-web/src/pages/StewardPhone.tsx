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
import { useAuthStore } from '@/stores/auth'

interface Page<T> { items: T[]; total: number; page: number; page_size: number }
interface Script { id: number; code: string; version: string; title: string; status: string; requires_recording: boolean; target_type: string }
interface Contact { id: number; user_id: number; name: string; relation: string; phone_masked: string; consent_status: string; consent_source: string | null }
interface Order {
  id: number; instance_id: number; purpose: string; target_type: string; script_code: string
  script_version: string; status: string; stop_reason: string | null; attempts: number
  receipt_ref: string | null; result: string | null; created_at: string
}

const STATUS_LABELS: Record<string, string> = {
  pending_review: '待审核', queued: '待拨打', pending_config: '待配置', in_progress: '执行中',
  no_answer: '未接通', busy: '忙线', wrong_number: '错号', refused: '对方拒绝',
  transferred_human: '人工跟进', completed: '已完成', failed: '失败', cancelled: '已停止',
}
const CONSENT_LABELS: Record<string, string> = { none: '未授权', granted: '已授权', refused: '已拒绝', revoked: '已撤回' }

export default function StewardPhone() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canRead = can(admin?.role, 'phone.read')
  const canManage = can(admin?.role, 'phone.manage')
  const [page, setPage] = useState(1)
  const [tab, setTab] = useState<'orders' | 'scripts' | 'contacts'>('orders')
  const pageSize = 20

  const ordersQuery = useQuery({
    queryKey: ['phone-orders', page],
    queryFn: async () =>
      (await api.get<Page<Order>>('/admin/phone/orders', { params: { page, page_size: pageSize } })).data,
    enabled: canRead && tab === 'orders',
  })
  const scriptsQuery = useQuery({
    queryKey: ['phone-scripts'],
    queryFn: async () => (await api.get<Script[]>('/admin/phone/scripts')).data,
    enabled: canRead && tab === 'scripts',
  })
  const contactsQuery = useQuery({
    queryKey: ['phone-contacts', page],
    queryFn: async () =>
      (await api.get<Page<Contact>>('/admin/phone/contacts', { params: { page, page_size: pageSize } })).data,
    enabled: canRead && tab === 'contacts',
  })

  const onError = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    toast.error(typeof detail === 'string' ? detail : '操作失败')
  }
  const orderAct = useMutation({
    mutationFn: ({ id, path, body }: { id: number; path: string; body?: Record<string, unknown> }) =>
      api.post(`/admin/phone/orders/${id}/${path}`, body ?? {}).then((r) => r.data),
    onSuccess: async () => { toast.success('已处理'); await queryClient.invalidateQueries({ queryKey: ['phone-orders'] }) },
    onError,
  })
  const scriptAct = useMutation({
    mutationFn: ({ id }: { id: number }) =>
      api.post(`/admin/phone/scripts/${id}/review`, { approve: true, note: '合规审核通过（有限告知）' }).then((r) => r.data),
    onSuccess: async () => { toast.success('已审核通过'); await queryClient.invalidateQueries({ queryKey: ['phone-scripts'] }) },
    onError,
  })

  if (!canRead) return <p className="text-sm text-muted-foreground">无权限查看电话通路。</p>
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">电话通路（真实外呼关闭：无 CCC 配置时工单停在待配置）</h1>
        <div className="flex gap-2">
          <Button size="sm" variant={tab === 'orders' ? 'default' : 'ghost'} onClick={() => setTab('orders')}>工单</Button>
          <Button size="sm" variant={tab === 'scripts' ? 'default' : 'ghost'} onClick={() => setTab('scripts')}>话术</Button>
          <Button size="sm" variant={tab === 'contacts' ? 'default' : 'ghost'} onClick={() => setTab('contacts')}>联系人</Button>
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
                  <TableHead>实例</TableHead><TableHead>用途</TableHead><TableHead>对象</TableHead>
                  <TableHead>话术</TableHead><TableHead>状态</TableHead><TableHead>尝试</TableHead>
                  <TableHead>结果</TableHead>{canManage && <TableHead>操作</TableHead>}
                </TableRow></TableHeader>
                <TableBody>
                  {data.items.map((o) => (
                    <TableRow key={o.id}>
                      <TableCell>#{o.instance_id}</TableCell>
                      <TableCell className="text-xs">{o.purpose}</TableCell>
                      <TableCell>{o.target_type === 'self' ? '本人' : '联系人'}</TableCell>
                      <TableCell className="text-xs">{o.script_code}:{o.script_version}</TableCell>
                      <TableCell><StatusBadge status={o.status} label={STATUS_LABELS[o.status] || o.status} /></TableCell>
                      <TableCell>{o.attempts}</TableCell>
                      <TableCell className="max-w-48 truncate text-xs text-muted-foreground">{o.result || o.receipt_ref || '-'}</TableCell>
                      {canManage && (
                        <TableCell className="space-x-1 whitespace-nowrap">
                          {o.status === 'pending_review' && (
                            <Button size="sm" variant="outline" onClick={() => orderAct.mutate({ id: o.id, path: 'review', body: { approve: true } })}>审核</Button>
                          )}
                          {!['completed', 'cancelled', 'failed', 'wrong_number', 'refused'].includes(o.status) && (
                            <>
                              <Button size="sm" variant="outline" onClick={() => {
                                const note = window.prompt('接管说明')
                                if (note !== null) orderAct.mutate({ id: o.id, path: 'takeover', body: { note } })
                              }}>接管</Button>
                              <Button size="sm" variant="ghost" onClick={() => {
                                const reason = window.prompt('停止原因')
                                if (reason) orderAct.mutate({ id: o.id, path: 'cancel', body: { reason } })
                              }}>停止</Button>
                            </>
                          )}
                          {o.status === 'transferred_human' && (
                            <Button size="sm" onClick={() => {
                              const result = window.prompt('人工结果（须由接管人录入）')
                              if (result) orderAct.mutate({ id: o.id, path: 'complete', body: { result } })
                            }}>录结果</Button>
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

      {tab === 'scripts' && (
        <AsyncList query={scriptsQuery}>
          {(data) => (
            <Table>
              <TableHeader><TableRow>
                <TableHead>代码</TableHead><TableHead>版本</TableHead><TableHead>标题</TableHead>
                <TableHead>对象</TableHead><TableHead>录音</TableHead><TableHead>状态</TableHead>
                {canManage && <TableHead>操作</TableHead>}
              </TableRow></TableHeader>
              <TableBody>
                {data.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-mono text-xs">{s.code}</TableCell>
                    <TableCell>{s.version}</TableCell>
                    <TableCell className="max-w-56 truncate">{s.title}</TableCell>
                    <TableCell>{s.target_type === 'self' ? '本人' : '联系人'}</TableCell>
                    <TableCell>{s.requires_recording ? '需录音授权' : '不录音'}</TableCell>
                    <TableCell><StatusBadge status={s.status} label={{ draft: '草稿', active: '生效', retired: '已退役', rejected: '已驳回' }[s.status] || s.status} /></TableCell>
                    {canManage && s.status === 'draft' && (
                      <TableCell><Button size="sm" variant="outline" onClick={() => scriptAct.mutate({ id: s.id })}>审核通过</Button></TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </AsyncList>
      )}

      {tab === 'contacts' && (
        <AsyncList query={contactsQuery}>
          {(data) => (
            <>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>用户</TableHead><TableHead>姓名</TableHead><TableHead>关系</TableHead>
                  <TableHead>电话</TableHead><TableHead>授权状态</TableHead><TableHead>来源</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {data.items.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell>#{c.user_id}</TableCell>
                      <TableCell>{c.name}</TableCell>
                      <TableCell>{c.relation}</TableCell>
                      <TableCell>{c.phone_masked}</TableCell>
                      <TableCell><StatusBadge status={c.consent_status} label={CONSENT_LABELS[c.consent_status] || c.consent_status} /></TableCell>
                      <TableCell className="max-w-56 truncate text-xs text-muted-foreground">{c.consent_source || '-'}</TableCell>
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
