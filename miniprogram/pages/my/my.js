// 我的：登录卡（微信一键 + 手机验证码双通道）/ 用户信息 / 档案完成度 / 功能列表
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

const PROFILE_FIELDS = [
  'name', 'gender', 'birth', 'height_cm', 'weight_kg', 'blood',
  'chronic_tags', 'allergy', 'surgery_history', 'smoke', 'drink', 'sport_freq'
];

Page({
  openTerms() {
    wx.navigateTo({ url: '/pages/legal/legal?doc=terms' });
  },

  openPrivacy() {
    wx.navigateTo({ url: '/pages/legal/legal?doc=privacy' });
  },

  data: {
    logged: false,
    user: null,
    profile: null,
    profilePercent: 0,
    smsPhone: '',
    smsCode: '',
    countdown: 0,
    sending: false,
    wxLoggingIn: false,
    smsLoggingIn: false,
    footer: config.COMPLIANCE.footer
  },

  onShow() {
    this.syncUser();
  },

  onUnload() {
    this.clearSmsTimer();
  },

  onHide() {
    // 页面隐藏时停止倒计时计时器，返回时由 onShow 继续
  },

  syncUser() {
    const app = getApp();
    const logged = !!app.globalData.token;
    this.setData({ logged, user: app.globalData.userInfo });
    if (logged) {
      this.loadProfile();
    } else {
      this.setData({ profile: null, profilePercent: 0 });
    }
  },

  loadProfile() {
    api
      .getProfile()
      .then((res) => {
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
        this.setData({ profile: p, profilePercent: percent });
      })
      .catch(() => {});
  },

  /* ---- 微信一键登录 ---- */
  wxLogin() {
    if (this.data.wxLoggingIn) return;
    this.setData({ wxLoggingIn: true });
    wx.login({
      success: (lr) => {
        if (!lr.code) {
          this.setData({ wxLoggingIn: false });
          wx.showToast({ title: '未获取到登录凭证，请重试', icon: 'none' });
          return;
        }
        api
          .wxLogin(lr.code)
          .then((res) => {
            getApp().setAuth(res);
            this.setData({ wxLoggingIn: false });
            wx.showToast({ title: '登录成功', icon: 'success' });
            this.syncUser();
          })
          .catch(() => {
            this.setData({ wxLoggingIn: false });
          });
      },
      fail: () => {
        this.setData({ wxLoggingIn: false });
        wx.showToast({ title: '微信登录失败，请重试', icon: 'none' });
      }
    });
  },

  /* ---- 手机验证码登录 ---- */
  onPhone(e) {
    this.setData({ smsPhone: e.detail.value });
  },

  onCode(e) {
    this.setData({ smsCode: e.detail.value });
  },

  sendSms() {
    if (this.data.countdown > 0 || this.data.sending) return;
    if (!format.phoneValid(this.data.smsPhone)) {
      wx.showToast({ title: '请输入正确的手机号', icon: 'none' });
      return;
    }
    this.setData({ sending: true });
    api
      .smsSend(this.data.smsPhone)
      .then(() => {
        this.setData({ sending: false });
        wx.showToast({ title: '验证码已发送', icon: 'none' });
        this.startCountdown();
      })
      .catch(() => {
        this.setData({ sending: false });
      });
  },

  startCountdown() {
    this.clearSmsTimer();
    this.setData({ countdown: 60 });
    this.smsTimer = setInterval(() => {
      const c = this.data.countdown - 1;
      if (c <= 0) {
        this.clearSmsTimer();
        this.setData({ countdown: 0 });
      } else {
        this.setData({ countdown: c });
      }
    }, 1000);
  },

  clearSmsTimer() {
    if (this.smsTimer) {
      clearInterval(this.smsTimer);
      this.smsTimer = null;
    }
  },

  smsLogin() {
    if (this.data.smsLoggingIn) return;
    const phone = this.data.smsPhone;
    const code = this.data.smsCode;
    if (!format.phoneValid(phone)) {
      wx.showToast({ title: '请输入正确的手机号', icon: 'none' });
      return;
    }
    if (!/^\d{4,6}$/.test(code)) {
      wx.showToast({ title: '请输入收到的验证码', icon: 'none' });
      return;
    }
    this.setData({ smsLoggingIn: true });
    api
      .smsLogin(phone, code)
      .then((res) => {
        this.setData({ smsLoggingIn: false });
        getApp().setAuth(res);
        wx.showToast({ title: '登录成功', icon: 'success' });
        this.syncUser();
      })
      .catch(() => {
        this.setData({ smsLoggingIn: false });
      });
  },

  /* ---- 功能入口 ---- */
  goAuthPage(e) {
    const url = e.currentTarget.dataset.url;
    if (!getApp().ensureLogin()) return;
    wx.navigateTo({ url });
  },

  goProfile() {
    if (!getApp().ensureLogin()) return;
    wx.navigateTo({ url: '/pages/profile-edit/profile-edit' });
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
});
