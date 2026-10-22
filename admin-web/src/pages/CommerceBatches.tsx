import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus, Search } from 'lucide-react'
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

interface OrgBrief { id: number; name: string }
interface OrgItem {
  id: number; name: string; contact_name: string | null; contact_phone: string | null
  note: string | null; status: string; account_count: number; created_at: string
}
interface PackageListItem {
  id: number; name: string; status: string
  current_version: { id: number; version_no: number } | null
  versions?: { id: number; version_no: number }[]
}
interface BatchRow { row_no: number; phone: string; status: string; error_detail: string | null }
interface BatchItem {
  id: number; batch_no: string; org: OrgBrief
  package: { id: number; name: string; version_no: number }
  quantity: number; file_name: string; file_sha256: string; status: string
  summary: { total: number; valid: number; duplicate: number; format_error: number; over_limit: number }
  issued_count: number; claimed_count: number; rejected_count: number; pending_count: number
  uploaded_by: string | null; confirmed_by: string | null; confirmed_at: string | null; created_at: string
}
interface BatchDetail extends BatchItem { rows: BatchRow[] }
interface EntitlementItem {
  id: number; source_type: string; status: string
  package: { id: number; name: string; version_no: number }
  phone: string | null; user: { id: number; nickname: string; phone: string | null } | null
  batch_id: number | null; valid_until: string | null; void_reason: string | null
  created_at: string
}
interface Page<T> { items: T[]; total: number; page: number; page_size: number }

type Tab = 'orgs' | 'batches' | 'entitlements'

export default function CommerceBatches() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canManage = can(admin?.role, 'commerce.batch.manage')
  const canConfirm = can(admin?.role, 'commerce.batch.confirm')

  const [tab, setTab] = useState<Tab>('batches')
  const [page, setPage] = useState(1)
  const [orgFilter, setOrgFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [batchId, setBatchId] = useState<number | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [orgCreateOpen, setOrgCreateOpen] = useState(false)
  const [entAction, setEntAction] = useState<{ ent: EntitlementItem; kind: 'void' | 'reassign' } | null>(null)
  const pageSize = 20

  const orgsQuery = useQuery({
    queryKey: ['commerce-orgs'],
    queryFn: async () => {
      const { data } = await api.get<Page<OrgItem>>('/admin/mp/orgs', { params: { page_size: 100 } })
      return data
    },
    enabled: canManage,
  })

  const packagesQuery = useQuery({
    queryKey: ['commerce-packages-for-batch'],
    queryFn: async () => {
      const { data } = await api.get<Page<PackageListItem>>('/admin/mp/packages', { params: { page_size: 100 } })
      return data.items
    },
    enabled: canManage,
  })

  const batchesQuery = useQuery({
    queryKey: ['commerce-batches', page, orgFilter, statusFilter],
    queryFn: async () => {
      const { data } = await api.get<Page<BatchItem>>('/admin/mp/batches', {
        params: {
          page, page_size: pageSize,
          org_id: orgFilter || undefined, status: statusFilter || undefined,
        },
      })
      return data
    },
    enabled: canManage && tab === 'batches',
  })

  const batchDetailQuery = useQuery({
    queryKey: ['commerce-batch', batchId],
    queryFn: async () => {
      const { data } = await api.get<BatchDetail>(`/admin/mp/batches/${batchId}`)
      return data
    },
    enabled: canManage && batchId !== null,
  })

  const entQuery = useQuery({
    queryKey: ['commerce-entitlements', page, keyword],
    queryFn: async () => {
      const { data } = await api.get<Page<EntitlementItem>>('/admin/mp/entitlements', {
        params: { page, page_size: pageSize, q: keyword || undefined },
      })
      return data
    },
    enabled: canManage && tab === 'entitlements',
  })

  const previewMutation = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await api.post('/admin/mp/batches/preview', payload)
      return data as { batch: BatchItem; rows: BatchRow[] }
    },
    onSuccess: async (data) => {
      toast.success('预览完成；确认后才会发放权益')
      setPreviewOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['commerce-batches'] })
      setBatchId(data.batch.id)
    },
  })

  const confirmMutation = useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<BatchItem>(`/admin/mp/batches/${id}/confirm`)
      return data
    },
    onSuccess: async () => {
      toast.success('批次已确认，权益已按有效行发放')
      await queryClient.invalidateQueries({ queryKey: ['commerce-batches'] })
      await queryClient.invalidateQueries({ queryKey: ['commerce-batch', batchId] })
    },
  })

  const cancelMutation = useMutation({
    mutationFn: async (id: number) => {
      const { data } = await api.post<BatchItem>(`/admin/mp/batches/${id}/cancel`)
      return data
    },
    onSuccess: async () => {
      toast.success('批次已取消')
      await queryClient.invalidateQueries({ queryKey: ['commerce-batches'] })
      await queryClient.invalidateQueries({ queryKey: ['commerce-batch', batchId] })
    },
  })

  const orgMutation = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await api.post<OrgItem>('/admin/mp/orgs', payload)
      return data
    },
    onSuccess: async () => {
      toast.success('单位已创建')
      setOrgCreateOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['commerce-orgs'] })
    },
  })

  const accountMutation = useMutation({
    mutationFn: async (payload: { orgId: number; username: string; password: string }) => {
      const { orgId, ...body } = payload
      const { data } = await api.post(`/admin/mp/orgs/${orgId}/accounts`, body)
      return data
    },
    onSuccess: async () => {
      toast.success('单位查看账号已创建；账号只读批次汇总，看不到个人信息')
      await queryClient.invalidateQueries({ queryKey: ['commerce-orgs'] })
    },
  })

  const voidMutation = useMutation({
    mutationFn: async (payload: { entId: number; reason: string }) => {
      const { entId, ...body } = payload
      const { data } = await api.post(`/admin/mp/entitlements/${entId}/void`, body)
      return data
    },
    onSuccess: async () => {
      toast.success('权益已作废（留痕）')
      setEntAction(null)
      await queryClient.invalidateQueries({ queryKey: ['commerce-entitlements'] })
    },
  })

  const reassignMutation = useMutation({
    mutationFn: async (payload: { entId: number; phone: string; reason: string }) => {
      const { entId, ...body } = payload
      const { data } = await api.post(`/admin/mp/entitlements/${entId}/reassign`, body)
      return data
    },
    onSuccess: async () => {
      toast.success('权益手机号已改派')
      setEntAction(null)
      await queryClient.invalidateQueries({ queryKey: ['commerce-entitlements'] })
    },
  })

  useEffect(() => { setPage(1) }, [tab, orgFilter, statusFilter])

  if (!canManage) {
    return <p className="admin-state rounded-xl border border-border bg-card">当前账号没有采购批次管理权限。</p>
  }

  const batchDetail = batchDetailQuery.data
  const orgs = orgsQuery.data?.items ?? []

  return (
    <div className="admin-page">
      <header className="admin-page-header">
        <div>
          <h1>单位采购与权益</h1>
          <p className="text-[13px] text-[#7a8880]">
            名单先预览校验、授权确认后才发放权益；单位查看账号只能看到汇总计数。
          </p>
        </div>
        <div className="flex gap-2">
          {tab === 'orgs' && <Button onClick={() => setOrgCreateOpen(true)}><Plus className="size-4" /> 新建单位</Button>}
          {tab === 'batches' && <Button onClick={() => setPreviewOpen(true)}><Plus className="size-4" /> 导入名单预览</Button>}
        </div>
      </header>

      <div className="mb-3 flex gap-2">
        <Button size="sm" variant={tab === 'batches' ? 'default' : 'outline'} onClick={() => setTab('batches')}>采购批次</Button>
        <Button size="sm" variant={tab === 'orgs' ? 'default' : 'outline'} onClick={() => setTab('orgs')}>采购单位</Button>
        <Button size="sm" variant={tab === 'entitlements' ? 'default' : 'outline'} onClick={() => setTab('entitlements')}>权益异常处理</Button>
      </div>

      {tab === 'batches' && (
        <>
          <form className="admin-filter-panel" onSubmit={(e) => e.preventDefault()}>
            <select value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)} className="h-9 rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
              <option value="">全部单位</option>
              {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="h-9 rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
              <option value="">全部状态</option>
              <option value="preview">待确认</option>
              <option value="confirmed">已确认发放</option>
              <option value="cancelled">已取消</option>
            </select>
          </form>
          <div className="admin-data-panel">
            {batchesQuery.isPending ? (
              <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
            ) : batchesQuery.isError ? (
              <DataState title="加载失败" onRetry={() => batchesQuery.refetch()} />
            ) : !batchesQuery.data.items.length ? (
              <DataState title="暂无批次；点击「导入名单预览」开始。" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>批次号</TableHead>
                    <TableHead>单位</TableHead>
                    <TableHead>服务包</TableHead>
                    <TableHead>数量/有效</TableHead>
                    <TableHead>发放/领取</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>创建</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {batchesQuery.data.items.map((b) => (
                    <TableRow key={b.id} className="cursor-pointer" onClick={() => setBatchId(b.id)}>
                      <TableCell className="font-mono text-[12px]">{b.batch_no}</TableCell>
                      <TableCell>{b.org.name}</TableCell>
                      <TableCell>{b.package.name} v{b.package.version_no}</TableCell>
                      <TableCell>{b.quantity} / {b.summary.valid}</TableCell>
                      <TableCell>{b.issued_count} / {b.claimed_count}</TableCell>
                      <TableCell><StatusBadge status={b.status} /></TableCell>
                      <TableCell className="text-[12px]">{formatDateTime(b.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {batchesQuery.data && <PagePagination page={page} pageSize={pageSize} total={batchesQuery.data.total} onChange={setPage} />}
          </div>
        </>
      )}

      {tab === 'orgs' && (
        <div className="admin-data-panel">
          {orgsQuery.isPending ? (
            <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : !orgs.length ? (
            <DataState title="暂无采购单位。" />
          ) : (
            <div className="space-y-3">
              {orgs.map((o) => (
                <OrgCard key={o.id} org={o} canManage={canManage} onCreateAccount={(username, password) =>
                  accountMutation.mutate({ orgId: o.id, username, password })
                } submitting={accountMutation.isPending} />
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'entitlements' && (
        <>
          <form className="admin-filter-panel" onSubmit={(e) => { e.preventDefault(); setPage(1); setKeyword(searchText.trim()) }}>
            <div className="flex min-w-64 items-center gap-2">
              <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="手机号后四位 / 服务包名" />
              <Button type="submit" variant="outline"><Search className="size-4" /></Button>
            </div>
          </form>
          <div className="admin-data-panel">
            {entQuery.isPending ? (
              <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
            ) : entQuery.isError ? (
              <DataState title="加载失败" onRetry={() => entQuery.refetch()} />
            ) : !entQuery.data.items.length ? (
              <DataState title="暂无权益记录。" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ID</TableHead>
                    <TableHead>来源</TableHead>
                    <TableHead>手机号</TableHead>
                    <TableHead>领取用户</TableHead>
                    <TableHead>服务包</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entQuery.data.items.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell>{e.id}</TableCell>
                      <TableCell>{e.source_type === 'batch' ? '单位分配' : '本人订单'}</TableCell>
                      <TableCell>{e.phone}</TableCell>
                      <TableCell>{e.user ? `${e.user.nickname}（${e.user.phone}）` : '—'}</TableCell>
                      <TableCell>{e.package.name} v{e.package.version_no}</TableCell>
                      <TableCell><StatusBadge status={e.status} /></TableCell>
                      <TableCell>
                        {canConfirm && (e.status === 'pending' || e.status === 'active') && (
                          <Button size="sm" variant="outline" onClick={() => setEntAction({ ent: e, kind: 'void' })}>作废</Button>
                        )}
                        {canConfirm && e.source_type === 'batch' && (e.status === 'pending' || e.status === 'rejected') && (
                          <Button size="sm" variant="outline" className="ml-1" onClick={() => setEntAction({ ent: e, kind: 'reassign' })}>改号</Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {entQuery.data && <PagePagination page={page} pageSize={pageSize} total={entQuery.data.total} onChange={setPage} />}
          </div>
        </>
      )}

      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>导入受益人名单（预览）</DialogTitle>
            <DialogDescription>
              提交后逐行校验格式、重复与采购数量；预览不发放权益，确认后才会创建待领取权益。
            </DialogDescription>
          </DialogHeader>
          <BatchPreviewForm
            orgs={orgs}
            packages={packagesQuery.data ?? []}
            submitting={previewMutation.isPending}
            onSubmit={(payload) => previewMutation.mutate(payload)}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={orgCreateOpen} onOpenChange={setOrgCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建采购单位</DialogTitle>
            <DialogDescription>单位名称唯一；创建后可再开只读查看账号。</DialogDescription>
          </DialogHeader>
          <OrgCreateForm submitting={orgMutation.isPending} onSubmit={(payload) => orgMutation.mutate(payload)} />
        </DialogContent>
      </Dialog>

      <Dialog open={batchId !== null && batchDetail !== undefined} onOpenChange={(open) => !open && setBatchId(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>批次 {batchDetail?.batch_no}</DialogTitle>
            <DialogDescription>
              {batchDetail ? `${batchDetail.org.name} · ${batchDetail.package.name} v${batchDetail.package.version_no} · 文件摘要 ${batchDetail.file_sha256.slice(0, 12)}…` : ''}
            </DialogDescription>
          </DialogHeader>
          {batchDetail && (
            <div className="max-h-[64vh] space-y-4 overflow-y-auto pr-1 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={batchDetail.status} />
                <span className="text-[13px] text-[#66756d]">
                  共 {batchDetail.summary.total} 行 · 有效 {batchDetail.summary.valid} · 重复 {batchDetail.summary.duplicate} · 格式错误 {batchDetail.summary.format_error} · 超量 {batchDetail.summary.over_limit}
                </span>
              </div>
              <p className="text-[13px] text-[#66756d]">
                发放 {batchDetail.issued_count} · 已领取 {batchDetail.claimed_count} · 拒绝 {batchDetail.rejected_count} · 待领取 {batchDetail.pending_count}
                {batchDetail.uploaded_by ? ` · 上传 ${batchDetail.uploaded_by}` : ''}
                {batchDetail.confirmed_by ? ` · 确认 ${batchDetail.confirmed_by}（${formatDateTime(batchDetail.confirmed_at)}）` : ''}
              </p>
              {canConfirm && batchDetail.status === 'preview' && (
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => confirmMutation.mutate(batchDetail.id)}>
                    {confirmMutation.isPending && <Loader2 className="size-4 animate-spin" />} 确认发放（{batchDetail.summary.valid} 份）
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => cancelMutation.mutate(batchDetail.id)}>取消批次</Button>
                </div>
              )}
              <section>
                <h3 className="mb-2 font-semibold">名单明细（手机号掩码）</h3>
                <div className="max-h-64 overflow-y-auto rounded-lg border border-[#e2e9e4]">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>行号</TableHead>
                        <TableHead>手机号</TableHead>
                        <TableHead>状态</TableHead>
                        <TableHead>说明</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {batchDetail.rows.map((r) => (
                        <TableRow key={r.row_no}>
                          <TableCell>{r.row_no}</TableCell>
                          <TableCell>{r.phone}</TableCell>
                          <TableCell><StatusBadge status={r.status} /></TableCell>
                          <TableCell className="text-[12px] text-[#8a978e]">{r.error_detail ?? '—'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </section>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={entAction !== null} onOpenChange={(open) => !open && setEntAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{entAction?.kind === 'void' ? '作废权益' : '改派手机号'}</DialogTitle>
            <DialogDescription>
              {entAction ? `权益 #${entAction.ent.id} · ${entAction.ent.package.name} · ${entAction.ent.phone}` : ''}
            </DialogDescription>
          </DialogHeader>
          {entAction && (
            <EntActionForm
              kind={entAction.kind}
              submitting={voidMutation.isPending || reassignMutation.isPending}
              onSubmit={(payload) => {
                if (entAction.kind === 'void') voidMutation.mutate({ entId: entAction.ent.id, reason: payload.reason })
                else reassignMutation.mutate({ entId: entAction.ent.id, phone: payload.phone!, reason: payload.reason })
              }}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function OrgCard({ org, canManage, onCreateAccount, submitting }: {
  org: OrgItem
  canManage: boolean
  onCreateAccount: (username: string, password: string) => void
  submitting: boolean
}) {
  const [open, setOpen] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  return (
    <div className="rounded-lg border border-[#e2e9e4] p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-medium">{org.name}</div>
        <span className="text-[12px] text-[#8a978e]">
          联系人 {org.contact_name ?? '—'}（{org.contact_phone ?? '—'}）· 查看 {org.account_count} 个账号 · {formatDateTime(org.created_at)}
        </span>
      </div>
      {org.note && <p className="mt-1 text-[13px] text-[#66756d]">{org.note}</p>}
      {canManage && (
        <>
          <Button size="sm" variant="outline" className="mt-2" onClick={() => setOpen(!open)}>开设查看账号</Button>
          {open && (
            <form
              className="mt-2 flex flex-wrap items-end gap-2"
              onSubmit={(e: FormEvent<HTMLFormElement>) => {
                e.preventDefault()
                if (username.trim().length < 4 || password.length < 10) return
                onCreateAccount(username.trim(), password)
                setUsername(''); setPassword(''); setOpen(false)
              }}
            >
              <div className="space-y-1">
                <Label className="text-[12px]" htmlFor={`u-${org.id}`}>用户名（4–32，字母数字下划线）</Label>
                <Input id={`u-${org.id}`} value={username} onChange={(e) => setUsername(e.target.value)} className="w-44" />
              </div>
              <div className="space-y-1">
                <Label className="text-[12px]" htmlFor={`p-${org.id}`}>初始口令（≥10 位）</Label>
                <Input id={`p-${org.id}`} type="text" value={password} onChange={(e) => setPassword(e.target.value)} className="w-44" />
              </div>
              <Button size="sm" type="submit" disabled={submitting}>
                {submitting && <Loader2 className="size-4 animate-spin" />} 创建
              </Button>
            </form>
          )}
        </>
      )}
    </div>
  )
}

function OrgCreateForm({ submitting, onSubmit }: {
  submitting: boolean
  onSubmit: (payload: Record<string, unknown>) => void
}) {
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [phone, setPhone] = useState('')
  return (
    <form className="space-y-3" onSubmit={(e) => {
      e.preventDefault()
      if (!name.trim()) return
      onSubmit({ name: name.trim(), contact_name: contact.trim() || undefined, contact_phone: phone.trim() || undefined })
    }}>
      <div className="space-y-1.5">
        <Label htmlFor="org-name">单位名称（唯一）</Label>
        <Input id="org-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="org-contact">联系人（可选）</Label>
          <Input id="org-contact" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={50} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="org-phone">联系电话（可选）</Label>
          <Input id="org-phone" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={20} />
        </div>
      </div>
      <Button type="submit" className="w-full" disabled={submitting || !name.trim()}>
        {submitting && <Loader2 className="size-4 animate-spin" />} 创建单位
      </Button>
    </form>
  )
}

function BatchPreviewForm({ orgs, packages, submitting, onSubmit }: {
  orgs: OrgItem[]
  packages: PackageListItem[]
  submitting: boolean
  onSubmit: (payload: Record<string, unknown>) => void
}) {
  const [orgId, setOrgId] = useState('')
  const [packageId, setPackageId] = useState('')
  const [versionId, setVersionId] = useState('')
  const [quantity, setQuantity] = useState('10')
  const [fileName, setFileName] = useState('')
  const [rowsText, setRowsText] = useState('')
  const [error, setError] = useState('')

  const pkg = packages.find((p) => String(p.id) === packageId)
  const pkgDetailQuery = useQuery({
    queryKey: ['commerce-package', packageId ? Number(packageId) : null],
    queryFn: async () => {
      const { data } = await api.get<PackageListItem>(`/admin/mp/packages/${packageId}`)
      return data
    },
    enabled: !!packageId,
  })
  const versions = pkgDetailQuery.data?.versions ?? (pkg?.current_version ? [pkg.current_version] : [])

  return (
    <form className="max-h-[62vh] space-y-3 overflow-y-auto pr-1" onSubmit={(e) => {
      e.preventDefault()
      const rows = rowsText.split('\n').map((r) => r.trim()).filter(Boolean)
      if (!orgId || !versionId || !rows.length) {
        setError('请选择单位、版本并至少填写一行手机号')
        return
      }
      setError('')
      onSubmit({
        org_id: Number(orgId),
        package_id: Number(packageId),
        version_id: Number(versionId),
        quantity: Number(quantity) || 1,
        file_name: fileName.trim() || '名单.txt',
        rows,
      })
    }}>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>采购单位</Label>
          <select value={orgId} onChange={(e) => setOrgId(e.target.value)} className="h-9 w-full rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
            <option value="">选择单位</option>
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label>采购数量（1–10000）</Label>
          <Input type="number" min="1" max="10000" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label>服务包</Label>
        <select value={packageId} onChange={(e) => { setPackageId(e.target.value); setVersionId('') }}
          className="h-9 w-full rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
          <option value="">选择服务包</option>
          {packages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </div>
      {pkg && (
        <div className="space-y-1.5">
          <Label>发放版本（历史订单绑定下单时版本；批次按所选版本发放）</Label>
          <select value={versionId} onChange={(e) => setVersionId(e.target.value)}
            className="h-9 w-full rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
            <option value="">选择版本</option>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>v{v.version_no}</option>
            ))}
          </select>
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="batch-file">名单文件名（记录留痕）</Label>
        <Input id="batch-file" value={fileName} onChange={(e) => setFileName(e.target.value)} placeholder="如 2026-10 名单.csv" maxLength={255} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="batch-rows">受益人手机号（每行一个，自动去 +86 前缀；服务端将计算文件摘要）</Label>
        <Textarea id="batch-rows" rows={8} value={rowsText} onChange={(e) => setRowsText(e.target.value)} placeholder={'13800000001\n13900000002'} required />
      </div>
      {error && <p className="text-[13px] text-[#a1665c]">{error}</p>}
      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting && <Loader2 className="size-4 animate-spin" />} 提交预览（不发放权益）
      </Button>
    </form>
  )
}

function EntActionForm({ kind, submitting, onSubmit }: {
  kind: 'void' | 'reassign'
  submitting: boolean
  onSubmit: (payload: { reason: string; phone?: string }) => void
}) {
  const [phone, setPhone] = useState('')
  const [reason, setReason] = useState('')
  return (
    <form className="space-y-3" onSubmit={(e) => {
      e.preventDefault()
      if (!reason.trim()) return
      onSubmit(kind === 'reassign' ? { reason: reason.trim(), phone: phone.trim() } : { reason: reason.trim() })
    }}>
      {kind === 'reassign' && (
        <div className="space-y-1.5">
          <Label htmlFor="ent-phone">新手机号（大陆手机号）</Label>
          <Input id="ent-phone" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={20} required />
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="ent-reason">{kind === 'void' ? '作废原因（留痕）' : '改号原因（留痕）'}</Label>
        <Textarea id="ent-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} required />
      </div>
      <Button type="submit" className="w-full" variant={kind === 'void' ? 'destructive' : 'default'} disabled={submitting || !reason.trim() || (kind === 'reassign' && !phone.trim())}>
        {submitting && <Loader2 className="size-4 animate-spin" />} {kind === 'void' ? '确认作废' : '确认改派'}
      </Button>
    </form>
  )
}
