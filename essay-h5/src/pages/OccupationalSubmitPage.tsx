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
type FieldName = 'title' | 'topic' | 'otherTopic' | 'summary' | 'authorName' | 'organization' | 'phone' | 'file' | 'consent';
type FieldErrors = Partial<Record<FieldName, string>>;

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
  { label: '形式初审', reached: (s) => s === 'pending_final' || s === 'accepted' },
  { label: '内容终审', reached: (s) => s === 'accepted' },
];

function readStoredReceipt(): string | null {
  try {
    const stored = window.localStorage.getItem(LAST_RECEIPT_KEY)?.trim().toUpperCase() ?? '';
    return /^OCC-[0-9A-F]{16}$/.test(stored) ? stored : null;
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
  if (bytes === 0) return '0 B';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.ceil(bytes / 1024))} KiB`;
  return `${Number((bytes / 1024 / 1024).toFixed(1))} MiB`;
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
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const formRef = useRef<HTMLFormElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
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
      if (axios.isAxiosError(query.state.error) && query.state.error.response?.status === 404) return false;
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

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  const clearFieldError = (field: FieldName) => {
    setFieldErrors((previous) => ({ ...previous, [field]: undefined }));
  };
  const fieldProps = (field: FieldName) => ({
    id: `oh-${field}`,
    'aria-labelledby': field !== 'file' && field !== 'consent' ? `oh-${field}-label` : undefined,
    'aria-invalid': Boolean(fieldErrors[field]),
    'aria-describedby': fieldErrors[field] ? `oh-${field}-error` : undefined,
  });
  const fieldError = (field: FieldName) => fieldErrors[field]
    ? <span className="oh-field-error" id={`oh-${field}-error`}>{fieldErrors[field]}</span>
    : null;

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
    clearFieldError('file');
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
    const errors: FieldErrors = {};
    if (!title.trim()) errors.title = '请填写作品标题。';
    if (!topic) errors.topic = '请选择征集方向。';
    if (topic === OTHER_TOPIC && !otherTopic.trim()) errors.otherTopic = '请填写其他方向名称。';
    if (!summary.trim()) errors.summary = '请简要介绍作品关注的问题与核心内容。';
    if (!authorName.trim()) errors.authorName = '请填写作者姓名。';
    if (!organization.trim()) errors.organization = '请填写工作单位。';
    if (!PHONE_RE.test(phone)) errors.phone = '请填写大陆手机号，或带区号的座机号码（可加分机）。';
    if (!file) errors.file = '请选择作品文件。';
    else if (!(workType === 'article' ? /\.docx$/i : /\.mp4$/i).test(file.name)) {
      errors.file = workType === 'article' ? '请上传 .docx 文件；.doc 请在 Word 中另存为 .docx。' : '请上传 .mp4 文件。';
    } else if (file.size === 0 || file.size > maxFileBytes) {
      errors.file = `文件不能为空，且不能超过 ${fileSizeLabel(maxFileBytes)}。`;
    }
    if (!meta.consent_text || !consent) errors.consent = '请阅读并确认本次评审与联系的数据处理说明。';
    setFieldErrors(errors);
    if (Object.keys(errors).length || !file) {
      window.requestAnimationFrame(() => {
        formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
      });
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
    rotateClientSubmissionId();
    setTitle('');
    setTopic('');
    setOtherTopic('');
    setSummary('');
    setAuthorName('');
    setOrganization('');
    setPhone('');
    setFile(null);
    setConsent(false);
    setProgress(0);
    setFieldErrors({});
    setReceipt(null);
    setTrackedReceiptNo(null);
    storeReceipt(null);
    setError(null);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="oh-page oh-submit-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1} className="oh-submit-main">
        <div className="oh-container">
          <div className="oh-submit-heading">
            <p className="oh-kicker">SUBMISSION / 在线投稿</p>
            <h1>{showProgressCard ? (receipt || progressStatus ? '作品已提交' : '核对投稿记录') : <>提交职业健康<br className="oh-mobile-title-break" />科普作品</>}</h1>
            <p>{showProgressCard
              ? (receipt || progressStatus ? '保存好回执号，可随时查询作品的评审进度。' : '发现本设备保存的回执号，正在向服务器核对实际状态。')
              : '填写作品信息并准备文件；能否提交以此页实时通道状态为准。'}</p>
          </div>

          {showProgressCard && shownReceiptNo ? (
            <section className="oh-receipt oh-receipt-result" aria-live="polite">
              {(receipt || progressStatus) && <p className="oh-receipt-confirmed">服务器已确认收稿</p>}
              <p className="oh-receipt-label">投稿回执号</p>
              <strong className="oh-receipt-number">{shownReceiptNo}</strong>
              {progressStatus ? (
                <>
                  <p className={`oh-receipt-status is-${progressStatus.status}`}>{PROGRESS_COPY[progressStatus.status]}</p>
                  {progressStatus.status !== 'rejected' && <ol className="oh-receipt-steps">
                    {PROGRESS_STEPS.map((step) => (
                      <li key={step.label} data-reached={step.reached(progressStatus.status)}>
                        <span aria-hidden="true" />
                        {step.label}
                      </li>
                    ))}
                  </ol>}
                  {progressStatus.status === 'rejected' && progressStatus.comment && (
                    <p className="oh-receipt-comment"><strong>未通过理由：</strong>{progressStatus.comment}</p>
                  )}
                  <p className="oh-receipt-hint">
                    {progressQuery.isError
                      ? '暂时无法刷新进度，以上为上次查询结果。请稍后重试。'
                      : progressFinal
                      ? '评审已结束。后续安排以活动组织方通知为准。'
                      : '评审进度每 15 秒自动刷新一次，无需手动查询；也可以稍后再回来，此页会继续显示最新进度。'}
                  </p>
                </>
              ) : (
                <p className="oh-receipt-hint">
                  {progressQuery.isError
                    ? (axios.isAxiosError(progressQuery.error) && progressQuery.error.response?.status === 404
                      ? '未查到本设备保存的回执号，请核对编号，或返回投稿表单。'
                      : '暂时无法获取评审进度，稍后会自动重试。')
                    : '正在获取最新评审进度…'}
                </p>
              )}
              <div className="oh-receipt-actions">
                <button type="button" className="oh-button oh-button-secondary" onClick={startNewSubmission}>
                  {receipt || progressStatus ? '提交新作品' : '返回投稿表单'}
                </button>
                <Link className="oh-receipt-detail-link" to={`/receipt?no=${encodeURIComponent(shownReceiptNo)}`}>
                  查看详情
                </Link>
              </div>
            </section>
          ) : (
            <div className="oh-submit-grid">
              <form ref={formRef} className="oh-submit-form" onSubmit={(event) => void handleSubmit(event)} noValidate aria-busy={submitting}>
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

                <p className="oh-required-note">标有 <span>*</span> 的项目均为必填。</p>
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
                    <span id="oh-title-label" className="oh-label">作品标题 <em>*</em></span>
                    <input className="oh-input" type="text" {...fieldProps('title')} value={title} onChange={(e) => { setTitle(e.target.value); clearFieldError('title'); }} placeholder="请填写作品标题" required />
                    {fieldError('title')}
                  </label>

                  <label className="oh-field">
                    <span id="oh-topic-label" className="oh-label">征集方向 <em>*</em></span>
                    <select className="oh-input" {...fieldProps('topic')} value={topic} onChange={(e) => { setTopic(e.target.value); clearFieldError('topic'); }} required>
                      <option value="">请选择作品方向</option>
                      {topics.map((item) => <option value={item} key={item}>{item}</option>)}
                      {(meta?.accepts_other_topic ?? true) && <option value={OTHER_TOPIC}>其他职业健康主题</option>}
                    </select>
                    {fieldError('topic')}
                  </label>
                  {topic === OTHER_TOPIC && (
                    <label className="oh-field">
                      <span id="oh-otherTopic-label" className="oh-label">其他方向名称 <em>*</em></span>
                      <input className="oh-input" type="text" {...fieldProps('otherTopic')} value={otherTopic} onChange={(e) => { setOtherTopic(e.target.value); clearFieldError('otherTopic'); }} placeholder="请简要写明职业健康主题" required />
                    {fieldError('otherTopic')}
                  </label>
                  )}

                  <label className="oh-field">
                    <span id="oh-summary-label" className="oh-label">作品简介 <em>*</em></span>
                    <textarea className="oh-input oh-textarea" {...fieldProps('summary')} value={summary} onChange={(e) => { setSummary(e.target.value); clearFieldError('summary'); }} placeholder="简要介绍作品关注的问题与核心内容" rows={4} required />
                    {fieldError('summary')}
                  </label>
                </fieldset>

                <fieldset className="oh-form-section" disabled={submitting}>
                  <legend><span>02</span> 作者与联系信息</legend>
                  <div className="oh-field-pair">
                    <label className="oh-field">
                      <span id="oh-authorName-label" className="oh-label">作者姓名 <em>*</em></span>
                      <input className="oh-input" type="text" autoComplete="name" {...fieldProps('authorName')} value={authorName} onChange={(e) => { setAuthorName(e.target.value); clearFieldError('authorName'); }} placeholder="请填写真实姓名" required />
                    {fieldError('authorName')}
                  </label>
                    <label className="oh-field">
                      <span id="oh-organization-label" className="oh-label">工作单位 <em>*</em></span>
                      <input className="oh-input" type="text" autoComplete="organization" {...fieldProps('organization')} value={organization} onChange={(e) => { setOrganization(e.target.value); clearFieldError('organization'); }} placeholder="请填写单位名称" required />
                    {fieldError('organization')}
                  </label>
                  </div>
                  <label className="oh-field">
                    <span id="oh-phone-label" className="oh-label">联系电话 <em>*</em></span>
                    <input className="oh-input" type="tel" inputMode="tel" autoComplete="tel" {...fieldProps('phone')} value={phone} onChange={(e) => { setPhone(e.target.value.replace(/[^\d#转-]/g, '').slice(0, 20)); clearFieldError('phone'); }} placeholder="如 13800000000 或 022-12345678" required />
                    {fieldError('phone')}
                  </label>
                </fieldset>

                <fieldset className="oh-form-section" disabled={submitting}>
                  <legend><span>03</span> 上传作品</legend>
                  <label className="oh-upload" htmlFor="oh-file">
                    <span className="oh-upload-icon" aria-hidden="true">↑</span>
                    <strong>{file ? file.name : '选择作品文件'}</strong>
                    <span>{file ? `${fileSizeLabel(file.size)} · 点击更换` : workType === 'article'
                      ? `.docx · 平台上传限制 ${fileSizeLabel(maxFileBytes)}`
                      : `.mp4 · 平台上传限制 ${fileSizeLabel(maxFileBytes)}`}</span>
                  </label>
                  <input
                    {...fieldProps('file')}
                    ref={fileInputRef}
                    className="oh-file-input"
                    type="file"
                    aria-describedby={fieldErrors.file ? "oh-file-help oh-file-error" : "oh-file-help"}
                    accept={workType === 'article' ? '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document' : '.mp4,video/mp4'}
                    onChange={(e) => {
                      const nextFile = e.target.files?.[0] ?? null;
                      if (!isSameAttachment(file, nextFile)) rotateClientSubmissionId();
                      setFile(nextFile);
                      clearFieldError('file');
                      setError(null);
                    }}
                    required
                  />
                  {fieldError('file')}
                  <p className="oh-help" id="oh-file-help">{workType === 'article'
                    ? '.doc 文件请在 Word 中另存为 .docx 后上传；服务器将在提交后解析校验文件。'
                    : '画面不低于 1080P（横屏或竖屏均可）；服务器将在提交后校验文件。'}</p>
                </fieldset>

                <div className="oh-consent-box">
                  <label className="oh-consent-label">
                    <input {...fieldProps('consent')} type="checkbox" checked={consent} onChange={(e) => { setConsent(e.target.checked); clearFieldError('consent'); }} disabled={submitting || !meta?.consent_text} />
                    <span>{meta?.consent_text || (metaQuery.isError ? '数据处理说明暂时无法加载，请在页面上方重新检查网络。' : '正在加载本次投稿的数据处理说明…')}<strong>此项确认不构成作品公开展示或传播授权。</strong></span>
                  </label>
                </div>

                {fieldError('consent')}
                {error && <p ref={errorRef} tabIndex={-1} className="oh-form-error" role="alert">{error}</p>}
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
