import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import type { ColumnDef } from '@tanstack/react-table'
import { Eye, Search, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, type BadgeVariant } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useDeleteDrugFeedback, useMpDrugDetail, useMpDrugs } from '@/hooks/useQueries'
import type { MpDrugFilters } from '@/hooks/useQueries'
import type { MpDrug, MpDrugFeedback, MpDrugTag } from '@/types'
import { formatDateTime } from '@/lib/utils'
import MpDrugLeaflets from './MpDrugLeaflets'

const INITIAL_FILTERS: MpDrugFilters = { q: '', tag: '', publication_status: '', page: 1, page_size: 20 }

const STATUS_OPTIONS: Array<{
  value: MpDrug['publication_status']
  label: string
  variant: BadgeVariant
}> = [
  { value: 'pending', label: '待审核', variant: 'warning' },
  { value: 'published', label: '已发布', variant: 'success' },
  { value: 'offline', label: '已下架', variant: 'destructive' },
  { value: 'demo', label: '演示数据', variant: 'secondary' },
]

function statusMeta(status: MpDrug['publication_status']) {
  return STATUS_OPTIONS.find((option) => option.value === status) ?? STATUS_OPTIONS[0]
}

function safeHttpsUrl(value: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? value : null
  } catch {
    return null
  }
}

const TAG_OPTIONS: Array<{ value: MpDrugTag; label: string; variant: BadgeVariant }> = [
  { value: 'otc', label: 'OTC', variant: 'success' },
  { value: 'rx', label: '处方药', variant: 'destructive' },
  { value: 'essential', label: '基药', variant: 'info' },
  { value: 'insurance', label: '医保', variant: 'warning' },
]

function tagMeta(tag: MpDrugTag): { label: string; variant: BadgeVariant } {
  return TAG_OPTIONS.find((option) => option.value === tag) ?? { label: tag, variant: 'secondary' }
}

/** 预格式化多行展示的说明区块 */
function InfoBlock({ label, content }: { label: string; content: string | null }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground/90">
        {content || '—'}
      </p>
    </div>
  )
}

/* ---------- 详情弹窗：基本信息 + 留言板（可删违规留言） ---------- */

function DrugDetailDialog({
  drugId,
  open,
  onOpenChange,
}: {
  drugId: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const detailQuery = useMpDrugDetail(open ? drugId : null)
  const drug = detailQuery.data
  const [feedbackTarget, setFeedbackTarget] = useState<MpDrugFeedback | null>(null)
  const deleteMutation = useDeleteDrugFeedback()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="pr-6">{drug?.common_name ?? '药品详情'}</DialogTitle>
          {drug ? (
            <DialogDescription>
              {drug.brand_name ? `${drug.brand_name} · ` : ''}{drug.form} · {drug.manufacturer}
            </DialogDescription>
          ) : (
            <DialogDescription>正在载入药品信息</DialogDescription>
          )}
        </DialogHeader>

        {detailQuery.isError ? (
          <DataState title="药品详情加载失败" description="请检查服务连接后重试。" onRetry={() => void detailQuery.refetch()} />
        ) : detailQuery.isLoading || !drug ? (
          <div className="space-y-3">
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant={statusMeta(drug.publication_status).variant}>
                {statusMeta(drug.publication_status).label}
              </Badge>
              {drug.tags.map((tag) => {
                const meta = tagMeta(tag)
                return (
                  <Badge key={tag} variant={meta.variant}>
                    {meta.label}
                  </Badge>
                )
              })}
            </div>
            <div className="space-y-4 rounded-lg bg-muted/40 p-4">
              <InfoBlock label="批准文号" content={drug.approval_no} />
              <InfoBlock label="规格" content={drug.specification} />
              <InfoBlock label="生产企业" content={drug.manufacturer} />
              {drug.license_holder && <InfoBlock label="上市许可持有人" content={drug.license_holder} />}
              {drug.source_name && <InfoBlock label="资料来源" content={drug.source_name} />}
              {drug.source_checked_at && <InfoBlock label="来源核对时间" content={formatDateTime(drug.source_checked_at)} />}
              {safeHttpsUrl(drug.source_url) && (
                <a className="break-all text-sm text-primary underline" href={safeHttpsUrl(drug.source_url) ?? undefined} target="_blank" rel="noopener noreferrer">
                  查看来源原文
                </a>
              )}
            </div>
            {(drug.indication || drug.usage_note || drug.contraindication) && (
              <div className="space-y-4 rounded-lg bg-muted/40 p-4">
                {drug.publication_status === 'demo' && (
                  <p className="text-xs font-medium text-amber-700">以下为演示内容，未经医学审核</p>
                )}
                {drug.indication && <InfoBlock label="适应症" content={drug.indication} />}
                {drug.usage_note && <InfoBlock label="用法用量" content={drug.usage_note} />}
                {drug.contraindication && <InfoBlock label="禁忌" content={drug.contraindication} />}
              </div>
            )}
            <Separator />
            <div>
              <p className="mb-1 text-sm font-medium">
                留言记录（仅后台查看）
                <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                  （{drug.board.length} 条）
                </span>
              </p>
              {drug.board.length === 0 ? (
                <p className="py-3 text-sm text-muted-foreground">暂无留言</p>
              ) : (
                <div className="divide-y divide-border">
                  {drug.board.map((feedback) => (
                    <div key={feedback.id} className="flex items-start gap-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="text-xs leading-5 text-muted-foreground">
                          <span className="font-medium text-foreground">{feedback.user_name}</span>
                          {' · '}
                          {formatDateTime(feedback.created_at)}
                        </p>
                        <p className="mt-1 text-sm leading-6 text-foreground/80">
                          {feedback.content}
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="shrink-0 text-destructive hover:bg-red-50 hover:text-destructive"
                        disabled={deleteMutation.isPending}
                        onClick={() => setFeedbackTarget(feedback)}
                      >
                        <Trash2 />
                        删除
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        <ConfirmDialog
          open={feedbackTarget !== null}
          onOpenChange={(open) => {
            if (!open) setFeedbackTarget(null)
          }}
          title="删除留言"
          description={feedbackTarget ? '删除该留言？删除后不可恢复。' : undefined}
          confirmText="删除"
          destructive
          loading={deleteMutation.isPending}
          onConfirm={async () => {
            if (feedbackTarget === null || drugId === null) return
            try {
              await deleteMutation.mutateAsync({ drugId, feedbackId: feedbackTarget.id })
              toast.success('留言已删除')
              setFeedbackTarget(null)
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

export default function MpDrugs() {
  const [filters, setFilters] = useState<MpDrugFilters>(INITIAL_FILTERS)
  const [keywordInput, setKeywordInput] = useState('')
  const [viewingId, setViewingId] = useState<number | null>(null)

  const { data, isLoading, isError, refetch } = useMpDrugs(filters)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((prev) =>
        prev.q === keywordInput ? prev : { ...prev, q: keywordInput, page: 1 },
      )
    }, 400)
    return () => window.clearTimeout(timer)
  }, [keywordInput])

  const columns: Array<ColumnDef<MpDrug>> = [
    {
      accessorKey: 'common_name',
      header: '通用名',
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="font-medium">{row.original.common_name}</p>
          {row.original.brand_name && <p className="text-xs text-muted-foreground">商品名：{row.original.brand_name}</p>}
          {row.original.approval_no && <p className="text-xs text-muted-foreground">{row.original.approval_no}</p>}
        </div>
      ),
    },
    {
      accessorKey: 'form',
      header: '剂型',
      cell: ({ row }) => <span className="whitespace-nowrap">{row.original.form}</span>,
    },
    {
      accessorKey: 'manufacturer',
      header: '生产企业',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted-foreground">{row.original.manufacturer}</span>
      ),
    },
    {
      accessorKey: 'tags',
      header: '标签',
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {row.original.tags.map((tag) => {
            const meta = tagMeta(tag)
            return (
              <Badge key={tag} variant={meta.variant}>
                {meta.label}
              </Badge>
            )
          })}
        </div>
      ),
    },
    {
      accessorKey: 'publication_status',
      header: '发布状态',
      cell: ({ row }) => (
        <Badge variant={statusMeta(row.original.publication_status).variant}>
          {statusMeta(row.original.publication_status).label}
        </Badge>
      ),
    },
    {
      id: 'actions',
      header: () => <span className="sr-only">操作</span>,
      cell: ({ row }) => (
        <div className="text-right">
          <Button variant="ghost" size="sm" onClick={() => setViewingId(row.original.id)}>
            <Eye />
            详情
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
        <h2 className="font-serif text-2xl font-semibold tracking-tight">药品展厅</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          查看来源核验和发布状态；待审核资料不会进入小程序公开列表
        </p>
      </div>

      <div className="admin-filter-panel">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
            placeholder="搜索通用名 / 商品名"
            className="w-56 pl-8"
          />
        </div>
        <Select
          value={filters.tag || 'all'}
          onValueChange={(value) =>
            setFilters((prev) => ({
              ...prev,
              tag: value === 'all' ? '' : (value as MpDrugTag),
              page: 1,
            }))
          }
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="标签" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部标签</SelectItem>
            {TAG_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={filters.publication_status || 'all'}
          onValueChange={(value) =>
            setFilters((prev) => ({
              ...prev,
              publication_status: value === 'all' ? '' : (value as MpDrug['publication_status']),
              page: 1,
            }))
          }
        >
          <SelectTrigger className="w-36">
            <SelectValue placeholder="发布状态" />
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
                <TableCell colSpan={columns.length}><DataState title="药品列表加载失败" description="请检查服务连接后重试。" onRetry={() => void refetch()} /></TableCell>
              </TableRow>
            ) : (data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="暂无药品条目" description="可以调整筛选条件后再查看。" /></TableCell>
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

      <DrugDetailDialog
        drugId={viewingId}
        open={viewingId !== null}
        onOpenChange={(open) => {
          if (!open) setViewingId(null)
        }}
      />
      <MpDrugLeaflets />
    </div>
  )
}
