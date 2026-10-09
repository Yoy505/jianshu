import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import type { ColumnDef } from '@tanstack/react-table'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Badge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
import { MpServiceTypeBadge, StatusBadge } from '@/components/common/StatusBadge'
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
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  useCreateMpNotice,
  useDeleteMpNotice,
  useMpNotices,
  useMpProducts,
  useMpServices,
  useUpdateMpService,
} from '@/hooks/useQueries'
import type { MpProductFilters, MpServiceFilters } from '@/hooks/useQueries'
import type {
  MpNotice,
  MpProduct,
  MpProductCategory,
  MpService,
  MpServiceType,
  MpServiceUpdate,
} from '@/types'
import { fenToYuan, formatDateTime } from '@/lib/utils'

type ServicesTab = 'services' | 'products' | 'notices'

/** 九大场景（与 docs/api.md 第二部分 Scenario.key 一致） */
const SCENARIO_OPTIONS = [
  { value: 'checkup', label: '体检慢病' },
  { value: 'consult', label: '看病问诊' },
  { value: 'medication', label: '合理用药' },
  { value: 'rehab', label: '术后康复' },
  { value: 'diet', label: '饮食调理' },
  { value: 'emotion', label: '失眠情绪' },
  { value: 'disable', label: '失能照护' },
  { value: 'escort', label: '就医陪同' },
  { value: 'mother-baby', label: '母婴·老年' },
]

const SERVICE_TYPE_OPTIONS: Array<{ value: MpServiceType; label: string }> = [
  { value: 'online', label: '线上服务' },
  { value: 'offline', label: '线下上门' },
  { value: 'third', label: '三方机构' },
]

const PRODUCT_CATEGORY_OPTIONS: Array<{ value: MpProductCategory; label: string }> = [
  { value: 'food', label: '药食同源' },
  { value: 'fsmp', label: '特医食品' },
  { value: 'package', label: '健康服务包' },
  { value: 'device', label: '器械耗材' },
]

function productCategoryLabel(category: MpProductCategory): string {
  return PRODUCT_CATEGORY_OPTIONS.find((option) => option.value === category)?.label ?? category
}

/* ---------- ① 服务编辑弹窗（价格元输入，提交 ×100 转分） ---------- */

function ServiceEditDialog({
  service,
  open,
  onOpenChange,
}: {
  service: MpService
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState('')
  const [priceYuan, setPriceYuan] = useState('')
  const [durationMin, setDurationMin] = useState('')
  const [unit, setUnit] = useState('')
  const [onShelf, setOnShelf] = useState(true)
  const updateMutation = useUpdateMpService()

  useEffect(() => {
    if (open) {
      setName(service.name)
      setPriceYuan((service.price / 100).toFixed(2))
      setDurationMin(String(service.duration_min))
      setUnit(service.unit)
      setOnShelf(service.on_shelf)
    }
  }, [open, service])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) {
      toast.error('请填写服务名称')
      return
    }
    const priceCents = Math.round(Number(priceYuan) * 100)
    if (priceYuan === '' || Number.isNaN(priceCents) || priceCents < 0) {
      toast.error('请输入正确的价格')
      return
    }
    const parsedDuration = Number(durationMin)
    if (durationMin === '' || !Number.isInteger(parsedDuration) || parsedDuration <= 0) {
      toast.error('时长需为正整数（分钟）')
      return
    }
    const payload: MpServiceUpdate = {
      name: trimmedName,
      price: priceCents,
      duration_min: parsedDuration,
      unit: unit.trim(),
      on_shelf: onShelf,
    }
    updateMutation.mutate(
      { id: service.id, payload },
      {
        onSuccess: () => {
          toast.success('服务已保存')
          onOpenChange(false)
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>编辑服务</DialogTitle>
          <DialogDescription>调整「{service.name}」的价格、时长与上架状态</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="service-name">服务名称</Label>
            <Input
              id="service-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="service-price">价格（元）</Label>
              <Input
                id="service-price"
                type="number"
                min={0}
                step="0.01"
                value={priceYuan}
                onChange={(event) => setPriceYuan(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="service-duration">时长（分钟）</Label>
              <Input
                id="service-duration"
                type="number"
                min={1}
                step={1}
                value={durationMin}
                onChange={(event) => setDurationMin(event.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="service-unit">计价单位</Label>
            <Input
              id="service-unit"
              value={unit}
              onChange={(event) => setUnit(event.target.value)}
              placeholder="如 次 / 次·上门"
            />
          </div>
          <div className="flex items-center justify-between rounded-lg border border-border p-3">
            <div>
              <p className="text-sm font-medium">上架状态</p>
              <p className="text-xs text-muted-foreground">下架后 C 端服务列表不再展示</p>
            </div>
            <Switch checked={onShelf} onCheckedChange={setOnShelf} aria-label="上架开关" />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={updateMutation.isPending} onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={updateMutation.isPending}>
              {updateMutation.isPending ? '保存中…' : '保存'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/* ---------- ③ 公告面板：顶部新增表单 + 列表 ---------- */

function NoticesPanel() {
  const noticesQuery = useMpNotices()
  const [content, setContent] = useState('')
  const [sortInput, setSortInput] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<MpNotice | null>(null)
  const createMutation = useCreateMpNotice()
  const deleteMutation = useDeleteMpNotice()

  function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = content.trim()
    if (!trimmed) {
      toast.error('请填写公告内容')
      return
    }
    let sort: number | undefined
    if (sortInput.trim() !== '') {
      const parsed = Number(sortInput)
      if (!Number.isInteger(parsed) || parsed < 0) {
        toast.error('排序号需为不小于 0 的整数')
        return
      }
      sort = parsed
    }
    createMutation.mutate(
      { content: trimmed, sort },
      {
        onSuccess: () => {
          toast.success('公告已添加')
          setContent('')
          setSortInput('')
        },
      },
    )
  }

  return (
    <div className="space-y-4">
      <form
        onSubmit={handleAdd}
        className="admin-filter-panel"
      >
        <Input
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder="新增首页公告内容，如「杏临千行」公益义诊本周六走进滨海新区"
          className="min-w-64 flex-1"
        />
        <Input
          type="number"
          min={0}
          step={1}
          value={sortInput}
          onChange={(event) => setSortInput(event.target.value)}
          placeholder="排序号"
          className="w-28"
          aria-label="排序号"
        />
        <Button type="submit" disabled={createMutation.isPending}>
          <Plus />
          {createMutation.isPending ? '添加中…' : '添加'}
        </Button>
      </form>

      <div className="admin-data-panel">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-20">排序号</TableHead>
              <TableHead>内容</TableHead>
              <TableHead className="w-40">发布时间</TableHead>
              <TableHead className="w-20 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {noticesQuery.isLoading ? (
              Array.from({ length: 4 }).map((_, rowIndex) => (
                <TableRow key={`skeleton-${rowIndex}`}>
                  <TableCell colSpan={4}>
                    <Skeleton className="h-5 w-full max-w-32" />
                  </TableCell>
                </TableRow>
              ))
            ) : noticesQuery.isError ? (
              <TableRow>
                <TableCell colSpan={4}><DataState title="公告列表加载失败" description="请检查服务连接后重试。" onRetry={() => void noticesQuery.refetch()} /></TableCell>
              </TableRow>
            ) : (noticesQuery.data?.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={4}><DataState title="暂无公告" description="可以使用上方表单添加第一条公告。" /></TableCell>
              </TableRow>
            ) : (
              noticesQuery.data?.map((notice) => (
                <TableRow key={notice.id}>
                  <TableCell>
                    <span className="tabular-nums text-muted-foreground">{notice.sort}</span>
                  </TableCell>
                  <TableCell className="font-medium">{notice.content}</TableCell>
                  <TableCell>
                    <span className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatDateTime(notice.created_at)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:bg-red-50 hover:text-destructive"
                      disabled={deleteMutation.isPending}
                      onClick={() => setDeleteTarget(notice)}
                    >
                      <Trash2 />
                      删除
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        title="删除公告"
        description={deleteTarget ? `确定删除公告「${deleteTarget.content}」吗？` : undefined}
        confirmText="删除"
        destructive
        loading={deleteMutation.isPending}
        onConfirm={async () => {
          if (deleteTarget === null) return
          try {
            await deleteMutation.mutateAsync(deleteTarget.id)
            toast.success('公告已删除')
            setDeleteTarget(null)
          } catch {
            /* 错误信息已由 api 拦截器统一 toast */
          }
        }}
      />
    </div>
  )
}

/* ---------- 页面：服务 / 商城 / 公告 三栏 ---------- */

const TAB_ITEMS: Array<{ key: ServicesTab; label: string }> = [
  { key: 'services', label: '服务' },
  { key: 'products', label: '商城' },
  { key: 'notices', label: '公告' },
]

export default function MpServices() {
  const [tab, setTab] = useState<ServicesTab>('services')
  const [serviceFilters, setServiceFilters] = useState<MpServiceFilters>({
    scenario: '',
    service_type: '',
    page: 1,
    page_size: 20,
  })
  const [productFilters, setProductFilters] = useState<MpProductFilters>({
    category: '',
    page: 1,
    page_size: 20,
  })
  const [editingService, setEditingService] = useState<MpService | null>(null)

  const servicesQuery = useMpServices(serviceFilters)
  const productsQuery = useMpProducts(productFilters)

  const serviceColumns: Array<ColumnDef<MpService>> = [
    {
      accessorKey: 'name',
      header: '服务名',
      cell: ({ row }) => (
        <div className="min-w-0 max-w-64">
          <p className="truncate font-medium">{row.original.name}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{row.original.desc}</p>
        </div>
      ),
    },
    {
      accessorKey: 'scenario_name',
      header: '场景',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted-foreground">{row.original.scenario_name}</span>
      ),
    },
    {
      accessorKey: 'service_type',
      header: '类型',
      cell: ({ row }) => <MpServiceTypeBadge serviceType={row.original.service_type} />,
    },
    {
      accessorKey: 'price',
      header: '价格',
      cell: ({ row }) => (
        <span className="whitespace-nowrap font-medium tabular-nums">
          {fenToYuan(row.original.price)}
          <span className="ml-0.5 text-xs font-normal text-muted-foreground">
            /{row.original.unit}
          </span>
        </span>
      ),
    },
    {
      accessorKey: 'duration_min',
      header: '时长',
      cell: ({ row }) => (
        <span className="whitespace-nowrap tabular-nums">{row.original.duration_min} 分钟</span>
      ),
    },
    {
      accessorKey: 'sales',
      header: '销量',
      cell: ({ row }) => <span className="tabular-nums">{row.original.sales}</span>,
    },
    {
      accessorKey: 'provider_count',
      header: '可匹配人数',
      cell: ({ row }) => <span className="tabular-nums">{row.original.provider_count} 人</span>,
    },
    {
      accessorKey: 'on_shelf',
      header: '上架状态',
      cell: ({ row }) =>
        row.original.on_shelf ? (
          <Badge variant="success">已上架</Badge>
        ) : (
          <Badge variant="secondary">已下架</Badge>
        ),
    },
    {
      id: 'actions',
      header: () => <span className="sr-only">操作</span>,
      cell: ({ row }) => (
        <div className="text-right">
          <Button variant="ghost" size="sm" onClick={() => setEditingService(row.original)}>
            <Pencil />
            编辑
          </Button>
        </div>
      ),
    },
  ]

  const productColumns: Array<ColumnDef<MpProduct>> = [
    {
      accessorKey: 'name',
      header: '商品',
      cell: ({ row }) => (
        <div className="min-w-0 max-w-64">
          <p className="truncate font-medium">{row.original.name}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{row.original.desc}</p>
        </div>
      ),
    },
    {
      accessorKey: 'category',
      header: '分类',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted-foreground">
          {productCategoryLabel(row.original.category)}
        </span>
      ),
    },
    {
      accessorKey: 'price',
      header: '价格',
      cell: ({ row }) => (
        <span className="whitespace-nowrap font-medium tabular-nums">
          {fenToYuan(row.original.price)}
        </span>
      ),
    },
    {
      accessorKey: 'status',
      header: '状态',
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
  ]

  const servicesTable = useReactTable({
    data: servicesQuery.data?.items ?? [],
    columns: serviceColumns,
    getCoreRowModel: getCoreRowModel(),
  })

  const productsTable = useReactTable({
    data: productsQuery.data?.items ?? [],
    columns: productColumns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="admin-page">
      <div className="admin-page-header">
        <h2 className="font-serif text-2xl font-semibold tracking-tight">服务与商城</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          管理健康服务的价格与上架状态、预览商城商品、维护首页公告
        </p>
      </div>

      {/* 页签切换 */}
      <div className="admin-tab-list" aria-label="服务与商城内容">
        {TAB_ITEMS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            aria-pressed={tab === item.key}
            data-active={tab === item.key}
            className="admin-tab"
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'services' ? (
        <>
          <div className="admin-filter-panel">
            <Select
              value={serviceFilters.scenario || 'all'}
              onValueChange={(value) =>
                setServiceFilters((prev) => ({ ...prev, scenario: value === 'all' ? '' : value, page: 1 }))
              }
            >
              <SelectTrigger className="w-36">
                <SelectValue placeholder="全部场景" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部场景</SelectItem>
                {SCENARIO_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={serviceFilters.service_type || 'all'}
              onValueChange={(value) =>
                setServiceFilters((prev) => ({
                  ...prev,
                  service_type: value === 'all' ? '' : (value as MpServiceType),
                  page: 1,
                }))
              }
            >
              <SelectTrigger className="w-32">
                <SelectValue placeholder="全部类型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                {SERVICE_TYPE_OPTIONS.map((option) => (
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
                {servicesTable.getHeaderGroups().map((headerGroup) => (
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
                {servicesQuery.isLoading ? (
                  Array.from({ length: 6 }).map((_, rowIndex) => (
                    <TableRow key={`skeleton-${rowIndex}`}>
                      {servicesTable.getAllLeafColumns().map((column) => (
                        <TableCell key={column.id}>
                          <Skeleton className="h-5 w-full max-w-32" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : servicesQuery.isError ? (
                  <TableRow>
                    <TableCell colSpan={serviceColumns.length}><DataState title="服务列表加载失败" description="请检查服务连接后重试。" onRetry={() => void servicesQuery.refetch()} /></TableCell>
                  </TableRow>
                ) : (servicesQuery.data?.items.length ?? 0) === 0 ? (
                  <TableRow>
                    <TableCell colSpan={serviceColumns.length}><DataState title="暂无服务项目" description="可以调整筛选条件后再查看。" /></TableCell>
                  </TableRow>
                ) : (
                  servicesTable.getRowModel().rows.map((row) => (
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
            page={serviceFilters.page}
            pageSize={serviceFilters.page_size}
            total={servicesQuery.data?.total ?? 0}
            onChange={(page) => setServiceFilters((prev) => ({ ...prev, page }))}
          />
        </>
      ) : tab === 'products' ? (
        <>
          <div className="admin-filter-panel">
            <Select
              value={productFilters.category || 'all'}
              onValueChange={(value) =>
                setProductFilters((prev) => ({
                  ...prev,
                  category: value === 'all' ? '' : (value as MpProductCategory),
                  page: 1,
                }))
              }
            >
              <SelectTrigger className="w-36">
                <SelectValue placeholder="全部分类" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部分类</SelectItem>
                {PRODUCT_CATEGORY_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">
              商城 v1 只读，商品均为「即将开放」状态
            </span>
          </div>

          <div className="admin-data-panel">
            <Table>
              <TableHeader>
                {productsTable.getHeaderGroups().map((headerGroup) => (
                  <TableRow key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <TableHead key={header.id}>
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {productsQuery.isLoading ? (
                  Array.from({ length: 6 }).map((_, rowIndex) => (
                    <TableRow key={`skeleton-${rowIndex}`}>
                      {productsTable.getAllLeafColumns().map((column) => (
                        <TableCell key={column.id}>
                          <Skeleton className="h-5 w-full max-w-32" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : productsQuery.isError ? (
                  <TableRow>
                    <TableCell colSpan={productColumns.length}><DataState title="商品列表加载失败" description="请检查服务连接后重试。" onRetry={() => void productsQuery.refetch()} /></TableCell>
                  </TableRow>
                ) : (productsQuery.data?.items.length ?? 0) === 0 ? (
                  <TableRow>
                    <TableCell colSpan={productColumns.length}><DataState title="暂无商品" description="当前筛选条件下没有商品条目。" /></TableCell>
                  </TableRow>
                ) : (
                  productsTable.getRowModel().rows.map((row) => (
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
            page={productFilters.page}
            pageSize={productFilters.page_size}
            total={productsQuery.data?.total ?? 0}
            onChange={(page) => setProductFilters((prev) => ({ ...prev, page }))}
          />
        </>
      ) : (
        <NoticesPanel />
      )}

      {editingService ? (
        <ServiceEditDialog
          service={editingService}
          open={editingService !== null}
          onOpenChange={(open) => {
            if (!open) setEditingService(null)
          }}
        />
      ) : null}
    </div>
  )
}
