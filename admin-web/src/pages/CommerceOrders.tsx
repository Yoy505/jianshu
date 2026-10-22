import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Search } from 'lucide-react'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
import { StatusBadge } from '@/components/common/StatusBadge'
import { toast } from '@/components/common/toast'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

interface UserBrief { id: number; nickname: string; phone: string | null }
interface Refund {
  id: number; refund_no: string; channel: string; status: string
  amount_cents: number; reason: string; note: string | null
  created_at: string; finished_at: string | null
}
interface Payment {
  id: number; prepay_id: string | null; transaction_id: string | null
  status: string; amount_cents: number; fail_reason: string | null; created_at: string
}
interface PaymentEvent {
  id: number; event_type: string; result: string; detail: Record<string, unknown> | null; created_at: string
}
interface OrderItem {
  id: number; order_no: string; status: string; pay_type: string
  package: { id: number; name: string; version_id: number; version_no: number }
  total_cents: number; points_cost: number | null
  entitlement_id: number | null; created_at: string
  user: UserBrief
}
interface OrderDetail extends OrderItem {
  expires_at: string | null; paid_at: string | null; closed_at: string | null
  payments: Payment[]; events: PaymentEvent[]; refunds: Refund[]
  entitlement: { id: number; status: string; valid_until: string | null } | null
}
interface QueueRefund extends Refund {
  order_id: number; order_no: string; pay_type: string
  user: UserBrief; requested_by: string | null; resolved_by: string | null
}
interface Page<T> { items: T[]; total: number; page: number; page_size: number }

const EVENT_LABELS: Record<string, string> = {
  notify_accepted: '回调入账', notify_replay: '重复回调（幂等）',
  notify_rejected: '回调拒绝', query_sync: '对账/同步',
}
const PAY_TYPE_LABELS: Record<string, string> = { wxpay: '微信支付', points: '积分兑换' }

function yuan(cents: number) {
  return (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2)
}

export default function CommerceOrders() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canRead = can(admin?.role, 'commerce.order.read')
  const canRefund = can(admin?.role, 'commerce.order.refund')

  const [tab, setTab] = useState<'orders' | 'refunds'>('orders')
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [searchText, setSearchText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [refundFor, setRefundFor] = useState<OrderDetail | null>(null)
  const pageSize = 20

  const listQuery = useQuery({
    queryKey: ['commerce-orders', page, status, keyword],
    queryFn: async () => {
      const { data } = await api.get<Page<OrderItem>>('/admin/mp/orders', {
        params: { page, page_size: pageSize, status: status || undefined, q: keyword || undefined },
      })
      return data
    },
    enabled: canRead,
  })

  const queueQuery = useQuery({
    queryKey: ['commerce-refunds', page],
    queryFn: async () => {
      const { data } = await api.get<Page<QueueRefund>>('/admin/mp/refunds', {
        params: { page, page_size: pageSize },
      })
      return data
    },
    enabled: canRead && tab === 'refunds',
  })

  const detailQuery = useQuery({
    queryKey: ['commerce-order', selectedId],
    queryFn: async () => {
      const { data } = await api.get<OrderDetail>(`/admin/mp/orders/${selectedId}`)
      return data
    },
    enabled: canRead && selectedId !== null,
  })

  const syncMutation = useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<OrderDetail>(`/admin/mp/orders/${id}/sync`)
      return data
    },
    onSuccess: async (data) => {
      toast.success(`对账完成：${data.status}`)
      await queryClient.invalidateQueries({ queryKey: ['commerce-order', selectedId] })
      await queryClient.invalidateQueries({ queryKey: ['commerce-orders'] })
    },
  })

  const refundMutation = useMutation({
    mutationFn: async (payload: { orderId: number; channel: string; amount_cents?: number; reason: string }) => {
      const { orderId, ...body } = payload
      const { data } = await api.post<Refund>(`/admin/mp/orders/${orderId}/refund`, body)
      return data
    },
    onSuccess: async () => {
      toast.success('退款已登记，处理进展见退款队列')
      setRefundFor(null)
      await queryClient.invalidateQueries({ queryKey: ['commerce-order', selectedId] })
      await queryClient.invalidateQueries({ queryKey: ['commerce-orders'] })
    },
  })

  const resolveMutation = useMutation({
    mutationFn: async (payload: { refundId: number; status: 'success' | 'failed'; note?: string }) => {
      const { refundId, ...body } = payload
      const { data } = await api.post<QueueRefund>(`/admin/mp/refunds/${refundId}/resolve`, body)
      return data
    },
    onSuccess: async () => {
      toast.success('退款结论已登记')
      await queryClient.invalidateQueries({ queryKey: ['commerce-refunds'] })
      await queryClient.invalidateQueries({ queryKey: ['commerce-orders'] })
    },
  })

  useEffect(() => { setPage(1) }, [status, tab])

  if (!canRead) {
    return <p className="admin-state rounded-xl border border-border bg-card">当前账号没有查看订单的权限。</p>
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPage(1)
    setKeyword(searchText.trim())
  }

  const detail = detailQuery.data

  return (
    <div className="admin-page">
      <header className="admin-page-header">
        <div>
          <h1>服务包订单</h1>
          <p className="text-[13px] text-[#7a8880]">
            支付成功以服务端回调/对账为准；退款经队列人工处理，不直接改账。
          </p>
        </div>
      </header>

      <div className="mb-3 flex gap-2">
        <Button size="sm" variant={tab === 'orders' ? 'default' : 'outline'} onClick={() => setTab('orders')}>订单查询</Button>
        <Button size="sm" variant={tab === 'refunds' ? 'default' : 'outline'} onClick={() => setTab('refunds')}>退款/异常队列</Button>
      </div>

      {tab === 'orders' && (
        <>
          <form className="admin-filter-panel" onSubmit={submitSearch}>
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
              <option value="">全部状态</option>
              <option value="pending_payment">待支付</option>
              <option value="paid">已支付</option>
              <option value="refund_pending">退款处理中</option>
              <option value="refund_failed">退款失败</option>
              <option value="refunded">已退款</option>
              <option value="closed">已关闭</option>
            </select>
            <div className="flex min-w-64 items-center gap-2">
              <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="订单号 / 昵称 / 手机号" />
              <Button type="submit" variant="outline"><Search className="size-4" /></Button>
            </div>
          </form>
          <div className="admin-data-panel">
            {listQuery.isPending ? (
              <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
            ) : listQuery.isError ? (
              <DataState title="加载失败" onRetry={() => listQuery.refetch()} />
            ) : !listQuery.data.items.length ? (
              <DataState title="暂无符合条件的订单。" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>订单号</TableHead>
                    <TableHead>服务包</TableHead>
                    <TableHead>用户</TableHead>
                    <TableHead>金额</TableHead>
                    <TableHead>支付方式</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>下单时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {listQuery.data.items.map((o) => (
                    <TableRow key={o.id} className="cursor-pointer" onClick={() => setSelectedId(o.id)}>
                      <TableCell className="font-mono text-[12px]">{o.order_no}</TableCell>
                      <TableCell>{o.package.name} v{o.package.version_no}</TableCell>
                      <TableCell>{o.user.nickname}（{o.user.phone}）</TableCell>
                      <TableCell>{o.pay_type === 'points' ? `${o.points_cost} 积分` : `${yuan(o.total_cents)} 元`}</TableCell>
                      <TableCell>{PAY_TYPE_LABELS[o.pay_type]}</TableCell>
                      <TableCell><StatusBadge status={o.status} /></TableCell>
                      <TableCell className="text-[12px]">{formatDateTime(o.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {listQuery.data && <PagePagination page={page} pageSize={pageSize} total={listQuery.data.total} onChange={setPage} />}
          </div>
        </>
      )}

      {tab === 'refunds' && (
        <div className="admin-data-panel">
          {queueQuery.isPending ? (
            <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : queueQuery.isError ? (
            <DataState title="加载失败" onRetry={() => queueQuery.refetch()} />
          ) : !queueQuery.data.items.length ? (
            <DataState title="当前没有待处理的退款。" />
          ) : (
            <div className="space-y-3">
              {queueQuery.data.items.map((r) => (
                <div key={r.id} className="rounded-lg border border-[#e2e9e4] p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium">
                      {r.order_no} · {yuan(r.amount_cents)} 元 · {r.channel === 'wxpay' ? '微信退款' : '人工处理'}
                    </div>
                    <StatusBadge status={r.status} />
                  </div>
                  <p className="mt-1 text-[13px] text-[#66756d]">
                    用户 {r.user.nickname}（{r.user.phone}）· 原因：{r.reason} · 申请 {formatDateTime(r.created_at)}
                    {r.requested_by ? ` · 发起人 ${r.requested_by}` : ''}
                  </p>
                  {r.note && <p className="mt-1 text-[13px] text-[#8a6d3b]">备注：{r.note}</p>}
                  {canRefund && (r.status === 'pending' || r.status === 'processing') && (
                    <div className="mt-2 flex gap-2">
                      <Button size="sm" onClick={() => resolveMutation.mutate({ refundId: r.id, status: 'success', note: '人工确认完成' })}>
                        {resolveMutation.isPending && <Loader2 className="size-4 animate-spin" />} 登记退款成功
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => resolveMutation.mutate({ refundId: r.id, status: 'failed', note: '渠道退回，待跟进' })}>
                        登记失败
                      </Button>
                    </div>
                  )}
                </div>
              ))}
              {queueQuery.data && (
                <PagePagination page={page} pageSize={pageSize} total={queueQuery.data.total} onChange={setPage} />
              )}
            </div>
          )}
        </div>
      )}

      <Dialog open={selectedId !== null && detail !== undefined} onOpenChange={(open) => !open && setSelectedId(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>订单 {detail?.order_no}</DialogTitle>
            <DialogDescription>
              {detail ? `${detail.package.name} v${detail.package.version_no} · ${PAY_TYPE_LABELS[detail.pay_type]}` : ''}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="max-h-[64vh] space-y-4 overflow-y-auto pr-1 text-sm">
              <div className="flex flex-wrap gap-2">
                {canRefund && detail.pay_type === 'wxpay' && detail.status === 'pending_payment' && (
                  <Button size="sm" variant="outline" onClick={() => syncMutation.mutate(detail.id)}>
                    {syncMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} 微信查单对账
                  </Button>
                )}
                {canRefund && (detail.status === 'paid' || detail.status === 'refund_failed') && (
                  <Button size="sm" variant="outline" onClick={() => setRefundFor(detail)}>发起退款</Button>
                )}
              </div>

              <section className="space-y-1">
                <h3 className="font-semibold">订单信息</h3>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] text-[#66756d]">
                  <p>状态：<StatusBadge status={detail.status} /></p>
                  <p>用户：{detail.user.nickname}（{detail.user.phone}）</p>
                  <p>金额：{detail.pay_type === 'points' ? `${detail.points_cost} 积分` : `${yuan(detail.total_cents)} 元`}</p>
                  <p>权益：{detail.entitlement ? `#${detail.entitlement.id}（${detail.entitlement.status}）` : '—'}</p>
                  <p>下单：{formatDateTime(detail.created_at)}</p>
                  <p>支付：{detail.paid_at ? formatDateTime(detail.paid_at) : '—'}</p>
                </div>
              </section>

              {detail.payments.length > 0 && (
                <section>
                  <h3 className="mb-2 font-semibold">支付记录</h3>
                  <div className="space-y-1 text-[13px] text-[#66756d]">
                    {detail.payments.map((p) => (
                      <p key={p.id}>
                        #{p.id} · {p.status} · {yuan(p.amount_cents)} 元
                        {p.transaction_id ? ` · 微信单号 ${p.transaction_id}` : ''}
                        {p.fail_reason ? ` · ${p.fail_reason}` : ''} · {formatDateTime(p.created_at)}
                      </p>
                    ))}
                  </div>
                </section>
              )}

              {detail.refunds.length > 0 && (
                <section>
                  <h3 className="mb-2 font-semibold">退款记录</h3>
                  <div className="space-y-1 text-[13px] text-[#66756d]">
                    {detail.refunds.map((r) => (
                      <p key={r.id}>
                        {r.refund_no} · <StatusBadge status={r.status} /> · {yuan(r.amount_cents)} 元 · {r.reason}
                        {r.finished_at ? ` · ${formatDateTime(r.finished_at)} 结论` : ''}
                      </p>
                    ))}
                  </div>
                </section>
              )}

              {detail.events.length > 0 && (
                <section>
                  <h3 className="mb-2 font-semibold">回调与对账事件</h3>
                  <div className="space-y-1 text-[12px] text-[#66756d]">
                    {detail.events.map((e) => (
                      <p key={e.id}>
                        {EVENT_LABELS[e.event_type] ?? e.event_type} · {e.result} · {formatDateTime(e.created_at)}
                        {e.detail ? ` · ${JSON.stringify(e.detail)}` : ''}
                      </p>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={refundFor !== null} onOpenChange={(open) => !open && setRefundFor(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>发起退款</DialogTitle>
            <DialogDescription>
              {refundFor ? `${refundFor.order_no} · ${refundFor.pay_type === 'points' ? '积分单仅人工处理' : '微信单将调用微信退款接口'}` : ''}
            </DialogDescription>
          </DialogHeader>
          {refundFor && (
            <RefundForm
              payType={refundFor.pay_type}
              defaultCents={refundFor.total_cents}
              submitting={refundMutation.isPending}
              onSubmit={(payload) => refundMutation.mutate({ orderId: refundFor.id, ...payload })}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function RefundForm({ payType, defaultCents, submitting, onSubmit }: {
  payType: string
  defaultCents: number
  submitting: boolean
  onSubmit: (payload: { channel: string; amount_cents?: number; reason: string }) => void
}) {
  const [channel, setChannel] = useState(payType === 'points' ? 'manual' : 'wxpay')
  const [amount, setAmount] = useState(yuan(defaultCents))
  const [reason, setReason] = useState('')
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        const cents = Math.round(Number(amount) * 100)
        if (!reason.trim() || !Number.isFinite(cents) || cents <= 0) return
        onSubmit({ channel, amount_cents: cents, reason: reason.trim() })
      }}
    >
      <div className="space-y-1.5">
        <Label>处理渠道</Label>
        <select value={channel} onChange={(e) => setChannel(e.target.value)} disabled={payType === 'points'}
          className="h-9 w-full rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
          <option value="wxpay">微信退款接口</option>
          <option value="manual">人工线下退款</option>
        </select>
        {payType === 'points' && <p className="text-[12px] text-[#8a978e]">积分兑换订单只能登记人工处理。</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="refund-amount">退款金额（元）</Label>
        <Input id="refund-amount" type="number" min="0.01" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="refund-reason">退款原因（必填）</Label>
        <Textarea id="refund-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} required />
      </div>
      <Button type="submit" className="w-full" disabled={submitting || !reason.trim()}>
        {submitting && <Loader2 className="size-4 animate-spin" />} 提交退款申请
      </Button>
    </form>
  )
}
