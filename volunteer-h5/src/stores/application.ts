/** 报名流程跨页面状态（说明页 ↔ 报名页）：草稿仅存内存，回执仍走 sessionStorage 最小快照。
 *  敏感字段（姓名/手机号/单位等）不新增任何持久化（Zustand 不加 persist）。 */
import { create } from 'zustand';
import { INITIAL_FORM, loadReceipt, type FormState, type SavedReceipt } from '../application';

interface ApplicationStore {
  form: FormState;
  receipt: SavedReceipt | null;
  submitting: boolean;
  submitError: string | null;
  storageFailed: boolean;
  locked: boolean;
  setForm: (updater: (previous: FormState) => FormState) => void;
  resetForm: () => void;
  setReceipt: (receipt: SavedReceipt | null) => void;
  setSubmitting: (submitting: boolean) => void;
  setSubmitError: (error: string | null) => void;
  setStorageFailed: (failed: boolean) => void;
  setLocked: (locked: boolean) => void;
  resetAll: () => void;
  clearReceipt: () => void;
}

function initialReceipt(): SavedReceipt | null {
  try { return loadReceipt(window.sessionStorage); } catch { return null; }
}

export const useApplicationStore = create<ApplicationStore>((set) => ({
  form: INITIAL_FORM,
  receipt: initialReceipt(),
  submitting: false,
  submitError: null,
  storageFailed: false,
  locked: false,
  setForm: (updater) => set((state) => ({ form: updater(state.form) })),
  resetForm: () => set({ form: INITIAL_FORM }),
  setReceipt: (receipt) => set({ receipt }),
  setSubmitting: (submitting) => set({ submitting }),
  setSubmitError: (submitError) => set({ submitError }),
  setStorageFailed: (storageFailed) => set({ storageFailed }),
  setLocked: (locked) => set({ locked }),
  resetAll: () => set({
    form: INITIAL_FORM,
    receipt: initialReceipt(),
    submitting: false,
    submitError: null,
    storageFailed: false,
    locked: false,
  }),
  clearReceipt: () => {
    try { window.sessionStorage.removeItem('volunteer.receipt.v1'); } catch { /* 同现有语义：清理失败仍清内存态 */ }
    set({ receipt: null });
  },
}));
