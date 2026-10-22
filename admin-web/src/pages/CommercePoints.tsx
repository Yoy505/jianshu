import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Search } from 'lucide-react'
import { DataState } from '@/components/common/DataState'
import { StatusBadge } from '@/components/common/StatusBadge'
import { toast } from '@/components/common/toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

interface PointsEntry {
  id: number; delta: number; balance_after: number; biz_type: string
  order_id: number | null; note: string | null; created_at: string
}
interface PointsResult {
  user: { id: number; nickname: string; phone: string | null }
  balance: number
  entries: { items: PointsEntry[]; total: number; page: number; page_size: number }
}

const BIZ_LABELS: Record<string, string> = {
  admin_adjust: '后台调整', redeem: '兑换扣减', refund_revert: '退款返还',
}

export default function CommercePoints() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canAdjust = can(admin?.role, 'commerce.points.adjust')

  const [searchText, setSearchText] = useState('')
  const [userId, setUserId] = useState<number | null>(null)
  const [delta, setDelta] = useState('')
  const [note, setNote] = useState('')

  const pointsQuery = useQuery({
    queryKey: ['commerce-points', userId],
    queryFn: async () => {
      const { data } = await api.get<PointsResult>(`/admin/mp/points/${userId}`)
      return data
    },
    enabled: canAdjust && userId !== null,
  })

  const adjustMutation = useMutation({
    mutationFn: async (payload: { userId: number; delta: number; note: string }) => {
      const { data } = await api.post<{ entry_id: number; balance_after: number }>(
        '/admin/mp/points/adjust',
        { user_id: payload.userId, delta: payload.delta, note: payload.note },
      )
      return data
    },
    onSuccess: async (data) => {
      toast.success(`已入账，当前余额 ${data.balance_after}`)
      setDelta(''); setNote('')
      await queryClient.invalidateQueries({ queryKey: ['commerce-points', userId] })
    },
  })

  if (!canAdjust) {
    return <p className="admin-state rounded-xl border border-border bg-card">积分调整仅管理员可用。</p>
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const id = Number(searchText.trim())
    if (!Number.isInteger(id) || id <= 0) {
      toast.error('请输入用户 ID（正整数）')
      return
    }
    setUserId(id)
  }

  const data = pointsQuery.data

  return (
    <div className="admin-page">
      <header className="admin-page-header">
        <div>
          <h1>积分账本</h1>
          <p className="text-[13px] text-[#7a8880]">
            余额只追加流水；兑换与退款返还由交易自动产生，此处仅人工调整并留审计。
          </p>
        </div>
      </header>

      <form className="admin-filter-panel" onSubmit={submitSearch}>
        <div className="flex min-w-64 items-center gap-2">
          <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="输入用户 ID 查询" />
          <Button type="submit" variant="outline"><Search className="size-4" /></Button>
        </div>
      </form>

      {userId !== null && (
        <div className="admin-data-panel">
          {pointsQuery.isPending ? (
            <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : pointsQuery.isError ? (
            <DataState title="加载失败" description="查询失败：用户可能不存在。" onRetry={() => pointsQuery.refetch()} />
          ) : data && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#e2e9e4] bg-[#f6faf7] p-3">
                <div>
                  <p className="font-medium">{data.user.nickname}（{data.user.phone}）· 用户 #{data.user.id}</p>
                  <p className="text-[13px] text-[#66756d]">当前余额 <span className="text-lg font-semibold text-[#0f766e]">{data.balance}</span> 分积分</p>
                </div>
                <form
                  className="flex flex-wrap items-end gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    const value = Number(delta)
                    if (!value || !note.trim()) return
                    adjustMutation.mutate({ userId: data.user.id, delta: value, note: note.trim() })
                  }}
                >
                  <div className="space-y-1">
                    <Label className="text-[12px]" htmlFor="pt-delta">调整（正=加 / 负=扣）</Label>
                    <Input id="pt-delta" type="number" value={delta} onChange={(e) => setDelta(e.target.value)} className="w-36" required />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[12px]" htmlFor="pt-note">原因（留审计）</Label>
                    <Input id="pt-note" value={note} onChange={(e) => setNote(e.target.value)} className="w-56" maxLength={200} required />
                  </div>
                  <Button type="submit" disabled={adjustMutation.isPending}>
                    {adjustMutation.isPending && <Loader2 className="size-4 animate-spin" />} 入账
                  </Button>
                </form>
              </div>

              <Table className="mt-3">
                <TableHeader>
                  <TableRow>
                    <TableHead>ID</TableHead>
                    <TableHead>变动</TableHead>
                    <TableHead>变动后余额</TableHead>
                    <TableHead>类型</TableHead>
                    <TableHead>关联订单</TableHead>
                    <TableHead>说明</TableHead>
                    <TableHead>时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.entries.items.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell>{e.id}</TableCell>
                      <TableCell className={e.delta > 0 ? 'text-[#217a54]' : 'text-[#a1665c]'}>
                        {e.delta > 0 ? `+${e.delta}` : e.delta}
                      </TableCell>
                      <TableCell>{e.balance_after}</TableCell>
                      <TableCell>{BIZ_LABELS[e.biz_type] ?? e.biz_type}</TableCell>
                      <TableCell>{e.order_id ?? '—'}</TableCell>
                      <TableCell>{e.note ?? '—'}</TableCell>
                      <TableCell className="text-[12px]">{formatDateTime(e.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="mt-2 text-[12px] text-[#8a978e]">
                共 {data.entries.total} 条流水（最近 20 条）；状态徽标：<StatusBadge status="success" />
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
