// 药品收藏列表
const api = require('../../utils/api');
const config = require('../../utils/config');

function tagsVM(tags) {
  return (tags || [])
    .map((t) => {
      const m = config.DRUG_TAGS[t];
      return m ? { key: t, label: m.label, cls: m.cls } : null;
    })
    .filter((x) => !!x);
}

Page({
  data: {
    needLogin: false,
    list: [],
    page: 1,
    pageSize: 15,
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
    this.setData({ list: [], page: 1, total: 0, finished: false, error: '' });
    this.fetch();
  },

  fetch() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    api
      .listFavorites({ page: this.data.page, page_size: this.data.pageSize })
      .then((res) => {
        const items = (res.items || []).map((d) => ({
          id: d.id,
          commonName: d.common_name,
          form: d.form || '',
          manufacturer: d.manufacturer || '',
          tags: tagsVM(d.tags)
        }));
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

  goLogin() {
    wx.switchTab({ url: '/pages/my/my' });
  },

  goHall() {
    wx.navigateTo({ url: '/pages/drug-hall/drug-hall' });
  },

  goDetail(e) {
    wx.navigateTo({ url: '/pages/drug-detail/drug-detail?id=' + e.currentTarget.dataset.id });
  }
});
