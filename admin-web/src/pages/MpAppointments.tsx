import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import type { ColumnDef } from '@tanstack/react-table'
import { Check, CheckCircle2, Search, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DataState } from '@/components/common/DataState'
import { PagePagination } from '@/components/common/PagePagination'
import { MpServiceTypeBadge, StatusBadge } from '@/components/common/StatusBadge'
import { toast } from '@/components/common/toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  useCancelMpAppointment,
  useCompleteMpAppointment,
  useConfirmMpAppointment,
  useMpAppointments,
  useMpStats,
} from '@/hooks/useQueries'
import type { MpAppointmentFilters } from '@/hooks/useQueries'
import type { MpAppointment, MpAppointmentStatus } from '@/types'
import { cn } from '@/lib/utils'

const INITIAL_FILTERS: MpAppointmentFilters = { status: '', date: '', q: '', page: 1, page_size: 20 }

const STATUS_OPTIONS: Array<{ value: MpAppointmentStatus; label: string }> = [
  { value: 'pending', label: '待确认' },
  { value: 'confirmed', label: '已确认' },
  { value: 'completed', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

/** 顶部统计卡（点击即按状态筛选；「全部」清除筛选） */
const STAT_CARDS: Array<{
  key: 'pending' | 'confirmed' | 'completed' | 'cancelled' | 'total'
  label: string
  status: MpAppointmentStatus | ''
}> = [
  { key: 'pending', label: '待确认', status: 'pending' },
  { key: 'confirmed', label: '已确认', status: 'confirmed' },
  { key: 'completed', label: '已完成', status: 'completed' },
  { key: 'cancelled', label: '已取消', status: 'cancelled' },
  { key: 'total', label: '全部', status: '' },
]

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** "2026-09-24" → "2026-09-24（周四）"，按本地时区安全解析避免日期偏移 */
function formatApptDate(date: string): string {
  const [year, month, day] = date.split('-').map(Number)
  if (!year || !month || !day) return date
  return `${date}（${WEEKDAYS[new Date(year, month - 1, day).getDay()]}）`
}

/* ---------- 行操作：待确认→确认/取消；已确认→完成/取消 ---------- */

function AppointmentRowActions({ appointment }: { appointment: MpAppointment }) {
  const [cancelOpen, setCancelOpen] = useState(false)
  const [decision, setDecision] = useState<'confirm' | 'complete' | null>(null)
  const confirmMutation = useConfirmMpAppointment()
  const completeMutation = useCompleteMpAppointment()
  const cancelMutation = useCancelMpAppointment()
  const actionPending =
    confirmMutation.isPending || completeMutation.isPending || cancelMutation.isPending

  if (appointment.status !== 'pending' && appointment.status !== 'confirmed') {
    return <span className="text-right text-muted-foreground">—</span>
  }

  return (
    <div className="flex justify-end gap-1">
      {appointment.status === 'pending' ? (
        <Button
          variant="ghost"
          size="sm"
          disabled={actionPending}
          onClick={() => setDecision('confirm')}
        >
          <Check />
          确认
        </Button>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          disabled={actionPending}
          onClick={() => setDecision('complete')}
        >
          <CheckCircle2 />
          标记完成
        </Button>
      )}
      <Button
        variant="ghost"
        size="sm"
        className="text-destructive hover:bg-red-50 hover:text-destructive"
        disabled={actionPending}
        onClick={() => setCancelOpen(true)}
      >
        <XCircle />
        取消
      </Button>
      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="取消预约"
        description={`确定取消「${appointment.service.name}」这单预约吗？联系人：${appointment.contact_name}（${appointment.contact_phone}）。`}
        confirmText="取消预约"
        destructive
        loading={cancelMutation.isPending}
        onConfirm={async () => {
          try {
            await cancelMutation.mutateAsync(appointment.id)
            toast.success('预约已取消')
            setCancelOpen(false)
          } catch {
            /* 错误信息已由 api 拦截器统一 toast */
          }
        }}
      />
      <ConfirmDialog
        open={decision !== null}
        onOpenChange={(open) => { if (!open) setDecision(null) }}
        title={decision === 'complete' ? '将预约标记为已完成' : '确认预约'}
        description={decision === 'complete'
          ? `请先核对「${appointment.service.name}」的服务交付记录。此操作仅更新平台预约状态，实际交付以核对记录为准。`
          : `请先核对「${appointment.service.name}」的预约安排。此操作将平台预约状态更新为“已确认”。`}
        confirmText={decision === 'complete' ? '标记已完成' : '确认预约'}
        loading={confirmMutation.isPending || completeMutation.isPending}
        onConfirm={async () => {
          try {
            if (decision === 'complete') {
              await completeMutation.mutateAsync(appointment.id)
              toast.success('平台预约状态已更新为“已完成”')
            } else if (decision === 'confirm') {
              await confirmMutation.mutateAsync(appointment.id)
              toast.success('预约已确认')
            }
            setDecision(null)
          } catch {
            /* 错误提示由 API 拦截器显示。 */
          }
        }}
      />
    </div>
  )
}

/* ---------- 页面 ---------- */

export default function MpAppointments() {
  const [filters, setFilters] = useState<MpAppointmentFilters>(INITIAL_FILTERS)
  const [keywordInput, setKeywordInput] = useState('')

  const statsQuery = useMpStats()
  const { data, isLoading, isError, refetch } = useMpAppointments(filters)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((prev) =>
        prev.q === keywordInput ? prev : { ...prev, q: keywordInput, page: 1 },
      )
    }, 400)
    return () => window.clearTimeout(timer)
  }, [keywordInput])

  const columns: Array<ColumnDef<MpAppointment>> = [
    {
      accessorKey: 'appt_date',
      header: '预约日期',
      cell: ({ row }) => (
        <span className="whitespace-nowrap tabular-nums">
          {formatApptDate(row.original.appt_date)}
        </span>
      ),
    },
    {
      accessorKey: 'period',
      header: '时段',
      cell: ({ row }) => <span className="whitespace-nowrap">{row.original.period}</span>,
    },
    {
      accessorKey: 'service',
      header: '服务',
      cell: ({ row }) => (
        <div className="flex items-center gap-2">
          <span className="whitespace-nowrap font-medium">{row.original.service.name}</span>
          <MpServiceTypeBadge serviceType={row.original.service.service_type} />
        </div>
      ),
    },
    {
      accessorKey: 'provider',
      header: '专业人员',
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="whitespace-nowrap">{row.original.provider.name}</p>
          <p className="text-xs text-muted-foreground">{row.original.provider.title}</p>
        </div>
      ),
    },
    {
      accessorKey: 'user',
      header: '用户',
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="whitespace-nowrap font-medium">{row.original.user.nickname}</p>
          <p className="text-xs tabular-nums text-muted-foreground">{row.original.user.phone}</p>
        </div>
      ),
    },
    {
      accessorKey: 'contact_name',
      header: '联系人',
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="whitespace-nowrap">{row.original.contact_name}</p>
          <p className="text-xs tabular-nums text-muted-foreground">
            {row.original.contact_phone}
          </p>
        </div>
      ),
    },
    {
      accessorKey: 'status',
      header: '状态',
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      id: 'actions',
      header: () => <span className="sr-only">操作</span>,
      cell: ({ row }) => <AppointmentRowActions appointment={row.original} />,
    },
  ]

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="admin-page">
      <div className="admin-page-header">
        <h2 className="font-serif text-2xl font-semibold tracking-tight">健康预约</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          管理杏临汇用户的健康服务预约。列表状态以平台预约记录为准，标记完成前请核对服务交付记录。
        </p>
      </div>

      {/* 统计卡：点击切换状态筛选 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        {STAT_CARDS.map((card) => {
          const active = filters.status === card.status
          return (
            <button
              key={card.key}
              type="button"
              onClick={() => setFilters((prev) => ({ ...prev, status: card.status, page: 1 }))}
              className={cn(
                'admin-kpi',
                active && 'border-primary bg-[#f4f9f6]',
              )}
            >
              <p className="text-xs text-muted-foreground">{card.label}</p>
              {statsQuery.isLoading ? (
                <Skeleton className="mt-1.5 h-7 w-12" />
              ) : (
                <p className="admin-kpi-value">
                  {statsQuery.data?.appointments[card.key] ?? '—'}
                </p>
              )}
            </button>
          )
        })}
      </div>

      <div className="admin-filter-panel">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
            placeholder="搜索联系人 / 电话 / 人员 / 服务"
            className="w-64 pl-8"
          />
        </div>
        <Select
          value={filters.status || 'all'}
          onValueChange={(value) =>
            setFilters((prev) => ({
              ...prev,
              status: value === 'all' ? '' : (value as MpAppointmentStatus),
              page: 1,
            }))
          }
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            {STATUS_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          type="date"
          value={filters.date}
          onChange={(event) =>
            setFilters((prev) => ({ ...prev, date: event.target.value, page: 1 }))
          }
          className="w-40"
          aria-label="按预约日期筛选"
        />
        {filters.date ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setFilters((prev) => ({ ...prev, date: '', page: 1 }))}
          >
            清除日期
          </Button>
        ) : null}
      </div>

      <div className="admin-data-panel">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id} className="last:text-right">
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 6 }).map((_, rowIndex) => (
                <TableRow key={`skeleton-${rowIndex}`}>
                  {table.getAllLeafColumns().map((column) => (
                    <TableCell key={column.id}>
                      <Skeleton className="h-5 w-full max-w-32" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="预约列表加载失败" description="请检查服务连接后重试。" onRetry={() => void refetch()} /></TableCell>
              </TableRow>
            ) : (data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="暂无预约" description="可以调整筛选条件后再查看。" /></TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <PagePagination
        page={filters.page}
        pageSize={filters.page_size}
        total={data?.total ?? 0}
        onChange={(page) => setFilters((prev) => ({ ...prev, page }))}
      />
    </div>
  )
}
