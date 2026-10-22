import { useRef, useState } from 'react';
import { INITIAL_FORM, loadReceipt, type SavedReceipt } from './application';

/** Drafts stay in app memory; only a minimal receipt is stored for this tab's session. */
export function useApplication() {
  const [form, setForm] = useState(INITIAL_FORM);
  const [receipt, setReceipt] = useState<SavedReceipt | null>(() => {
    try { return loadReceipt(window.sessionStorage); } catch { return null; }
  });
  const [submitting, setSubmitting] = useState(false);
  const submissionLock = useRef(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [storageFailed, setStorageFailed] = useState(false);
  return { form, setForm, receipt, setReceipt, submitting, setSubmitting, submissionLock,
    submitError, setSubmitError, storageFailed, setStorageFailed };
}
export type ApplicationState = ReturnType<typeof useApplication>;
