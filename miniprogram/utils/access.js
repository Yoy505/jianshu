// 首页可浏览，使用操作和二级业务页面需登录。协议页面始终公开。
const PAGE_KEYS = ['index', 'steward', 'mall', 'my', 'scenario', 'service-detail', 'providers', 'provider-detail', 'book', 'appointments', 'review', 'consult-list', 'chat', 'drug-hall', 'drug-detail', 'favorites', 'profile-edit', 'certify', 'legal', 'package-detail', 'order-confirm', 'order-result', 'orders', 'order-detail', 'benefits', 'my-service'];
const routes = PAGE_KEYS.map(key => 'pages/' + key + '/' + key);
const PUBLIC = ['index', 'steward', 'mall', 'my', 'scenario', 'drug-hall', 'certify', 'legal'];
const TABS = ['index', 'steward', 'mall', 'my'];
function keyOf(url) { return (url || '').split('?')[0].split('/')[2]; }
function validUrl(url) { return typeof url === 'string' && routes.includes(url.split('?')[0].replace(/^\//, '')); }
function pageUrl(key, options) {
  const query = Object.keys(options || {}).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(options[k])).join('&');
  return '/pages/' + key + '/' + key + (query ? '?' + query : '');
}
function navigate(url) {
  if (url && typeof url === 'object') url = url.url;
  if (!validUrl(url)) return;
  if (!PUBLIC.includes(keyOf(url)) && !getApp().ensureLogin(url)) return;
  if (TABS.includes(keyOf(url))) wx.switchTab({ url: url.split('?')[0] });
  else wx.navigateTo({ url });
}
function protectPage(definition, key) {
  if (PUBLIC.includes(key)) return definition;
  definition.data = Object.assign({}, definition.data, { accessBlocked: true });
  const load = definition.onLoad, show = definition.onShow;
  definition.onLoad = function (options) {
    this._accessUrl = pageUrl(key, options);
    if (!getApp().isLoggedIn()) {
      getApp().ensureLogin(this._accessUrl, true);
      return;
    }
    this.setData({ accessBlocked: false });
    if (load) return load.call(this, options);
  };
  definition.onShow = function () {
    if (!getApp().isLoggedIn()) {
      this.setData({ accessBlocked: true });
      getApp().ensureLogin(this._accessUrl || pageUrl(key), true);
      return;
    }
    this.setData({ accessBlocked: false });
    if (show) return show.call(this);
  };
  // 防止已打开的页面在退出/过期后继续执行事件、轮询或分页。
  for (const name of Object.keys(definition)) {
    if (typeof definition[name] !== 'function' || ['onLoad', 'onShow', 'onHide', 'onUnload', 'hfBack', 'hfChrome'].includes(name)) continue;
    const action = definition[name];
    definition[name] = function (...args) {
      if (!getApp().isLoggedIn()) {
        this.setData({ accessBlocked: true });
        getApp().ensureLogin(this._accessUrl || pageUrl(key), true);
        return;
      }
      return action.apply(this, args);
    };
  }
  return definition;
}
module.exports = { PUBLIC, TABS, validUrl, pageUrl, navigate, protectPage };
