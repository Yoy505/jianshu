import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import type { ColumnDef } from '@tanstack/react-table'
import { useState } from 'react'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuditLogs } from '@/hooks/useQueries'
import type { AuditLogFilters } from '@/hooks/useQueries'
import type { AuditLog } from '@/types'
import { formatDateTime } from '@/lib/utils'

const INITIAL_FILTERS: AuditLogFilters = { action: '', resource_type: '', page: 1, page_size: 20 }

/** 当前运营流程的审计操作筛选；历史日志记录仍可通过“全部操作”查看。 */
const ACTION_OPTIONS = [
  { value: 'admin.login', label: '登录' },
  { value: 'occupational.detail_read', label: '查看职业健康投稿' },
  { value: 'occupational.file_download', label: '下载职业健康作品' },
  { value: 'occupational.review', label: '审核职业健康投稿' },
]

const RESOURCE_OPTIONS = [
  { value: 'admin_user', label: '后台账号' },
  { value: 'occupational_submission', label: '职业健康投稿' },
]

/** 变更要点：JSON 压缩成单行展示，超长截断 */
function detailText(detail: Record<string, unknown> | null): string {
  if (!detail) return '—'
  const text = Object.entries(detail)
    .map(([key, value]) => `${key}=${value === null || value === undefined ? 'null' : String(value)}`)
    .join(' · ')
  return text.length > 60 ? `${text.slice(0, 60)}…` : text || '—'
}

export default function AuditLogs() {
  const [filters, setFilters] = useState<AuditLogFilters>(INITIAL_FILTERS)
  const { data, isLoading, isError, refetch } = useAuditLogs(filters)

  const columns: Array<ColumnDef<AuditLog>> = [
    {
      accessorKey: 'created_at',
      header: '时间',
      cell: ({ row }) => (
        <span className="whitespace-nowrap tabular-nums text-muted-foreground">
          {formatDateTime(row.original.created_at)}
        </span>
      ),
    },
    {
      accessorKey: 'actor_name',
      header: '操作者',
      cell: ({ row }) => (
        <div className="flex flex-col">
          <span className="font-medium">{row.original.actor_name || '系统'}</span>
          <span className="text-xs text-muted-foreground">
            {row.original.actor_type}
            {row.original.actor_id !== null ? ` #${row.original.actor_id}` : ''}
          </span>
        </div>
      ),
    },
    {
      accessorKey: 'action',
      header: '操作',
      cell: ({ row }) => (
        <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{row.original.action}</code>
      ),
    },
    {
      accessorKey: 'resource_type',
      header: '对象',
      cell: ({ row }) => (
        <span className="whitespace-nowrap">
          {row.original.resource_type}
          {row.original.resource_id !== null && (
            <span className="text-muted-foreground"> #{row.original.resource_id}</span>
          )}
        </span>
      ),
    },
    {
      accessorKey: 'detail',
      header: '变更要点',
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">{detailText(row.original.detail)}</span>
      ),
    },
    {
      accessorKey: 'ip_address',
      header: 'IP',
      cell: ({ row }) => (
        <span className="whitespace-nowrap tabular-nums text-muted-foreground">
          {row.original.ip_address || '—'}
        </span>
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
        <h2 className="text-xl font-semibold">审计日志</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          后台命令操作的只追加留痕，与业务同事务写入
        </p>
      </div>

      <div className="admin-filter-panel">
        <Select
          value={filters.action || 'all'}
          onValueChange={(value) =>
            setFilters((prev) => ({ ...prev, action: value === 'all' ? '' : value, page: 1 }))
          }
        >
          <SelectTrigger className="w-44">
            <SelectValue placeholder="全部操作" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部操作</SelectItem>
            {ACTION_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filters.resource_type || 'all'}
          onValueChange={(value) =>
            setFilters((prev) => ({
              ...prev,
              resource_type: value === 'all' ? '' : value,
              page: 1,
            }))
          }
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="全部对象" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部对象</SelectItem>
            {RESOURCE_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {(filters.action || filters.resource_type) && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setFilters({ ...INITIAL_FILTERS })}
          >
            重置筛选
          </Button>
        )}

        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          刷新
        </Button>
      </div>

      <div className="admin-data-panel">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id}>
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 8 }, (_, i) => (
                <TableRow key={i}>
                  {columns.map((_column, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-5 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : isError ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="审计日志加载失败" description="请检查服务连接后重试。" onRetry={() => void refetch()} /></TableCell>
              </TableRow>
            ) : (data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="暂无审计记录" description="后台操作发生后，相关记录会显示在这里。" /></TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
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
        onChange={(page: number) => setFilters((prev) => ({ ...prev, page }))}
      />
    </div>
  )
}
