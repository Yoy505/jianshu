// wx.request Promise 封装
// - 自动附加 Authorization: Bearer <token>
// - 401 时用 refresh_token 换发新 token 并重试一次；再失败则清登录态
// - 错误体 {detail} 自动 toast（silent 请求除外）
const config = require('./config');

let refreshPromise = null; // 单飞锁：并发 401 只触发一次 refresh

function showError(msg) {
  wx.showToast({ title: msg || '请求失败', icon: 'none', duration: 2200 });
}

function rawRequest(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: /^http/.test(options.url) ? options.url : config.BASE_URL + options.url,
      method: options.method || 'GET',
      data: options.data || {},
      timeout: options.timeout || 15000,
      header: options.header || {},
      success: resolve,
      fail() {
        reject({ statusCode: -1, detail: '网络异常，请稍后重试' });
      }
    });
  });
}

function onAuthExpired(options) {
  const app = getApp();
  if (app && app.clearAuth) app.clearAuth();
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : [];
  const page = pages[pages.length - 1];
  const access = require('./access');
  const route = page && '/' + page.route;
  if (app && app.ensureLogin && route && access.validUrl(route)) {
    app.ensureLogin(page._accessUrl || route, !access.PUBLIC.includes(page.route.split('/')[1]));
  } else if (!options.silent) showError('登录已过期，请重新登录');
}

// POST /auth/refresh {refresh_token} → 新 token 对
function doRefresh() {
  if (refreshPromise) return refreshPromise;
  const app = getApp();
  const rt = app && app.globalData.refreshToken;
  const task = !rt
    ? Promise.reject({ statusCode: 401, detail: '登录已过期，请重新登录' })
    : rawRequest({
        url: config.BASE_URL + '/auth/refresh',
        method: 'POST',
        data: { refresh_token: rt },
        header: { 'Content-Type': 'application/json' }
      }).then((res) => {
        const d = res.data || {};
        const token = d.token || d.access_token;
        if (res.statusCode >= 200 && res.statusCode < 300 && token) {
          const newRt = d.refresh_token || rt;
          app.globalData.token = token;
          app.globalData.refreshToken = newRt;
          try {
            wx.setStorageSync('token', token);
            wx.setStorageSync('refresh_token', newRt);
          } catch (e) {}
          return token;
        }
        return Promise.reject({ statusCode: 401, detail: '登录已过期，请重新登录' });
      });
  refreshPromise = task.then(
    (v) => {
      refreshPromise = null;
      return v;
    },
    (e) => {
      refreshPromise = null;
      throw e;
    }
  );
  return refreshPromise;
}

// options: { url, method, data, header, silent }
function request(options) {
  const app = getApp();
  const header = Object.assign({ 'Content-Type': 'application/json' }, options.header || {});
  if (app && app.globalData && app.globalData.token) {
    header.Authorization = 'Bearer ' + app.globalData.token;
  }
  return rawRequest(Object.assign({}, options, { header })).then(
    (res) => {
      const code = res.statusCode;
      if (code === 401) {
        if (options._retried) {
          onAuthExpired(options);
          return Promise.reject({
            statusCode: 401,
            detail: (res.data && res.data.detail) || '登录已过期，请重新登录'
          });
        }
        return doRefresh().then(
          () => request(Object.assign({}, options, { _retried: true })),
          () => {
            onAuthExpired(options);
            return Promise.reject({ statusCode: 401, detail: '登录已过期，请重新登录' });
          }
        );
      }
      if (code >= 200 && code < 300) return res.data;
      const detail = (res.data && res.data.detail) || '请求失败(' + code + ')';
      if (!options.silent) showError(detail);
      return Promise.reject({ statusCode: code, detail, data: res.data });
    },
    (err) => {
      if (!options.silent) showError(err.detail || '网络异常，请稍后重试');
      return Promise.reject(err);
    }
  );
}

module.exports = { request };
