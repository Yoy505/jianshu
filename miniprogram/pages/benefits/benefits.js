// 我的权益：本人购买与单位分配权益；待领取可认领/拒绝，已生效展示服务准备入口
const api = require('../../utils/api');
const format = require('../../utils/format');

const TABS = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待领取' },
  { key: 'active', label: '已生效' },
  { key: 'rejected', label: '已拒绝' },
  { key: 'void', label: '已作废' }
];
const STATUS_LABEL = {
  pending: '待领取', active: '已生效', rejected: '已拒绝', void: '已作废'
};
const SOURCE_LABEL = { order: '本人购买', batch: '单位分配' };

Page(require('../../utils/hifi-page')({
  data: {
    tabs: TABS,
    active: '',
    balance: 0,
    loading: true,
    error: '',
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    finished: false,
    busyId: 0,
    expandedId: 0
  },

  onShow() {
    this.reload();
    api.getPoints().then((p) => this.setData({ balance: p.balance || 0 })).catch(() => {});
  },

  onPullDownRefresh() {
    this.reload(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    this.loadMore();
  },

  onTab(e) {
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
      .listEntitlements({ status: data.active, page: data.page, page_size: data.pageSize })
      .then((res) => {
        if (gen !== this._gen) return;
        const items = (res.items || []).map((e2) => ({
          id: e2.id,
          status: e2.status,
          statusLabel: STATUS_LABEL[e2.status] || e2.status,
          sourceLabel: SOURCE_LABEL[e2.source_type] || e2.source_type,
          packageName: e2.package.name,
          versionNo: e2.package.version_no,
          benefits: e2.benefits || [],
          validityDays: e2.validity_days,
          validUntil: e2.valid_until ? format.full(e2.valid_until) : '',
          orgName: e2.org_name || '',
          created: format.full(e2.created_at)
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
        this.setData({ loading: false, error: err.detail || '权益加载失败' });
      })
      .then(() => { if (done) done(); });
  },

  toggle(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ expandedId: this.data.expandedId === id ? 0 : id });
  },

  claim(e) {
    const id = e.currentTarget.dataset.id;
    if (this.data.busyId) return;
    this.setData({ busyId: id });
    api
      .claimEntitlement(id)
      .then(() => {
        this.setData({ busyId: 0 });
        wx.showToast({ title: '已领取，权益生效', icon: 'none' });
        this.reload();
      })
      .catch(() => this.setData({ busyId: 0 }));
  },

  reject(e) {
    const id = e.currentTarget.dataset.id;
    if (this.data.busyId) return;
    wx.showModal({
      title: '拒绝该权益',
      content: '拒绝后如需再次领取，仍可在此页面操作。',
      confirmText: '拒绝领取',
      cancelText: '暂不',
      success: (res) => {
        if (!res.confirm) return;
        this.setData({ busyId: id });
        api
          .rejectEntitlement(id)
          .then(() => {
            this.setData({ busyId: 0 });
            wx.showToast({ title: '已拒绝', icon: 'none' });
            this.reload();
          })
          .catch(() => this.setData({ busyId: 0 }));
      }
    });
  }
}, 'benefits'));
