import { useEffect, useRef, useState, type FormEvent, type InputHTMLAttributes } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Copy } from 'lucide-react';
import { API_BASE, fetchVolunteerMeta, OFFLINE_OPTIONS, TRACKS, type VolunteerMeta } from './content';
import { applicationPayload, parseReceipt, saveReceipt, validateForm, type FieldErrors, type FormState } from './application';
import { useApplicationStore } from './stores/application';
import { Button } from './components/ui/button';

type ChannelStatus = 'loading' | 'error' | 'open' | 'closed';
type Field = keyof FormState;
const fieldId = (key: Field) => `field-${key}`;
const errorId = (key: Field) => `error-${key}`;
function focusField(key: Field) {
  const target = document.getElementById(fieldId(key));
  const details = target?.closest('details');
  if (details) details.open = true;
  target?.focus();
  target?.scrollIntoView({ block: 'center', behavior: 'auto' });
}
function TextField({ label, field, error, ...props }: InputHTMLAttributes<HTMLInputElement> & {
  label: string; field: Field; error?: string;
}) {
  return <div className="vol-field">
    <label className="vol-label" htmlFor={fieldId(field)}>{label}{props.required && <em aria-hidden="true"> *</em>}</label>
    <input {...props} id={fieldId(field)} name={field} className="vol-input"
      aria-invalid={Boolean(error)} aria-describedby={error ? errorId(field) : undefined} />
    {error && <span className="vol-field-error" id={errorId(field)}>{error}</span>}
  </div>;
}

export default function ApplyPage() {
  const { form, setForm, resetForm, receipt, setReceipt, clearReceipt, submitting, setSubmitting,
    submitError, setSubmitError, storageFailed, setStorageFailed, locked, setLocked } = useApplicationStore();
  const queryClient = useQueryClient();
  const [errors, setErrors] = useState<FieldErrors>({});
  const [copyMessage, setCopyMessage] = useState('');
  const [focusTarget, setFocusTarget] = useState<Field | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  // 报名通道元数据：Query 统一加载/错误/重试（重新检查 = refetch）
  const metaQuery = useQuery({
    queryKey: ['volunteer-meta'],
    // 保留原实现的 10s 超时中止（停滞请求转可重试状态），并与 Query 信号联动
    queryFn: async ({ signal }) => {
      const timeout = new AbortController();
      const timer = setTimeout(() => timeout.abort(), 10000);
      const forwardAbort = () => timeout.abort();
      signal?.addEventListener('abort', forwardAbort);
      try {
        return await fetchVolunteerMeta(timeout.signal);
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', forwardAbort);
      }
    },
    // 同意文案每次进入报名页必须重新核验（文案变化要重置勾选），故不做长缓存
    staleTime: 0,
  });
  const meta = metaQuery.data;
  const channelStatus: ChannelStatus = metaQuery.isPending
    ? 'loading'
    : metaQuery.isError || !meta ? 'error' : (meta.enabled ? 'open' : 'closed');
  const isOpen = channelStatus === 'open';
  const consentText = meta?.consent_text ?? '';
  const channel: { status: ChannelStatus; meta?: VolunteerMeta } = { status: channelStatus, meta };

  // 同意文案变化时重置勾选（与原实现一致：防止对旧文案的同意冒充新文案确认）
  useEffect(() => {
    if (!consentText) return;
    const current = useApplicationStore.getState().form;
    if (current.consentFor !== consentText) setForm((previous) => ({ ...previous, consent: false, consentFor: '' }));
  }, [consentText, setForm]);

  useEffect(() => { if (receipt) heading.current?.focus(); }, [receipt]);

  useEffect(() => {
    if (focusTarget && !submitting) { focusField(focusTarget); setFocusTarget(null); }
  }, [focusTarget, submitting, errors]);

  // 提交：副作用 mutation，不自动重试；服务端校验/限流/未知结果的处理与原实现逐条一致
  const submitMutation = useMutation({
    retry: false,
    mutationFn: async () => {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 20000);
      try {
        const res = await fetch(`${API_BASE}/h5/volunteer-applications`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify(applicationPayload(form)),
        });
        const data = await res.json().catch(() => null);
        return { status: res.status, data } as { status: number; data: unknown };
      } finally {
        window.clearTimeout(timer);
      }
    },
    onMutate: () => { setLocked(true); setSubmitting(true); setSubmitError(null); },
    onSettled: () => { setLocked(false); setSubmitting(false); },
  });

  function setField<K extends Field>(key: K, value: FormState[K]) {
    const next = { ...form, [key]: value };
    if (key === 'consent') next.consentFor = value ? consentText : '';
    if (key === 'track' && value !== 'other') next.trackNote = '';
    setForm(() => next);
    const currentErrors = validateForm(next);
    setErrors((previous) => {
      const updated = { ...previous };
      if (previous[key]) {
        if (currentErrors[key]) updated[key] = currentErrors[key];
        else delete updated[key];
      }
      if (key === 'track' && value !== 'other') delete updated.trackNote;
      return updated;
    });
  }
  function fieldError(key: Field) {
    return errors[key] ? <p className="vol-field-error" id={errorId(key)}>{errors[key]}</p> : null;
  }
  function validationProps(key: Field) {
    return { 'aria-invalid': Boolean(errors[key]), 'aria-describedby': errors[key] ? errorId(key) : undefined };
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked || submitting || !isOpen || !consentText) return;
    const invalid = validateForm(form);
    if (form.consent && form.consentFor !== consentText) invalid.consent = '信息使用说明已更新，请重新阅读并确认。';
    setErrors(invalid);
    const first = Object.keys(invalid)[0] as Field | undefined;
    if (first) { setFocusTarget(first); return; }
    const result = await submitMutation.mutateAsync().catch(() => ({ status: 0, data: null }));

    if (result.status === 0) {
      setSubmitError('连接中断或请求超时，未能确认提交结果，报名可能已被接收。请勿连续重复提交；你填写的信息仍保留在本页。');
      return;
    }
    const data = result.data as { detail?: unknown } | null;
    const receiptResult = parseReceipt(data);
    if (result.status >= 200 && result.status < 300 && receiptResult) {
      let saved = false;
      try { saved = saveReceipt(window.sessionStorage, receiptResult); } catch { /* Storage may be unavailable. */ }
      setStorageFailed(!saved);
      setReceipt(receiptResult);
      resetForm();
      setErrors({});
      return;
    }
    const detail = typeof data?.detail === 'string' ? data.detail : '';
    if (result.status === 503 && detail === '志愿者报名暂未开放' && meta) {
      queryClient.setQueryData(['volunteer-meta'], { ...meta, enabled: false });
    }
    if (result.status === 429) setSubmitError('提交过于频繁，请稍后再试。你填写的信息仍保留在本页。');
    else if (result.status === 422) {
      // FastAPI validation errors may be an array instead of a plain message.
      const fields: Record<string, Field> = { name: 'name', phone: 'phone', city: 'city', birth_year: 'birthYear',
        occupation: 'occupation', organization: 'organization', track: 'track', track_note: 'trackNote',
        offline_per_year: 'offlinePerYear', can_station: 'canStation', intro: 'intro', consent: 'consent' };
      const serverErrors: FieldErrors = {};
      if (Array.isArray(data?.detail)) for (const item of data.detail as Array<{ loc?: unknown }>) {
        const key = Array.isArray(item?.loc) ? fields[(item.loc as string[])[item.loc.length - 1]] : undefined;
        if (key) serverErrors[key] = '请检查此项内容与格式后重新提交。';
      }
      setErrors(serverErrors);
      const firstServerError = Object.keys(serverErrors)[0] as Field | undefined;
      if (firstServerError) setFocusTarget(firstServerError);
      setSubmitError(detail || '部分报名信息未通过校验，请检查标记的内容。');
    } else if (result.status === 503 && detail) setSubmitError(`${detail}。你填写的信息仍保留在本页。`);
    else if ((result.status >= 200 && result.status < 300) || result.status >= 500) setSubmitError('未能确认提交结果，报名可能已被接收。请勿连续重复提交；保留本页，稍后确认后再操作。');
    else setSubmitError(detail || `提交未成功（${result.status}），请稍后重试。你填写的信息仍保留在本页。`);
  }

  async function copyReceipt() {
    if (!receipt) return;
    try { await navigator.clipboard.writeText(receipt.receipt_no); setCopyMessage('回执号已复制。'); }
    catch { setCopyMessage('暂时无法自动复制，请长按或选中上方回执号复制。'); }
  }
  function startAnother() {
    clearReceipt(); resetForm(); setErrors({}); setSubmitError(null); setCopyMessage('');
    heading.current?.focus();
  }

  return <main id="main-content" tabIndex={-1} className="vol-main">
    <section className="vol-section vol-apply-section"><div className="vol-container vol-apply-container">
      <Link className="vol-back-link" to="/">返回招募说明</Link>
      <div className="vol-section-heading">
        <p className="vol-kicker">APPLY / 在线报名</p>
        <h1 ref={heading} tabIndex={-1}>{receipt ? '报名已提交' : '填写报名信息'}</h1>
        <p className="vol-section-subtitle">{receipt ? '保存回执，方便后续联系与确认。' : '请确认已阅读招募条件；带 * 为必填项。'}</p>
      </div>
      {receipt ? <div className="vol-receipt">
        <CheckCircle2 className="vol-receipt-mark" size={48} aria-hidden="true" />
        <p className="vol-receipt-label">志愿者报名回执号</p>
        <strong className="vol-receipt-number">{receipt.receipt_no}</strong>
        <p className="vol-receipt-time">提交时间：{new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(receipt.submitted_at))}（北京时间）</p>
        <Button type="button" variant="secondary" className="vol-button vol-button-secondary" onClick={() => void copyReceipt()}><Copy size={18} aria-hidden="true" />复制回执号</Button>
        <p className="vol-copy-status" role="status">{copyMessage}</p>
        <p className="vol-receipt-hint">工作人员将通过你填写的手机号联系，请保持电话畅通。报名提交不代表已录用。</p>
        <p className="vol-form-hint">{storageFailed ? '此浏览器无法保存回执，请现在复制回执号或截图留存。' : '此回执暂存在当前浏览器会话中，24小时内可刷新恢复。请及时复制或截图留存。'}</p>
        <div className="vol-receipt-actions">
          <Link className="vol-button vol-button-primary" to="/">返回招募首页</Link>
          <Button type="button" variant="secondary" className="vol-button vol-button-secondary" onClick={startAnother}>再帮一位伙伴报名</Button>
        </div>
      </div> : <form className="vol-form" onSubmit={(event) => void handleSubmit(event)} noValidate aria-busy={submitting}>
        <div className="vol-form-status" data-state={channel.status} role="status">
          <span>{channel.status === 'loading' ? '正在确认报名通道状态…' : channel.status === 'open' ? '报名通道已开放'
            : channel.status === 'closed' ? '报名通道暂未开放，开放后可填写并提交。' : '暂时无法确认报名状态，请重新检查。'}</span>
          {(channel.status === 'error' || channel.status === 'closed') && <button type="button" className="vol-text-button" onClick={() => void metaQuery.refetch()}>重新检查</button>}
        </div>
        {Object.keys(errors).length > 0 && <div className="vol-error-summary" role="alert"><strong>请检查以下信息</strong><ul>
          {Object.entries(errors).map(([key, message]) => <li key={key}><a href={`#${fieldId(key as Field)}`} onClick={(event) => { event.preventDefault(); focusField(key as Field); }}>{message}</a></li>)}
        </ul></div>}
        {isOpen && <fieldset className="vol-form-body" disabled={submitting}>
          <legend className="vol-sr-only">志愿者报名信息</legend>
          <fieldset className="vol-form-section"><legend><span>01</span>基本信息</legend>
            <div className="vol-field-pair">
              <TextField label="姓名" field="name" required autoComplete="name" value={form.name} maxLength={50} error={errors.name} onChange={(event) => setField('name', event.target.value)} placeholder="请填写真实姓名" />
              <TextField label="手机号" field="phone" required type="tel" inputMode="tel" autoComplete="tel-national" value={form.phone} maxLength={11} error={errors.phone} onChange={(event) => setField('phone', event.target.value.replace(/\D/g, ''))} placeholder="用于活动联系" />
            </div>
            <TextField label="所在城市" field="city" required autoComplete="address-level2" value={form.city} maxLength={50} error={errors.city} onChange={(event) => setField('city', event.target.value)} placeholder="如 天津" />
          </fieldset>
          <fieldset className="vol-form-section"><legend><span>02</span>服务意向</legend>
            <div className="vol-field"><span className="vol-label" id="track-label">意向服务方向 <em aria-hidden="true">*</em></span>
              <div className="vol-track-grid" role="radiogroup" aria-labelledby="track-label" aria-required="true" {...validationProps('track')}>
                {TRACKS.map((item, index) => <label key={item.key} className={`vol-choice vol-choice-track ${form.track === item.key ? 'is-selected' : ''}`}>
                  <input id={index === 0 ? fieldId('track') : `track-${item.key}`} type="radio" name="track" {...validationProps('track')} value={item.key} checked={form.track === item.key} required onChange={() => setField('track', item.key)} />{item.label}
                </label>)}
              </div>{fieldError('track')}
              {form.track === 'other' && <div className="vol-track-note"><TextField label="具体服务方向" field="trackNote" required value={form.trackNote} maxLength={50} error={errors.trackNote} onChange={(event) => setField('trackNote', event.target.value)} placeholder="请填写你想参与的服务方向" /></div>}
            </div>
            <div className="vol-field"><label className="vol-label" htmlFor={fieldId('offlinePerYear')}>每年可参加线下活动 <em aria-hidden="true">*</em></label>
              <select id={fieldId('offlinePerYear')} className="vol-input" required value={form.offlinePerYear || ''} {...validationProps('offlinePerYear')} onChange={(event) => setField('offlinePerYear', Number(event.target.value) || 0)}>
                <option value="">请选择</option>{OFFLINE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>{fieldError('offlinePerYear')}
            </div>
            <div className="vol-field"><span className="vol-label" id="station-label">能否承担3–5天驻点服务 <em aria-hidden="true">*</em></span>
              <p className="vol-field-help" id="station-help">能承担驻点服务者优先，暂不能也可报名。</p>
              <div className="vol-choice-row" role="radiogroup" aria-labelledby="station-label" aria-required="true" {...validationProps('canStation')}>
                {([['yes', '可以'], ['no', '暂不能']] as const).map(([value, label], index) => <label key={value} className={`vol-choice ${form.canStation === value ? 'is-selected' : ''}`}>
                  <input id={index === 0 ? fieldId('canStation') : 'station-no'} type="radio" name="can-station" value={value} required aria-invalid={Boolean(errors.canStation)} aria-describedby={errors.canStation ? `station-help ${errorId('canStation')}` : 'station-help'} checked={form.canStation === value} onChange={() => setField('canStation', value)} />{label}
                </label>)}
              </div>{fieldError('canStation')}
            </div>
          </fieldset>
          <details className="vol-optional"><summary>补充资料 <span>选填，可跳过</span></summary>
            <div className="vol-optional-content">
              <TextField label="出生年份（选填）" field="birthYear" inputMode="numeric" value={form.birthYear} maxLength={4} error={errors.birthYear} onChange={(event) => setField('birthYear', event.target.value.replace(/\D/g, ''))} placeholder="1900–2010，如 1995" />
              <div className="vol-field-pair">
                <TextField label="职业 / 行业（选填）" field="occupation" value={form.occupation} maxLength={50} error={errors.occupation} onChange={(event) => setField('occupation', event.target.value)} />
                <TextField label="工作单位 / 学校（选填）" field="organization" autoComplete="organization" value={form.organization} maxLength={100} error={errors.organization} onChange={(event) => setField('organization', event.target.value)} />
              </div>
              <div className="vol-field"><label className="vol-label" htmlFor={fieldId('intro')}>个人简介 / 特长（选填）</label>
                <textarea id={fieldId('intro')} className="vol-input vol-textarea" rows={4} maxLength={500} value={form.intro} {...validationProps('intro')} onChange={(event) => setField('intro', event.target.value)} placeholder="简单介绍你的专长、志愿服务经历或期望" />
                {fieldError('intro')}<span className="vol-field-help">{form.intro.length} / 500字</span>
              </div>
            </div>
          </details>
          <label className="vol-consent"><input id={fieldId('consent')} type="checkbox" required checked={form.consent} {...validationProps('consent')} onChange={(event) => setField('consent', event.target.checked)} /><span>{consentText}</span></label>
          {fieldError('consent')}
        </fieldset>}
        {submitError && <p className="vol-form-error" role="alert">{submitError}</p>}
        <button className="vol-button vol-button-primary vol-submit" type="submit" disabled={submitting || !isOpen}>{submitting ? '正在提交…' : '提交报名'}</button>
        <p className="vol-form-hint">报名成功后可复制回执号，请妥善保存。</p>
        <p className="vol-form-hint">页面内往返保留已填信息；刷新或关闭页面会清空未提交内容。</p>
      </form>}
    </div></section>
  </main>;
}
