// 订单详情：订单事实、支付/退款进展；待支付可继续支付或取消（关闭）
const api = require('../../utils/api');
const format = require('../../utils/format');

const STATUS_LABEL = {
  pending_payment: '待支付', paid: '已支付', closed: '已关闭',
  refund_pending: '退款处理中', refund_failed: '退款失败', refunded: '已退款'
};
const REFUND_LABEL = { pending: '待处理', processing: '微信处理中', success: '已退款', failed: '失败' };

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    order: null,
    paying: false,
    cancelling: false
  },

  onLoad(options) {
    this.id = options.id;
    this.fetch();
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    api
      .getOrder(this.id)
      .then((d) => {
        this.setData({
          loading: false,
          order: {
            id: d.id,
            orderNo: d.order_no,
            status: d.status,
            statusLabel: STATUS_LABEL[d.status] || d.status,
            packageName: d.package.name,
            versionNo: d.package.version_no,
            payType: d.pay_type,
            payTypeLabel: d.pay_type === 'points' ? '积分兑换' : '微信支付',
            amount: d.pay_type === 'points' ? (d.points_cost + ' 积分') : (format.price(d.total_cents) + ' 元'),
            entitlementId: d.entitlement_id,
            created: format.full(d.created_at),
            paidAt: d.paid_at ? format.full(d.paid_at) : '',
            expiresAt: d.expires_at ? format.full(d.expires_at) : '',
            refunds: (d.refunds || []).map((r) => ({
              id: r.id,
              no: r.refund_no,
              channel: r.channel === 'wxpay' ? '微信退款' : '人工处理',
              status: REFUND_LABEL[r.status] || r.status,
              amount: format.price(r.amount_cents),
              reason: r.reason,
              time: format.full(r.created_at)
            }))
          }
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '订单加载失败' });
      });
  },

  retry() {
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
      .then(() => this.fetch());
  },

  cancelOrder() {
    const order = this.data.order;
    if (!order || this.data.cancelling || order.status !== 'pending_payment') return;
    wx.showModal({
      title: '取消订单',
      content: '确认取消该待支付订单？取消后需重新下单。',
      confirmText: '取消订单',
      cancelText: '再想想',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ cancelling: true });
        api
          .closeOrder(order.id)
          .then(() => {
            this.setData({ cancelling: false });
            wx.showToast({ title: '订单已取消', icon: 'none' });
            this.fetch();
          })
          .catch(() => this.setData({ cancelling: false }));
      }
    });
  },

  goBenefits() {
    wx.navigateTo({ url: '/pages/benefits/benefits' });
  }
}, 'order-detail'));
