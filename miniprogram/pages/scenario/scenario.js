// 场景服务列表（带场景名标题，复用服务卡；也承载 AI 医师助手等搜索入口）
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

function toVM(s) {
  const t = config.SERVICE_TYPE[s.service_type] || { label: s.service_type, cls: 'tag-gray' };
  return {
    id: s.id,
    name: s.name,
    desc: s.desc || '',
    typeLabel: t.label,
    typeCls: t.cls,
    price: format.price(s.price),
    unit: s.unit || '',
    duration: s.duration_min ? s.duration_min + '分钟' : '时长待定',
    sales: s.sales || 0,
    providerCount: s.provider_count || 0
  };
}

Page({
  data: {
    title: '场景服务',
    tagline: '',
    keyword: '',
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    loading: false,
    finished: false,
    error: ''
  },

  onLoad(options) {
    this.scenarioKey = options.key ? decodeURIComponent(options.key) : '';
    this.searchQ = options.q ? decodeURIComponent(options.q) : '';
    const name = options.name ? decodeURIComponent(options.name) : '场景服务';
    wx.setNavigationBarTitle({ title: name });
    this.setData({ title: name, keyword: this.searchQ });
    if (this.scenarioKey) {
      this.loadTagline();
    }
    this.reload();
  },

  loadTagline() {
    api
      .getScenarios()
      .then((res) => {
        const arr = Array.isArray(res) ? res : res && res.items ? res.items : [];
        const hit = arr.filter((s) => s.key === this.scenarioKey)[0];
        if (hit && hit.tagline) this.setData({ tagline: hit.tagline });
      })
      .catch(() => {});
  },

  reload() {
    this.setData({ list: [], page: 1, total: 0, finished: false, error: '' });
    this.fetch();
  },

  fetch() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    api
      .getServices({
        scenario: this.scenarioKey,
        q: this.searchQ,
        page: this.data.page,
        page_size: this.data.pageSize
      })
      .then((res) => {
        const items = (res.items || []).map(toVM);
        const total = res.total || 0;
        const list = this.data.page === 1 ? items : this.data.list.concat(items);
        this.setData({
          list,
          total,
          loading: false,
          finished: list.length >= total || items.length === 0
        });
        wx.stopPullDownRefresh();
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
        wx.stopPullDownRefresh();
      });
  },

  onPullDownRefresh() {
    this.reload();
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading || !this.data.list.length) return;
    this.setData({ page: this.data.page + 1 });
    this.fetch();
  },

  goDetail(e) {
    wx.navigateTo({
      url: '/pages/service-detail/service-detail?id=' + e.currentTarget.dataset.id
    });
  }
});
