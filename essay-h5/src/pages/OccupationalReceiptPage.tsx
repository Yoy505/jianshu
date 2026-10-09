import { useEffect, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  queryOccupationalReceipt,
  type OccupationalReceiptStatus,
} from '../lib/occupational';
import { OccupationalFooter, OccupationalHeader } from '../components/OccupationalShell';

const RECEIPT_RE = /^OCC-[0-9A-F]{16}$/;

const STATUS_COPY: Record<OccupationalReceiptStatus['status'], { label: string; hint: string }> = {
  submitted: { label: '已提交，等待初审', hint: '作品已进入评审队列，请耐心等待形式初审。' },
  initial_reviewing: { label: '初审中', hint: '工作人员正在核对材料与格式。' },
  pending_final: { label: '初审已通过，等待终审', hint: '作品已通过形式初审，进入内容终审环节。' },
  accepted: { label: '审核通过', hint: '作品已通过两级评审。收录、证书等后续安排以组织方通知为准。' },
  rejected: { label: '未通过', hint: '作品未通过评审，理由见下方说明。' },
};

const STAGE_STEPS: Array<{ key: string; label: string; reached: (status: OccupationalReceiptStatus['status']) => boolean }> = [
  { key: 'submitted', label: '提交', reached: () => true },
  { key: 'initial', label: '形式初审', reached: (s) => s === 'pending_final' || s === 'accepted' || s === 'rejected' },
  { key: 'final', label: '内容终审', reached: (s) => s === 'accepted' },
];

function formatBeijing(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export default function OccupationalReceiptPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialNo = (searchParams.get('no') ?? '').trim();
  const [input, setInput] = useState(initialNo);
  const [result, setResult] = useState<OccupationalReceiptStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    document.title = '进度查询 · 2026 职业健康科普作品征集';
  }, []);

  const lookup = async (receiptNo: string) => {
    const normalized = receiptNo.trim().toUpperCase();
    if (!RECEIPT_RE.test(normalized)) {
      setError('回执号格式不正确，应为提交成功时显示的 OCC- 开头编号。');
      setResult(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await queryOccupationalReceipt(normalized);
      setResult(data);
    } catch {
      setResult(null);
      setError('未找到该回执号对应的投稿。请核对编号，或以提交成功页面显示的回执号为准。');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (RECEIPT_RE.test(initialNo.toUpperCase())) {
      void lookup(initialNo);
    }
    // 仅在携带 ?no= 进入时自动查询一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialNo]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = input.trim().toUpperCase();
    if (normalized && RECEIPT_RE.test(normalized)) {
      setSearchParams({ no: normalized }, { replace: true });
    } else {
      void lookup(input);
    }
  };

  const status = result?.status;
  const copy = status ? STATUS_COPY[status] : null;

  return (
    <div className="oh-page oh-submit-page">
      <OccupationalHeader />
      <main className="oh-submit-main">
        <div className="oh-container">
          <div className="oh-submit-heading">
            <p className="oh-kicker">STATUS / 进度查询</p>
            <h1>投稿进度查询</h1>
            <p>输入提交作品时生成的回执号，查询评审进度。查询结果不含联系方式等个人信息。</p>
          </div>

          <form className="oh-receipt-lookup" onSubmit={handleSubmit} noValidate>
            <label className="oh-field">
              <span className="oh-label">投稿回执号 <em>*</em></span>
              <input
                className="oh-input"
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value.toUpperCase().slice(0, 20))}
                placeholder="OCC-XXXXXXXXXXXXXXXX"
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
              />
            </label>
            <button className="oh-button oh-button-primary" type="submit" disabled={loading}>
              {loading ? '正在查询…' : '查询进度'}
            </button>
            {error && <p className="oh-form-error" role="alert">{error}</p>}
          </form>

          {result && copy && (
            <section className="oh-receipt oh-receipt-result" aria-live="polite">
              <p className="oh-receipt-label">回执号</p>
              <strong className="oh-receipt-number">{result.receipt_no}</strong>
              <p className="oh-receipt-work-title">{result.title} · {result.work_type === 'article' ? '图文作品' : '科普视频'}</p>
              <p className={`oh-receipt-status is-${result.status}`}>{copy.label}</p>
              <ol className="oh-receipt-steps">
                {STAGE_STEPS.map((step) => (
                  <li key={step.key} data-reached={status ? step.reached(status) : false}>
                    <span aria-hidden="true" />
                    {step.label}
                  </li>
                ))}
              </ol>
              <p className="oh-receipt-hint">{copy.hint}</p>
              <dl className="oh-receipt-meta">
                <div><dt>提交时间</dt><dd>{formatBeijing(result.submitted_at)}</dd></div>
                <div><dt>结论时间</dt><dd>{formatBeijing(result.decided_at)}</dd></div>
              </dl>
              {result.status === 'rejected' && result.comment && (
                <p className="oh-receipt-comment"><strong>未通过理由：</strong>{result.comment}</p>
              )}
            </section>
          )}

          <p className="oh-receipt-footnote">
            如回执号遗失，请通过投稿时填写的联系电话与活动组织方联系；重新提交作品会生成新的回执号。
            <Link to="/submit">返回投稿页</Link>
          </p>
        </div>
      </main>
      <OccupationalFooter />
    </div>
  );
}
