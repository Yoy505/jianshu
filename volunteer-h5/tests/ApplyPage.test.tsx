import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import App from '../src/App';
import { RECEIPT_KEY } from '../src/application';

const meta = { enabled: true, consent_text: '我同意仅为报名与活动联系使用填报信息。' };
const receipt = { id: 7, receipt_no: 'VOL-0123456789ABCDEF', submitted_at: '2026-10-19T00:00:00Z' };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function setup(fetcher = vi.fn().mockImplementation(() => Promise.resolve(json(meta)))) {
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  const view = render(<MemoryRouter initialEntries={['/apply']}><App /></MemoryRouter>);
  return { fetcher, user, ...view };
}
async function fill(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('报名通道已开放');
  await user.type(screen.getByRole('textbox', { name: '姓名', exact: true }), '界面测试');
  await user.type(screen.getByRole('textbox', { name: '手机号', exact: true }), '13800000000');
  await user.type(screen.getByRole('textbox', { name: '所在城市', exact: true }), '天津');
  await user.click(screen.getByRole('radio', { name: '公益实践', exact: true }));
  await user.selectOptions(screen.getByRole('combobox', { name: '每年可参加线下活动', exact: true }), '1');
  await user.click(screen.getByRole('radio', { name: '暂不能', exact: true }));
  await user.click(screen.getByRole('checkbox'));
}
describe('registration experience', () => {
  it('fails closed while loading and when disabled, including direct form submission', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ ...meta, enabled: false }));
    const { container } = setup(fetcher);
    expect((screen.getByRole('button', { name: '提交报名' }) as HTMLButtonElement).disabled).toBe(true);
    await screen.findByText('报名通道暂未开放，开放后可填写并提交。');
    fireEvent.submit(container.querySelector('form')!);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('textbox', { name: '姓名', exact: true })).toBeNull();
  });
  it('shows retry for network failures and rejects incomplete metadata', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(Error('offline')).mockResolvedValueOnce(json({ enabled: true })).mockResolvedValueOnce(json(meta));
    const { user } = setup(fetcher);
    await screen.findByText('暂时无法确认报名状态，请重新检查。');
    await user.click(screen.getByRole('button', { name: '重新检查' }));
    await screen.findByText('暂时无法确认报名状态，请重新检查。');
    await user.click(screen.getByRole('button', { name: '重新检查' }));
    await screen.findByText('报名通道已开放');
  });
  it('turns a stalled metadata request into a retry state after timeout', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(Error('timeout')))));
    setup(fetcher);
    await act(async () => { await vi.advanceTimersByTimeAsync(10001); });
    expect(screen.getByText('暂时无法确认报名状态，请重新检查。')).toBeTruthy();
    expect((screen.getByRole('button', { name: '提交报名' }) as HTMLButtonElement).disabled).toBe(true);
  });
  it('focuses the first invalid field and clears its error only after a valid edit', async () => {
    const { user, fetcher } = setup();
    await screen.findByText('报名通道已开放');
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    const name = screen.getByRole('textbox', { name: '姓名', exact: true });
    expect(document.activeElement).toBe(name);
    expect(name.getAttribute('aria-describedby')).toBe('error-name');
    await user.type(name, ' ');
    expect(name.getAttribute('aria-invalid')).toBe('true');
    await user.type(name, '测试');
    expect(name.getAttribute('aria-invalid')).toBe('false');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('opens collapsed optional details when their contents are invalid', async () => {
    const { user } = setup();
    await fill(user);
    const summary = screen.getByText('补充资料');
    await user.click(summary);
    await user.type(screen.getByLabelText('出生年份（选填）'), '2011');
    await user.click(summary);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    const year = screen.getByLabelText('出生年份（选填）');
    expect(document.activeElement).toBe(year);
    expect(year.closest('details')?.open).toBe(true);
  });
  it('preserves drafts across internal navigation without storing personal data', async () => {
    const { user } = setup();
    await screen.findByText('报名通道已开放');
    await user.type(screen.getByRole('textbox', { name: '姓名', exact: true }), '草稿测试');
    await user.click(screen.getByRole('link', { name: '返回招募说明', exact: true }));
    await user.click(screen.getByRole('link', { name: '在线报名', exact: true }));
    await screen.findByText('报名通道已开放');
    expect((screen.getByRole('textbox', { name: '姓名', exact: true }) as HTMLInputElement).value).toBe('草稿测试');
    expect(sessionStorage.length).toBe(0);
  });
  it('requires other-direction detail and removes it after switching away', async () => {
    const { user, fetcher } = setup(vi.fn().mockResolvedValueOnce(json(meta)).mockResolvedValue(json(receipt, 201)));
    await fill(user);
    await user.click(screen.getByRole('radio', { name: '其他', exact: true }));
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    expect(document.activeElement?.id).toBe('field-trackNote');
    await user.type(screen.getByRole('textbox', { name: '具体服务方向', exact: true }), '社区支持');
    await user.click(screen.getByRole('radio', { name: '活动协助', exact: true }));
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    await screen.findByText('报名已提交');
    expect(JSON.parse(fetcher.mock.calls[1][1].body).track_note).toBeNull();
  });
  it('blocks duplicate submissions, saves the real receipt, restores after remount and handles copy failures', async () => {
    let complete!: (value: Response) => void;
    const fetcher = vi.fn().mockResolvedValueOnce(json(meta)).mockImplementationOnce(() => new Promise<Response>((resolve) => { complete = resolve; })).mockImplementation(() => Promise.resolve(json(meta)));
    const view = setup(fetcher);
    await fill(view.user);
    fireEvent.submit(view.container.querySelector('form')!);
    fireEvent.submit(view.container.querySelector('form')!);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect((screen.getByRole('button', { name: '正在提交…' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => complete(json(receipt, 201)));
    expect(await screen.findByText(receipt.receipt_no)).toBeTruthy();
    expect(sessionStorage.getItem(RECEIPT_KEY)).not.toContain('界面测试');
    view.unmount();
    const restored = setup();
    expect(screen.getByText(receipt.receipt_no)).toBeTruthy();
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValueOnce(Error('denied')).mockResolvedValueOnce();
    await restored.user.click(screen.getByRole('button', { name: '复制回执号' }));
    expect(screen.getByText('暂时无法自动复制，请长按或选中上方回执号复制。')).toBeTruthy();
    await restored.user.click(screen.getByRole('button', { name: '复制回执号' }));
    expect(screen.getByText('回执号已复制。')).toBeTruthy();
    await restored.user.click(screen.getByRole('button', { name: '再帮一位伙伴报名' }));
    await screen.findByText('报名通道已开放');
    expect(sessionStorage.getItem(RECEIPT_KEY)).toBeNull();
    expect((screen.getByRole('textbox', { name: '姓名', exact: true }) as HTMLInputElement).value).toBe('');
  });
  it('reports uncertain outcomes without claiming failure or clearing the draft', async () => {
    const { user } = setup(vi.fn().mockResolvedValueOnce(json(meta)).mockRejectedValueOnce(Error('network')));
    await fill(user);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    await screen.findByText(/连接中断或请求超时，未能确认提交结果/);
    expect((screen.getByRole('textbox', { name: '姓名', exact: true }) as HTMLInputElement).value).toBe('界面测试');
    expect(screen.queryByText('报名已提交')).toBeNull();
  });
  it('maps backend 422 errors to fields and focuses them after submit is unlocked', async () => {
    const { user } = setup(vi.fn().mockResolvedValueOnce(json(meta)).mockResolvedValueOnce(json({ detail: [{ loc: ['body', 'phone'], msg: 'invalid' }] }, 422)));
    await fill(user);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    await waitFor(() => expect(document.activeElement?.id).toBe('field-phone'));
    expect(screen.getByRole('textbox', { name: '手机号', exact: true }).getAttribute('aria-invalid')).toBe('true');
    await user.type(screen.getByRole('textbox', { name: '姓名', exact: true }), '甲');
    expect(screen.getByRole('textbox', { name: '手机号', exact: true }).getAttribute('aria-invalid')).toBe('true');
  });
  it('handles channel closing during submission', async () => {
    const { user } = setup(vi.fn().mockResolvedValueOnce(json(meta)).mockResolvedValueOnce(json({ detail: '志愿者报名暂未开放' }, 503)));
    await fill(user);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    await screen.findByText('报名通道暂未开放，开放后可填写并提交。');
    expect((screen.getByRole('button', { name: '提交报名' }) as HTMLButtonElement).disabled).toBe(true);
  });
  it('requires renewed consent when the server text changes across navigation', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(meta)).mockResolvedValueOnce(json({ ...meta, consent_text: '更新后的信息使用说明。' }));
    const { user } = setup(fetcher);
    await fill(user);
    await user.click(screen.getByRole('link', { name: '返回招募说明', exact: true }));
    await user.click(screen.getByRole('link', { name: '在线报名', exact: true }));
    await screen.findByText('更新后的信息使用说明。');
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    expect(document.activeElement?.id).toBe('field-consent');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('preserves inputs after rate limiting and does not show a fabricated receipt', async () => {
    const { user } = setup(vi.fn().mockResolvedValueOnce(json(meta)).mockResolvedValueOnce(json({ detail: '提交过于频繁' }, 429)));
    await fill(user);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    await screen.findByText('提交过于频繁，请稍后再试。你填写的信息仍保留在本页。');
    expect((screen.getByRole('textbox', { name: '姓名', exact: true }) as HTMLInputElement).value).toBe('界面测试');
    expect(sessionStorage.getItem(RECEIPT_KEY)).toBeNull();
  });
  it('keeps a successful receipt visible when session storage is blocked', async () => {
    const { user } = setup(vi.fn().mockResolvedValueOnce(json(meta)).mockResolvedValueOnce(json(receipt, 201)));
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error('blocked'); });
    await fill(user);
    await user.click(screen.getByRole('button', { name: '提交报名' }));
    await screen.findByText('此浏览器无法保存回执，请现在复制回执号或截图留存。');
    expect(screen.getByText(receipt.receipt_no)).toBeTruthy();
  });

});
