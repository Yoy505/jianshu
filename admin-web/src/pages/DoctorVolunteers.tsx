import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, Loader2, Search } from 'lucide-react'
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

/** ClinInsOS 志愿者备案库（医师志愿者：H5/小程序报名 → 初审 → 终审）。 */

const TOKEN_KEY = 'clininsos_admin_token'
const CLININSOS_ORIGIN = 'https://clininsos.smartstudio.cc'

interface VolunteerItem {
  public_id: string
  name: string
  phone: string
  work_unit: string
  title: string | null
  branch: string | null
  status: 'pending' | 'approved' | 'rejected'
  review_level: number
  reviewed_at: string | null
  review_comment: string | null
  created_at: string
}

interface ReviewLog {
  public_id: string
  level: number
  action: string
  comment: string
  reviewer_username: string
  created_at: string
}

interface VolunteerDetail extends VolunteerItem {
  gender: string | null
  birth_date: string | null
  political_status: string | null
  ethnicity: string | null
  address: string | null
  health_status: string | null
  id_card: string | null
  photo_url: string | null
  signature_url: string | null
  title_cert_url: string | null
  license_cert_url: string | null
  education: string | null
  degree: string | null
  school: string | null
  work_years: string | null
  department_title: string | null
  specialty: string | null
  title_date: string | null
  qualification: string | null
  join_date: string | null
  registered_on_platform: boolean | null
  work_resume: string | null
  volunteer_experience: string | null
  intentions: string[]
  intention_other: string | null
  source: string | null
  review_logs: ReviewLog[]
}

function statusView(item: { status: string; review_level: number }): { label: string; cls: string } {
  if (item.status === 'approved') return { label: '已通过', cls: 'border-[#cae3d5] bg-[#edf6ef] text-[#27724f]' }
  if (item.status === 'rejected') return { label: '已驳回', cls: 'border-[#e6c8c8] bg-[#fbf0f0] text-[#a04040]' }
  return item.review_level >= 1
    ? { label: '待终审', cls: 'border-[#cedee6] bg-[#eff5f8] text-[#316b86]' }
    : { label: '待初审', cls: 'border-[#eddfbf] bg-[#fcf6e9] text-[#8b6628]' }
}

function StatusBadge({ value }: { value: { status: string; review_level: number } }) {
  const v = statusView(value)
  return <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium leading-none before:size-1.5 before:rounded-full before:bg-current ${v.cls}`}>{v.label}</span>
}

function MaterialImg({ label, url }: { label: string; url?: string | null }) {
  if (!url) return null
  const full = url.startsWith('http') ? url : CLININSOS_ORIGIN + url
  return (
    <a href={full} target="_blank" rel="noreferrer" className="block space-y-1">
      <img src={full} alt={label} className="h-28 w-28 rounded-md border border-border object-cover" />
      <p className="text-center text-[11px] text-muted-foreground">{label}</p>
    </a>
  )
}

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{value || '—'}</dd>
    </>
  )
}

export default function DoctorVolunteers() {
  const admin = useAuthStore((state) => state.admin)
  const queryClient = useQueryClient()
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [status, setStatus] = useState('')
  const [searchText, setSearchText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [resetPwd, setResetPwd] = useState('')

  const canRead = can(admin?.role, 'volunteer.read')
  const headers = { 'X-ClinInsOS-Token': token }

  const loginMutation = useMutation({
    mutationFn: async (payload: { username: string; password: string }) => {
      const { data } = await api.post<{ access_token: string }>('/admin/clininsos-volunteers/login', payload)
      return data
    },
    onSuccess: (data) => {
      localStorage.setItem(TOKEN_KEY, data.access_token)
      setToken(data.access_token)
      toast.success('已连接志愿者备案库')
    },
    onError: () => {
      toast.error('连接失败：ClinInsOS 账号或密码不正确')
    },
  })

  const listQuery = useQuery({
    queryKey: ['doctor-volunteers', status, keyword, token],
    queryFn: async () => {
      const { data } = await api.get<VolunteerItem[]>('/admin/clininsos-volunteers', {
        params: {
          status: status || undefined,
          keyword: keyword || undefined,
          limit: 200,
        },
        headers,
      })
      return data
    },
    enabled: canRead && !!token,
    retry: false,
  })

  const detailQuery = useQuery({
    queryKey: ['doctor-volunteer', selectedId, token],
    queryFn: async () => {
      const { data } = await api.get<VolunteerDetail>(`/admin/clininsos-volunteers/${selectedId}`, { headers })
      return data
    },
    enabled: canRead && !!token && selectedId !== null,
    retry: false,
  })

  function handleUnauthorized() {
    localStorage.removeItem(TOKEN_KEY)
    setToken('')
    toast.error('备案库登录已过期，请重新连接')
  }

  const resetPwdMutation = useMutation({
    mutationFn: async (payload: { phone: string; new_password: string }) => {
      const { data } = await api.post('/admin/clininsos-volunteers/h5-password/reset', payload, { headers })
      return data
    },
    onSuccess: async () => {
      toast.success('登录密码已重置')
      setResetPwd('')
    },
    onError: (error: { response?: { status?: number; data?: { detail?: string } } }) => {
      if (error.response?.status === 401) return handleUnauthorized()
      toast.error(error.response?.data?.detail || '重置失败，请重试')
    },
  })

  const reviewMutation = useMutation({
    mutationFn: async (payload: { action: 'approve' | 'reject'; level: number; comment: string }) => {
      if (selectedId === null) throw new Error('未选择记录')
      const { data } = await api.post<VolunteerDetail>(
        `/admin/clininsos-volunteers/${selectedId}/review`,
        payload,
        { headers },
      )
      return data
    },
    onSuccess: async () => {
      toast.success('审核结果已提交')
      setComment('')
      await queryClient.invalidateQueries({ queryKey: ['doctor-volunteers'] })
      await queryClient.invalidateQueries({ queryKey: ['doctor-volunteer', selectedId] })
    },
    onError: (error: { response?: { status?: number; data?: { detail?: string } } }) => {
      if (error.response?.status === 401) return handleUnauthorized()
      toast.error(error.response?.data?.detail || '审核提交失败，请重试')
    },
  })

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setKeyword(searchText.trim())
  }

  function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    loginMutation.mutate({
      username: String(form.get('username') || ''),
      password: String(form.get('password') || ''),
    })
  }

  useEffect(() => {
    if (listQuery.isError) {
      const status = (listQuery.error as { response?: { status?: number } })?.response?.status
      if (status === 401) handleUnauthorized()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listQuery.isError])

  if (!canRead) {
    return <p className="admin-state rounded-xl border border-border bg-card">当前账号没有查看志愿者报名的权限。</p>
  }

  if (!token) {
    return (
      <div className="admin-page">
        <div className="admin-page-header">
          <h2 className="text-lg font-semibold">医师志愿者</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            查看与审核医师志愿者报名（H5 / 小程序提交，初审 → 终审）。首次使用需连接志愿者备案库。
          </p>
        </div>
        <div className="admin-data-panel max-w-md p-6">
          <form onSubmit={submitLogin} className="space-y-4">
            <p className="flex items-center gap-2 text-sm font-medium"><Link2 className="size-4" />连接志愿者备案库（ClinInsOS 后台账号）</p>
            <label className="block space-y-1 text-xs text-muted-foreground">
              <span>账号</span>
              <Input name="username" autoComplete="username" placeholder="ClinInsOS 后台账号" />
            </label>
            <label className="block space-y-1 text-xs text-muted-foreground">
              <span>密码</span>
              <Input name="password" type="password" autoComplete="current-password" placeholder="密码" />
            </label>
            <Button type="submit" disabled={loginMutation.isPending} className="w-full">
              {loginMutation.isPending && <Loader2 className="animate-spin" />}
              连接
            </Button>
            <p className="text-xs text-muted-foreground">凭证仅保存在本机浏览器，用于访问备案库数据。</p>
          </form>
        </div>
      </div>
    )
  }

  const detail = detailQuery.data

  return (
    <div className="admin-page">
      <div className="admin-page-header">
        <h2 className="text-lg font-semibold">医师志愿者</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          医师志愿者报名备案库（公众号 H5 与小程序提交）。审核流程：初审 → 终审；驳回需填写意见。
        </p>
      </div>

      <div className="admin-filter-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            <span>审核状态</span>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="block h-9 min-w-32 rounded-md border border-input bg-background px-3 text-sm text-foreground"
            >
              <option value="">全部</option>
              <option value="pending">待审核</option>
              <option value="approved">已通过</option>
              <option value="rejected">已驳回</option>
            </select>
          </label>
          <form onSubmit={submitSearch} className="flex flex-1 items-end gap-2">
            <label className="min-w-40 flex-1 space-y-1 text-xs text-muted-foreground">
              <span>搜索姓名 / 手机号 / 单位 / 职称</span>
              <Input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="输入关键词" />
            </label>
            <Button type="submit" variant="outline"><Search />查询</Button>
          </form>
          <Button variant="ghost" size="sm" onClick={() => { localStorage.removeItem(TOKEN_KEY); setToken('') }}>
            断开备案库
          </Button>
        </div>
      </div>

      <div className="admin-data-panel">
        {listQuery.isLoading ? (
          <div className="space-y-3 p-5"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /></div>
        ) : listQuery.isError ? (
          <DataState title="报名列表加载失败" description="请确认备案库连接有效后重试。" onRetry={() => void listQuery.refetch()} />
        ) : !listQuery.data?.length ? (
          <DataState title="暂无报名" description="当前筛选条件下没有医师志愿者报名记录。" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader><TableRow>
                <TableHead>姓名</TableHead><TableHead>工作单位</TableHead><TableHead>职称 / 专业</TableHead><TableHead>状态</TableHead><TableHead>报名时间</TableHead><TableHead className="text-right">操作</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {listQuery.data.map((item) => (
                  <TableRow key={item.public_id}>
                    <TableCell><p className="font-medium">{item.name}</p><p className="mt-1 text-xs text-muted-foreground">{item.phone}</p></TableCell>
                    <TableCell className="max-w-56 truncate">{item.work_unit}</TableCell>
                    <TableCell className="text-xs">{item.title || '—'}<br /><span className="text-muted-foreground">{item.branch || ''}</span></TableCell>
                    <TableCell><StatusBadge value={item} /></TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(item.created_at)}</TableCell>
                    <TableCell className="text-right"><Button variant="outline" size="sm" onClick={() => { setSelectedId(item.public_id); setComment('') }}>查看与审核</Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {!!listQuery.data?.length && (
          <p className="border-t px-4 py-3 text-xs text-muted-foreground">共 {listQuery.data.length} 条（最多加载 200 条）</p>
        )}
      </div>

      <Dialog open={selectedId !== null} onOpenChange={(open) => { if (!open) setSelectedId(null) }}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>医师志愿者报名详情</DialogTitle>
            <DialogDescription>资质信息仅用于志愿者备案审核。</DialogDescription>
          </DialogHeader>
          {detailQuery.isLoading ? <Skeleton className="h-60 w-full" /> : detailQuery.isError ? (
            <DataState title="详情加载失败" description="请重试。" onRetry={() => void detailQuery.refetch()} />
          ) : detail ? (
            <div className="space-y-5 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <StatusBadge value={detail} />
                {detail.reviewed_at && <span className="text-xs text-muted-foreground">最近审核 {formatDateTime(detail.reviewed_at)}</span>}
              </div>

              <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 rounded-md bg-muted/50 p-4">
                <Field label="姓名" value={detail.name} />
                <Field label="性别" value={detail.gender} />
                <Field label="出生年月" value={detail.birth_date} />
                <Field label="手机号" value={detail.phone} />
                <Field label="政治面貌" value={detail.political_status} />
                <Field label="民族" value={detail.ethnicity} />
                <Field label="身份证号" value={detail.id_card} />
                <Field label="通讯地址" value={detail.address} />
                <Field label="健康状况" value={detail.health_status} />
                <Field label="学历 / 学位" value={[detail.education, detail.degree].filter(Boolean).join(' / ')} />
                <Field label="毕业院校" value={detail.school} />
                <Field label="工作年限" value={detail.work_years} />
                <Field label="工作单位" value={detail.work_unit} />
                <Field label="科室 / 职务" value={detail.department_title} />
                <Field label="职称" value={detail.title} />
                <Field label="专业特长" value={detail.specialty} />
                <Field label="职称评定" value={detail.title_date} />
                <Field label="执业资格" value={detail.qualification} />
                <Field label="参加工作" value={detail.join_date} />
                <Field label="党支部" value={detail.branch} />
                <Field label="协同平台注册" value={detail.registered_on_platform == null ? undefined : detail.registered_on_platform ? '是' : '否'} />
                <Field label="服务意向" value={[...detail.intentions, detail.intention_other].filter(Boolean).join('、')} />
                <Field label="来源" value={detail.source} />
                <Field label="报名时间" value={formatDateTime(detail.created_at)} />
              </dl>

              {(detail.work_resume || detail.volunteer_experience) && (
                <div className="space-y-2">
                  {detail.work_resume && <div><p className="font-medium">工作简历</p><p className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">{detail.work_resume}</p></div>}
                  {detail.volunteer_experience && <div><p className="font-medium">志愿服务经历</p><p className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">{detail.volunteer_experience}</p></div>}
                </div>
              )}

              {(detail.photo_url || detail.signature_url || detail.title_cert_url || detail.license_cert_url) && (
                <div>
                  <p className="mb-2 font-medium">报名材料（点击查看大图）</p>
                  <div className="flex flex-wrap gap-4">
                    <MaterialImg label="证件照" url={detail.photo_url} />
                    <MaterialImg label="手写签名" url={detail.signature_url} />
                    <MaterialImg label="职称证书" url={detail.title_cert_url} />
                    <MaterialImg label="执业证书" url={detail.license_cert_url} />
                  </div>
                </div>
              )}

              {detail.review_logs.length > 0 && (
                <div>
                  <p className="mb-2 font-medium">审核时间线</p>
                  <div className="space-y-2">
                    {detail.review_logs.map((log) => (
                      <p key={log.public_id} className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
                        <span className="font-medium">{log.level === 2 ? '终审' : '初审'} · {log.action === 'approve' ? '通过' : '驳回'}</span>
                        <span className="ml-2 text-muted-foreground">{log.reviewer_username} · {formatDateTime(log.created_at)}</span>
                        {log.comment && <span className="mt-1 block text-muted-foreground">意见：{log.comment}</span>}
                      </p>
                    ))}
                  </div>
                </div>
              )}

              <div className="space-y-2 border-t pt-4">
                <p className="text-sm font-medium">重置 H5 登录密码（用户忘记密码时使用）</p>
                <div className="flex gap-2">
                  <Input
                    value={resetPwd}
                    onChange={(e) => setResetPwd(e.target.value)}
                    placeholder="输入新密码（6-32 位）"
                    type="text"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={resetPwdMutation.isPending || resetPwd.trim().length < 6}
                    onClick={() => resetPwdMutation.mutate({ phone: detail.phone, new_password: resetPwd.trim() })}
                  >
                    {resetPwdMutation.isPending && <Loader2 className="animate-spin" />}
                    重置
                  </Button>
                </div>
              </div>
              {detail.status === 'pending' && (
                <div className="space-y-3 border-t pt-4">
                  <p className="font-medium">审核操作{detail.review_level >= 1 ? '（当前：初审通过，待终审）' : '（当前：待初审）'}</p>
                  <label className="block space-y-1 text-xs text-muted-foreground">
                    <span>审核意见（驳回时必填）</span>
                    <Input value={comment} onChange={(event) => setComment(event.target.value)} placeholder="填写审核意见" />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {detail.review_level < 1 && (
                      <Button size="sm" disabled={reviewMutation.isPending} onClick={() => reviewMutation.mutate({ action: 'approve', level: 1, comment })}>
                        {reviewMutation.isPending && <Loader2 className="animate-spin" />}初审通过
                      </Button>
                    )}
                    <Button size="sm" disabled={reviewMutation.isPending} onClick={() => reviewMutation.mutate({ action: 'approve', level: 2, comment })}>
                      {reviewMutation.isPending && <Loader2 className="animate-spin" />}终审通过
                    </Button>
                    <Button size="sm" variant="destructive" disabled={reviewMutation.isPending || !comment.trim()}
                      onClick={() => reviewMutation.mutate({ action: 'reject', level: Math.max(detail.review_level, 1), comment })}>
                      驳回
                    </Button>
                  </div>
                </div>
              )}
              {detail.status === 'rejected' && detail.review_comment && (
                <p className="rounded-md border border-[#e6c8c8] bg-[#fbf0f0] px-3 py-2 text-xs text-[#a04040]">驳回意见：{detail.review_comment}</p>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
