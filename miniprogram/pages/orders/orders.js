// 我的订单：状态筛选 + 分页；点击进入订单详情
const api = require('../../utils/api');
const format = require('../../utils/format');

const STATUS = [
  { key: '', label: '全部' },
  { key: 'pending_payment', label: '待支付' },
  { key: 'paid', label: '已支付' },
  { key: 'refund_pending', label: '退款中' },
  { key: 'refunded', label: '已退款' },
  { key: 'closed', label: '已关闭' }
];
const STATUS_LABEL = {
  pending_payment: '待支付', paid: '已支付', closed: '已关闭',
  refund_pending: '退款处理中', refund_failed: '退款失败', refunded: '已退款'
};

Page(require('../../utils/hifi-page')({
  data: {
    tabs: STATUS,
    active: '',
    loading: true,
    error: '',
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    finished: false
  },

  onLoad(options) {
    if (options.status) this.setData({ active: options.status });
    this.reload();
  },

  onPullDownRefresh() {
    this.reload(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    this.loadMore();
  },

  onTab(e) {
    if (!getApp().ensureLogin('/pages/orders/orders')) return;
    this.setData({ active: e.currentTarget.dataset.v });
    this.reload();
  },

  reload(done) {
    this._gen = (this._gen || 0) + 1;
    this.setData({ page: 1, loading: true, error: '' });
    this.fetch(this._gen, done);
  },

  loadMore() {
    if (this.data.loading || this.data.finished) return;
    this.fetch(this._gen);
  },

  fetch(gen, done) {
    const data = this.data;
    api
      .listOrders({ status: data.active, page: data.page, page_size: data.pageSize })
      .then((res) => {
        if (gen !== this._gen) return;
        const items = (res.items || []).map((o) => ({
          id: o.id,
          orderNo: o.order_no,
          packageName: o.package.name,
          status: o.status,
          statusLabel: STATUS_LABEL[o.status] || o.status,
          amount: o.pay_type === 'points' ? (o.points_cost + ' 积分') : (format.price(o.total_cents) + ' 元'),
          payTypeLabel: o.pay_type === 'points' ? '积分兑换' : '微信支付',
          time: format.full(o.created_at)
        }));
        this.setData({
          loading: false,
          list: data.page === 1 ? items : data.list.concat(items),
          total: res.total,
          finished: data.page * data.pageSize >= res.total
        });
      })
      .catch((err) => {
        if (gen !== this._gen) return;
        this.setData({ loading: false, error: err.detail || '订单加载失败' });
      })
      .then(() => { if (done) done(); });
  },

  goDetail(e) {
    wx.navigateTo({ url: '/pages/order-detail/order-detail?id=' + e.currentTarget.dataset.id });
  }
}, 'orders'));
