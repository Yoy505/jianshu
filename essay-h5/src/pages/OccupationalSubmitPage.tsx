import { useEffect, useRef, useState, type FormEvent } from 'react';
import axios from 'axios';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { apiErrorDetail } from '../lib/api';
import {
  getOccupationalMeta,
  queryOccupationalReceipt,
  OCCUPATIONAL_TOPICS,
  submitOccupationalWork,
  type OccupationalReceipt,
  type OccupationalReceiptStatus,
  type OccupationalWorkType,
} from '../lib/occupational';
import { OccupationalFooter, OccupationalHeader } from '../components/OccupationalShell';

const OTHER_TOPIC = '__other__';
const PHONE_RE = /^(?:1[3-9]\d{9}|0\d{2,3}-[1-9]\d{6,7}(?:[-#转]\d{1,6})?)$/;
const SUBMISSION_ID_KEY = 'occupational-client-submission-id';
const LAST_RECEIPT_KEY = 'occupational-last-receipt';

/** 终态：轮询停止条件 */
const FINAL_STATUSES: OccupationalReceiptStatus['status'][] = ['accepted', 'rejected'];

const PROGRESS_COPY: Record<OccupationalReceiptStatus['status'], string> = {
  submitted: '已提交，等待初审',
  initial_reviewing: '初审中',
  pending_final: '初审已通过，等待终审',
  accepted: '审核通过',
  rejected: '未通过',
};

const PROGRESS_STEPS: Array<{ label: string; reached: (s: OccupationalReceiptStatus['status']) => boolean }> = [
  { label: '提交', reached: () => true },
  { label: '形式初审', reached: (s) => s === 'pending_final' || s === 'accepted' || s === 'rejected' },
  { label: '内容终审', reached: (s) => s === 'accepted' },
];

function readStoredReceipt(): string | null {
  try {
    return window.localStorage.getItem(LAST_RECEIPT_KEY);
  } catch {
    return null;
  }
}

function storeReceipt(receiptNo: string | null): void {
  try {
    if (receiptNo) window.localStorage.setItem(LAST_RECEIPT_KEY, receiptNo);
    else window.localStorage.removeItem(LAST_RECEIPT_KEY);
  } catch { /* 存储被禁时仅当前会话内跟踪 */ }
}

function newClientSubmissionId(): string {
  try {
    return window.sessionStorage.getItem(SUBMISSION_ID_KEY) || window.crypto.randomUUID();
  } catch {
    return window.crypto.randomUUID();
  }
}

function isSameAttachment(previous: File | null, next: File | null): boolean {
  if (previous === next) return true;
  if (!previous || !next) return false;
  return previous.name === next.name
    && previous.size === next.size
    && previous.lastModified === next.lastModified
    && previous.type === next.type
    && previous.webkitRelativePath === next.webkitRelativePath;
}

function fileSizeLabel(bytes: number): string {
  return `${Math.round(bytes / 1024 / 1024)} MiB`;
}

export default function OccupationalSubmitPage() {
  const metaQuery = useQuery({
    queryKey: ['occupational-submissions', 'meta'],
    queryFn: getOccupationalMeta,
    staleTime: 30_000,
  });
  const meta = metaQuery.data;
  const [workType, setWorkType] = useState<OccupationalWorkType>('article');
  const [title, setTitle] = useState('');
  const [topic, setTopic] = useState('');
  const [otherTopic, setOtherTopic] = useState('');
  const [authorName, setAuthorName] = useState('');
  const [organization, setOrganization] = useState('');
  const [phone, setPhone] = useState('');
  const [summary, setSummary] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<OccupationalReceipt | null>(null);
  const [trackedReceiptNo, setTrackedReceiptNo] = useState<string | null>(readStoredReceipt);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [clientSubmissionId, setClientSubmissionId] = useState(newClientSubmissionId);

  // 已提交稿件的实时进度：未到终态每 15 秒自动刷新（页面切后台时暂停），终态即停
  const progressQuery = useQuery({
    queryKey: ['occupational-receipt-progress', trackedReceiptNo],
    queryFn: () => queryOccupationalReceipt(trackedReceiptNo as string),
    enabled: trackedReceiptNo !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && FINAL_STATUSES.includes(status) ? false : 15_000;
    },
  });
  const progressStatus = progressQuery.data ?? null;
  const progressFinal = progressStatus ? FINAL_STATUSES.includes(progressStatus.status) : false;

  useEffect(() => {
    document.title = '提交作品 · 2026 职业健康科普作品征集';
  }, []);

  useEffect(() => {
    try { window.sessionStorage.setItem(SUBMISSION_ID_KEY, clientSubmissionId); } catch { /* 浏览器禁用存储时仍可在当前页面重试 */ }
    return () => {
      try { window.sessionStorage.removeItem(SUBMISSION_ID_KEY); } catch { /* 同上 */ }
    };
  }, [clientSubmissionId]);

  const topics = meta?.topics?.length ? meta.topics : [...OCCUPATIONAL_TOPICS];
  const isOpen = meta?.enabled === true;
  const maxFileBytes = workType === 'article'
    ? meta?.limits.article_max_bytes ?? 10 * 1024 * 1024
    : meta?.limits.video_max_bytes ?? 300 * 1024 * 1024;

  const rotateClientSubmissionId = () => {
    const nextId = window.crypto.randomUUID();
    setClientSubmissionId(nextId);
    try { window.sessionStorage.setItem(SUBMISSION_ID_KEY, nextId); } catch { /* 浏览器禁用存储时仍可在当前页面重试 */ }
  };

  const changeWorkType = (next: OccupationalWorkType) => {
    if (submitting || next === workType) return;
    rotateClientSubmissionId();
    setWorkType(next);
    setFile(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setError(null);

    if (!isOpen || !meta) {
      setError('投稿通道尚未开放或暂时无法确认状态，请稍后重试。');
      return;
    }
    if (!meta.accepted_work_types.includes(workType)) {
      setError('当前投稿通道暂不接受所选作品类型。');
      return;
    }
    const finalTopic = topic === OTHER_TOPIC ? otherTopic.trim() : topic.trim();
    if (!title.trim() || !finalTopic || !authorName.trim() || !organization.trim() || !summary.trim()) {
      setError('请填写标题、征集方向、作者姓名、单位和作品简介。');
      return;
    }
    if (!PHONE_RE.test(phone)) {
      setError('请填写大陆手机号，或带区号的座机号码（可加分机）。');
      return;
    }
    if (!file) {
      setError('请选择作品文件后再提交。');
      return;
    }
    const correctExtension = workType === 'article' ? /\.docx$/i.test(file.name) : /\.mp4$/i.test(file.name);
    if (!correctExtension) {
      setError(workType === 'article'
        ? '图文作品只接收 .docx。请将 .doc 文件在 Word 中另存为 .docx。'
        : '视频作品只接收 .mp4 文件。');
      return;
    }
    if (file.size === 0 || file.size > maxFileBytes) {
      setError(`文件不能为空，且不能超过平台单文件上传限制 ${fileSizeLabel(maxFileBytes)}。`);
      return;
    }
    if (!meta.consent_text || !consent) {
      setError('请先确认仅用于本次评审与联系的数据处理说明。');
      return;
    }

    const payload = new FormData();
    payload.append('work_type', workType);
    payload.append('title', title.trim());
    payload.append('topic', finalTopic);
    payload.append('author_name', authorName.trim());
    payload.append('organization', organization.trim());
    payload.append('phone', phone);
    payload.append('summary', summary.trim());
    payload.append('consent_review_contact', 'true');
    payload.append('client_submission_id', clientSubmissionId);
    payload.append('file', file, file.name);

    setSubmitting(true);
    setProgress(0);
    try {
      const result = await submitOccupationalWork(payload, setProgress);
      setReceipt(result);
      setTrackedReceiptNo(result.receipt_no);
      storeReceipt(result.receipt_no);
      try { window.sessionStorage.removeItem(SUBMISSION_ID_KEY); } catch { /* 同上 */ }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (caught) {
      if (axios.isAxiosError(caught) && caught.response?.status === 409) {
        rotateClientSubmissionId();
        setError('原提交编号可能已有一份已提交作品。当前表单和附件已保留；请核对后再提交。再次提交会作为新投稿生成另一条记录。');
        return;
      }
      const detail = caught instanceof Error && !('response' in caught)
        ? caught.message
        : apiErrorDetail(caught, '作品未提交成功，请检查文件要求后重试。');
      setError(`${detail} 表单会保留；网络异常时可在当前页重试，相同投稿编号不会重复创建作品。`);
    } finally {
      setSubmitting(false);
    }
  };

  const showProgressCard = receipt !== null || trackedReceiptNo !== null;
  const shownReceiptNo = trackedReceiptNo ?? receipt?.receipt_no ?? null;

  const startNewSubmission = () => {
    setReceipt(null);
    setTrackedReceiptNo(null);
    storeReceipt(null);
    setError(null);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="oh-page oh-submit-page">
      <OccupationalHeader />
      <main className="oh-submit-main">
        <div className="oh-container">
          <div className="oh-submit-heading">
            <p className="oh-kicker">SUBMISSION / 在线投稿</p>
            <h1>{showProgressCard ? '作品已提交' : <>提交职业健康<br className="oh-mobile-title-break" />科普作品</>}</h1>
            <p>{showProgressCard
              ? '下方实时显示评审进度，无需手动查询；页面保持打开会自动刷新。'
              : '填写作品信息并准备文件；能否提交以此页实时通道状态为准。'}</p>
          </div>

          {showProgressCard && shownReceiptNo ? (
            <section className="oh-receipt oh-receipt-result" aria-live="polite">
              <div className="oh-receipt-mark" aria-hidden="true">✓</div>
              <p className="oh-receipt-label">投稿回执号</p>
              <strong className="oh-receipt-number">{shownReceiptNo}</strong>
              {progressStatus ? (
                <>
                  <p className={`oh-receipt-status is-${progressStatus.status}`}>{PROGRESS_COPY[progressStatus.status]}</p>
                  <ol className="oh-receipt-steps">
                    {PROGRESS_STEPS.map((step) => (
                      <li key={step.label} data-reached={step.reached(progressStatus.status)}>
                        <span aria-hidden="true" />
                        {step.label}
                      </li>
                    ))}
                  </ol>
                  {progressStatus.status === 'rejected' && progressStatus.comment && (
                    <p className="oh-receipt-comment"><strong>未通过理由：</strong>{progressStatus.comment}</p>
                  )}
                  <p className="oh-receipt-hint">
                    {progressFinal
                      ? '评审已结束。后续安排以活动组织方通知为准。'
                      : '评审进度每 15 秒自动刷新一次，无需手动查询；也可以稍后再回来，此页会继续显示最新进度。'}
                  </p>
                </>
              ) : (
                <p className="oh-receipt-hint">
                  {progressQuery.isError
                    ? '暂时无法获取评审进度，稍后会自动重试。'
                    : '正在获取最新评审进度…'}
                </p>
              )}
              <div className="oh-receipt-actions">
                <button type="button" className="oh-button oh-button-secondary" onClick={startNewSubmission}>
                  再投一篇
                </button>
                <Link className="oh-receipt-detail-link" to={`/receipt?no=${encodeURIComponent(shownReceiptNo)}`}>
                  查看详情
                </Link>
              </div>
            </section>
          ) : (
            <div className="oh-submit-grid">
              <form className="oh-submit-form" onSubmit={(event) => void handleSubmit(event)} noValidate aria-busy={submitting}>
                {metaQuery.isPending ? (
                  <div className="oh-status oh-status-info" role="status">正在确认投稿通道状态…</div>
                ) : metaQuery.isError ? (
                  <div className="oh-status oh-status-error" role="alert">
                    暂时无法确认投稿通道状态。请检查网络后重试。
                    <button type="button" onClick={() => void metaQuery.refetch()}>重新检查</button>
                  </div>
                ) : !isOpen ? (
                  <div className="oh-status oh-status-closed" role="status">投稿通道当前未开放。你可以先查看要求并准备作品，开放状态以此页为准。</div>
                ) : (
                  <div className="oh-status oh-status-ready" role="status">投稿通道已开放</div>
                )}

                <fieldset className="oh-form-section" disabled={submitting}>
                  <legend><span>01</span> 作品信息</legend>
                  <div className="oh-field">
                    <span className="oh-label">作品形式 <em>*</em></span>
                    <div className="oh-type-grid" role="group" aria-label="作品形式">
                      <label className={`oh-type-choice ${workType === 'article' ? 'is-selected' : ''}`}>
                        <input type="radio" name="work-type" value="article" checked={workType === 'article'} onChange={() => changeWorkType('article')} />
                        <strong>图文作品</strong><span>Word .docx</span>
                      </label>
                      <label className={`oh-type-choice ${workType === 'video' ? 'is-selected' : ''}`}>
                        <input type="radio" name="work-type" value="video" checked={workType === 'video'} onChange={() => changeWorkType('video')} />
                        <strong>科普视频</strong><span>MP4 · ≥1080P</span>
                      </label>
                    </div>
                  </div>

                  <label className="oh-field">
                    <span className="oh-label">作品标题 <em>*</em></span>
                    <input className="oh-input" type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="请填写作品标题" required />
                  </label>

                  <label className="oh-field">
                    <span className="oh-label">征集方向 <em>*</em></span>
                    <select className="oh-input" value={topic} onChange={(e) => setTopic(e.target.value)} required>
                      <option value="">请选择作品方向</option>
                      {topics.map((item) => <option value={item} key={item}>{item}</option>)}
                      {(meta?.accepts_other_topic ?? true) && <option value={OTHER_TOPIC}>其他职业健康主题</option>}
                    </select>
                  </label>
                  {topic === OTHER_TOPIC && (
                    <label className="oh-field">
                      <span className="oh-label">其他方向名称 <em>*</em></span>
                      <input className="oh-input" type="text" value={otherTopic} onChange={(e) => setOtherTopic(e.target.value)} placeholder="请简要写明职业健康主题" required />
                    </label>
                  )}

                  <label className="oh-field">
                    <span className="oh-label">作品简介 <em>*</em></span>
                    <textarea className="oh-input oh-textarea" value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="简要介绍作品关注的问题与核心内容" rows={4} required />
                  </label>
                </fieldset>

                <fieldset className="oh-form-section" disabled={submitting}>
                  <legend><span>02</span> 作者与联系信息</legend>
                  <div className="oh-field-pair">
                    <label className="oh-field">
                      <span className="oh-label">作者姓名 <em>*</em></span>
                      <input className="oh-input" type="text" autoComplete="name" value={authorName} onChange={(e) => setAuthorName(e.target.value)} placeholder="请填写真实姓名" required />
                    </label>
                    <label className="oh-field">
                      <span className="oh-label">工作单位 <em>*</em></span>
                      <input className="oh-input" type="text" autoComplete="organization" value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="请填写单位名称" required />
                    </label>
                  </div>
                  <label className="oh-field">
                    <span className="oh-label">联系电话 <em>*</em></span>
                    <input className="oh-input" type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value.replace(/[^\d#转-]/g, '').slice(0, 20))} placeholder="如 13800000000 或 022-12345678" required />
                  </label>
                </fieldset>

                <fieldset className="oh-form-section" disabled={submitting}>
                  <legend><span>03</span> 上传作品</legend>
                  <label className="oh-upload" htmlFor="oh-work-file">
                    <span className="oh-upload-icon" aria-hidden="true">↑</span>
                    <strong>{file ? file.name : '选择作品文件'}</strong>
                    <span>{file ? `${fileSizeLabel(file.size)} · 点击更换` : workType === 'article'
                      ? `.docx · 平台上传限制 ${fileSizeLabel(maxFileBytes)}`
                      : `.mp4 · 平台上传限制 ${fileSizeLabel(maxFileBytes)}`}</span>
                  </label>
                  <input
                    id="oh-work-file"
                    ref={fileInputRef}
                    className="oh-file-input"
                    type="file"
                    aria-describedby="oh-file-help"
                    accept={workType === 'article' ? '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document' : '.mp4,video/mp4'}
                    onChange={(e) => {
                      const nextFile = e.target.files?.[0] ?? null;
                      if (!isSameAttachment(file, nextFile)) rotateClientSubmissionId();
                      setFile(nextFile);
                      setError(null);
                    }}
                    required
                  />
                  <p className="oh-help" id="oh-file-help">{workType === 'article'
                    ? '.doc 文件请在 Word 中另存为 .docx 后上传；服务器将在提交后解析校验文件。'
                    : '画面不低于 1080P（横屏或竖屏均可）；服务器将在提交后校验文件。'}</p>
                </fieldset>

                <div className="oh-consent-box">
                  <label className="oh-consent-label">
                    <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} disabled={submitting || !meta?.consent_text} />
                    <span>{meta?.consent_text || '正在加载本次投稿的数据处理说明…'}<strong>此项确认不构成作品公开展示或传播授权。</strong></span>
                  </label>
                </div>

                {error && <p className="oh-form-error" role="alert">{error}</p>}
                {submitting && (
                  <div className="oh-progress" role="status" aria-live="polite">
                    <span>{progress < 100 ? `正在上传 · ${progress}%` : '文件已上传，正在校验并生成回执…'}</span>
                    <div><span style={{ width: `${progress}%` }} /></div>
                  </div>
                )}
                <button className="oh-button oh-button-primary oh-submit-button" type="submit" disabled={!isOpen || submitting}>
                  {submitting ? '正在提交，请勿关闭页面' : '确认提交作品'}
                </button>
                <p className="oh-submit-hint">仅在服务器生成回执号后，投稿才视为成功。</p>
              </form>

              <aside className="oh-submit-aside" aria-label="投稿须知">
                <div className="oh-aside-card">
                  <p className="oh-kicker">BEFORE YOU SUBMIT</p>
                  <h2>提交前检查</h2>
                  <ul>
                    <li>作品围绕职业健康主题，科学严谨、通俗易懂。</li>
                    <li>图文文件为 .docx；视频文件为 MP4（不低于 1080P）。</li>
                    <li>作者姓名、单位、联系电话与作品简介均已填写。</li>
                  </ul>
                </div>
                <div className="oh-aside-note">
                  <strong>评审安排</strong>
                  <p>线上初筛：2026 年 10 月 8 日—11 月 12 日<br />区域复评：11 月 13 日—12 月 5 日<br />全国终评暨线上分享会：12 月 10 日</p>
                  <small>以上为评审时间，并非投稿截止时间。</small>
                </div>
              </aside>
            </div>
          )}
        </div>
      </main>
      <OccupationalFooter />
    </div>
  );
}
