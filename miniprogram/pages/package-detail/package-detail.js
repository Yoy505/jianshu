// 服务包详情：版本价格、权益项目、有效期与售后规则；购买入口按服务端 purchasable 禁用
const api = require('../../utils/api');
const format = require('../../utils/format');

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    pkg: null,
    buying: false
  },

  onLoad(options) {
    this.id = options.id;
    this.fetch();
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    api
      .getPackage(this.id)
      .then((d) => {
        this.setData({
          loading: false,
          pkg: {
            id: d.id,
            name: d.name,
            subtitle: d.subtitle || '',
            coverUrl: d.cover_url || '',
            price: format.price(d.price_cents),
            pointsCost: d.points_cost,
            validityDays: d.validity_days,
            versionNo: d.version_no,
            termsVersion: d.terms_version,
            benefits: (d.benefits || []).map((b) => ({
              name: b.name,
              desc: b.desc || ''
            })),
            serviceDesc: d.service_desc || '',
            terms: d.terms || '',
            canWxpay: !!(d.purchasable && d.purchasable.wxpay),
            canPoints: !!(d.purchasable && d.purchasable.points),
            blockReasons: (d.purchasable && d.purchasable.reasons) || []
          }
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
      });
  },

  retry() {
    this.fetch();
  },

  goBuy() {
    const pkg = this.data.pkg;
    if (!pkg || (!pkg.canWxpay && !pkg.canPoints)) {
      wx.showToast({ title: '该服务包暂不可购买', icon: 'none' });
      return;
    }
    wx.navigateTo({ url: '/pages/order-confirm/order-confirm?id=' + pkg.id });
  }
}, 'package-detail'));
