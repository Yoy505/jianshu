import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus, Search } from 'lucide-react'
import { PagePagination } from '@/components/common/PagePagination'
import { DataState } from '@/components/common/DataState'
import { StatusBadge, statusLabel } from '@/components/common/StatusBadge'
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

interface Benefit { name: string; desc?: string | null }
interface PackageVersion {
  id: number
  version_no: number
  price_cents: number
  points_cost: number | null
  validity_days: number
  benefits: Benefit[]
  service_desc: string
  terms: string
  fulfillment_type: 'none' | 'steward'
  service_days: number | null
  fulfillment_config: { template_version?: string; followup_days?: number[] } | null
  created_by: string | null
  created_at: string
}
interface PackageItem {
  id: number
  name: string
  subtitle: string | null
  sort: number
  status: 'draft' | 'on_shelf' | 'off_shelf'
  current_version: PackageVersion | null
  blockers: string[]
  created_at: string
  updated_at: string
}
interface Release {
  id: number
  version_id: number
  version_no: number
  env: string
  basis: string
  released_by: string | null
  released_at: string
  revoked_at: string | null
  revoked_by: string | null
  revoke_reason: string | null
}
interface PackageDetail extends PackageItem {
  versions: PackageVersion[]
  releases: Release[]
}
interface Page<T> { items: T[]; total: number; page: number; page_size: number }

function yuan(cents: number) {
  return (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2)
}

export default function CommercePackages() {
  const admin = useAuthStore((s) => s.admin)
  const queryClient = useQueryClient()
  const canManage = can(admin?.role, 'commerce.product.manage')
  const canRelease = can(admin?.role, 'commerce.sale.release')

  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [searchText, setSearchText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [versionOpen, setVersionOpen] = useState(false)
  const pageSize = 20

  const listQuery = useQuery({
    queryKey: ['commerce-packages', page, status, keyword],
    queryFn: async () => {
      const { data } = await api.get<Page<PackageItem>>('/admin/mp/packages', {
        params: { page, page_size: pageSize, status: status || undefined, q: keyword || undefined },
      })
      return data
    },
  })

  const detailQuery = useQuery({
    queryKey: ['commerce-package', selectedId],
    queryFn: async () => {
      const { data } = await api.get<PackageDetail>(`/admin/mp/packages/${selectedId}`)
      return data
    },
    enabled: selectedId !== null,
  })

  const createMutation = useMutation({
    mutationFn: async (payload: { name: string; subtitle?: string }) => {
      const { data } = await api.post<PackageItem>('/admin/mp/packages', payload)
      return data
    },
    onSuccess: async () => {
      toast.success('服务包已创建（草稿）')
      setCreateOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['commerce-packages'] })
    },
  })

  const versionMutation = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      if (selectedId === null) throw new Error('未选择服务包')
      const { data } = await api.post<PackageVersion>(
        `/admin/mp/packages/${selectedId}/versions`, payload,
      )
      return data
    },
    onSuccess: async () => {
      toast.success('新版本已创建；上架需在详情中指定该版本')
      setVersionOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['commerce-package', selectedId] })
    },
  })

  const shelfMutation = useMutation({
    mutationFn: async (payload: { on_shelf: boolean; version_id?: number }) => {
      if (selectedId === null) throw new Error('未选择服务包')
      const { data } = await api.post<PackageItem>(`/admin/mp/packages/${selectedId}/shelf`, payload)
      return data
    },
    onSuccess: async (_data, variables) => {
      toast.success(variables.on_shelf ? '服务包已上架' : '服务包已下架')
      await queryClient.invalidateQueries({ queryKey: ['commerce-packages'] })
      await queryClient.invalidateQueries({ queryKey: ['commerce-package', selectedId] })
    },
    onError: (error: unknown) => {
      const detail = (error as { response?: { data?: { detail?: string; blockers?: string[] } } })
        .response?.data
      if (detail?.blockers?.length) {
        toast.error(`暂不可上架：${detail.blockers.join('、')}`)
      }
    },
  })

  const releaseMutation = useMutation({
    mutationFn: async (payload: { versionId: number; env: string; basis: string }) => {
      if (selectedId === null) throw new Error('未选择服务包')
      const { data } = await api.post(`/admin/mp/packages/${selectedId}/releases`, {
        version_id: payload.versionId,
        env: payload.env,
        basis: payload.basis,
      })
      return data
    },
    onSuccess: async () => {
      toast.success('已放行；C 端即可购买该版本')
      await queryClient.invalidateQueries({ queryKey: ['commerce-package', selectedId] })
    },
  })

  const revokeMutation = useMutation({
    mutationFn: async (payload: { releaseId: number; reason: string }) => {
      const { data } = await api.post(`/admin/mp/releases/${payload.releaseId}/revoke`, {
        reason: payload.reason,
      })
      return data
    },
    onSuccess: async () => {
      toast.success('放行已撤销；该版本立即回到不可售')
      await queryClient.invalidateQueries({ queryKey: ['commerce-package', selectedId] })
    },
  })

  useEffect(() => { setPage(1) }, [status])

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPage(1)
    setKeyword(searchText.trim())
  }

  const detail = detailQuery.data

  return (
    <div className="admin-page">
      <header className="admin-page-header">
        <div>
          <h1>服务包商品</h1>
          <p className="text-[13px] text-[#7a8880]">
            价格、权益、有效期与售后规则按版本维护；已售订单绑定下单时版本。调价即建新版本。
          </p>
        </div>
        {canManage && (
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> 新建服务包
          </Button>
        )}
      </header>

      <form className="admin-filter-panel" onSubmit={submitSearch}>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-9 rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
          <option value="">全部状态</option>
          <option value="draft">草稿</option>
          <option value="on_shelf">上架中</option>
          <option value="off_shelf">已下架</option>
        </select>
        <div className="flex min-w-64 items-center gap-2">
          <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="搜索服务包名称" />
          <Button type="submit" variant="outline"><Search className="size-4" /></Button>
        </div>
      </form>

      <div className="admin-data-panel">
        {listQuery.isPending ? (
          <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
        ) : listQuery.isError ? (
          <DataState title="加载失败" onRetry={() => listQuery.refetch()} />
        ) : !listQuery.data.items.length ? (
          <DataState title="暂无服务包；点击右上角「新建服务包」开始配置。" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>当前版本</TableHead>
                <TableHead>价格</TableHead>
                <TableHead>积分价</TableHead>
                <TableHead>有效期</TableHead>
                <TableHead>配置阻断项</TableHead>
                <TableHead>更新时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {listQuery.data.items.map((p) => (
                <TableRow key={p.id} className="cursor-pointer" onClick={() => setSelectedId(p.id)}>
                  <TableCell>{p.id}</TableCell>
                  <TableCell className="font-medium">{p.name}</TableCell>
                  <TableCell><StatusBadge status={p.status} /></TableCell>
                  <TableCell>{p.current_version ? `v${p.current_version.version_no}` : '—'}</TableCell>
                  <TableCell>{p.current_version ? `${yuan(p.current_version.price_cents)} 元` : '—'}</TableCell>
                  <TableCell>{p.current_version?.points_cost ?? '—'}</TableCell>
                  <TableCell>{p.current_version ? `${p.current_version.validity_days} 天 · ${fulfillmentLabel(p.current_version)}` : '—'}</TableCell>
                  <TableCell className="max-w-52 text-[12px] text-[#a1665c]">{p.blockers.join('、') || '—'}</TableCell>
                  <TableCell className="text-[12px]">{formatDateTime(p.updated_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {listQuery.data && (
          <PagePagination page={page} pageSize={pageSize} total={listQuery.data.total} onChange={setPage} />
        )}
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建服务包</DialogTitle>
            <DialogDescription>创建后为草稿状态；需先创建版本并通过配置检查后才能上架。</DialogDescription>
          </DialogHeader>
          <CreatePackageForm
            submitting={createMutation.isPending}
            onSubmit={(payload) => createMutation.mutate(payload)}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={selectedId !== null && detail !== undefined} onOpenChange={(open) => !open && setSelectedId(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{detail?.name}</DialogTitle>
            <DialogDescription>
              {detail ? `状态：${statusLabel(detail.status)}${detail.subtitle ? ' · ' + detail.subtitle : ''}` : ''}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="max-h-[62vh] space-y-4 overflow-y-auto pr-1 text-sm">
              <div className="flex flex-wrap gap-2">
                {canManage && detail.status !== 'on_shelf' && (
                  <Button
                    size="sm"
                    disabled={!detail.current_version}
                    onClick={() => detail.current_version && shelfMutation.mutate({
                      on_shelf: true, version_id: detail.current_version.id,
                    })}
                  >
                    {shelfMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                    上架当前版本
                  </Button>
                )}
                {canManage && detail.status === 'on_shelf' && (
                  <Button size="sm" variant="outline" onClick={() => shelfMutation.mutate({ on_shelf: false })}>
                    下架
                  </Button>
                )}
                {canManage && (
                  <Button size="sm" variant="outline" onClick={() => setVersionOpen(true)}>
                    <Plus className="size-4" /> 新版本
                  </Button>
                )}
              </div>

              {canRelease && <ReleasePanel
                detail={detail}
                submitting={releaseMutation.isPending || revokeMutation.isPending}
                onRelease={(versionId, env, basis) => releaseMutation.mutate({ versionId, env, basis })}
                onRevoke={(releaseId, reason) => revokeMutation.mutate({ releaseId, reason })}
              />}

              <section>
                <h3 className="mb-2 font-semibold">版本历史</h3>
                <div className="space-y-3">
                  {detail.versions.map((v) => (
                    <div key={v.id} className="rounded-lg border border-[#e2e9e4] p-3">
                      <div className="flex items-center justify-between">
                        <span className="font-medium">v{v.version_no}</span>
                        {detail.current_version?.id === v.id && <StatusBadge status="on_shelf" />}
                      </div>
                      <p className="mt-1 text-[13px] text-[#66756d]">
                        {yuan(v.price_cents)} 元 · {v.points_cost ? `${v.points_cost} 积分` : '不支持积分'} · 权益 {v.validity_days} 天 · {fulfillmentLabel(v)} · {v.benefits.length} 项权益
                      </p>
                      <ul className="mt-2 list-disc pl-5 text-[13px] text-[#5b6a62]">
                        {v.benefits.map((b) => <li key={b.name}>{b.name}{b.desc ? `：${b.desc}` : ''}</li>)}
                      </ul>
                      <details className="mt-2 text-[13px]">
                        <summary className="cursor-pointer text-[#4d7c61]">服务说明与售后规则</summary>
                        <p className="mt-1 whitespace-pre-line text-[#66756d]">{v.service_desc}</p>
                        <p className="mt-2 whitespace-pre-line text-[#66756d]">{v.terms}</p>
                      </details>
                      <p className="mt-2 text-[12px] text-[#8a978e]">由 {v.created_by ?? '—'} 创建于 {formatDateTime(v.created_at)}；版本创建后不可修改。</p>
                    </div>
                  ))}
                  {!detail.versions.length && <DataState title="尚未创建版本。" />}
                </div>
              </section>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={versionOpen} onOpenChange={setVersionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>创建新版本</DialogTitle>
            <DialogDescription>版本一经创建不可修改；调整价格或权益会生成新版本。</DialogDescription>
          </DialogHeader>
          <VersionForm
            submitting={versionMutation.isPending}
            onSubmit={(payload) => versionMutation.mutate(payload)}
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}

function CreatePackageForm({ submitting, onSubmit }: {
  submitting: boolean
  onSubmit: (payload: { name: string; subtitle?: string }) => void
}) {
  const [name, setName] = useState('')
  const [subtitle, setSubtitle] = useState('')
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        onSubmit({ name: name.trim(), subtitle: subtitle.trim() || undefined })
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="pkg-name">服务包名称（1–100 字）</Label>
        <Input id="pkg-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="pkg-subtitle">副标题（可选）</Label>
        <Input id="pkg-subtitle" value={subtitle} onChange={(e) => setSubtitle(e.target.value)} maxLength={200} />
      </div>
      <Button type="submit" className="w-full" disabled={submitting || !name.trim()}>
        {submitting && <Loader2 className="size-4 animate-spin" />} 创建草稿
      </Button>
    </form>
  )
}

function fulfillmentLabel(v: Pick<PackageVersion, 'fulfillment_type' | 'service_days'>): string {
  return v.fulfillment_type === 'steward' ? `管家履约 ${v.service_days ?? '?'} 天` : '普通商品'
}

function VersionForm({ submitting, onSubmit }: {
  submitting: boolean
  onSubmit: (payload: Record<string, unknown>) => void
}) {
  const [priceYuan, setPriceYuan] = useState('')
  const [points, setPoints] = useState('')
  const [validity, setValidity] = useState('45')
  const [benefitsText, setBenefitsText] = useState('')
  const [serviceDesc, setServiceDesc] = useState('')
  const [terms, setTerms] = useState('')
  const [fulfillmentType, setFulfillmentType] = useState<'none' | 'steward'>('none')
  const [serviceDays, setServiceDays] = useState('45')
  const [templateVersion, setTemplateVersion] = useState('')
  const [followupDays, setFollowupDays] = useState('')

  const priceCents = Math.round(Number(priceYuan) * 100)
  const followupList = followupDays.split(/[,，\s]+/).map((v) => v.trim()).filter(Boolean).map(Number)
  const followupValid = followupList.every((n) => Number.isInteger(n) && n >= 1)
    && followupList.every((n, i) => i === 0 || n > followupList[i - 1])
    && (fulfillmentType !== 'steward' || followupList.every((n) => n <= (Number(serviceDays) || 0)))

  return (
    <form
      className="max-h-[60vh] space-y-3 overflow-y-auto pr-1"
      onSubmit={(e) => {
        e.preventDefault()
        const benefits = benefitsText.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
          const [name, ...rest] = line.split('：')
          return rest.length ? { name: name.trim(), desc: rest.join('：').trim() } : { name: line }
        })
        if (!Number.isFinite(priceCents) || priceCents <= 0 || !benefits.length || !serviceDesc.trim() || !terms.trim()) return
        if (fulfillmentType === 'steward' && (!(Number(serviceDays) >= 1) || !followupValid)) return
        const fulfillment: Record<string, unknown> = { fulfillment_type: fulfillmentType }
        if (fulfillmentType === 'steward') {
          fulfillment.service_days = Number(serviceDays)
          const config: Record<string, unknown> = {}
          if (templateVersion.trim()) config.template_version = templateVersion.trim()
          if (followupList.length) config.followup_days = followupList
          if (Object.keys(config).length) fulfillment.fulfillment_config = config
        }
        onSubmit({
          price_cents: priceCents,
          points_cost: points ? Number(points) : null,
          validity_days: Number(validity) || 45,
          benefits,
          service_desc: serviceDesc.trim(),
          terms: terms.trim(),
          ...fulfillment,
        })
      }}
    >
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="v-price">价格（元）</Label>
          <Input id="v-price" type="number" min="0.01" step="0.01" value={priceYuan} onChange={(e) => setPriceYuan(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="v-points">积分价（可选）</Label>
          <Input id="v-points" type="number" min="1" value={points} onChange={(e) => setPoints(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="v-validity">权益有效期（天）</Label>
          <Input id="v-validity" type="number" min="1" max="3650" value={validity} onChange={(e) => setValidity(e.target.value)} />
        </div>
      </div>
      <div className="rounded-md border border-[#e2e9e4] p-3 space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="v-fulfillment">履约类型</Label>
            <select id="v-fulfillment" value={fulfillmentType} onChange={(e) => setFulfillmentType(e.target.value as 'none' | 'steward')}
              className="h-9 w-full rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
              <option value="none">普通商品（只发权益，不建服务实例）</option>
              <option value="steward">全域健康管家履约</option>
            </select>
          </div>
          {fulfillmentType === 'steward' && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="v-service-days">实际服务期（天，1–365）</Label>
                <Input id="v-service-days" type="number" min="1" max="365" value={serviceDays} onChange={(e) => setServiceDays(e.target.value)} required />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="v-template">服务模板版本（可选）</Label>
                <Input id="v-template" maxLength={32} value={templateVersion} onChange={(e) => setTemplateVersion(e.target.value)} placeholder="如 sleep_45d_v1" />
              </div>
            </>
          )}
        </div>
        {fulfillmentType === 'steward' && (
          <div className="space-y-1.5">
            <Label htmlFor="v-followup">随访节点（服务日序号，逗号分隔、递增且不超过服务期；留空用全局默认）</Label>
            <Input id="v-followup" value={followupDays} onChange={(e) => setFollowupDays(e.target.value)} placeholder="7,14,21,28" />
            {!followupValid && <p className="text-[12px] text-[#c0392b]">随访节点须为 ≥1、严格递增且不超过服务期的整数</p>}
          </div>
        )}
        <p className="text-[12px] text-[#8a978e]">
          权益有效期与实际服务期是两个独立字段：有效期约束领取 / 激活窗口，服务期决定管家任务日历长度；历史订单按购买时版本解释。
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="v-benefits">权益项目（每行一项，可用「名称：说明」格式，1–20 项）</Label>
        <Textarea id="v-benefits" rows={4} value={benefitsText} onChange={(e) => setBenefitsText(e.target.value)} placeholder={'健康管家服务：45 天全域健康管家\n用药咨询'} required />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="v-desc">公开服务说明（1–5000 字）</Label>
        <Textarea id="v-desc" rows={3} value={serviceDesc} onChange={(e) => setServiceDesc(e.target.value)} required />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="v-terms">售后规则（1–5000 字，下单时作为授权文本快照）</Label>
        <Textarea id="v-terms" rows={3} value={terms} onChange={(e) => setTerms(e.target.value)} required />
      </div>
      <Button type="submit" className="w-full" disabled={submitting}>
        {submitting && <Loader2 className="size-4 animate-spin" />} 创建版本
      </Button>
    </form>
  )
}


function ReleasePanel({ detail, submitting, onRelease, onRevoke }: {
  detail: PackageDetail
  submitting: boolean
  onRelease: (versionId: number, env: string, basis: string) => void
  onRevoke: (releaseId: number, reason: string) => void
}) {
  const current = detail.current_version
  const active = detail.releases.find(
    (r) => !r.revoked_at && current && r.version_id === current.id,
  )
  const [env, setEnv] = useState('all')
  const [basis, setBasis] = useState('')
  return (
    <section className="rounded-lg border border-[#e2e9e4] p-3">
      <h3 className="font-semibold">开售放行（默认不放行）</h3>
      <p className="mt-1 text-[12px] text-[#8a978e]">
        放行后该版本在所选环境可购（微信与积分一致判定）；撤销立即回到不可售。依据须写明业务确认来源。
      </p>
      {active ? (
        <div className="mt-2 text-[13px]">
          <p className="text-[#217a54]">
            当前版本已放行：{active.env} · {active.released_by} · {formatDateTime(active.released_at)}
          </p>
          <p className="mt-1 text-[#66756d]">依据：{active.basis}</p>
          <Button
            size="sm"
            variant="outline"
            className="mt-2"
            disabled={submitting}
            onClick={() => onRevoke(active.id, '运营决定暂停销售')}
          >
            撤销放行
          </Button>
        </div>
      ) : current ? (
        <div className="mt-2 space-y-2">
          <div className="grid grid-cols-[7rem_1fr] gap-2">
            <select value={env} onChange={(e) => setEnv(e.target.value)}
              className="h-9 rounded-md border border-[#d5ded8] bg-white px-2 text-[13px]">
              <option value="all">全环境</option>
              <option value="dev">dev</option>
              <option value="test">test</option>
              <option value="staging">staging</option>
              <option value="prod">prod</option>
            </select>
            <Input value={basis} onChange={(e) => setBasis(e.target.value)} maxLength={500}
              placeholder="放行依据（如：售价/退款规则/履约团队确认单号）" />
          </div>
          <Button size="sm" disabled={submitting || !basis.trim() || !current}
            onClick={() => onRelease(current!.id, env, basis.trim())}>
            放行 v{current.version_no}
          </Button>
        </div>
      ) : (
        <p className="mt-2 text-[13px] text-[#8a978e]">先创建版本并上架后再放行。</p>
      )}
      {detail.releases.length > 0 && (
        <details className="mt-2 text-[12px] text-[#66756d]">
          <summary className="cursor-pointer text-[#4d7c61]">放行历史（{detail.releases.length}）</summary>
          <ul className="mt-1 space-y-1">
            {detail.releases.map((r) => (
              <li key={r.id}>
                v{r.version_no} · {r.env} · {r.revoked_at ? `已撤销（${r.revoke_reason ?? ''}）` : '生效中'} · {r.released_by} · {formatDateTime(r.released_at)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
