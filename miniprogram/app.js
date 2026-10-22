// 杏临汇·全域健康管家 —— 应用入口
// 登录态集中管理：storage key 固定为 token / refresh_token / userInfo
App({
  globalData: {
    token: '',
    refreshToken: '',
    userInfo: null,
    loginRequested: false,
    loginReturnUrl: ''
  },

  onLaunch() {
    try {
      const token = wx.getStorageSync('token') || '';
      const refreshToken = wx.getStorageSync('refresh_token') || '';
      const userInfo = wx.getStorageSync('userInfo') || null;
      if (token && !(userInfo && userInfo.phone)) { this.clearAuth(); return; }
      this.globalData.token = token;
      this.globalData.refreshToken = refreshToken;
      this.globalData.userInfo = userInfo;
    } catch (e) {
      // storage 读取失败时保持未登录态
    }
  },

  // 写入登录态（wx/login、sms/login、refresh 成功后调用）
  setAuth(data) {
    if (!data || !(data.token || data.access_token) || !data.user || !data.user.phone) {
      throw new Error('登录结果不完整，请重新授权手机号');
    }
    const token = data.token || data.access_token || '';
    const refreshToken = data.refresh_token || '';
    const userInfo = data.user || null;
    this.globalData.token = token;
    this.globalData.refreshToken = refreshToken;
    this.globalData.userInfo = userInfo;
    try {
      wx.setStorageSync('token', token);
      wx.setStorageSync('refresh_token', refreshToken);
      wx.setStorageSync('userInfo', userInfo);
    } catch (e) {}
  },

  // 清除登录态（refresh 失败 / 主动退出）
  clearAuth() {
    this.globalData.token = '';
    this.globalData.refreshToken = '';
    this.globalData.userInfo = null;
    this.globalData.loginReturnUrl = '';
    this.globalData.loginRequested = false;
    // 已打开的详情立即遮蔽；公开目录清除上一个账号的检索状态。
    const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : [];
    const access = require('./utils/access');
    pages.forEach(page => {
      const key = (page.route || '').split('/')[1];
      if (!page.setData) return;
      if (key && !access.PUBLIC.includes(key)) page.setData({ accessBlocked: true });
      if (['steward', 'drug-hall', 'mall'].includes(key)) {
        page.setData({ keyword: '', searchKeyword: '', activeKey: '', hasFilter: false,
          q: '', activeTag: '', activeLetter: '', active: '', list: [], page: 1, loading: false });
        if (page.reload) page.reload();
      }
    });
    try {
      wx.removeStorageSync('token');
      wx.removeStorageSync('refresh_token');
      wx.removeStorageSync('userInfo');
    } catch (e) {}
  },

  isLoggedIn() {
    return !!this.globalData.token;
  },

  // 仅记录页面位置，登录后不自动重放预约、发送消息等写入操作。
  ensureLogin(returnUrl, blockedPage) {
    if (this.isLoggedIn()) return true;
    const access = require('./utils/access');
    if (this._loginPromptOpen) return false;
    this._loginPromptOpen = true;
    wx.showModal({
      title: '请先登录',
      content: '登录后即可使用此功能，可通过微信关联手机号快捷登录。',
      confirmText: '去登录', cancelText: '继续浏览',
      success: (res) => {
        this._loginPromptOpen = false;
        if (res.confirm) {
          this.globalData.loginReturnUrl = access.validUrl(returnUrl) ? returnUrl : '';
          this.openLogin();
        } else if (blockedPage) {
          wx.switchTab({ url: '/pages/index/index' });
        }
      },
      fail: () => { this._loginPromptOpen = false; },
      complete: () => { this._loginPromptOpen = false; }
    });
    return false;
  },

  openLogin() {
    this.globalData.loginRequested = true;
    const pages = getCurrentPages();
    const page = pages[pages.length - 1];
    if (page && page.route === 'pages/my/my' && page.hfOpenLogin) {
      this.globalData.loginRequested = false;
      page.hfOpenLogin();
    } else {
      wx.switchTab({ url: '/pages/my/my' });
    }
  },

  resumeAfterLogin() {
    const url = this.globalData.loginReturnUrl;
    this.globalData.loginReturnUrl = '';
    this.globalData.loginRequested = false;
    if (url) require('./utils/access').navigate(url);
  }
});
