// 我的积分：余额与完整流水（余额加载失败显示错误态，不假装为 0）
const api = require('../../utils/api');
const format = require('../../utils/format');

const BIZ_LABEL = {
  admin_adjust: '平台调整',
  redeem: '兑换扣减',
  refund_revert: '退款返还'
};

Page(require('../../utils/hifi-page')({
  data: {
    balanceLoading: true,
    balanceError: '',
    balance: 0,
    loading: true,
    error: '',
    list: [],
    page: 1,
    pageSize: 20,
    total: 0,
    finished: false
  },

  onShow() {
    this.reload();
  },

  onPullDownRefresh() {
    this.reload(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    this.loadMore();
  },

  reload(done) {
    this._gen = (this._gen || 0) + 1;
    this.setData({ page: 1 });
    api
      .getPoints()
      .then((p) => this.setData({ balanceLoading: false, balanceError: '', balance: p.balance || 0 }))
      .catch(() => this.setData({ balanceLoading: false, balanceError: '积分余额暂时无法确认' }));
    this.fetch(this._gen, done);
  },

  retryBalance() {
    this.setData({ balanceLoading: true, balanceError: '' });
    api
      .getPoints()
      .then((p) => this.setData({ balanceLoading: false, balance: p.balance || 0 }))
      .catch(() => this.setData({ balanceLoading: false, balanceError: '积分余额暂时无法确认' }));
  },

  loadMore() {
    if (this.data.loading || this.data.finished) return;
    this.fetch(this._gen);
  },

  fetch(gen, done) {
    const data = this.data;
    this.setData({ loading: true, error: '' });
    api
      .listPointsEntries({ page: data.page, page_size: data.pageSize })
      .then((res) => {
        if (gen !== this._gen) return;
        const items = (res.items || []).map((e) => ({
          id: e.id,
          delta: e.delta,
          deltaLabel: e.delta > 0 ? '+' + e.delta : String(e.delta),
          plus: e.delta > 0,
          balanceAfter: e.balance_after,
          bizLabel: BIZ_LABEL[e.biz_type] || e.biz_type,
          orderId: e.order_id,
          note: e.note || '',
          time: format.full(e.created_at)
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
        this.setData({ loading: false, error: err.detail || '流水加载失败' });
      })
      .then(() => { if (done) done(); });
  }
}, 'points'));
