// 人员详情：资料待核验；仅开发/测试环境展示演示时段和入口。
const api = require('../../utils/api');
const format = require('../../utils/format');

function starsOf(n) {
  const r = Math.round(Number(n) || 0);
  const arr = [];
  for (let i = 1; i <= 5; i++) arr.push(i <= r);
  return arr;
}

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    provider: null,
    slots: [],
    reviews: []
  },

  onLoad(options) {
    this.id = options.id;
    this.serviceId = options.service_id || '';
    this.scenario = options.scenario ? decodeURIComponent(options.scenario) : '';
    this.serviceName = options.service_name ? decodeURIComponent(options.service_name) : '';
    this.creating = false;
    this.fetch();
    // 从场景维度进入时，兜底取该场景第一个服务作为咨询/预约载体
    if (!this.serviceId && this.scenario) {
      api
        .getServices({ scenario: this.scenario, page: 1, page_size: 1 })
        .then((res) => {
          const first = (res.items || [])[0];
          if (first) {
            this.serviceId = first.id;
            this.serviceName = first.name;
          }
        })
        .catch(() => {});
    }
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    api
      .providerDetail(this.id)
      .then((p) => {
        this.setData({
          loading: false,
          provider: {
            id: p.id,
            name: p.name,
            initial: p.name ? p.name.slice(0, 1) : '医',
            avatar: p.avatar || '',
            online: !!p.online,
            title: p.title || '',
            dept: p.dept || '',
            org: p.org || '',
            years: p.years || 0,
            rating: format.rating(p.rating),
            serviceCount: p.service_count || 0,
            intro: p.intro || '该专业人员暂无简介。',
            skills: p.skills || []
          },
          slots: (p.slots || []).map((s) => ({
            date: s.date,
            label: format.mdOf(s.date),
            week: format.weekOf(s.date),
            periods: s.periods || []
          })),
          reviews: (p.reviews || []).map((r) => ({
            id: r.id,
            userName: r.user_name || '匿名用户',
            stars: starsOf(r.rating),
            content: r.content || '',
            time: format.friendly(r.created_at)
          }))
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
      });
  },

  retry() {
    this.fetch();
  },

  // 在线咨询（咚咚）
  goChat() {
    if (!getApp().ensureLogin()) return;
    if (!this.serviceId) {
      wx.showToast({ title: '请从服务详情进入后再发起咨询', icon: 'none' });
      return;
    }
    if (this.creating) return;
    this.creating = true;
    wx.showLoading({ title: '正在接入', mask: true });
    const content = this.serviceName
      ? '您好，我想咨询「' + this.serviceName + '」相关服务。'
      : '您好，我想咨询健康相关问题。';
    api
      .createConsult({
        service_id: Number(this.serviceId),
        provider_id: Number(this.id),
        content: content
      })
      .then((res) => {
        wx.hideLoading();
        this.creating = false;
        wx.redirectTo({ url: '/pages/chat/chat?id=' + res.id + '&service_name=' + encodeURIComponent(this.serviceName || '') + '&provider_name=' + encodeURIComponent((this.data.provider || {}).name || '') });
      })
      .catch(() => {
        wx.hideLoading();
        this.creating = false;
      });
  },

  // 预约服务
  goBook() {
    if (!this.data.slots.length) {
      wx.showToast({ title: '真实排班尚未接入', icon: 'none' });
      return;
    }
    if (!getApp().ensureLogin()) return;
    if (!this.serviceId) {
      wx.showToast({ title: '请从服务详情进入后再预约', icon: 'none' });
      return;
    }
    require('../../utils/access').navigate({
      url: '/pages/book/book?service_id=' + this.serviceId + '&provider_id=' + this.id
    });
  }
}, 'provider-detail'));
