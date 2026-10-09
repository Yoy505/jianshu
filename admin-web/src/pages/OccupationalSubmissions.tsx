import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileText, Loader2, Lock, Search, Unlock, Video } from 'lucide-react'
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
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import { can } from '@/lib/permissions'
import { formatDateTime } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

type SubmissionStatus =
  | 'submitted'
  | 'initial_reviewing'
  | 'pending_final'
  | 'accepted'
  | 'rejected'
type WorkType = 'article' | 'video'
type ReviewStage = 'initial' | 'final'

interface SubmissionItem {
  id: number
  receipt_no: string
  work_type: WorkType
  title: string
  topic: string
  author_name: string
  organization: string
  phone_masked: string
  status: SubmissionStatus
  submitted_at: string
  word_count: number | null
  duration_seconds: number | null
}

interface ReviewRecord {
  stage: ReviewStage
  reviewer_id: number
  reviewer_name: string
  conclusion: 'passed' | 'rejected'
  comment: string | null
  score: number | null
  created_at: string
}

interface LockInfo {
  locked_by_admin_id: number | null
  locked_by_name: string | null
  locked_at: string | null
  expired: boolean
}

interface SubmissionDetail extends SubmissionItem {
  client_submission_id: string
  phone: string
  summary: string
  original_filename: string
  file_size: number
  file_sha256: string
  video_width: number | null
  video_height: number | null
  consent_review_contact: boolean
  consent_text: string
  consented_at: string
  review_comment: string | null
  reviewed_by: number | null
  reviewed_at: string | null
  reviews: ReviewRecord[]
  lock: LockInfo
}

interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

const STATUS_LABELS: Record<SubmissionStatus, string> = {
  submitted: '待初审',
  initial_reviewing: '初审中',
  pending_final: '待终审',
  accepted: '终审通过',
  rejected: '未通过',
}

const STATUS_STYLE: Record<SubmissionStatus, string> = {
  submitted: 'border-[#eddfbf] bg-[#fcf6e9] text-[#8b6628]',
  initial_reviewing: 'border-[#cedee6] bg-[#eff5f8] text-[#316b86]',
  pending_final: 'border-[#d5d0ea] bg-[#f1effa] text-[#5546a5]',
  accepted: 'border-[#cae3d5] bg-[#edf6ef] text-[#27724f]',
  rejected: 'border-[#efcecd] bg-[#fdf0ee] text-[#a6403d]',
}

const STAGE_LABELS: Record<ReviewStage, string> = {
  initial: '形式初审',
  final: '内容终审',
}

function fileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function Status({ value }: { value: SubmissionStatus }) {
  return <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium leading-none before:size-1.5 before:rounded-full before:bg-current ${STATUS_STYLE[value]}`}>{STATUS_LABELS[value]}</span>
}

export default function OccupationalSubmissions() {
  const admin = useAuthStore((state) => state.admin)
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [workType, setWorkType] = useState('')
  const [searchText, setSearchText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [comment, setComment] = useState('')
  const [score, setScore] = useState('')
  const [downloading, setDownloading] = useState(false)
  const pageSize = 20

  const canRead = can(admin?.role, 'occupational.read')
  const canInitial = can(admin?.role, 'occupational.initial_review')
  const canFinal = can(admin?.role, 'occupational.final_review')

  const listQuery = useQuery({
    queryKey: ['occupational-submissions', page, status, workType, keyword],
    queryFn: async () => {
      const { data } = await api.get<Page<SubmissionItem>>('/admin/occupational-submissions', {
        params: {
          page,
          page_size: pageSize,
          status: status || undefined,
          work_type: workType || undefined,
          q: keyword || undefined,
        },
      })
      return data
    },
    enabled: canRead,
  })

  const detailQuery = useQuery({
    queryKey: ['occupational-submission', selectedId],
    queryFn: async () => {
      const { data } = await api.get<SubmissionDetail>(`/admin/occupational-submissions/${selectedId}`)
      return data
    },
    enabled: canRead && selectedId !== null,
  })

  const detail = detailQuery.data
  useEffect(() => {
    setComment('')
    setScore('')
  }, [detail?.id, detail?.status])

  const initialMutation = useMutation({
    mutationFn: async (payload: { action: 'claim' | 'release' | 'pass' | 'reject'; comment?: string }) => {
      if (selectedId === null) throw new Error('未选择投稿')
      const { data } = await api.post<SubmissionDetail>(
        `/admin/occupational-submissions/${selectedId}/initial-review`,
        payload,
      )
      return data
    },
    onSuccess: async () => {
      toast.success('初审操作已记录')
      await queryClient.invalidateQueries({ queryKey: ['occupational-submissions'] })
      await queryClient.invalidateQueries({ queryKey: ['occupational-submission', selectedId] })
    },
  })

  const finalMutation = useMutation({
    mutationFn: async (payload: { action: 'pass' | 'reject'; comment?: string; score?: number }) => {
      if (selectedId === null) throw new Error('未选择投稿')
      const { data } = await api.post<SubmissionDetail>(
        `/admin/occupational-submissions/${selectedId}/final-review`,
        payload,
      )
      return data
    },
    onSuccess: async () => {
      toast.success('终审结论已记录')
      await queryClient.invalidateQueries({ queryKey: ['occupational-submissions'] })
      await queryClient.invalidateQueries({ queryKey: ['occupational-submission', selectedId] })
    },
  })

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPage(1)
    setKeyword(searchText.trim())
  }

  function submitInitialReject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!comment.trim()) {
      toast.error('初审驳回时请填写理由')
      return
    }
    initialMutation.mutate({ action: 'reject', comment: comment.trim() })
  }

  function runFinalPass() {
    const parsedScore = score.trim() === '' ? undefined : Number(score)
    if (parsedScore !== undefined && (!Number.isInteger(parsedScore) || parsedScore < 0 || parsedScore > 100)) {
      toast.error('分数须为 0–100 的整数')
      return
    }
    finalMutation.mutate({ action: 'pass', comment: comment.trim() || undefined, score: parsedScore })
  }

  function runFinalReject() {
    if (!comment.trim()) {
      toast.error('终审驳回时请填写理由')
      return
    }
    finalMutation.mutate({ action: 'reject', comment: comment.trim() })
  }

  async function downloadFile() {
    if (!detail || downloading) return
    setDownloading(true)
    try {
      const response = await api.get<Blob>(`/admin/occupational-submissions/${detail.id}/file`, {
        responseType: 'blob',
        timeout: 300_000,
      })
      const blobUrl = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = detail.original_filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000)
    } catch {
      // API 响应拦截器已经显示下载失败原因。
    } finally {
      setDownloading(false)
    }
  }

  if (!canRead) {
    return <p className="admin-state rounded-xl border border-border bg-card">当前账号没有查看职业健康投稿的权限。</p>
  }

  // 初审软锁的可用性判断（与后端语义一致）
  const lockHeldByMe = detail?.lock.locked_by_admin_id != null && detail.lock.locked_by_admin_id === admin?.id
  const lockBlocked = !!detail && detail.status === 'initial_reviewing' && !lockHeldByMe && !detail.lock.expired
  const initialRecord = detail?.reviews.find((r) => r.stage === 'initial') ?? null
  const finalByMeBlocked =
    !!detail && detail.status === 'pending_final' && initialRecord?.reviewer_id === admin?.id
  const finalDoneByOther = !!detail && detail.status === 'pending_final' && !canFinal

  return (
    <div className="admin-page">
      <div className="admin-page-header">
        <h2 className="text-lg font-semibold">2026 职业健康科普作品征集</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          两级评审：编辑负责形式初审（材料/格式/合规），管理员负责内容终审；同一篇稿件的初审与终审不得为同一账号。
          此处的「终审通过」仅是后台处理状态，不代表公众号收录、区域证书或全国评选结果。
        </p>
      </div>

      <div className="admin-filter-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            <span>处理状态</span>
            <select
              value={status}
              onChange={(event) => { setStatus(event.target.value); setPage(1) }}
              className="block h-9 min-w-32 rounded-md border border-input bg-background px-3 text-sm text-foreground"
            >
              <option value="">全部</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            <span>作品形式</span>
            <select
              value={workType}
              onChange={(event) => { setWorkType(event.target.value); setPage(1) }}
              className="block h-9 min-w-32 rounded-md border border-input bg-background px-3 text-sm text-foreground"
            >
              <option value="">全部</option>
              <option value="article">图文</option>
              <option value="video">视频</option>
            </select>
          </label>
          <form onSubmit={submitSearch} className="flex flex-1 items-end gap-2">
            <label className="min-w-40 flex-1 space-y-1 text-xs text-muted-foreground">
              <span>搜索标题、作者或回执</span>
              <Input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="输入关键词" />
            </label>
            <Button type="submit" variant="outline"><Search />查询</Button>
          </form>
        </div>
      </div>

      <div className="admin-data-panel">
        {listQuery.isLoading ? (
          <div className="space-y-3 p-5"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /></div>
        ) : listQuery.isError ? (
          <DataState title="投稿列表加载失败" description="请检查服务连接后重试。" onRetry={() => void listQuery.refetch()} />
        ) : listQuery.data?.items.length === 0 ? (
          <DataState title="暂无投稿" description="当前筛选条件下没有投稿记录。" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader><TableRow>
                <TableHead>回执编号</TableHead><TableHead>作品</TableHead><TableHead>作者</TableHead><TableHead>形式</TableHead><TableHead>状态</TableHead><TableHead>投稿时间</TableHead><TableHead className="text-right">操作</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {listQuery.data?.items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="font-mono text-xs">{item.receipt_no}</TableCell>
                    <TableCell className="max-w-64"><p className="truncate font-medium" title={item.title}>{item.title}</p><p className="mt-1 truncate text-xs text-muted-foreground" title={item.topic}>{item.topic}</p></TableCell>
                    <TableCell><p>{item.author_name}</p><p className="mt-1 max-w-40 truncate text-xs text-muted-foreground">{item.organization} · {item.phone_masked}</p></TableCell>
                    <TableCell><span className="inline-flex items-center gap-1 text-xs">{item.work_type === 'article' ? <FileText className="size-4" /> : <Video className="size-4" />}{item.work_type === 'article' ? '图文' : '视频'}</span></TableCell>
                    <TableCell><Status value={item.status} /></TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(item.submitted_at)}</TableCell>
                    <TableCell className="text-right"><Button variant="outline" size="sm" onClick={() => setSelectedId(item.id)}>查看与评审</Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        <PagePagination
          page={page}
          pageSize={pageSize}
          total={listQuery.data?.total ?? 0}
          onChange={setPage}
          className="border-t px-4 py-3"
        />
      </div>

      <Dialog open={selectedId !== null} onOpenChange={(open) => { if (!open) setSelectedId(null) }}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>投稿详情与评审</DialogTitle>
            <DialogDescription>作者资料和原文件仅供本次征集评审、联系使用。</DialogDescription>
          </DialogHeader>
          {detailQuery.isLoading ? <Skeleton className="h-60 w-full" /> : detailQuery.isError ? (
            <DataState title="投稿详情加载失败" description="请检查服务连接后重试。" onRetry={() => void detailQuery.refetch()} />
          ) : detail ? (
            <div className="space-y-5 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <Status value={detail.status} />
                <span className="font-mono text-xs text-muted-foreground">{detail.receipt_no}</span>
                {lockBlocked && (
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-[#cedee6] bg-[#eff5f8] px-2.5 py-1 text-[11px] text-[#316b86]">
                    <Lock className="size-3" />
                    {detail.lock.locked_by_name ?? '其他初审员'} 初审中 · {formatDateTime(detail.lock.locked_at ?? '')}
                  </span>
                )}
                {detail.status === 'initial_reviewing' && detail.lock.expired && !lockHeldByMe && (
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-[#eddfbf] bg-[#fcf6e9] px-2.5 py-1 text-[11px] text-[#8b6628]">
                    <Unlock className="size-3" />
                    原领取已超时，可接管初审
                  </span>
                )}
              </div>
              <div>
                <h3 className="text-lg font-semibold">{detail.title}</h3>
                <p className="mt-1 text-muted-foreground">{detail.topic} · {detail.work_type === 'article' ? '图文' : '视频'}</p>
              </div>
              <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2 rounded-md bg-muted/50 p-4">
                <dt className="text-muted-foreground">作者</dt><dd>{detail.author_name}</dd>
                <dt className="text-muted-foreground">工作单位</dt><dd>{detail.organization}</dd>
                <dt className="text-muted-foreground">联系电话</dt><dd>{detail.phone}<p className="mt-1 text-xs text-muted-foreground">作者自行填写，尚未通过短信核验</p></dd>
                <dt className="text-muted-foreground">投稿时间</dt><dd>{formatDateTime(detail.submitted_at)}</dd>
                <dt className="text-muted-foreground">资料同意</dt><dd>{detail.consent_review_contact ? `已确认 · ${formatDateTime(detail.consented_at)}` : '未记录'}<p className="mt-1 text-xs text-muted-foreground">{detail.consent_text}</p></dd>
              </dl>
              <div><p className="font-medium">作品简介</p><p className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">{detail.summary}</p></div>
              <div className="rounded-md border p-4">
                <p className="break-all font-medium">{detail.original_filename}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {fileSize(detail.file_size)}
                  {detail.word_count !== null ? ` · ${detail.word_count} 字` : ''}
                  {detail.duration_seconds !== null ? ` · ${Math.round(detail.duration_seconds)} 秒` : ''}
                  {detail.video_width !== null && detail.video_height !== null ? ` · ${detail.video_width}×${detail.video_height}` : ''}
                </p>
                <Button type="button" variant="outline" className="mt-3" disabled={downloading} onClick={() => void downloadFile()}>
                  {downloading ? <Loader2 className="animate-spin" /> : <Download />}
                  {downloading ? '下载中' : '下载原文件'}
                </Button>
              </div>

              {detail.reviews.length > 0 && (
                <div className="space-y-2">
                  <p className="font-medium">分级评审记录</p>
                  {detail.reviews.map((record) => (
                    <div key={record.stage} className="rounded-md border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-xs font-medium">
                          {STAGE_LABELS[record.stage]} · {record.conclusion === 'passed' ? '通过' : '驳回'}
                          {record.score !== null ? ` · ${record.score} 分` : ''}
                        </p>
                        <p className="text-xs text-muted-foreground">{record.reviewer_name} · {formatDateTime(record.created_at)}</p>
                      </div>
                      {record.comment && <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">{record.comment}</p>}
                    </div>
                  ))}
                </div>
              )}

              {/* —— 初审操作区 —— */}
              {canInitial && detail.status === 'submitted' && (
                <div className="space-y-3 border-t pt-4">
                  <Label>形式初审</Label>
                  <p className="text-xs text-muted-foreground">领取后稿件进入「初审中」并由你持有 30 分钟处理锁，期间其他初审员不能操作。</p>
                  <Button
                    type="button"
                    disabled={initialMutation.isPending}
                    onClick={() => initialMutation.mutate({ action: 'claim' })}
                  >
                    {initialMutation.isPending && <Loader2 className="animate-spin" />}
                    领取初审
                  </Button>
                </div>
              )}
              {canInitial && detail.status === 'initial_reviewing' && !lockBlocked && (
                <form onSubmit={submitInitialReject} className="space-y-3 border-t pt-4">
                  <Label>形式初审（{lockHeldByMe ? '你持有处理锁' : '原锁已超时，可接管'}）</Label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      disabled={initialMutation.isPending}
                      onClick={() => initialMutation.mutate({ action: 'pass', comment: comment.trim() || undefined })}
                    >
                      {initialMutation.isPending && <Loader2 className="animate-spin" />}
                      初审通过，转待终审
                    </Button>
                    {lockHeldByMe && (
                      <Button
                        type="button"
                        variant="outline"
                        disabled={initialMutation.isPending}
                        onClick={() => initialMutation.mutate({ action: 'release' })}
                      >
                        释放回待初审
                      </Button>
                    )}
                  </div>
                  <Label htmlFor="occupational-initial-comment">初审意见（驳回时必填）</Label>
                  <Textarea id="occupational-initial-comment" rows={3} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="仅后台可见；驳回理由会通过回执查询告知作者" />
                  <Button type="submit" variant="destructive" disabled={initialMutation.isPending}>
                    {initialMutation.isPending && <Loader2 className="animate-spin" />}
                    初审驳回
                  </Button>
                </form>
              )}
              {lockBlocked && canInitial && (
                <p className="rounded-md border border-[#cedee6] bg-[#eff5f8] p-3 text-xs text-[#316b86]">
                  该稿件正由 {detail.lock.locked_by_name ?? '其他初审员'} 处理，锁超时（30 分钟）后可接管。
                </p>
              )}

              {/* —— 终审操作区 —— */}
              {detail.status === 'pending_final' && canFinal && !finalByMeBlocked && (
                <form className="space-y-3 border-t pt-4">
                  <Label>内容终审</Label>
                  <p className="text-xs text-muted-foreground">
                    初审：{initialRecord?.reviewer_name ?? '—'}
                    {initialRecord?.conclusion === 'passed' ? ' 已通过' : ''}。终审结论与初审意见分级留痕。
                  </p>
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="space-y-1 text-xs text-muted-foreground">
                      <span>内容分数（可选，0–100）</span>
                      <Input
                        inputMode="numeric"
                        className="w-24"
                        value={score}
                        onChange={(event) => setScore(event.target.value)}
                        placeholder="如 88"
                      />
                    </label>
                  </div>
                  <Label htmlFor="occupational-final-comment">终审意见（驳回时必填）</Label>
                  <Textarea id="occupational-final-comment" rows={3} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="仅后台可见；驳回理由会通过回执查询告知作者" />
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" disabled={finalMutation.isPending} onClick={runFinalPass}>
                      {finalMutation.isPending && <Loader2 className="animate-spin" />}
                      终审通过
                    </Button>
                    <Button type="button" variant="destructive" disabled={finalMutation.isPending} onClick={runFinalReject}>
                      终审驳回
                    </Button>
                  </div>
                </form>
              )}
              {finalByMeBlocked && (
                <p className="rounded-md border border-[#eddfbf] bg-[#fcf6e9] p-3 text-xs text-[#8b6628]">
                  这篇稿件的初审是你完成的，按双签规则需由其他管理员终审。
                </p>
              )}
              {finalDoneByOther && (
                <p className="rounded-md border border-[#d5d0ea] bg-[#f1effa] p-3 text-xs text-[#5546a5]">
                  稿件已通过初审，等待具备终审权限的管理员处理。
                </p>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
