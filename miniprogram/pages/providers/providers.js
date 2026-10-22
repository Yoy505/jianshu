// 人员资料列表：资质尚未核验，不从列表直接创建咨询会话。
const api = require('../../utils/api');
const format = require('../../utils/format');

function toVM(p) {
  return {
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
    skills: (p.skills || []).slice(0, 3)
  };
}

Page(require('../../utils/hifi-page')({
  data: {
    list: [],
    page: 1,
    pageSize: 10,
    total: 0,
    loading: false,
    finished: false,
    error: ''
  },

  onLoad(options) {
    this.serviceId = options.service_id || '';
    this.serviceName = options.service_name ? decodeURIComponent(options.service_name) : '';
    this.scenario = options.scenario || '';
    if (this.serviceName) {
      wx.setNavigationBarTitle({
        title: '人员资料'
      });
    }
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
      .providers({
        service_id: this.serviceId,
        scenario: this.scenario,
        page: this.data.page,
        page_size: this.data.pageSize
      })
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

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    require('../../utils/access').navigate({
      url:
        '/pages/provider-detail/provider-detail?id=' +
        id +
        '&service_id=' +
        this.serviceId +
        '&service_name=' +
        encodeURIComponent(this.serviceName) +
        '&scenario=' +
        encodeURIComponent(this.scenario || '')
    });
  }
}, 'providers'));
