// 授权管理：购买等授权记录查看与撤回（撤回不影响既有订单事实，仅记录意愿）
const api = require('../../utils/api');
const format = require('../../utils/format');

const PURPOSE_LABEL = {
  package_purchase: '服务包购买协议'
};
const SOURCE_LABEL = { order_confirm: '下单确认' };

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    list: [],
    page: 1,
    pageSize: 20,
    total: 0,
    finished: false,
    expandedId: 0
  },

  onShow() {
    this.reload();
  },

  onPullDownRefresh() {
    this.reload(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (this.data.loading || this.data.finished) return;
    this.fetch(this._gen);
  },

  reload(done) {
    this._gen = (this._gen || 0) + 1;
    this.setData({ page: 1, loading: true, error: '' });
    this.fetch(this._gen, done);
  },

  fetch(gen, done) {
    const data = this.data;
    api
      .listConsents({ page: data.page, page_size: data.pageSize })
      .then((res) => {
        if (gen !== this._gen) return;
        const items = (res.items || []).map((c) => ({
          id: c.id,
          purposeLabel: PURPOSE_LABEL[c.purpose] || c.purpose,
          version: c.version,
          sourceLabel: SOURCE_LABEL[c.source] || c.source,
          text: c.text,
          grantedAt: format.full(c.granted_at),
          revokedAt: c.revoked_at ? format.full(c.revoked_at) : '',
          revoked: !!c.revoked_at
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
        this.setData({ loading: false, error: err.detail || '授权记录加载失败' });
      })
      .then(() => { if (done) done(); });
  },

  toggle(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ expandedId: this.data.expandedId === id ? 0 : id });
  },

  revoke(e) {
    const id = e.currentTarget.dataset.id;
    wx.showModal({
      title: '撤回授权',
      content: '撤回仅记录您的意愿并供平台后续处理，不改变已完成订单和权益的状态。',
      confirmText: '确认撤回',
      cancelText: '暂不',
      success: (res) => {
        if (!res.confirm) return;
        api
          .revokeConsent(id)
          .then(() => {
            wx.showToast({ title: '已撤回', icon: 'none' });
            this.reload();
          })
          .catch(() => {});
      }
    });
  }
}, 'consents'));
