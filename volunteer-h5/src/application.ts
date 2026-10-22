import { MOBILE_RE, type TrackKey } from './content';

export interface FormState {
  name: string; phone: string; city: string; birthYear: string;
  occupation: string; organization: string; track: TrackKey | '';
  trackNote: string; offlinePerYear: number; canStation: '' | 'yes' | 'no';
  intro: string; consent: boolean; consentFor: string;
}
export const INITIAL_FORM: FormState = {
  name: '', phone: '', city: '', birthYear: '', occupation: '', organization: '',
  track: '', trackNote: '', offlinePerYear: 0, canStation: '', intro: '', consent: false, consentFor: '',
};
export type FieldErrors = Partial<Record<keyof FormState, string>>;

export function validateForm(form: FormState): FieldErrors {
  const errors: FieldErrors = {};
  if (!form.name.trim()) errors.name = '请填写姓名。';
  if (!MOBILE_RE.test(form.phone.trim())) errors.phone = '请填写11位大陆手机号。';
  if (!form.city.trim()) errors.city = '请填写所在城市。';
  if (!form.track) errors.track = '请选择意向服务方向。';
  if (form.track === 'other' && !form.trackNote.trim()) errors.trackNote = '请填写具体服务方向。';
  if (![1, 2, 4].includes(form.offlinePerYear)) errors.offlinePerYear = '请选择每年参加线下活动的次数。';
  if (!form.canStation) errors.canStation = '请选择能否承担3–5天驻点服务。';
  if (form.birthYear.trim()) {
    const year = Number(form.birthYear);
    if (!Number.isInteger(year) || year < 1900 || year > 2010) errors.birthYear = '出生年份需在1900–2010之间。';
  }
  if (!form.consent) errors.consent = '请阅读并勾选报名信息使用说明。';
  return errors;
}

export function applicationPayload(form: FormState) {
  return {
    name: form.name.trim(), phone: form.phone.trim(), city: form.city.trim(),
    birth_year: form.birthYear.trim() ? Number(form.birthYear) : null,
    occupation: form.occupation.trim() || null, organization: form.organization.trim() || null,
    track: form.track, track_note: form.track === 'other' ? form.trackNote.trim() : null,
    offline_per_year: form.offlinePerYear, can_station: form.canStation === 'yes',
    intro: form.intro.trim() || null, consent: form.consent,
  };
}

export interface SavedReceipt { receipt_no: string; submitted_at: string }
export const RECEIPT_KEY = 'volunteer.receipt.v1';
export const RECEIPT_TTL = 24 * 60 * 60 * 1000;
export function parseReceipt(value: unknown): SavedReceipt | null {
  if (!value || typeof value !== 'object') return null;
  const data = value as Partial<SavedReceipt>;
  if (typeof data.receipt_no !== 'string' || !/^VOL-[A-F0-9]{16}$/.test(data.receipt_no)
    || typeof data.submitted_at !== 'string' || !Number.isFinite(Date.parse(data.submitted_at))) return null;
  return { receipt_no: data.receipt_no, submitted_at: data.submitted_at };
}
export function loadReceipt(storage: Pick<Storage, 'getItem' | 'removeItem'>, now = Date.now()): SavedReceipt | null {
  try {
    const raw = storage.getItem(RECEIPT_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    const receipt = parseReceipt(data?.receipt);
    if (!receipt || !Number.isFinite(data.expiresAt) || data.expiresAt <= now || data.expiresAt > now + RECEIPT_TTL) {
      storage.removeItem(RECEIPT_KEY);
      return null;
    }
    return receipt;
  } catch { return null; }
}
export function saveReceipt(storage: Pick<Storage, 'setItem'>, receipt: SavedReceipt, now = Date.now()): boolean {
  try {
    const minimal = parseReceipt(receipt);
    if (!minimal) return false;
    storage.setItem(RECEIPT_KEY, JSON.stringify({ receipt: minimal, expiresAt: now + RECEIPT_TTL }));
    return true;
  } catch { return false; }
}
