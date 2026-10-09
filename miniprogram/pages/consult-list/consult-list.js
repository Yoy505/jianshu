// 咨询会话列表：最后一条消息预览 + 时间
const api = require('../../utils/api');
const format = require('../../utils/format');

function toVM(c) {
  const p = c.provider || {};
  let lastMsg = '暂无消息';
  if (typeof c.last_message === 'string') {
    lastMsg = c.last_message;
  } else if (c.last_message && c.last_message.content) {
    lastMsg = c.last_message.content;
  }
  return {
    id: c.id,
    providerName: p.name || '专业人员',
    providerTitle: p.title || '',
    online: !!p.online,
    serviceName: c.service_name || (c.service && c.service.name) || '',
    statusLabel: c.status === 'closed' ? '已结束' : '进行中',
    statusCls: c.status === 'closed' ? 'tag-gray' : 'tag-success',
    lastMsg: lastMsg,
    time: format.friendly(c.last_message_at || c.created_at)
  };
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
    this._gen = (this._gen || 0) + 1; // 请求代际：旧响应丢弃
    this.setData({ list: [], page: 1, total: 0, finished: false, error: '' });
    this.fetch();
  },

  fetch() {
    const gen = (this._gen = this._gen || 0);
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    api
      .listConsults({ page: this.data.page, page_size: this.data.pageSize })
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

  goLogin() {
    wx.switchTab({ url: '/pages/my/my' });
  },

  goSteward() {
    wx.switchTab({ url: '/pages/steward/steward' });
  },

  goChat(e) {
    wx.navigateTo({ url: '/pages/chat/chat?id=' + e.currentTarget.dataset.id });
  }
});
