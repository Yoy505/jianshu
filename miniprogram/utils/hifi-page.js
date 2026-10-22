// Native presentation and navigation shared by the high-fidelity pages.
// Original page controllers continue to own data loading, permissions and writes.
const config = require('./config');
const TITLES = {
  mall: '健康商城', my: '我的', scenario: '健康场景', 'service-detail': '服务详情',
  providers: '人员资料', 'provider-detail': '人员详情', book: '预约服务',
  appointments: '我的预约', review: '服务评价', 'consult-list': '我的咨询',
  chat: '咨询会话', 'drug-hall': '药品数字展厅', 'drug-detail': '药品资料',
  favorites: '药品收藏', 'profile-edit': '健康档案', certify: '专业人员入驻', legal: '用户协议',
  'package-detail': '服务包详情', 'order-confirm': '确认订单', 'order-result': '支付结果',
  orders: '我的订单', 'order-detail': '订单详情', benefits: '我的权益', 'my-service': '我的服务'
};
const TABS = ['index', 'steward', 'mall', 'my'];
const SCENE_COPY = [
  ['早筛评估 · 长期管理', '从了解体检指标开始\n关注日常健康变化'],
  ['健康咨询 · 就医指导', '了解就医信息，从了解开始'],
  ['用药查询 · 安全科普', '查阅用药资料，从了解开始'],
  ['专业指导 · 恢复训练', '了解康复服务，从了解开始'],
  ['科学膳食 · 营养管理', '关注日常饮食，从了解开始'],
  ['睡眠改善 · 心理关怀', '关注睡眠情绪，从了解开始'],
  ['居家照护 · 生活支持', '了解生活照护，从了解开始'],
  ['全程陪伴 · 省心就医', '了解就医陪同，从了解开始'],
  ['全龄关怀 · 家庭健康', '关怀全龄健康，从了解开始']
];
module.exports = function hifiPage(definition, key) {
  const originalLoad = definition.onLoad;
  const originalResize = definition.onResize;
  definition.data = Object.assign({
    hfTitle: TITLES[key], hfStatus: 20, hfWidth: 390,
    loginOpen: false
  }, definition.data);

  definition.hfChrome = function () {
    const window = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    this.setData({ hfStatus: window.statusBarHeight || 20, hfWidth: window.windowWidth || 390 });
  };
  definition.onLoad = function (options) {
    options = options || {};
    this._accessUrl = require('./access').pageUrl(key, options);
    this.hfChrome();
    if (originalLoad) originalLoad.call(this, options);
    if (key === 'scenario') {
      const index = Math.max(0, config.SCENARIOS.findIndex(scene => scene.key === this.scenarioKey));
      this.setData({
        hfTitle: this.data.title,
        hfSceneKey: this.scenarioKey || 'checkup',
        hfSceneNumber: '0' + (index + 1),
        hfSceneDesc: SCENE_COPY[index][0],
        hfSceneCaption: SCENE_COPY[index][1],
        hfAudience: config.SCENARIO_AUDIENCE[this.scenarioKey] || '关注健康管理的人群'
      });
    }
    if (key === 'review') this.setData({ hfReviewEligible: !!this.id });
    if (key === 'providers') this.setData({ hfServiceName: this.serviceName ? '与' + this.serviceName + '相关的人员资料' : '' });
    if (key === 'legal') this.setData({ hfTitle: this.data.doc.title, hfDoc: options.doc === 'privacy' ? 'privacy' : 'terms' });
  };
  definition.onResize = function (event) {
    this.hfChrome();
    if (originalResize) originalResize.call(this, event);
  };
  definition.hfBack = function () {
    if (getCurrentPages().length > 1) wx.navigateBack();
    else wx.switchTab({ url: '/pages/index/index' });
  };
  definition.hfGo = function (event) {
    const url = event.currentTarget.dataset.url;
    if (!url) return;
    if (url === '/pages/my/my' && !getApp().isLoggedIn()) getApp().openLogin();
    else require('./access').navigate(url);
  };
  definition.hfRetry = function () {
    const retry = this.retry || this.reload || this.fetch;
    if (retry) retry.call(this);
  };
  definition.hfUnavailable = function () { wx.showToast({ title: '当前功能暂未开放', icon: 'none' }); };
  definition.hfOpenLogin = function () {
    this.setData({ loginOpen: true });
    if (key === 'my') wx.hideTabBar({ animation: false });
  };
  definition.hfCloseLogin = function (event) {
    this.setData({ loginOpen: false });
    if (key === 'my' && event && !getApp().isLoggedIn()) getApp().globalData.loginReturnUrl = '';
    if (key === 'my') wx.showTabBar({ animation: false });
  };
  if (key === 'my') {
    const originalHide = definition.onHide;
    definition.onHide = function () {
      if (this.data.loginOpen) this.hfCloseLogin();
      if (originalHide) originalHide.call(this);
    };
  }
  definition.hfNoop = function () {};
  definition.hfLegal = function (event) {
    const documentKey = event.currentTarget.dataset.doc === 'privacy' ? 'PRIVACY' : 'TERMS';
    const doc = require('./legal')[documentKey];
    this.setData({ doc, hfDoc: documentKey === 'PRIVACY' ? 'privacy' : 'terms', hfTitle: doc.title });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  };
  return require('./access').protectPage(definition, key);
};
