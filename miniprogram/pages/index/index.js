// 原生首页：固定场景导航与远端服务目录状态分开，空目录不伪装成可用服务。
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

const descriptions = {
  checkup: '早筛评估 · 长期管理', consult: '健康咨询 · 就医指导',
  medication: '用药查询 · 安全科普', rehab: '专业指导 · 恢复训练',
  diet: '科学膳食 · 营养管理', emotion: '睡眠改善 · 心理关怀',
  disable: '居家照护 · 生活支持', escort: '全程陪伴 · 省心就医',
  'mother-baby': '全龄关怀 · 家庭健康'
};
const navigationScenarios = config.SCENARIOS.map((item) => ({
  key: item.key, name: item.name, description: descriptions[item.key],
  icon: '/assets/home-v1/scene-' + item.key + '.png'
}));

Page({
  data: {
    loading: true,
    error: '',
    scenarios: navigationScenarios,
    catalogEmpty: false,
    statusBarHeight: 20,
    menuTop: 24,
    menuHeight: 32,
    menuRightSpace: 98,
    navBottom: 64,
    navScrolled: false,
    notices: [],
    stats: { providers: '0', services: '0', users: '0' },
    footer: config.COMPLIANCE.footer
  },

  onLoad() {
    this.updateChrome();
    this.fetchHome();
  },

  onResize() {
    this.updateChrome();
  },

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

  onPullDownRefresh() {
    this.fetchHome();
  },

  fetchHome() {
    this.setData({ loading: true, error: '' });
    api
      .getHome()
      .then((res) => {
        const remoteScenarios = Array.isArray(res.scenarios) ? res.scenarios : [];
        const stats = res.stats || {};
        this.setData({
          catalogEmpty: remoteScenarios.length === 0,
          error: '',
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
    require('../../utils/access').navigate({
      url:
        '/pages/scenario/scenario?key=' +
        encodeURIComponent(key) +
        '&name=' +
        encodeURIComponent(name)
    });
  },

  goAI() {
    if (!getApp().ensureLogin('/pages/index/index')) return;
    wx.showToast({ title: '请通过健康管家查看相关服务', icon: 'none' });
  },

  goRehab() {
    require('../../utils/access').navigate({ url: '/pages/scenario/scenario?key=rehab&name=' + encodeURIComponent('术后康复') });
  },

  goDrugHall() {
    require('../../utils/access').navigate({ url: '/pages/drug-hall/drug-hall' });
  },

  goMall() {
    wx.switchTab({ url: '/pages/mall/mall' });
  }
});
