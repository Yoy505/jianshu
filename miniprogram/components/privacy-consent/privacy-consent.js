// 微信隐私授权弹窗：配合 app.json __usePrivacyCheck__ 使用。
// 触发隐私接口时由 wx.onNeedPrivacyAuthorization 回调弹出；
// 用户同意（官方 agreePrivacyAuthorization 按钮）或拒绝后回调 resolve。
Component({
  lifetimes: {
    attached() {
      const self = this;
      if (wx.onNeedPrivacyAuthorization) {
        this._handler = (resolve) => {
          self.setData({ visible: true });
          self._resolve = resolve;
        };
        wx.onNeedPrivacyAuthorization(this._handler);
      }
    },
    detached() {
      if (wx.offNeedPrivacyAuthorization && this._handler) {
        wx.offNeedPrivacyAuthorization(this._handler);
        this._handler = null;
      }
    }
  },

  data: {
    visible: false
  },

  methods: {
    noop() {},

    openPrivacyDoc() {
      wx.navigateTo({ url: '/pages/legal/legal?doc=privacy' });
    },

    handleAgree() {
      this.setData({ visible: false });
      if (this._resolve) {
        this._resolve({ buttonId: 'agree-btn', event: 'agree' });
        this._resolve = null;
      }
    },

    handleDisagree() {
      this.setData({ visible: false });
      if (this._resolve) {
        this._resolve({ event: 'disagree' });
        this._resolve = null;
      }
      wx.showToast({ title: '未同意隐私政策，相关功能无法使用', icon: 'none' });
    }
  }
});
