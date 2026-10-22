// 管家：真实服务目录；验收演示数据仅在模拟器内临时注入。
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

function toVM(s) {
  const t = config.SERVICE_TYPE[s.service_type] || { label: s.service_type, cls: 'tag-gray' };
  const scenario = config.SCENARIOS.find((item) => item.key === s.scenario);
  const duration = s.duration_min > 0 ? '参考时长 ' + s.duration_min + '分钟' : '参考时长待确认';
  const cost = s.price === null || s.price === undefined || s.price === ''
    ? '参考费用待确认' : '参考 ¥' + format.price(s.price) + (s.unit ? '/' + s.unit : '');
  return {
    icon: scenario ? '/assets/home-v1/scene-' + scenario.key + '.png' : '/assets/home-v1/heart-teal.png',
    category: [s.scenario_name || (scenario && scenario.name), t.label].filter(Boolean).join(' · '),
    meta: duration + ' · ' + cost,
    providerLabel: s.provider_count === null || s.provider_count === undefined ? '' : '人员资料 ' + s.provider_count + ' 条',
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
    searchKeyword: '',
    hasFilter: false,
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    loading: false,
    finished: false,
    error: '',
    statusBarHeight: 20, menuTop: 24, menuHeight: 32, menuRightSpace: 98, navBottom: 64, navScrolled: false,
    footer: config.COMPLIANCE.footer
  },

  onLoad() {
    this.updateChrome();
    this.setData({
      chips: [{ key: '', name: '全部' }].concat(
        config.SCENARIOS.map((s) => ({ key: s.key, name: s.name }))
      )
    });
    this.loadChips();
    this.reload();
  },

  onResize() { this.updateChrome(); },

  onPageScroll(event) {
    const navScrolled = event.scrollTop > 8;
    if (navScrolled !== this.data.navScrolled) this.setData({ navScrolled });
  },

  updateChrome() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const statusBarHeight = info.statusBarHeight || 20;
    let capsule;
    try { capsule = wx.getMenuButtonBoundingClientRect(); } catch (e) {}
    const valid = capsule && capsule.height > 0 && capsule.top >= statusBarHeight;
    this.setData({
      statusBarHeight,
      menuTop: valid ? capsule.top : statusBarHeight + 4,
      menuHeight: valid ? capsule.height : 32,
      navBottom: valid ? capsule.top + capsule.height + 8 : statusBarHeight + 44,
      menuRightSpace: valid ? Math.max(0, info.windowWidth - capsule.left + 8) : 98
    });
  },

  onUnload() { this._gen = (this._gen || 0) + 1; },

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
    this.setData({ list: [], page: 1, total: 0, finished: false, error: '', loading: false,
      hasFilter: Boolean(this.data.activeKey || this.data.searchKeyword) });
    this.fetch(1);
  },

  fetch(page) {
    if (this.data.loading) return;
    const requestPage = typeof page === 'number' ? page : this.data.page;
    const gen = (this._gen = this._gen || 0);
    this.setData({ loading: true, error: '' });
    api
      .getServices({
        scenario: this.data.activeKey,
        q: this.data.searchKeyword,
        page: requestPage,
        page_size: this.data.pageSize
      })
      .then((res) => {
        if (gen !== this._gen) return; // 已被更新的筛选/刷新取代
        const items = (res.items || []).map(toVM);
        const total = res.total || 0;
        const list = requestPage === 1 ? items : this.data.list.concat(items);
        this.setData({
          list,
          page: requestPage,
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
    if (!getApp().ensureLogin('/pages/steward/steward')) return;
    this.fetch(this.data.page + 1);
  },

  retryMore() {
    if (!getApp().ensureLogin('/pages/steward/steward')) return; this.fetch(this.data.page + 1); },

  onChip(e) {
    if (!getApp().ensureLogin('/pages/steward/steward')) return;
    const key = e.currentTarget.dataset.key || '';
    if (key === this.data.activeKey) return;
    this.setData({ activeKey: key, searchKeyword: this.data.keyword.trim() });
    this.reload();
  },

  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    if (!getApp().ensureLogin('/pages/steward/steward')) return;
    this.setData({ searchKeyword: this.data.keyword.trim() });
    this.reload();
  },

  onClearKeyword() {
    if (!this.data.keyword) return;
    this.setData({ keyword: '', searchKeyword: '' });
    this.reload();
  },

  onResetFilters() {
    this.setData({ keyword: '', searchKeyword: '', activeKey: '' });
    this.reload();
  },

  goDetail(e) {
    require('../../utils/access').navigate({
      url: '/pages/service-detail/service-detail?id=' + encodeURIComponent(e.currentTarget.dataset.id)
    });
  }
});
