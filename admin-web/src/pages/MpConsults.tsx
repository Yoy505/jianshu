import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import type { ColumnDef } from '@tanstack/react-table'
import { Eye, Search, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { DataState } from '@/components/common/DataState'
import { PagePagination } from '@/components/common/PagePagination'
import { StatusBadge } from '@/components/common/StatusBadge'
import { toast } from '@/components/common/toast'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCloseMpConsult, useMpConsultDetail, useMpConsults } from '@/hooks/useQueries'
import type { MpConsultFilters } from '@/hooks/useQueries'
import type { MpConsult, MpConsultMessage, MpConsultStatus } from '@/types'
import { cn, formatDateTime } from '@/lib/utils'

const INITIAL_FILTERS: MpConsultFilters = { status: '', q: '', page: 1, page_size: 20 }

const STATUS_OPTIONS: Array<{ value: MpConsultStatus; label: string }> = [
  { value: 'open', label: '进行中' },
  { value: 'closed', label: '已关闭' },
]

/** 截断预览（默认 40 字） */
function preview(text: string | null, max = 40): string {
  if (!text) return '—'
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/* ---------- 消息气泡：user 右侧蓝底 / provider 左侧灰底 / system 居中 ---------- */

function MessageBubble({ message }: { message: MpConsultMessage }) {
  if (message.sender === 'system') {
    return (
      <div className="py-1 text-center">
        <p className="text-xs leading-5 text-muted-foreground">{message.content}</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground/70">
          {formatDateTime(message.created_at)}
        </p>
      </div>
    )
  }
  const isUser = message.sender === 'user'
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn('flex max-w-[75%] flex-col', isUser ? 'items-end' : 'items-start')}>
        <div
          className={cn(
            'whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm leading-6',
            isUser
              ? 'rounded-br-sm bg-primary text-primary-foreground'
              : 'rounded-bl-sm bg-muted text-foreground',
          )}
        >
          {message.content}
        </div>
        <p className="mt-1 text-[10px] text-muted-foreground">
          {isUser ? '用户' : '专业人员'} · {formatDateTime(message.created_at)}
        </p>
      </div>
    </div>
  )
}

/* ---------- 会话详情：消息时间线 + 关闭会话 ---------- */

function ConsultDetailDialog({
  consultId,
  open,
  onOpenChange,
}: {
  consultId: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const detailQuery = useMpConsultDetail(open ? consultId : null)
  const consult = detailQuery.data
  const closeMutation = useCloseMpConsult()
  const [closeOpen, setCloseOpen] = useState(false)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="pr-6">
            {consult ? `${consult.user.nickname} ↔ ${consult.provider.name}` : '会话详情'}
          </DialogTitle>
          {consult ? (
            <DialogDescription>
              {consult.service.name} · 创建于 {formatDateTime(consult.created_at)}
            </DialogDescription>
          ) : (
            <DialogDescription>正在载入会话信息</DialogDescription>
          )}
        </DialogHeader>

        {detailQuery.isError ? (
          <DataState title="会话详情加载失败" description="请检查服务连接后重试。" onRetry={() => void detailQuery.refetch()} />
        ) : detailQuery.isLoading || !consult ? (
          <div className="space-y-3">
            <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
            <Skeleton className="h-10 w-1/2 rounded-2xl" />
            <Skeleton className="ml-auto h-10 w-3/5 rounded-2xl" />
          </div>
        ) : (
          <>
            <div className="min-h-60 flex-1 space-y-3 overflow-y-auto rounded-lg bg-muted/30 p-4">
              {consult.messages.length === 0 ? (
                <p className="py-10 text-center text-sm text-muted-foreground">暂无消息</p>
              ) : (
                consult.messages.map((message) => <MessageBubble key={message.id} message={message} />)
              )}
            </div>
            <DialogFooter className="sm:justify-between">
              <p className="text-xs text-muted-foreground">共 {consult.message_count} 条消息</p>
              {consult.status === 'open' ? (
                <Button
                  variant="outline"
                  className="text-destructive hover:bg-red-50 hover:text-destructive"
                  disabled={closeMutation.isPending}
                  onClick={() => setCloseOpen(true)}
                >
                  <XCircle />
                  关闭会话
                </Button>
              ) : (
                <span className="text-xs text-muted-foreground">会话已关闭，仅可查看</span>
              )}
            </DialogFooter>
          </>
        )}

        <ConfirmDialog
          open={closeOpen}
          onOpenChange={setCloseOpen}
          title="关闭会话"
          description={
            consult
              ? `确定关闭「${consult.user.nickname}」与「${consult.provider.name}」的咨询会话吗？关闭后双方不能再继续沟通。`
              : '确定关闭该咨询会话吗？'
          }
          confirmText="关闭会话"
          destructive
          loading={closeMutation.isPending}
          onConfirm={async () => {
            if (consultId === null) return
            try {
              await closeMutation.mutateAsync(consultId)
              toast.success('会话已关闭')
              setCloseOpen(false)
            } catch {
              /* 错误信息已由 api 拦截器统一 toast */
            }
          }}
        />
      </DialogContent>
    </Dialog>
  )
}

/* ---------- 页面 ---------- */

export default function MpConsults() {
  const [filters, setFilters] = useState<MpConsultFilters>(INITIAL_FILTERS)
  const [keywordInput, setKeywordInput] = useState('')
  const [viewingId, setViewingId] = useState<number | null>(null)

  const { data, isLoading, isError, refetch } = useMpConsults(filters)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((prev) =>
        prev.q === keywordInput ? prev : { ...prev, q: keywordInput, page: 1 },
      )
    }, 400)
    return () => window.clearTimeout(timer)
  }, [keywordInput])

  const columns: Array<ColumnDef<MpConsult>> = [
    {
      accessorKey: 'user',
      header: '用户',
      cell: ({ row }) => (
        <span className="whitespace-nowrap font-medium">{row.original.user.nickname}</span>
      ),
    },
    {
      accessorKey: 'provider',
      header: '专业人员',
      cell: ({ row }) => (
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              row.original.provider.online ? 'bg-emerald-500' : 'bg-zinc-300',
            )}
            title={row.original.provider.online ? '在线' : '离线'}
          />
          <div className="min-w-0">
            <p className="whitespace-nowrap">{row.original.provider.name}</p>
            <p className="text-xs text-muted-foreground">{row.original.provider.title}</p>
          </div>
        </div>
      ),
    },
    {
      accessorKey: 'service',
      header: '服务',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted-foreground">
          {row.original.service.name}
        </span>
      ),
    },
    {
      accessorKey: 'message_count',
      header: '消息数',
      cell: ({ row }) => <span className="tabular-nums">{row.original.message_count}</span>,
    },
    {
      accessorKey: 'last_message',
      header: '最后一条',
      cell: ({ row }) => (
        <span className="text-sm text-muted-foreground">{preview(row.original.last_message)}</span>
      ),
    },
    {
      accessorKey: 'last_message_at',
      header: '最后活跃',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {formatDateTime(row.original.last_message_at)}
        </span>
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
      cell: ({ row }) => (
        <div className="text-right">
          <Button variant="ghost" size="sm" onClick={() => setViewingId(row.original.id)}>
            <Eye />
            查看
          </Button>
        </div>
      ),
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
        <h2 className="font-serif text-2xl font-semibold tracking-tight">在线咨询</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          查看咚咚在线咨询会话与消息记录，必要时关闭违规会话
        </p>
      </div>

      <div className="admin-filter-panel">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
            placeholder="搜索用户昵称 / 人员姓名 / 服务名"
            className="w-64 pl-8"
          />
        </div>
        <Select
          value={filters.status || 'all'}
          onValueChange={(value) =>
            setFilters((prev) => ({
              ...prev,
              status: value === 'all' ? '' : (value as MpConsultStatus),
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
                <TableCell colSpan={columns.length}><DataState title="咨询列表加载失败" description="请检查服务连接后重试。" onRetry={() => void refetch()} /></TableCell>
              </TableRow>
            ) : (data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="暂无咨询会话" description="有实际会话记录后会显示在这里。" /></TableCell>
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

      <ConsultDetailDialog
        consultId={viewingId}
        open={viewingId !== null}
        onOpenChange={(open) => {
          if (!open) setViewingId(null)
        }}
      />
    </div>
  )
}
