const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '..');
let calls = [];
const wx = new Proxy({
  getWindowInfo: () => ({ statusBarHeight: 47, windowWidth: 390 }),
  getStorageSync: () => '', getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } })
}, { get: (target, key) => target[key] || (arg => calls.push([key, arg])) });
global.wx = wx; global.getCurrentPages = () => [{}];
const app = { ensureLogin: () => true, isLoggedIn: () => true, globalData: {} };
global.getApp = () => app;
const api = new Proxy({}, { get: (_, key) => (...args) => { calls.push(['api.' + key, args]); return Promise.resolve({}); } });
function page(key) {
  let result;
  const file = path.join(root, 'pages', key, key + '.js');
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: p => result = p, wx, getApp: () => app, getCurrentPages: global.getCurrentPages,
    require: spec => spec.endsWith('/api') ? api : require(path.resolve(path.dirname(file), spec)),
    setTimeout, clearTimeout, setInterval, clearInterval, console
  }, { filename: file });
  result.data = JSON.parse(JSON.stringify(result.data));
  result.setData = function (update, cb) {
    for (const [key, value] of Object.entries(update)) {
      const parts = key.split('.'); let dest = this.data;
      while (parts.length > 1) dest = dest[parts.shift()];
      dest[parts[0]] = value;
    }
    if (cb) cb();
  };
  return result;
}
const event = (dataset = {}, value = '') => ({ currentTarget: { dataset }, detail: { value } });
const review = page('review');
review.setRating(event({ n: 4 })); assert.equal(review.data.rating, 4);
assert.deepEqual(Array.from(review.data.stars), [true, true, true, true, false]);
review.onContent(event({}, '服务体验')); assert.equal(review.data.content, '服务体验');
const book = page('book');
book.setData({ slots: [{ date: '2030-01-01', periods: ['上午', '下午'] }] });
book.selectDate(event({ date: '2030-01-01' })); assert.equal(book.data.selectedPeriod, '上午');
book.selectPeriod(event({ period: '下午' })); assert.equal(book.data.selectedPeriod, '下午');
calls = []; book.setData({ selectedDate: '', selectedPeriod: '' }); book.submit();
assert.equal(calls.some(([name]) => name.startsWith('api.')), false);
const profile = page('profile-edit'); calls = []; profile.save();
assert.equal(calls.some(([name]) => name.startsWith('api.')), false);
profile.toggleChronic(event({ tag: '高血压' })); assert(profile.data.form.chronicTags.includes('高血压'));
profile.toggleChronic(event({ tag: '高血压' })); assert.equal(profile.data.form.chronicTags.length, 0);
const shared = require('../utils/hifi-page');
for (const scene of require('../utils/config').SCENARIOS) {
 const p = shared({ data: { title: scene.name }, onLoad() { this.scenarioKey = scene.key; } }, 'scenario');
 p.setData = update => Object.assign(p.data, update); p.onLoad({});
 assert.equal(p.data.hfSceneKey, scene.key); assert.equal(p.data.hfStatus, 47);
 assert(fs.existsSync(path.join(root, 'assets/home-v1/scene-' + scene.key + '.png')));
}
const legal = page('legal'); legal.onLoad({ doc: 'terms' }); legal.hfLegal(event({ doc: 'privacy' }));
assert.equal(legal.data.hfTitle, '隐私政策');
calls = []; legal.hfGo(event({ url: '/pages/my/my' })); assert.equal(calls[0][0], 'switchTab');
calls = []; legal.hfGo(event({ url: '/pages/providers/providers' })); assert.equal(calls[0][0], 'navigateTo');
const chat = page('chat'); chat.lastId = 0;
chat.appendMessages([{ id: 2, sender: 'system', content: '回执', created_at: '2030-01-01' }, { id: 1, sender: 'user', content: '留言' }]);
chat.appendMessages([{ id: 2, sender: 'system', content: '重复' }]);
assert.deepEqual(Array.from(chat.data.messages, m => m.id), [1, 2]);
assert.equal(chat.data.messages[1].content, '回执');
const my = page('my'); my.hfOpenLogin(); assert(my.data.loginOpen);
app.globalData = { token: '', userInfo: null }; my.syncUser(); assert.equal(my.data.profile, null);
console.log('PASS: navigation, 9 scenes, rating, date/period, closed profile/booking guards, tag selection, legal tabs, chat deduplication, login dialog');
