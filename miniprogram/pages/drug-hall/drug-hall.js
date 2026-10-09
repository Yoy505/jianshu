// 药品数字展厅：搜索 + 标签筛选 + 字母索引 + 结果列表
const api = require('../../utils/api');
const config = require('../../utils/config');
const OFFICIAL_QUERY_URL = 'https://www.nmpa.gov.cn/zwfwqjd/index.html';

const TAG_CHIPS = [
  { v: '', n: '全部' },
  { v: 'otc', n: 'OTC' },
  { v: 'rx', n: '处方药' },
  { v: 'essential', n: '基药' },
  { v: 'insurance', n: '医保' }
];

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');

function tagsVM(tags) {
  return (tags || [])
    .map((t) => {
      const m = config.DRUG_TAGS[t];
      return m ? { key: t, label: m.label, cls: m.cls } : null;
    })
    .filter((x) => !!x);
}

function publicationVM(status) {
  if (status === 'demo') return { label: '演示资料 · 非正式发布', cls: 'demo' };
  if (status === 'published') return { label: '来源公开资料', cls: 'published' };
  return { label: '资料状态待核对', cls: 'unknown' };
}

Page({
  data: {
    tagChips: TAG_CHIPS,
    letters: LETTERS,
    q: '',
    activeTag: '',
    activeLetter: '',
    list: [],
    page: 1,
    pageSize: 15,
    total: 0,
    hasDemo: false,
    loading: false,
    finished: false,
    error: ''
  },

  onLoad() {
    this.reload();
  },

  reload() {
    this._gen = (this._gen || 0) + 1; // 请求代际：旧响应丢弃
    this.setData({ list: [], page: 1, total: 0, hasDemo: false, loading: false, finished: false, error: '' });
    this.fetch();
  },

  fetch() {
    const gen = (this._gen = this._gen || 0);
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    api
      .listDrugs({
        q: this.data.q,
        tag: this.data.activeTag,
        letter: this.data.activeLetter,
        page: this.data.page,
        page_size: this.data.pageSize
      })
      .then((res) => {
        if (gen !== this._gen) return;
        const items = (res.items || []).map((d) => {
          const publication = publicationVM(d.publication_status);
          return {
            id: d.id,
            commonName: d.common_name,
            brandName: d.brand_name || '',
            formSpec: [d.form, d.specification].filter(Boolean).join(' · '),
            manufacturer: d.manufacturer || '',
            approvalNo: d.approval_no || '',
            sourceName: d.source_name || '',
            publicationLabel: publication.label,
            publicationClass: publication.cls,
            tags: tagsVM(d.tags)
          };
        });
        const total = res.total || 0;
        const list = this.data.page === 1 ? items : this.data.list.concat(items);
        this.setData({
          list,
          total,
          hasDemo: list.some((item) => item.publicationClass === 'demo'),
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
    this.reload();
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading || !this.data.list.length) return;
    this.setData({ page: this.data.page + 1 });
    this.fetch();
  },

  onInput(e) {
    this.setData({ q: e.detail.value });
  },

  onSearch() {
    this.reload();
  },

  onClear() {
    if (!this.data.q) return;
    this.setData({ q: '' });
    this.reload();
  },

  onTag(e) {
    const v = e.currentTarget.dataset.v || '';
    if (v === this.data.activeTag) return;
    this.setData({ activeTag: v });
    this.reload();
  },

  onLetter(e) {
    const v = e.currentTarget.dataset.letter;
    // 再次点击同一字母取消筛选
    const next = v === this.data.activeLetter ? '' : v;
    this.setData({ activeLetter: next });
    this.reload();
  },

  goDetail(e) {
    wx.navigateTo({ url: '/pages/drug-detail/drug-detail?id=' + e.currentTarget.dataset.id });
  },

  copyOfficialQuery() {
    wx.setClipboardData({ data: OFFICIAL_QUERY_URL });
  }
});
