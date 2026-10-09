import { Eye, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, type BadgeVariant } from '@/components/ui/badge'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useMpDrugLeafletDetail, useMpDrugLeaflets } from '@/hooks/useQueries'
import type { MpDrugLeafletFilters } from '@/hooks/useQueries'
import type { MpDrugLeafletStatus } from '@/types'
import { formatDateTime } from '@/lib/utils'

const STATUS_OPTIONS: Array<{ value: MpDrugLeafletStatus; label: string; variant: BadgeVariant }> = [
  { value: 'pending', label: '待审核', variant: 'warning' },
  { value: 'approved', label: '已审核', variant: 'info' },
  { value: 'published', label: '已发布', variant: 'success' },
  { value: 'withdrawn', label: '已撤回', variant: 'destructive' },
]

function statusMeta(value: MpDrugLeafletStatus) {
  return STATUS_OPTIONS.find((option) => option.value === value) ?? STATUS_OPTIONS[0]
}

function identityCheckLabel(method: string | null) {
  if (method === 'pypdf_text_match') return 'PDF 文本提取匹配（机器核验）'
  return method || '未记录'
}

function MetadataRow({ label, value, mono = false }: { label: string; value: string | number | null; mono?: boolean }) {
  return (
    <div className="grid gap-1 border-b border-border/70 py-2 last:border-b-0 sm:grid-cols-[8rem_minmax(0,1fr)]">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`min-w-0 break-all text-sm ${mono ? 'font-mono' : ''}`}>{value ?? '—'}</dd>
    </div>
  )
}

function LeafletDetailDialog({ leafletId, onClose }: { leafletId: number | null; onClose: () => void }) {
  const query = useMpDrugLeafletDetail(leafletId)
  const leaflet = query.data
  return (
    <Dialog open={leafletId !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>说明书来源核对</DialogTitle>
          <DialogDescription>仅展示待审元数据；原件文件未开放下载或发布。</DialogDescription>
        </DialogHeader>
        {query.isError ? (
          <DataState title="说明书详情加载失败" description="请检查服务连接后重试。" onRetry={() => void query.refetch()} />
        ) : query.isLoading || !leaflet ? (
          <div className="space-y-3"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-24 w-full" /></div>
        ) : (
          <>
            <Badge variant={statusMeta(leaflet.status).variant}>{statusMeta(leaflet.status).label}</Badge>
            {leaflet.authorization_ref.startsWith('internal-evaluation-only:') && (
              <p className="text-xs text-amber-700">仅供内部核验，此标识不是公开转载授权。</p>
            )}
            <dl className="rounded-lg bg-muted/40 px-4 py-2">
              <MetadataRow label="关联药品" value={`${leaflet.drug_common_name}（ID ${leaflet.drug_id}）`} />
              <MetadataRow label="批准文号" value={leaflet.approval_no} />
              <MetadataRow label="来源单位" value={leaflet.source_name} />
              <MetadataRow label="来源文档编号" value={leaflet.source_document_id} />
              <MetadataRow label="使用依据或内部标识" value={leaflet.authorization_ref} />
              <MetadataRow label="身份校验方式" value={identityCheckLabel(leaflet.identity_check_method)} />
              <MetadataRow label="文档日期" value={leaflet.document_date} />
              <MetadataRow label="文档版本" value={leaflet.document_version} />
              <MetadataRow label="来源核对日期" value={leaflet.source_checked_at} />
              <MetadataRow label="文件大小" value={`${leaflet.file_size.toLocaleString('zh-CN')} 字节`} />
              <MetadataRow label="文件 SHA-256" value={leaflet.file_sha256} mono />
              <MetadataRow label="清单 SHA-256" value={leaflet.manifest_sha256} mono />
              <MetadataRow label="入库时间" value={formatDateTime(leaflet.created_at)} />
              <MetadataRow label="更新时间" value={formatDateTime(leaflet.updated_at)} />
            </dl>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

export default function MpDrugLeaflets() {
  const [filters, setFilters] = useState<MpDrugLeafletFilters>({
    status: 'pending', approval_no: '', page: 1, page_size: 20,
  })
  const [approvalInput, setApprovalInput] = useState('')
  const [viewingId, setViewingId] = useState<number | null>(null)
  const { data, isLoading, isError, isFetching, refetch } = useMpDrugLeaflets(filters)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((previous) =>
        previous.approval_no === approvalInput.trim()
          ? previous
          : { ...previous, approval_no: approvalInput.trim(), page: 1 },
      )
    }, 400)
    return () => window.clearTimeout(timer)
  }, [approvalInput])

  return (
    <section className="flex flex-col gap-4 border-t border-border pt-7" aria-labelledby="drug-leaflet-heading">
      <div>
        <h3 id="drug-leaflet-heading" className="admin-section-title">说明书待审清单</h3>
        <p className="mt-1 text-sm text-muted-foreground">按批准文号核对来源、使用范围和文件哈希。此处仅供后台查看，不代表可以对外展示。</p>
      </div>
      <div className="admin-filter-panel">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={approvalInput}
            onChange={(event) => setApprovalInput(event.target.value)}
            placeholder="精确查询批准文号"
            aria-label="查询说明书批准文号"
            className="w-60 pl-8"
          />
        </div>
        <Select
          value={filters.status || 'all'}
          onValueChange={(value) => setFilters((previous) => ({
            ...previous, status: value === 'all' ? '' : value as MpDrugLeafletStatus, page: 1,
          }))}
        >
          <SelectTrigger className="w-36" aria-label="筛选说明书状态"><SelectValue placeholder="状态" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            {STATUS_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {isFetching && !isLoading && <span className="text-xs text-muted-foreground">正在更新…</span>}
      </div>
      <div className="admin-data-panel">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>关联药品</TableHead>
              <TableHead>来源资料</TableHead>
              <TableHead>文件 SHA-256</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 3 }).map((_, index) => (
                <TableRow key={`leaflet-loading-${index}`}><TableCell colSpan={5}><Skeleton className="h-5 w-full" /></TableCell></TableRow>
              ))
            ) : isError ? (
              <TableRow><TableCell colSpan={5}><DataState title="说明书清单加载失败" description="请检查服务连接后重试。" onRetry={() => void refetch()} /></TableCell></TableRow>
            ) : !data?.items.length ? (
              <TableRow><TableCell colSpan={5}><DataState title="暂无说明书记录" description="当前筛选条件下没有待审元数据。" /></TableCell></TableRow>
            ) : data.items.map((leaflet) => (
              <TableRow key={leaflet.id}>
                <TableCell>
                  <p className="font-medium">{leaflet.drug_common_name}</p>
                  <p className="text-xs text-muted-foreground">{leaflet.approval_no} · 药品 ID {leaflet.drug_id}</p>
                </TableCell>
                <TableCell>
                  <p className="max-w-xs break-words text-sm">{leaflet.source_name}</p>
                  <p className="max-w-xs break-all text-xs text-muted-foreground">{leaflet.source_document_id}</p>
                </TableCell>
                <TableCell className="font-mono text-xs" title={leaflet.file_sha256}>{leaflet.file_sha256.slice(0, 16)}…</TableCell>
                <TableCell><Badge variant={statusMeta(leaflet.status).variant}>{statusMeta(leaflet.status).label}</Badge></TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => setViewingId(leaflet.id)}><Eye />详情</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <PagePagination
        page={filters.page}
        pageSize={filters.page_size}
        total={data?.total ?? 0}
        onChange={(page) => setFilters((previous) => ({ ...previous, page }))}
      />
      <LeafletDetailDialog leafletId={viewingId} onClose={() => setViewingId(null)} />
    </section>
  )
}
