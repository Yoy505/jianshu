// 商城：分类 tabs + 商品卡（coming 状态「即将开放」）
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

const CATEGORIES = [
  { v: '', n: '全部' },
  { v: 'food', n: '药食同源' },
  { v: 'fsmp', n: '特医食品' },
  { v: 'package', n: '健康服务包' },
  { v: 'device', n: '器械耗材' }
];

function toVM(p) {
  const cat = config.PRODUCT_CATEGORY[p.category] || { label: p.category, char: '品' };
  return {
    id: p.id,
    name: p.name,
    desc: p.desc || '',
    price: format.price(p.price),
    catLabel: cat.label,
    catChar: cat.char,
    coming: p.status === 'coming'
  };
}

Page({
  data: {
    categories: CATEGORIES,
    active: '',
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    loading: false,
    finished: false,
    error: ''
  },

  onLoad() {
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
      .listProducts({ category: this.data.active, page: this.data.page, page_size: this.data.pageSize })
      .then((res) => {
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
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
        wx.stopPullDownRefresh();
      });
  },

  onPullDownRefresh() {
    this.reload();
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading || !this.data.list.length) return;
    this.setData({ page: this.data.page + 1 });
    this.fetch();
  },

  onTab(e) {
    const v = e.currentTarget.dataset.v || '';
    if (v === this.data.active) return;
    this.setData({ active: v });
    this.reload();
  }
});
