// 我的：微信手机号快捷登录 / 用户信息 / 档案完成度 / 功能列表
const api = require('../../utils/api');
const config = require('../../utils/config');

const PROFILE_FIELDS = [
  'name', 'gender', 'birth', 'height_cm', 'weight_kg', 'blood',
  'chronic_tags', 'allergy', 'surgery_history', 'smoke', 'drink', 'sport_freq'
];

Page(require('../../utils/hifi-page')({
  openTerms() {
    if (this.data.loginOpen) getApp().globalData.loginRequested = true;
    wx.navigateTo({ url: '/pages/legal/legal?doc=terms' });
  },

  openPrivacy() {
    if (this.data.loginOpen) getApp().globalData.loginRequested = true;
    wx.navigateTo({ url: '/pages/legal/legal?doc=privacy' });
  },

  data: {
    logged: false,
    user: null,
    profile: null,
    profilePercent: 0,
    hfProfileError: false,
    wxLoggingIn: false,
    loginAgreed: false,
    loginError: '',
    footer: config.COMPLIANCE.footer
  },

  onShow() {
    this.syncUser();
    const app = getApp();
    if (app.globalData.loginRequested && !this.data.logged) {
      app.globalData.loginRequested = false;
      this.hfOpenLogin();
    }
  },

  syncUser() {
    const app = getApp();
    const logged = !!app.globalData.token;
    if (logged && this.data.loginOpen) this.hfCloseLogin();
    this.setData({ logged, user: app.globalData.userInfo, ...(logged ? { loginOpen: false } : {}) });
    if (logged) {
      this.loadProfile();
    } else {
      this.setData({ profile: null, profilePercent: 0, hfProfileError: false });
    }
  },

  loadProfile() {
    const token = getApp().globalData.token;
    api
      .getProfile()
      .then((res) => {
        if (getApp().globalData.token !== token) return;
        const p = res || null;
        let percent = 0;
        if (p) {
          let filled = 0;
          PROFILE_FIELDS.forEach((k) => {
            const v = p[k];
            if (v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length)) {
              filled += 1;
            }
          });
          percent = Math.round((filled / PROFILE_FIELDS.length) * 100);
        }
        this.setData({ profile: p, profilePercent: percent, hfProfileError: false });
      })
      .catch(() => { if (getApp().globalData.token === token) this.setData({ hfProfileError: true }); });
  },

  onLoginAgreement(e) {
    this.setData({ loginAgreed: (e.detail.value || []).includes('agree'), loginError: '' });
  },

  requireAgreement() {
    this.setData({ loginError: '请先阅读并同意用户协议与隐私政策' });
  },

  // 手机号授权码与 wx.login 登录码分别获取，均仅交给后端验证。
  onGetPhoneNumber(e) {
    if (this.data.wxLoggingIn) return;
    if (!this.data.loginAgreed) { this.requireAgreement(); return; }
    const detail = e.detail || {};
    if (!detail.code) {
      const cancelled = /deny|cancel/i.test(detail.errMsg || '');
      this.setData({ loginError: cancelled ? '你已取消手机号授权，可以继续浏览首页' : '暂未获取到手机号授权，请重试' });
      return;
    }
    this.setData({ wxLoggingIn: true, loginError: '' });
    const fail = message => this.setData({ wxLoggingIn: false, loginError: message });
    wx.login({
      timeout: 10000,
      success: lr => {
        if (!lr.code) { fail('未获取到微信登录凭证，请重试'); return; }
        api.wxLogin(lr.code, detail.code).then(res => {
          getApp().setAuth(res);
          this.setData({ wxLoggingIn: false });
          this.syncUser();
          wx.showToast({ title: '登录成功', icon: 'success' });
          getApp().resumeAfterLogin();
        }).catch(err => fail(err.detail || err.message || '登录失败，请重新授权手机号'));
      },
      fail: () => fail('微信登录失败，请重试')
    });
  },

  contactLogin() {
    getApp().ensureLogin('/pages/my/my');
  },

  /* ---- 功能入口 ---- */
  goAuthPage(e) {
    const url = e.currentTarget.dataset.url;
    require('../../utils/access').navigate(url);
  },

  goProfile() {
    require('../../utils/access').navigate('/pages/profile-edit/profile-edit');
  },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确定退出当前账号吗？',
      confirmText: '退出',
      cancelText: '取消',
      success: (r) => {
        if (r.confirm) {
          getApp().clearAuth();
          this.syncUser();
          wx.showToast({ title: '已退出登录', icon: 'none' });
        }
      }
    });
  }
}, 'my'));
