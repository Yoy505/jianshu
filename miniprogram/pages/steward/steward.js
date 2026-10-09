// 管家：场景横向筛选 chips + 服务列表（三态 + 下拉刷新 + 触底加载）
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

function toVM(s) {
  const t = config.SERVICE_TYPE[s.service_type] || { label: s.service_type, cls: 'tag-gray' };
  return {
    id: s.id,
    name: s.name,
    desc: s.desc || '',
    scenarioName: s.scenario_name || '',
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
    chips: [{ key: '', name: '全部' }],
    activeKey: '',
    keyword: '',
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    loading: false,
    finished: false,
    error: ''
  },

  onLoad() {
    this.setData({
      chips: [{ key: '', name: '全部' }].concat(
        config.SCENARIOS.map((s) => ({ key: s.key, name: s.name }))
      )
    });
    this.loadChips();
    this.reload();
  },

  // 用后端场景名刷新 chips（失败保留本地常量，静默）
  loadChips() {
    api
      .getScenarios()
      .then((res) => {
        const arr = Array.isArray(res) ? res : res && res.items ? res.items : [];
        if (arr.length) {
          this.setData({
            chips: [{ key: '', name: '全部' }].concat(arr.map((s) => ({ key: s.key, name: s.name })))
          });
        }
      })
      .catch(() => {});
  },

  reload() {
    // 请求代际 +1：本次 reload 之后返回的旧响应一律丢弃
    this._gen = (this._gen || 0) + 1;
    this.setData({ list: [], page: 1, total: 0, finished: false, error: '' });
    this.fetch();
  },

  fetch() {
    if (this.data.loading) return;
    const gen = (this._gen = this._gen || 0);
    this.setData({ loading: true, error: '' });
    api
      .getServices({
        scenario: this.data.activeKey,
        q: this.data.keyword,
        page: this.data.page,
        page_size: this.data.pageSize
      })
      .then((res) => {
        if (gen !== this._gen) return; // 已被更新的筛选/刷新取代
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
        if (gen !== this._gen) return;
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

  onChip(e) {
    const key = e.currentTarget.dataset.key || '';
    if (key === this.data.activeKey) return;
    this.setData({ activeKey: key });
    this.reload();
  },

  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  onClearKeyword() {
    if (!this.data.keyword) return;
    this.setData({ keyword: '' });
    this.reload();
  },

  goDetail(e) {
    wx.navigateTo({
      url: '/pages/service-detail/service-detail?id=' + e.currentTarget.dataset.id
    });
  }
});
