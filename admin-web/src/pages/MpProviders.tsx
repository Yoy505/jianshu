import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import type { ColumnDef } from '@tanstack/react-table'
import { Pencil, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Badge } from '@/components/ui/badge'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
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
import { Textarea } from '@/components/ui/textarea'
import { useMpProviders, useUpdateMpProvider } from '@/hooks/useQueries'
import type { MpProviderFilters } from '@/hooks/useQueries'
import type { MpProvider, MpProviderUpdate } from '@/types'
import { cn } from '@/lib/utils'

const INITIAL_FILTERS: MpProviderFilters = { q: '', online: '', page: 1, page_size: 20 }

/* ---------- 编辑弹窗：PATCH 部分更新（评分/服务次数只读） ---------- */

function ProviderEditDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider: MpProvider
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [dept, setDept] = useState('')
  const [org, setOrg] = useState('')
  const [years, setYears] = useState('')
  const [skillsText, setSkillsText] = useState('')
  const [intro, setIntro] = useState('')
  const [online, setOnline] = useState(false)
  const updateMutation = useUpdateMpProvider()

  useEffect(() => {
    if (open) {
      setName(provider.name)
      setTitle(provider.title)
      setDept(provider.dept)
      setOrg(provider.org)
      setYears(String(provider.years))
      setSkillsText(provider.skills.join('，'))
      setIntro(provider.intro)
      setOnline(provider.online)
    }
  }, [open, provider])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) {
      toast.error('请填写姓名')
      return
    }
    const parsedYears = Number(years)
    if (years === '' || !Number.isInteger(parsedYears) || parsedYears < 0) {
      toast.error('从业年限需为不小于 0 的整数')
      return
    }
    const payload: MpProviderUpdate = {
      name: trimmedName,
      title: title.trim(),
      dept: dept.trim(),
      org: org.trim(),
      years: parsedYears,
      skills: skillsText
        .split(/[,，]/)
        .map((skill) => skill.trim())
        .filter(Boolean),
      intro: intro.trim(),
      online,
    }
    updateMutation.mutate(
      { id: provider.id, payload },
      {
        onSuccess: () => {
          toast.success('人员信息已保存')
          onOpenChange(false)
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑专业人员</DialogTitle>
          <DialogDescription>
            修改「{provider.name}」的资料；评分与服务次数由评价体系维护，不可编辑
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="provider-name">姓名</Label>
              <Input
                id="provider-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="如 陈杏林"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-title">职称</Label>
              <Input
                id="provider-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="如 主任医师"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-dept">科室</Label>
              <Input
                id="provider-dept"
                value={dept}
                onChange={(event) => setDept(event.target.value)}
                placeholder="如 全科医学科"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-org">执业单位</Label>
              <Input
                id="provider-org"
                value={org}
                onChange={(event) => setOrg(event.target.value)}
                placeholder="如 滨海新区社区卫生服务中心"
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="provider-years">从业年限（年）</Label>
            <Input
              id="provider-years"
              type="number"
              min={0}
              step={1}
              value={years}
              onChange={(event) => setYears(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="provider-skills">技能特长（逗号分隔）</Label>
            <Input
              id="provider-skills"
              value={skillsText}
              onChange={(event) => setSkillsText(event.target.value)}
              placeholder="如 高血压管理，慢病随访，家庭医生签约"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="provider-intro">简介</Label>
            <Textarea
              id="provider-intro"
              rows={4}
              value={intro}
              onChange={(event) => setIntro(event.target.value)}
              placeholder="从业人员介绍"
            />
          </div>
          <div className="flex items-center justify-between rounded-lg border border-border p-3">
            <div>
              <p className="text-sm font-medium">在线接单</p>
              <p className="text-xs text-muted-foreground">关闭后 C 端将不再优先展示该人员</p>
            </div>
            <Switch checked={online} onCheckedChange={setOnline} aria-label="在线接单开关" />
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

/* ---------- 页面 ---------- */

export default function MpProviders() {
  const [filters, setFilters] = useState<MpProviderFilters>(INITIAL_FILTERS)
  const [keywordInput, setKeywordInput] = useState('')
  const [editing, setEditing] = useState<MpProvider | null>(null)

  const { data, isLoading, isError, refetch } = useMpProviders(filters)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((prev) =>
        prev.q === keywordInput ? prev : { ...prev, q: keywordInput, page: 1 },
      )
    }, 400)
    return () => window.clearTimeout(timer)
  }, [keywordInput])

  const columns: Array<ColumnDef<MpProvider>> = [
    {
      accessorKey: 'name',
      header: '姓名',
      cell: ({ row }) => (
        <div className="flex items-center gap-2.5">
          {row.original.avatar ? (
            <img
              src={row.original.avatar}
              alt={row.original.name}
              className="size-8 rounded-full object-cover"
            />
          ) : (
            <span className="flex size-8 items-center justify-center rounded-full bg-secondary text-xs font-medium text-secondary-foreground">
              {row.original.name.slice(0, 1)}
            </span>
          )}
          <span className="font-medium">{row.original.name}</span>
        </div>
      ),
    },
    {
      accessorKey: 'title',
      header: '职称',
      cell: ({ row }) => <span className="whitespace-nowrap">{row.original.title}</span>,
    },
    {
      accessorKey: 'dept',
      header: '科室',
      cell: ({ row }) => <span className="whitespace-nowrap">{row.original.dept}</span>,
    },
    {
      accessorKey: 'org',
      header: '执业单位',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted-foreground">{row.original.org}</span>
      ),
    },
    {
      accessorKey: 'years',
      header: '从业年限',
      cell: ({ row }) => <span className="tabular-nums">{row.original.years} 年</span>,
    },
    {
      accessorKey: 'skills',
      header: '技能特长',
      cell: ({ row }) => (
        <div className="flex max-w-56 flex-wrap gap-1">
          {row.original.skills.slice(0, 3).map((skill) => (
            <Badge key={skill} variant="secondary">
              {skill}
            </Badge>
          ))}
          {row.original.skills.length > 3 ? (
            <span className="text-xs leading-5 text-muted-foreground">
              +{row.original.skills.length - 3}
            </span>
          ) : null}
        </div>
      ),
    },
    {
      accessorKey: 'rating',
      header: '评分',
      cell: ({ row }) => (
        <span className="tabular-nums font-medium text-amber-600">
          {row.original.rating.toFixed(1)}
        </span>
      ),
    },
    {
      accessorKey: 'service_count',
      header: '服务次数',
      cell: ({ row }) => <span className="tabular-nums">{row.original.service_count}</span>,
    },
    {
      accessorKey: 'online',
      header: '在线',
      cell: ({ row }) => (
        <span className="flex items-center gap-1.5 whitespace-nowrap">
          <span
            className={cn(
              'size-1.5 rounded-full',
              row.original.online ? 'bg-emerald-500' : 'bg-zinc-300',
            )}
          />
          <span className={row.original.online ? '' : 'text-muted-foreground'}>
            {row.original.online ? '在线' : '离线'}
          </span>
        </span>
      ),
    },
    {
      id: 'actions',
      header: () => <span className="sr-only">操作</span>,
      cell: ({ row }) => (
        <div className="text-right">
          <Button variant="ghost" size="sm" onClick={() => setEditing(row.original)}>
            <Pencil />
            编辑
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
        <h2 className="font-serif text-2xl font-semibold tracking-tight">专业人员</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          维护医生、药师、护师与康复师等专业人员的展示资料
        </p>
      </div>

      <div className="admin-filter-panel">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
            placeholder="搜索姓名 / 科室 / 单位"
            className="w-56 pl-8"
          />
        </div>
        <Select
          value={filters.online === '' ? 'all' : String(filters.online)}
          onValueChange={(value) =>
            setFilters((prev) => ({
              ...prev,
              online: value === 'all' ? '' : value === 'true',
              page: 1,
            }))
          }
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="在线状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="true">在线</SelectItem>
            <SelectItem value="false">离线</SelectItem>
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
                <TableCell colSpan={columns.length}><DataState title="人员资料加载失败" description="请检查服务连接后重试。" onRetry={() => void refetch()} /></TableCell>
              </TableRow>
            ) : (data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length}><DataState title="暂无专业人员" description="当前筛选条件下没有资料条目。" /></TableCell>
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

      {editing ? (
        <ProviderEditDialog
          provider={editing}
          open={editing !== null}
          onOpenChange={(open) => {
            if (!open) setEditing(null)
          }}
        />
      ) : null}
    </div>
  )
}
