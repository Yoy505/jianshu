// 评价页：星级 1-5 + 文字
const api = require('../../utils/api');

Page({
  data: {
    stars: [false, false, false, false, false],
    rating: 0,
    ratingText: '',
    content: '',
    submitting: false
  },

  onLoad(options) {
    this.id = options.id;
  },

  onUnload() {
    if (this.backTimer) {
      clearTimeout(this.backTimer);
      this.backTimer = null;
    }
  },

  setRating(e) {
    const n = Number(e.currentTarget.dataset.n);
    const stars = [];
    for (let i = 1; i <= 5; i++) stars.push(i <= n);
    const texts = ['', '较差', '一般', '满意', '很满意', '非常满意'];
    this.setData({ rating: n, stars: stars, ratingText: texts[n] || '' });
  },

  onContent(e) {
    this.setData({ content: e.detail.value });
  },

  submit() {
    if (this.data.submitting) return;
    if (!this.data.rating) {
      wx.showToast({ title: '请先选择星级评分', icon: 'none' });
      return;
    }
    const payload = { rating: this.data.rating };
    const content = this.data.content.trim();
    if (content) payload.content = content;
    this.setData({ submitting: true });
    api
      .reviewAppointment(this.id, payload)
      .then(() => {
        this.setData({ submitting: false });
        wx.showToast({ title: '评价成功，感谢反馈', icon: 'success' });
        this.backTimer = setTimeout(() => {
          wx.navigateBack();
        }, 1200);
      })
      .catch(() => {
        this.setData({ submitting: false });
      });
  }
});
