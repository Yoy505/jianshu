import axios from 'axios';
import { API_BASE } from './api';

export const OCCUPATIONAL_TOPICS = [
  '职业病危害防护',
  '办公人群肌肉骨骼健康',
  '职场心理健康疏导',
  '新就业形态劳动者健康保护',
  '职业健康法律科普',
  '职场急救',
  '工作场所健康生活方式',
] as const;

export type OccupationalWorkType = 'article' | 'video';

export interface OccupationalMeta {
  enabled: boolean;
  consent_text: string;
  accepted_work_types: OccupationalWorkType[];
  topics: string[];
  accepts_other_topic: boolean;
  /** 2026-10-08 起不限字数与时长；仅保留体积与分辨率上限 */
  limits: {
    article_max_bytes: number;
    video_max_bytes: number;
    video_min_short_edge: number;
    video_min_long_edge: number;
  };
}

export interface OccupationalReceipt {
  id: number;
  receipt_no: string;
  status: 'submitted';
  submitted_at: string;
}

/** 公开回执查询：仅状态与驳回理由，不含联系方式或文件信息。 */
export interface OccupationalReceiptStatus {
  receipt_no: string;
  title: string;
  work_type: OccupationalWorkType;
  status: 'submitted' | 'initial_reviewing' | 'pending_final' | 'accepted' | 'rejected';
  submitted_at: string;
  decided_at: string | null;
  comment: string | null;
}

const submissionUrl = `${API_BASE}/h5/occupational-submissions`;

export async function getOccupationalMeta(): Promise<OccupationalMeta> {
  const { data } = await axios.get<OccupationalMeta>(`${submissionUrl}/meta`, {
    timeout: 15_000,
  });
  return data;
}

export async function queryOccupationalReceipt(receiptNo: string): Promise<OccupationalReceiptStatus> {
  const { data } = await axios.get<OccupationalReceiptStatus>(
    `${submissionUrl}/receipt/${encodeURIComponent(receiptNo.trim().toUpperCase())}`,
    { timeout: 15_000 },
  );
  return data;
}

export async function submitOccupationalWork(
  payload: FormData,
  onProgress: (percent: number) => void,
): Promise<OccupationalReceipt> {
  // 公开投稿接口由浏览器自行设置 multipart boundary。
  const { data } = await axios.post<OccupationalReceipt>(submissionUrl, payload, {
    timeout: 0,
    onUploadProgress: (event) => {
      if (event.total) onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
    },
  });
  if (!data || !data.receipt_no || data.status !== 'submitted') {
    throw new Error('服务器未返回有效回执。表单仍在，请使用同一页面重试或联系活动组织方核对。');
  }
  return data;
}
