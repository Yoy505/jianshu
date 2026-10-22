import { describe, expect, it } from 'vitest';
import { applicationPayload, INITIAL_FORM, loadReceipt, parseReceipt, RECEIPT_KEY, RECEIPT_TTL, saveReceipt, validateForm } from '../src/application';
const receipt = { receipt_no: 'VOL-0123456789ABCDEF', submitted_at: '2026-10-19T00:00:00Z' };
describe('application contracts', () => {
  it('returns errors in visual field order and validates other direction and optional year', () => {
    expect(Object.keys(validateForm(INITIAL_FORM))).toEqual(['name', 'phone', 'city', 'track', 'offlinePerYear', 'canStation', 'consent']);
    expect(validateForm({ ...INITIAL_FORM, track: 'other', birthYear: '2011' })).toMatchObject({ trackNote: expect.any(String), birthYear: expect.any(String) });
    expect(validateForm({ ...INITIAL_FORM, track: 'assist', birthYear: '' }).trackNote).toBeUndefined();
  });
  it('trims payloads and never submits a stale other-direction description', () => {
    expect(applicationPayload({ ...INITIAL_FORM, name: ' 测试 ', track: 'practice', trackNote: '旧方向', canStation: 'no' })).toMatchObject({ name: '测试', track_note: null, birth_year: null, occupation: null, can_station: false, consent: false });
  });
  it('saves only minimal receipt data and expires it after 24 hours', () => {
    expect(saveReceipt(sessionStorage, { ...receipt, phone: 'sensitive' } as typeof receipt, 1000)).toBe(true);
    expect(sessionStorage.getItem(RECEIPT_KEY)).not.toContain('sensitive');
    expect(loadReceipt(sessionStorage, 1001)).toEqual(receipt);
    expect(loadReceipt(sessionStorage, 1000 + RECEIPT_TTL)).toBeNull();
    expect(sessionStorage.getItem(RECEIPT_KEY)).toBeNull();
  });
  it('rejects malformed receipts and survives unavailable storage', () => {
    expect(parseReceipt({ receipt_no: 'fake', submitted_at: 'bad' })).toBeNull();
    sessionStorage.setItem(RECEIPT_KEY, 'null');
    expect(loadReceipt(sessionStorage)).toBeNull();
    expect(loadReceipt({ getItem() { throw Error(); }, removeItem() {} })).toBeNull();
    expect(saveReceipt({ setItem() { throw Error(); } }, receipt)).toBe(false);
  });
});
