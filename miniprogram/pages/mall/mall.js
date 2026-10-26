// 商城：真实服务包（/mp/packages）+ 其他品类商品卡（coming 状态「即将开放」）
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

function pkgVM(p) {
  return {
    id: p.id,
    name: p.name,
    subtitle: p.subtitle || '',
    price: format.price(p.price_cents),
    pointsCost: p.points_cost || 0,
    validityDays: p.validity_days,
    benefitCount: p.benefit_count || 0,
    purchasable: !!(p.purchasable && (p.purchasable.wxpay || p.purchasable.points)),
    blockReasons: (p.purchasable && p.purchasable.reasons && p.purchasable.reasons.length) ? p.purchasable.reasons[0] : ''
  };
}

Page(require('../../utils/hifi-page')({
  data: {
    categories: CATEGORIES,
    active: '',
    packages: [],
    packagesLoading: true,
    packagesError: '',
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
    this.fetchPackages();
  },

  fetchPackages() {
    this.setData({ packagesLoading: true, packagesError: '' });
    api
      .listPackages({ page: 1, page_size: 20 })
      .then((res) => {
        this.setData({ packagesLoading: false, packages: (res.items || []).map(pkgVM) });
      })
      .catch(() =>
        this.setData({
          packagesLoading: false,
          packages: [],
          packagesError: '服务包目录暂时无法加载'
        })
      );
  },

  retryPackages() {
    this.fetchPackages();
  },

  goPackage(e) {
    const id = e.currentTarget.dataset.id;
    const pkg = this.data.packages.find((p) => p.id === id);
    if (pkg && !pkg.purchasable && pkg.blockReasons) {
      wx.showToast({ title: '该服务包暂不可购买：' + pkg.blockReasons, icon: 'none' });
    }
    // 未登录由 access.navigate 弹登录；详情仍可浏览协议文案
    require('../../utils/access').navigate('/pages/package-detail/package-detail?id=' + id);
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
    this.fetchPackages();
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading || !this.data.list.length) return;
    if (!getApp().ensureLogin('/pages/mall/mall')) return;
    this.setData({ page: this.data.page + 1 });
    this.fetch();
  },

  onTab(e) {
    if (!getApp().ensureLogin('/pages/mall/mall')) return;
    const v = e.currentTarget.dataset.v || '';
    if (v === this.data.active) return;
    this.setData({ active: v });
    this.reload();
  }
}, 'mall'));
