import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Search } from 'lucide-react'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
import { toast } from '@/components/common/toast'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

type ApplicationStatus = 'pending' | 'contacted' | 'enrolled'
type TrackKey = 'practice' | 'publicity' | 'assist' | 'promotion' | 'other'

interface ApplicationItem {
  id: number
  receipt_no: string
  name: string
  phone_masked: string
  city: string
  track: TrackKey
  offline_per_year: number
  can_station: boolean
  status: ApplicationStatus
  submitted_at: string
}

interface ApplicationDetail extends ApplicationItem {
  phone: string
  track_note: string | null
  birth_year: number | null
  occupation: string | null
  organization: string | null
  intro: string | null
  consent_text: string
  consented_at: string
}

interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

const STATUS_LABELS: Record<ApplicationStatus, string> = {
  pending: '新报名',
  contacted: '已联系',
  enrolled: '已录入',
}

const STATUS_STYLE: Record<ApplicationStatus, string> = {
  pending: 'border-[#eddfbf] bg-[#fcf6e9] text-[#8b6628]',
  contacted: 'border-[#cedee6] bg-[#eff5f8] text-[#316b86]',
  enrolled: 'border-[#cae3d5] bg-[#edf6ef] text-[#27724f]',
}

const TRACK_LABELS: Record<TrackKey, string> = {
  practice: '公益实践',
  publicity: '科普宣传',
  assist: '活动协助',
  promotion: '宣传支持',
  other: '其他',
}

// 与报名端 OFFLINE_OPTIONS 保持一致；2 和 4 表示区间，不是精确次数。
const FREQUENCY_LABELS: Record<number, string> = {
  1: '每年 1 次',
  2: '每年 2-3 次',
  4: '每年 4 次及以上',
}

function frequencyLabel(value: number) {
  return FREQUENCY_LABELS[value] ?? `每年 ${value} 次`
}

function Status({ value }: { value: ApplicationStatus }) {
  return <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium leading-none before:size-1.5 before:rounded-full before:bg-current ${STATUS_STYLE[value]}`}>{STATUS_LABELS[value]}</span>
}

export default function VolunteerApplications() {
  const admin = useAuthStore((state) => state.admin)
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [searchText, setSearchText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const pageSize = 20

  const canRead = can(admin?.role, 'volunteer.read')

  const listQuery = useQuery({
    queryKey: ['volunteer-applications', page, status, keyword],
    queryFn: async () => {
      const { data } = await api.get<Page<ApplicationItem>>('/admin/volunteer-applications', {
        params: {
          page,
          page_size: pageSize,
          status: status || undefined,
          q: keyword || undefined,
        },
      })
      return data
    },
    enabled: canRead,
  })

  const detailQuery = useQuery({
    queryKey: ['volunteer-application', selectedId],
    queryFn: async () => {
      const { data } = await api.get<ApplicationDetail>(`/admin/volunteer-applications/${selectedId}`)
      return data
    },
    enabled: canRead && selectedId !== null,
  })

  const statusMutation = useMutation({
    mutationFn: async (payload: { status: ApplicationStatus }) => {
      if (selectedId === null) throw new Error('未选择报名记录')
      const { data } = await api.post<ApplicationDetail>(
        `/admin/volunteer-applications/${selectedId}/status`,
        payload,
      )
      return data
    },
    onSuccess: async () => {
      toast.success('状态已更新')
      await queryClient.invalidateQueries({ queryKey: ['volunteer-applications'] })
      await queryClient.invalidateQueries({ queryKey: ['volunteer-application', selectedId] })
    },
  })

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPage(1)
    setKeyword(searchText.trim())
  }

  useEffect(() => {
    setPage(1)
  }, [status])

  if (!canRead) {
    return <p className="admin-state rounded-xl border border-border bg-card">当前账号没有查看志愿者报名的权限。</p>
  }

  const detail = detailQuery.data

  return (
    <div className="admin-page">
      <div className="admin-page-header">
        <h2 className="text-lg font-semibold">杏临千行 · 志愿者报名</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          公开填报的志愿者信息（姓名/电话/城市/服务意向）。联系电话为报名人自行填写，未经身份核验，仅用于活动联系。
        </p>
      </div>

      <div className="admin-filter-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            <span>处理状态</span>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="block h-9 min-w-32 rounded-md border border-input bg-background px-3 text-sm text-foreground"
            >
              <option value="">全部</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <form onSubmit={submitSearch} className="flex flex-1 items-end gap-2">
            <label className="min-w-40 flex-1 space-y-1 text-xs text-muted-foreground">
              <span>搜索姓名、电话、城市或回执号</span>
              <Input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="输入关键词" />
            </label>
            <Button type="submit" variant="outline"><Search />查询</Button>
          </form>
        </div>
      </div>

      <div className="admin-data-panel">
        {listQuery.isLoading ? (
          <div className="space-y-3 p-5"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /></div>
        ) : listQuery.isError ? (
          <DataState title="报名列表加载失败" description="请检查服务连接后重试。" onRetry={() => void listQuery.refetch()} />
        ) : listQuery.data?.items.length === 0 ? (
          <DataState title="暂无报名" description="当前筛选条件下没有志愿者报名记录。" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader><TableRow>
                <TableHead>回执号</TableHead><TableHead>姓名</TableHead><TableHead>城市</TableHead><TableHead>意向方向</TableHead><TableHead>线下/驻点</TableHead><TableHead>状态</TableHead><TableHead>报名时间</TableHead><TableHead className="text-right">操作</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {listQuery.data?.items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="font-mono text-xs">{item.receipt_no}</TableCell>
                    <TableCell><p className="font-medium">{item.name}</p><p className="mt-1 text-xs text-muted-foreground">{item.phone_masked}</p></TableCell>
                    <TableCell>{item.city}</TableCell>
                    <TableCell>{TRACK_LABELS[item.track]}</TableCell>
                    <TableCell className="text-xs">{frequencyLabel(item.offline_per_year)} · {item.can_station ? '可驻点' : '不驻点'}</TableCell>
                    <TableCell><Status value={item.status} /></TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(item.submitted_at)}</TableCell>
                    <TableCell className="text-right"><Button variant="outline" size="sm" onClick={() => setSelectedId(item.id)}>查看与管理</Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        <PagePagination
          page={page}
          pageSize={pageSize}
          total={listQuery.data?.total ?? 0}
          onChange={setPage}
          className="border-t px-4 py-3"
        />
      </div>

      <Dialog open={selectedId !== null} onOpenChange={(open) => { if (!open) setSelectedId(null) }}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>志愿者报名详情</DialogTitle>
            <DialogDescription>个人信息仅用于本次招募报名与活动联系。</DialogDescription>
          </DialogHeader>
          {detailQuery.isLoading ? <Skeleton className="h-60 w-full" /> : detailQuery.isError ? (
            <DataState title="详情加载失败" description="请检查服务连接后重试。" onRetry={() => void detailQuery.refetch()} />
          ) : detail ? (
            <div className="space-y-5 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <Status value={detail.status} />
                <span className="font-mono text-xs text-muted-foreground">{detail.receipt_no}</span>
              </div>
              <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 rounded-md bg-muted/50 p-4">
                <dt className="text-muted-foreground">姓名</dt><dd>{detail.name}</dd>
                <dt className="text-muted-foreground">联系电话</dt><dd>{detail.phone}</dd>
                <dt className="text-muted-foreground">出生年份</dt><dd>{detail.birth_year ?? '—'}</dd>
                <dt className="text-muted-foreground">所在城市</dt><dd>{detail.city}</dd>
                <dt className="text-muted-foreground">职业 / 单位</dt><dd>{detail.occupation || '—'} / {detail.organization || '—'}</dd>
                <dt className="text-muted-foreground">意向方向</dt><dd>{TRACK_LABELS[detail.track]}{detail.track_note ? ` · ${detail.track_note}` : ''}</dd>
                <dt className="text-muted-foreground">参与能力</dt><dd>{frequencyLabel(detail.offline_per_year)}线下 · {detail.can_station ? '可承担 3-5 天驻点' : '暂不能驻点'}</dd>
                <dt className="text-muted-foreground">报名时间</dt><dd>{formatDateTime(detail.submitted_at)}</dd>
                <dt className="text-muted-foreground">信息同意</dt><dd>已确认 · {formatDateTime(detail.consented_at)}<p className="mt-1 text-xs text-muted-foreground">{detail.consent_text}</p></dd>
              </dl>
              {detail.intro && (
                <div><p className="font-medium">个人简介 / 特长</p><p className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">{detail.intro}</p></div>
              )}
              <div className="space-y-3 border-t pt-4">
                <p className="font-medium">处理状态</p>
                <div className="flex flex-wrap gap-2">
                  {(['pending', 'contacted', 'enrolled'] as const).map((next) => (
                    <Button
                      key={next}
                      type="button"
                      variant={detail.status === next ? 'default' : 'outline'}
                      size="sm"
                      disabled={statusMutation.isPending || detail.status === next}
                      onClick={() => statusMutation.mutate({ status: next })}
                    >
                      {statusMutation.isPending && <Loader2 className="animate-spin" />}
                      标记为{STATUS_LABELS[next]}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
