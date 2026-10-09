// 杏临汇·全域健康管家 —— 应用入口
// 登录态集中管理：storage key 固定为 token / refresh_token / userInfo
App({
  globalData: {
    token: '',
    refreshToken: '',
    userInfo: null
  },

  onLaunch() {
    try {
      const token = wx.getStorageSync('token') || '';
      const refreshToken = wx.getStorageSync('refresh_token') || '';
      const userInfo = wx.getStorageSync('userInfo') || null;
      this.globalData.token = token;
      this.globalData.refreshToken = refreshToken;
      this.globalData.userInfo = userInfo;
    } catch (e) {
      // storage 读取失败时保持未登录态
    }
  },

  // 写入登录态（wx/login、sms/login、refresh 成功后调用）
  setAuth(data) {
    if (!data) return;
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
    try {
      wx.removeStorageSync('token');
      wx.removeStorageSync('refresh_token');
      wx.removeStorageSync('userInfo');
    } catch (e) {}
  },

  isLoggedIn() {
    return !!this.globalData.token;
  },

  // 未登录操作统一引导：预约 / 咨询 / 收藏 / 档案等
  ensureLogin() {
    if (this.globalData.token) return true;
    wx.showModal({
      title: '请先登录',
      content: '登录后即可使用该功能',
      confirmText: '去登录',
      cancelText: '暂不',
      success(res) {
        if (res.confirm) {
          wx.switchTab({ url: '/pages/my/my' });
        }
      }
    });
    return false;
  }
});
