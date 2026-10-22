// 全局配置与领域常量
// 开发环境指向本地后端；上线改为已备案域名（singlin.cn，2026-09-22 定稿），
// 并在小程序管理后台「开发设置-服务器域名」配置：
//   request 合法域名：https://mp.singlin.cn
//   socket 合法域名：wss://mp.singlin.cn（咚咚 WebSocket）
// const BASE_URL = 'http://127.0.0.1:8000/api/v1'; // 本地开发
const BASE_URL = 'https://mp.singlin.cn/api/v1'; // 生产（已备案+已上线）

// 九大场景（key 与后端 Scenario.key 枚举一致；char 为九宫格文字图标）
const SCENARIOS = [
  { key: 'checkup', name: '体检慢病', char: '检' },
  { key: 'consult', name: '看病问诊', char: '诊' },
  { key: 'medication', name: '合理用药', char: '药' },
  { key: 'rehab', name: '术后康复', char: '康' },
  { key: 'diet', name: '饮食调理', char: '食' },
  { key: 'emotion', name: '失眠情绪', char: '眠' },
  { key: 'disable', name: '失能照护', char: '护' },
  { key: 'escort', name: '就医陪同', char: '陪' },
  { key: 'mother-baby', name: '母婴·老年', char: '育' }
];

// 各场景适用人群（服务详情页「适用人群」展示）
const SCENARIO_AUDIENCE = {
  checkup: '体检指标异常、高血压/糖尿病等慢病需长期管理的人群',
  consult: '有明确就诊需求、需要全科或专科医师指导的人群',
  medication: '多药并用、老年人及慢病长期用药需要用药指导的人群',
  rehab: '术后恢复期、功能锻炼期需要康复指导的人群',
  diet: '需要营养干预、体重与代谢管理的人群',
  emotion: '受失眠、焦虑等情绪困扰，需要心理支持的人群',
  disable: '失能/半失能长者的家庭照护者与照护需求家庭',
  escort: '独自就医困难、需要专业人员陪同协助的人群',
  'mother-baby': '孕产妇、婴幼儿家庭及需要老年健康照护的家庭'
};

// 服务类型（Service.service_type）
const SERVICE_TYPE = {
  online: { label: '线上服务', cls: 'tag-info' },
  offline: { label: '线下上门', cls: 'tag-brand' },
  third: { label: '三方机构', cls: 'tag-warn' }
};

// 预约状态（Appointment.status）
const APPOINTMENT_STATUS = {
  pending: { label: '待确认', cls: 'tag-warn' },
  confirmed: { label: '已确认', cls: 'tag-info' },
  completed: { label: '已完成', cls: 'tag-success' },
  cancelled: { label: '已取消', cls: 'tag-gray' }
};

// 药品标签（Drug.tags）
const DRUG_TAGS = {
  otc: { label: 'OTC', cls: 'tag-success' },
  rx: { label: '处方药', cls: 'tag-danger' },
  essential: { label: '基药', cls: 'tag-info' },
  insurance: { label: '医保', cls: 'tag-warn' }
};

// 商城商品分类（Product.category）
const PRODUCT_CATEGORY = {
  food: { label: '药食同源', char: '食' },
  fsmp: { label: '特医食品', char: '特' },
  package: { label: '健康服务包', char: '包' },
  device: { label: '器械耗材', char: '械' }
};

// 预约时段（后端校验 period ∈ 上午/下午/晚间）
const PERIODS = ['上午', '下午', '晚间'];

// 健康档案慢病标签
const CHRONIC_TAGS = ['高血压', '糖尿病', '冠心病', '痛风', '高血脂', '其他'];

// 医疗合规固定文案
const COMPLIANCE = {
  footer: '本平台提供健康管理与信息服务，不替代诊疗；就医请前往正规医疗机构。',
  chat: '当前仅提供系统自动回执，不提供真人咨询或个体化用药建议；紧急情况请拨打 120。',
  ai: '健康信息参考，不构成诊疗建议',
  verified: '人员资质尚未完成核验',
  drug: '药品信息仅供参考，具体用药请遵医嘱或咨询执业药师。'
};

const STORAGE_KEYS = { token: 'token', refreshToken: 'refresh_token', userInfo: 'userInfo' };

module.exports = {
  BASE_URL,
  SCENARIOS,
  SCENARIO_AUDIENCE,
  SERVICE_TYPE,
  APPOINTMENT_STATUS,
  DRUG_TAGS,
  PRODUCT_CATEGORY,
  PERIODS,
  CHRONIC_TAGS,
  COMPLIANCE,
  STORAGE_KEYS
};
