/** 杏临千行志愿者招募：两级页面共享内容常量 */

export const TRACKS = [
  { key: 'practice', label: '公益实践' },
  { key: 'publicity', label: '科普宣传' },
  { key: 'assist', label: '活动协助' },
  { key: 'promotion', label: '宣传支持' },
  { key: 'other', label: '其他' },
] as const;
export type TrackKey = (typeof TRACKS)[number]['key'];

export const OFFLINE_OPTIONS = [
  { value: 1, label: '每年 1 次' },
  { value: 2, label: '每年 2-3 次' },
  { value: 4, label: '每年 4 次及以上' },
] as const;

export const TENET_TEXT =
  '“杏临千行”公益行动以守护职业健康为使命，汇聚各界志愿力量，组织志愿者深入基层，' +
  '开展公益实践、科普宣传、便民服务、互助帮扶等志愿服务，为参与者搭建社会实践锻炼平台，' +
  '让专业能力服务社会所需，让各行各业的责任与担当在行动中彰显。';

export const CONDITIONS = [
  { title: '招募范围', desc: '社会各界人士均可报名，不限行业、不限专业。' },
  { title: '品行要求', desc: '遵守纪律，无不良记录；自愿参与志愿服务，具有奉献精神。' },
  { title: '综合能力', desc: '身体健康，善于沟通协作，服从活动统一安排与调度。' },
  { title: '时间要求', desc: '每年至少参加 1 次线下志愿活动，能承担 3-5 天驻点服务或短期实践者优先。' },
];

export const SERVICE_TRACKS = [
  { title: '公益实践', desc: '深入基层一线，承担公益服务、现场协助、咨询引导等实践工作。' },
  { title: '科普宣传', desc: '面向基层群众开展职业健康科普宣讲、便民知识讲解与政策宣传。' },
  { title: '活动协助', desc: '参与活动实操支持、资料整理与会务辅助，助力活动顺利开展。' },
  { title: '宣传支持', desc: '协助基层单位做好宣传推广与交流活动落地，扩大公益行动影响。' },
];

export const MOBILE_RE = /^1[3-9]\d{9}$/;
export const API_BASE = '/api/v1';

export interface VolunteerMeta {
  enabled: boolean;
  consent_text: string;
}

export interface VolunteerReceipt {
  id: number;
  receipt_no: string;
  submitted_at: string;
}

export async function fetchVolunteerMeta(signal?: AbortSignal): Promise<VolunteerMeta> {
  const res = await fetch(`${API_BASE}/h5/volunteer-applications/meta`, { signal });
  if (!res.ok) throw new Error('无法确认报名通道状态');
  const data: unknown = await res.json();
  if (!data || typeof data !== 'object' || !('enabled' in data) || typeof data.enabled !== 'boolean'
    || !('consent_text' in data) || typeof data.consent_text !== 'string' || !data.consent_text.trim()) {
    throw new Error('报名通道返回的信息不完整');
  }
  return { enabled: data.enabled, consent_text: data.consent_text };
}
