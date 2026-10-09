// 首页：品牌头 + 公告滚动 + 九宫格场景 + 快捷入口 + 平台数据 + 免责声明
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

Page({
  data: {
    loading: true,
    error: '',
    scenarios: [],
    notices: [],
    stats: { providers: '0', services: '0', users: '0' },
    footer: config.COMPLIANCE.footer
  },

  onLoad() {
    this.fetchHome();
  },

  onPullDownRefresh() {
    this.fetchHome();
  },

  fetchHome() {
    const firstLoad = !this.data.notices.length && !this.data.scenarios.length;
    this.setData({ loading: firstLoad, error: '' });
    api
      .getHome()
      .then((res) => {
        const charMap = {};
        config.SCENARIOS.forEach((s) => {
          charMap[s.key] = s.char;
        });
        const scenarios = (res.scenarios || []).map((s) => ({
          key: s.key,
          name: s.name || s.key,
          tagline: s.tagline || '',
          char: charMap[s.key] || (s.name ? s.name.slice(0, 1) : '康')
        }));
        const stats = res.stats || {};
        this.setData({
          scenarios,
          notices: res.notices || [],
          stats: {
            providers: format.thousands(stats.providers || 0),
            services: format.thousands(stats.services || 0),
            users: format.thousands(stats.users || 0)
          },
          loading: false
        });
        wx.stopPullDownRefresh();
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
        wx.stopPullDownRefresh();
      });
  },

  retry() {
    this.setData({ loading: true, error: '' });
    this.fetchHome();
  },

  goScenario(e) {
    const key = e.currentTarget.dataset.key;
    const name = e.currentTarget.dataset.name;
    wx.navigateTo({
      url:
        '/pages/scenario/scenario?key=' +
        encodeURIComponent(key) +
        '&name=' +
        encodeURIComponent(name)
    });
  },

  goAI() {
    wx.showToast({ title: '健康问答功能筹备中', icon: 'none' });
  },

  goDrugHall() {
    wx.navigateTo({ url: '/pages/drug-hall/drug-hall' });
  },

  goMall() {
    wx.switchTab({ url: '/pages/mall/mall' });
  }
});
