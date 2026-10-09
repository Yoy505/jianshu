// 我的预约：状态 tab + 卡片操作（取消 / 去评价）
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

const TABS = [
  { v: '', n: '全部' },
  { v: 'pending', n: '待确认' },
  { v: 'confirmed', n: '已确认' },
  { v: 'completed', n: '已完成' },
  { v: 'cancelled', n: '已取消' }
];

function toVM(a) {
  const s = a.service || {};
  const p = a.provider || {};
  const st = config.APPOINTMENT_STATUS[a.status] || { label: a.status, cls: 'tag-gray' };
  return {
    id: a.id,
    serviceName: a.service_name || s.name || '健康服务',
    providerName: a.provider_name || p.name || '专业人员',
    date: a.date || '',
    period: a.period || '',
    address: a.address || '',
    statusLabel: st.label,
    statusCls: st.cls,
    time: format.full(a.created_at),
    canCancel: a.status === 'pending' || a.status === 'confirmed',
    canReview: a.status === 'completed' && !(a.reviewed || a.has_review || a.reviewed_at)
  };
}

Page({
  data: {
    tabs: TABS,
    active: '',
    needLogin: false,
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    loading: false,
    finished: false,
    error: ''
  },

  onShow() {
    if (!getApp().isLoggedIn()) {
      this.setData({ needLogin: true, list: [] });
      return;
    }
    this.setData({ needLogin: false });
    this.reload();
  },

  reload() {
    this._gen = (this._gen || 0) + 1; // 请求代际：旧响应丢弃
    this.setData({ list: [], page: 1, total: 0, finished: false, error: '' });
    this.fetch();
  },

  fetch() {
    const gen = (this._gen = this._gen || 0);
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    api
      .listAppointments({ status: this.data.active, page: this.data.page, page_size: this.data.pageSize })
      .then((res) => {
        if (gen !== this._gen) return;
        const items = (res.items || []).map(toVM);
        const total = res.total || 0;
        const list = this.data.page === 1 ? items : this.data.list.concat(items);
        this.setData({
          list,
          total,
          loading: false,
          finished: list.length >= total || items.length === 0
        });
        wx.stopPullDownRefresh();
      })
      .catch((err) => {
        if (gen !== this._gen) return;
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
        wx.stopPullDownRefresh();
      });
  },

  onPullDownRefresh() {
    if (this.data.needLogin) {
      wx.stopPullDownRefresh();
      return;
    }
    this.reload();
  },

  onReachBottom() {
    if (this.data.needLogin || this.data.finished || this.data.loading || !this.data.list.length) return;
    this.setData({ page: this.data.page + 1 });
    this.fetch();
  },

  onTab(e) {
    const v = e.currentTarget.dataset.v || '';
    if (v === this.data.active) return;
    this.setData({ active: v });
    this.reload();
  },

  goLogin() {
    wx.switchTab({ url: '/pages/my/my' });
  },

  goSteward() {
    wx.switchTab({ url: '/pages/steward/steward' });
  },

  cancel(e) {
    const id = e.currentTarget.dataset.id;
    wx.showModal({
      title: '取消预约',
      content: '确定取消该预约吗？取消后需重新预约。',
      confirmText: '取消预约',
      cancelText: '再想想',
      confirmColor: '#B42318',
      success: (r) => {
        if (!r.confirm) return;
        api
          .cancelAppointment(id)
          .then(() => {
            wx.showToast({ title: '已取消预约', icon: 'none' });
            this.reload();
          })
          .catch(() => {});
      }
    });
  },

  review(e) {
    wx.navigateTo({ url: '/pages/review/review?id=' + e.currentTarget.dataset.id });
  }
});
