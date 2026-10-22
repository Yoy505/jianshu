// 支付结果：以服务端订单状态为准轮询（客户端支付回调不作为成功依据）
const api = require('../../utils/api');
const format = require('../../utils/format');

const STATUS_TEXT = {
  pending_payment: { label: '待支付', cls: 'warn', desc: '订单已创建，支付完成后自动入账' },
  paid: { label: '支付成功', cls: 'ok', desc: '权益已发放，可在「我的权益」中查看' },
  closed: { label: '订单已关闭', cls: 'muted', desc: '超时未支付或已取消，可重新下单' },
  refund_pending: { label: '退款处理中', cls: 'warn', desc: '退款申请已受理，请关注处理结果' },
  refund_failed: { label: '退款失败', cls: 'err', desc: '请留意客服联系或在订单中查看进展' },
  refunded: { label: '已退款', cls: 'muted', desc: '退款已完成，相关权益已作废' }
};

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    order: null,
    statusText: null,
    polling: false,
    paying: false
  },

  onLoad(options) {
    this.id = options.id;
    this._pollCount = 0;
    this.fetch();
  },

  onUnload() {
    this._stopped = true;
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    api
      .getOrder(this.id)
      .then((d) => this.applyOrder(d))
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '订单加载失败' });
      });
  },

  applyOrder(d) {
    const text = STATUS_TEXT[d.status] || { label: d.status, cls: 'muted', desc: '' };
    this.setData({
      loading: false,
      order: {
        id: d.id,
        orderNo: d.order_no,
        status: d.status,
        payType: d.pay_type,
        payTypeLabel: d.pay_type === 'points' ? '积分兑换' : '微信支付',
        packageName: d.package.name,
        amount: d.pay_type === 'points' ? (d.points_cost + ' 积分') : (format.price(d.total_cents) + ' 元'),
        entitlementId: d.entitlement_id,
        created: format.full(d.created_at),
        refunds: (d.refunds || []).map((r) => ({
          no: r.refund_no,
          status: r.status,
          amount: format.price(r.amount_cents),
          time: format.full(r.created_at)
        }))
      },
      statusText: text
    });
    // 微信支付单刚拉起后短暂轮询，等待服务端回调入账
    if (d.status === 'pending_payment' && d.pay_type === 'wxpay' && this._pollCount < 8 && !this._stopped) {
      this._pollCount += 1;
      this.setData({ polling: true });
      setTimeout(() => {
        if (this._stopped) return;
        api
          .getOrder(this.id)
          .then((next) => this.applyOrder(next))
          .catch(() => this.setData({ polling: false }));
      }, 1500);
    } else {
      this.setData({ polling: false });
    }
  },

  retry() {
    this._pollCount = 0;
    this.fetch();
  },

  payAgain() {
    const order = this.data.order;
    if (!order || this.data.paying || order.status !== 'pending_payment' || order.payType !== 'wxpay') return;
    this.setData({ paying: true });
    api
      .prepayOrder(order.id)
      .then((params) => new Promise((resolve, reject) => {
        wx.requestPayment({
          timeStamp: params.timeStamp,
          nonceStr: params.nonceStr,
          package: params.package,
          signType: params.signType,
          paySign: params.paySign,
          success: resolve,
          fail: reject
        });
      }))
      .then(() => this.setData({ paying: false }))
      .catch(() => this.setData({ paying: false }))
      .then(() => {
        this._pollCount = 0;
        this.fetch();
      });
  },

  goBenefits() {
    wx.navigateTo({ url: '/pages/benefits/benefits' });
  },

  goOrders() {
    wx.navigateTo({ url: '/pages/orders/orders' });
  }
}, 'order-result'));
