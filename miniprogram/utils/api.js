// 全部后端接口（路径与 docs/api.md 第二部分逐一对应）
const { request } = require('./request');

// 过滤空参数，避免 GET 查询串出现 undefined/空值
function clean(params) {
  const out = {};
  Object.keys(params || {}).forEach((k) => {
    const v = params[k];
    if (v !== '' && v !== null && v !== undefined) out[k] = v;
  });
  return out;
}

module.exports = {
  // ---- 登录 ----
  wxLogin(code) {
    return request({ url: '/wx/login', method: 'POST', data: { code } });
  },
  smsSend(phone) {
    return request({ url: '/h5/auth/sms/send', method: 'POST', data: { phone } });
  },
  smsLogin(phone, code) {
    return request({ url: '/h5/auth/sms/login', method: 'POST', data: { phone, code } });
  },

  // ---- 首页聚合 ----
  getHome() {
    return request({ url: '/mp/home', silent: true });
  },

  // ---- 场景与服务 ----
  getScenarios() {
    return request({ url: '/mp/scenarios', silent: true });
  },
  getServices(params) {
    return request({ url: '/mp/services', data: clean(params), silent: true });
  },
  getService(id) {
    return request({ url: '/mp/services/' + id, silent: true });
  },

  // ---- 专业人员 ----
  providers(params) {
    return request({ url: '/mp/providers', data: clean(params), silent: true });
  },
  providerDetail(id) {
    return request({ url: '/mp/providers/' + id, silent: true });
  },

  // ---- 预约 ----
  book(data) {
    return request({ url: '/mp/appointments', method: 'POST', data });
  },
  listAppointments(params) {
    return request({ url: '/mp/appointments', data: clean(params), silent: true });
  },
  cancelAppointment(id) {
    return request({ url: '/mp/appointments/' + id + '/cancel', method: 'POST' });
  },
  reviewAppointment(id, data) {
    return request({ url: '/mp/appointments/' + id + '/review', method: 'POST', data });
  },

  // ---- 咚咚在线咨询 ----
  createConsult(data) {
    return request({ url: '/mp/consults', method: 'POST', data });
  },
  listConsults(params) {
    return request({ url: '/mp/consults', data: clean(params), silent: true });
  },
  getMessages(id, afterId) {
    return request({
      url: '/mp/consults/' + id + '/messages',
      data: { after_id: afterId || 0 },
      silent: true
    });
  },
  sendMessage(id, content) {
    return request({ url: '/mp/consults/' + id + '/messages', method: 'POST', data: { content } });
  },

  // ---- 药品数字展厅 ----
  listDrugs(params) {
    return request({ url: '/mp/drugs', data: clean(params), silent: true });
  },
  getDrug(id) {
    return request({ url: '/mp/drugs/' + id, silent: true });
  },
  toggleFavorite(id) {
    return request({ url: '/mp/drugs/' + id + '/favorite', method: 'POST' });
  },
  drugFeedback(id, content) {
    return request({ url: '/mp/drugs/' + id + '/feedback', method: 'POST', data: { content } });
  },
  listFavorites(params) {
    return request({ url: '/mp/favorites', data: clean(params), silent: true });
  },

  // ---- 健康档案 ----
  getProfileCapabilities() {
    return request({ url: '/mp/profile/capabilities', silent: true });
  },
  getProfile() {
    return request({ url: '/mp/profile', silent: true });
  },
  saveProfile(data) {
    return request({ url: '/mp/profile', method: 'PUT', data });
  },

  // ---- 健康商城 ----
  listProducts(params) {
    return request({ url: '/mp/products', data: clean(params), silent: true });
  }
};
