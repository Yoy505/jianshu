// 服务详情：内容包含 / 适用人群 / 服务须知 + 匹配专业人员 + 立即咨询（online）
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

const SERVICE_NOTICE = {
  online: '请先查看服务内容，并选择相关服务人员。',
  offline: '请根据可选时段预约，并确认服务地点和具体需求。',
  third: '请查看机构与人员资料，确认服务内容和具体安排。'
};

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    service: null
  },

  onLoad(options) {
    this.id = options.id;
    this.fetch();
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    api
      .getService(this.id)
      .then((s) => {
        const t = config.SERVICE_TYPE[s.service_type] || { label: s.service_type, cls: 'tag-gray' };
        this.setData({
          loading: false,
          service: {
            id: s.id,
            scenarioKey: s.scenario,
            scenarioName: s.scenario_name || '',
            name: s.name,
            desc: s.desc || '暂无详细介绍',
            typeLabel: t.label,
            typeCls: t.cls,
            isOnline: s.service_type === 'online',
            price: format.price(s.price),
            unit: s.unit || '',
            duration: s.duration_min ? s.duration_min + '分钟' : '时长待定',
            sales: s.sales || 0,
            providerCount: s.provider_count || 0,
            audience: config.SCENARIO_AUDIENCE[s.scenario] || '关注健康、需要专业指导的人群',
            notice: SERVICE_NOTICE[s.service_type] || SERVICE_NOTICE.online
          }
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
      });
  },

  retry() {
    this.fetch();
  },

  matchProviders() {
    const s = this.data.service;
    require('../../utils/access').navigate({
      url:
        '/pages/providers/providers?service_id=' +
        s.id +
        '&service_name=' +
        encodeURIComponent(s.name)
    });
  },

  consultNow() {
    this.matchProviders();
  }
}, 'service-detail'));
