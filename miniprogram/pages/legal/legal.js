// 法务文档页：/pages/legal/legal?doc=terms|privacy
// 内容来自 utils/legal.js；仅静态展示，无任何数据请求。
const legal = require('../../utils/legal');

Page({
  data: {
    doc: null
  },

  onLoad(options) {
    const key = options && options.doc === 'privacy' ? 'PRIVACY' : 'TERMS';
    this.setData({ doc: legal[key] });
    wx.setNavigationBarTitle({ title: legal[key].title });
  }
});
