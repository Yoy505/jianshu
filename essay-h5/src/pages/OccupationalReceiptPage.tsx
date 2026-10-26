import axios from 'axios';
import { Button } from '../components/ui/button';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { queryOccupationalReceipt, type OccupationalReceiptStatus } from '../lib/occupational';
import { OccupationalFooter, OccupationalHeader } from '../components/OccupationalShell';

const RECEIPT_RE = /^OCC-[0-9A-F]{16}$/;
const LAST_RECEIPT_KEY = 'occupational-last-receipt';
const STAGES = ['作品提交', '形式初审', '内容终审'] as const;

const STATUS_COPY: Record<OccupationalReceiptStatus['status'], { label: string; hint: string }> = {
  submitted: { label: '已提交，等待初审', hint: '作品已进入评审队列，形式初审尚未开始。' },
  initial_reviewing: { label: '形式初审中', hint: '工作人员正在核对投稿材料与格式。' },
  pending_final: { label: '等待内容终审', hint: '作品已通过形式初审，正在等待内容终审。' },
  accepted: { label: '评审通过', hint: '作品已通过评审。收录、证书等后续安排以组织方通知为准。' },
  rejected: { label: '未通过评审', hint: '作品未通过评审；如有评审说明，将在下方展示。' },
};

function stageState(status: OccupationalReceiptStatus['status'], index: number): 'complete' | 'current' | 'upcoming' | 'undetermined' {
  if (index === 0) return 'complete';
  if (status === 'rejected') return 'undetermined';
  if (status === 'accepted') return 'complete';
  if (index === 1) return status === 'initial_reviewing' ? 'current' : status === 'pending_final' ? 'complete' : 'upcoming';
  return status === 'pending_final' ? 'current' : 'upcoming';
}

function formatBeijing(iso: string): string {
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(iso));
  } catch { return iso; }
}

function readStoredReceipt(): string | null {
  try {
    const stored = window.localStorage.getItem(LAST_RECEIPT_KEY)?.trim().toUpperCase() ?? '';
    return RECEIPT_RE.test(stored) ? stored : null;
  } catch { return null; }
}

function lookupError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 404) return '没有查到这个回执号。请核对提交成功页显示的完整编号。';
    if (error.response?.status === 429) return '查询较频繁，请稍后再试。';
    if (!error.response) return '暂时无法连接查询服务，请检查网络后重试。';
  }
  return '查询服务暂时不可用，请稍后重试。';
}

export default function OccupationalReceiptPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialNo = (searchParams.get('no') ?? '').trim().toUpperCase();
  const [input, setInput] = useState(initialNo);
  const [recentReceipt] = useState(readStoredReceipt);
  const [result, setResult] = useState<OccupationalReceiptStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  const resultRef = useRef<HTMLElement>(null);

  useEffect(() => { document.title = '进度查询 · 2026 职业健康科普作品征集'; }, []);

  useEffect(() => {
    if (!result) return;
    resultRef.current?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start',
    });
  }, [result]);

  const lookup = async (receiptNo: string) => {
    const normalized = receiptNo.trim().toUpperCase();
    if (!RECEIPT_RE.test(normalized)) {
      requestId.current += 1;
      setLoading(false);
      setError('请输入提交成功时获得的完整回执号，格式为 OCC- 后接 16 位数字或 A–F 字母。');
      setResult(null);
      return;
    }
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await queryOccupationalReceipt(normalized);
      if (currentRequest === requestId.current) setResult(data);
    } catch (caught) {
      if (currentRequest === requestId.current) setError(lookupError(caught));
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  };

  useEffect(() => {
    setInput(initialNo);
    if (RECEIPT_RE.test(initialNo)) void lookup(initialNo);
    // URL 中的回执号改变时自动查询；手动重复查询由 handleSubmit 处理。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialNo]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = input.trim().toUpperCase();
    if (!RECEIPT_RE.test(normalized) || normalized === initialNo) void lookup(normalized);
    else setSearchParams({ no: normalized }, { replace: true });
  };

  const useRecentReceipt = () => {
    if (!recentReceipt) return;
    setInput(recentReceipt);
    if (recentReceipt === initialNo) void lookup(recentReceipt);
    else setSearchParams({ no: recentReceipt }, { replace: true });
  };

  const status = result?.status;
  const copy = status ? STATUS_COPY[status] : null;

  return (
    <div className="oh-page oh-submit-page oh-tracking-page">
      <OccupationalHeader />
      <main id="main-content" tabIndex={-1} className="oh-tracking-main">
        <div className="oh-container">
          <div className="oh-tracking-heading">
            <p className="oh-kicker">05 / STATUS LOOKUP</p>
            <h1>查询投稿进度<span>了解作品所处阶段</span></h1>
            <p>凭提交成功时获得的回执号，查看作品的最新评审状态。</p>
          </div>

          <div className="oh-tracking-grid">
            <section className="oh-tracking-lookup" aria-labelledby="tracking-lookup-title">
              <div className="oh-tracking-lookup-top">
                <span className="oh-tracking-section-number">01 / QUERY</span>
                <h2 id="tracking-lookup-title">输入回执号</h2>
                <p>回执号仅用于查询这份作品的评审进度。</p>
              </div>
              <form onSubmit={handleSubmit} noValidate aria-busy={loading}>
                <label htmlFor="receipt-number" className="oh-tracking-label">投稿回执号</label>
                <input id="receipt-number" className="oh-tracking-input" type="text" value={input}
                  onChange={(event) => {
                    requestId.current += 1;
                    setLoading(false);
                    setResult(null);
                    setInput(event.target.value.trim().toUpperCase());
                    if (error) setError(null);
                  }}
                  placeholder="OCC-XXXXXXXXXXXXXXXX" autoComplete="off" autoCapitalize="characters"
                  spellCheck={false} aria-describedby={error ? "receipt-format receipt-error" : "receipt-format"} aria-invalid={Boolean(error) && !RECEIPT_RE.test(input)} />
                <p id="receipt-format" className="oh-tracking-format">OCC- 后接 16 位数字或 A–F 字母，按回执原样输入即可。</p>
                <Button className="oh-tracking-submit" type="submit" disabled={loading}>{loading ? '正在查询…' : '查询进度'}</Button>
              </form>
              {recentReceipt && !result && <Button className="oh-tracking-recent" variant="ghost" type="button" onClick={useRecentReceipt} disabled={loading}>使用本设备上次投稿的回执号</Button>}
              {error && <p id="receipt-error" className="oh-form-error" role="alert">{error}</p>}
            </section>

            <aside className="oh-tracking-guide" aria-label="评审流程说明">
              <span className="oh-tracking-section-number">02 / REVIEW FLOW</span>
              <h2>评审流程</h2>
              <p>查询后会显示以下节点的实际状态。页面不会展示联系方式或投稿文件。</p>
              <ol>{STAGES.map((stage, index) => <li key={stage}><span>{String(index + 1).padStart(2, '0')}</span><strong>{stage}</strong></li>)}</ol>
              <small>具体结论以查询结果及组织方后续通知为准。</small>
            </aside>
          </div>

          {result && copy && (
            <section ref={resultRef} className="oh-tracking-result" aria-live="polite" aria-labelledby="tracking-result-title">
              <div className="oh-tracking-result-head">
                <div><span className="oh-tracking-section-number">QUERY RESULT / 查询结果</span><h2 id="tracking-result-title">{result.title}</h2><p>{result.work_type === 'article' ? '图文作品' : '科普视频'} · {result.receipt_no}</p></div>
                <span className={`oh-receipt-status is-${result.status}`}>{copy.label}</span>
              </div>
              <ol className="oh-tracking-stages" aria-label="作品评审节点">
                {STAGES.map((stage, index) => {
                  const state = stageState(result.status, index);
                  return <li key={stage} data-state={state}>
                    <span className="oh-tracking-stage-index">{String(index + 1).padStart(2, '0')}</span>
                    <strong>{stage}</strong>
                    <small>{state === 'complete' ? '已完成' : state === 'current' ? '进行中' : state === 'undetermined' ? '阶段未公开' : '尚未开始'}</small>
                  </li>;
                })}
              </ol>
              <p className="oh-tracking-result-hint">{copy.hint}</p>
              <dl className="oh-tracking-meta">
                <div><dt>提交时间</dt><dd>{formatBeijing(result.submitted_at)}</dd></div>
                {result.decided_at && <div><dt>结论时间</dt><dd>{formatBeijing(result.decided_at)}</dd></div>}
              </dl>
              {result.status === 'rejected' && result.comment && <p className="oh-receipt-comment"><strong>评审说明：</strong>{result.comment}</p>}
            </section>
          )}

          <div className="oh-tracking-help">
            <div><strong>找不到回执号？</strong><p>请核对提交成功页保存的编号；遗失时可用投稿时填写的电话联系活动组织方。</p></div>
            <Link to="/submit">前往投稿页</Link>
          </div>
        </div>
      </main>
      <OccupationalFooter />
    </div>
  );
}
