// 药品详情：注册资料、独立的用药内容、来源信息、留言板和收藏
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');

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

function checkedDate(value) {
  if (!value) return '';
  const date = String(value);
  return /^\d{4}-\d{2}-\d{2}/.test(date) ? date.slice(0, 10) : date;
}

Page({
  data: {
    loading: true,
    error: '',
    drug: null,
    boardInput: '',
    boardSubmitting: false,
    drugNote: config.COMPLIANCE.drug
  },

  onLoad(options) {
    this.id = options.id;
    this.fetch();
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    api
      .getDrug(this.id)
      .then((d) => {
        const publication = publicationVM(d.publication_status);
        const indication = d.indication || '';
        const usageNote = d.usage_note || '';
        const contraindication = d.contraindication || '';
        const science = d.science || '';
        this.setData({
          loading: false,
          drug: {
            id: d.id,
            commonName: d.common_name,
            brandName: d.brand_name || '',
            form: d.form || '',
            specification: d.specification || '',
            manufacturer: d.manufacturer || '',
            approvalNo: d.approval_no || '',
            licenseHolder: d.license_holder || '',
            sourceName: d.source_name || '',
            sourceUrl: /^https:\/\/\S+$/.test(d.source_url || '') ? d.source_url : '',
            sourceCheckedAt: checkedDate(d.source_checked_at),
            publicationLabel: publication.label,
            publicationClass: publication.cls,
            tags: tagsVM(d.tags),
            indication,
            usageNote,
            contraindication,
            science,
            hasMedicalContent: !!(indication || usageNote || contraindication || science),
            videoUrl: d.video_url || '',
            favorited: !!d.favorited,
            feedbackMode: d.feedback_mode === 'demo' ? 'demo' : 'closed',
            board: (d.board || []).map((b) => ({
              id: b.id,
              userName: b.user_name || '匿名用户',
              content: b.content,
              time: format.friendly(b.created_at)
            }))
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

  copySourceUrl() {
    const sourceUrl = this.data.drug && this.data.drug.sourceUrl;
    if (!sourceUrl) return;
    wx.setClipboardData({ data: sourceUrl });
  },

  toggleFav() {
    if (!getApp().ensureLogin()) return;
    const drug = this.data.drug;
    api
      .toggleFavorite(drug.id)
      .then((res) => {
        const fav = !!(res && res.favorited);
        this.setData({ 'drug.favorited': fav });
        wx.showToast({ title: fav ? '已加入收藏' : '已取消收藏', icon: 'none' });
      })
      .catch(() => {});
  },

  onBoardInput(e) {
    this.setData({ boardInput: e.detail.value });
  },

  submitBoard() {
    if (this.data.boardSubmitting) return;
    if (!this.data.drug || this.data.drug.feedbackMode !== 'demo') {
      wx.showToast({ title: '留言功能暂未开放', icon: 'none' });
      return;
    }
    if (!getApp().ensureLogin()) return;
    const content = (this.data.boardInput || '').trim();
    if (content.length < 20 || content.length > 500) {
      wx.showToast({ title: '留言需 20-500 字', icon: 'none' });
      return;
    }
    this.setData({ boardSubmitting: true });
    api
      .drugFeedback(this.id, content)
      .then(() => {
        this.setData({ boardSubmitting: false, boardInput: '' });
        wx.showToast({ title: '演示留言已提交', icon: 'none' });
        this.fetch();
      })
      .catch(() => {
        this.setData({ boardSubmitting: false });
      });
  }
});
