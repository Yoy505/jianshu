// 确认下单：选择支付方式（微信支付 / 积分兑换），勾选协议后幂等下单。
// 微信支付：prepay → wx.requestPayment → 结果页以服务端订单状态为准。
const api = require('../../utils/api');
const format = require('../../utils/format');

function uuid() {
  let out = '';
  for (let i = 0; i < 32; i += 1) {
    const hex = Math.floor(Math.random() * 16).toString(16);
    out += (i === 8 || i === 12 || i === 16 || i === 20) ? '-' + hex : hex;
  }
  return out;
}

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    pkg: null,
    balance: 0,
    payType: 'wxpay',
    pointsEnough: false,
    agreed: false,
    submitting: false
  },

  onLoad(options) {
    this.id = options.id;
    this._clientOrderId = '';
    this.fetch();
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    Promise.all([
      api.getPackage(this.id),
      api.getPoints().catch(() => ({ balance: 0 }))
    ]).then(([d, points]) => {
      const balance = points && points.balance ? points.balance : 0;
      const canPoints = !!(d.purchasable && d.purchasable.points);
      const pointsEnough = canPoints && !!d.points_cost && balance >= d.points_cost;
      this.setData({
        loading: false,
        balance,
        pointsEnough,
        payType: d.purchasable && d.purchasable.wxpay ? 'wxpay' : (pointsEnough ? 'points' : ''),
        pkg: {
          id: d.id,
          versionId: d.version_id,
          name: d.name,
          price: format.price(d.price_cents),
          priceCents: d.price_cents,
          pointsCost: d.points_cost || 0,
          validityDays: d.validity_days,
          termsVersion: d.terms_version,
          terms: d.terms || '',
          canWxpay: !!(d.purchasable && d.purchasable.wxpay),
          canPoints
        }
      });
    }).catch((err) => {
      this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
    });
  },

  retry() {
    this.fetch();
  },

  choosePay(e) {
    this.setData({ payType: e.currentTarget.dataset.v });
  },

  toggleAgree(e) {
    this.setData({ agreed: !!e.detail.value.length });
  },

  openTerms() {
    wx.navigateTo({ url: '/pages/legal/legal?doc=terms' });
  },

  submit() {
    if (this.data.submitting) return;
    const pkg = this.data.pkg;
    if (!pkg) return;
    if (!this.data.payType) {
      wx.showToast({ title: '该服务包暂不可购买', icon: 'none' });
      return;
    }
    if (!this.data.agreed) {
      wx.showToast({ title: '请先阅读并同意售后规则', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    if (!this._clientOrderId) this._clientOrderId = uuid();
    api
      .createOrder({
        client_order_id: this._clientOrderId,
        package_id: pkg.id,
        version_id: pkg.versionId,
        pay_type: this.data.payType,
        agree_terms: true
      })
      .then((order) => {
        if (this.data.payType === 'points') {
          this.setData({ submitting: false });
          wx.redirectTo({ url: '/pages/order-result/order-result?id=' + order.id });
          return;
        }
        return api
          .prepayOrder(order.id)
          .then((params) => {
            return new Promise((resolve, reject) => {
              wx.requestPayment({
                timeStamp: params.timeStamp,
                nonceStr: params.nonceStr,
                package: params.package,
                signType: params.signType,
                paySign: params.paySign,
                success: resolve,
                fail: reject
              });
            });
          })
          .then(() => {
            this.setData({ submitting: false });
            wx.redirectTo({ url: '/pages/order-result/order-result?id=' + order.id });
          })
          .catch(() => {
            // 取消或调起失败：不显示成功，进入结果页展示准确待支付状态
            this.setData({ submitting: false });
            wx.redirectTo({ url: '/pages/order-result/order-result?id=' + order.id });
          });
      })
      .catch((err) => {
        this.setData({ submitting: false });
        const detail = err && err.detail ? String(err.detail) : '';
        if (detail.indexOf('重新确认') > -1 || detail.indexOf('已更新') > -1) {
          // 版本已变化：作废旧幂等单号，重新拉取详情要求用户再次确认
          this._clientOrderId = '';
          wx.showModal({
            title: '服务包已更新',
            content: '价格或条款已调整，请返回重新查看并确认新版内容。',
            showCancel: false,
            success: () => this.fetch()
          });
        }
      });
  }
}, 'order-confirm'));
