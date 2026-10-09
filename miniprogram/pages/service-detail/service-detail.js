// 服务详情：内容包含 / 适用人群 / 服务须知 + 匹配专业人员 + 立即咨询（online）
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

const SERVICE_NOTICE = {
  online: '目前仅可查看服务介绍并体验系统自动回执，尚未接入真人线上服务。',
  offline: '上门服务人员核验和真实排班尚未接入，暂不能作为实际预约依据。',
  third: '三方机构合作和实际履约流程尚未核验，暂不能作为实际预约依据。'
};

Page({
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
            desc: s.desc || '服务介绍筹备中',
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
    wx.navigateTo({
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
});
