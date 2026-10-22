const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const root = path.resolve(__dirname, '..');
let calls = [], pages = [], app;
const wx = new Proxy({getStorageSync:()=>'',getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}}),getWindowInfo:()=>({statusBarHeight:47,windowWidth:390})}, {get:(o,k)=>o[k] || (arg=>calls.push([k,arg]))});
global.wx = wx; global.getCurrentPages = () => pages; global.getApp = () => app;
vm.runInNewContext(fs.readFileSync(path.join(root,'app.js'),'utf8'), {App:p=>app=p,wx,getCurrentPages:()=>pages,require:s=>require(path.join(root,s))});
const access = require('../utils/access');
for (const route of require('../app.json').pages) assert(access.validUrl('/' + route), route);
let apiCalls = [], apiResolve;
const api = { wxLogin:(...args)=>{apiCalls.push(args);return new Promise(resolve=>apiResolve=resolve)},getProfile:()=>Promise.resolve(null) };
function page(key) {
 let p; const file=path.join(root,'pages',key,key+'.js');
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:x=>p=x,wx,getApp:()=>app,getCurrentPages:()=>pages,require:s=>s.endsWith('/api')?api:require(path.resolve(path.dirname(file),s)),setTimeout,clearTimeout,setInterval,clearInterval});
 p.setData=function(data){Object.assign(this.data,data)}; p.route='pages/'+key+'/'+key; return p;
}
function modal(confirm){const m=calls.filter(c=>c[0]==='showModal').at(-1)[1];m.success({confirm});m.complete();}
(async()=>{
 for (const key of access.PUBLIC) {
  calls=[];access.navigate('/pages/'+key+'/'+key);
  assert(!calls.some(c=>c[0]==='showModal'),key+' landing stays public');
 }
 calls=[];access.navigate('/pages/service-detail/service-detail?id=8');
 access.navigate('/pages/drug-detail/drug-detail?id=1');
 assert.equal(calls.filter(c=>c[0]==='showModal').length,1);
 assert(!calls.some(c=>c[0]==='navigateTo'));
 modal(true);assert.equal(app.globalData.loginReturnUrl,'/pages/service-detail/service-detail?id=8');
 const my=page('my');pages=[my];my.onShow();assert(my.data.loginOpen);
 my.onGetPhoneNumber({detail:{code:'unconsented'}});assert.equal(apiCalls.length,0);
 my.onLoginAgreement({detail:{value:['agree']}});
 my.onGetPhoneNumber({detail:{errMsg:'getPhoneNumber:fail user deny'}});assert.equal(apiCalls.length,0);assert(my.data.loginError.includes('取消'));
 my.onGetPhoneNumber({detail:{code:'phone-code'}});
 my.onGetPhoneNumber({detail:{code:'duplicate'}});
 const login=calls.filter(c=>c[0]==='login');assert.equal(login.length,1);
 login[0][1].success({code:'wx-code'});assert.deepEqual(apiCalls,[['wx-code','phone-code']]);
 apiResolve({token:'access',refresh_token:'refresh',user:{id:1,phone:'139****0009'}});
 await new Promise(r=>setImmediate(r));assert(app.isLoggedIn());assert(!my.data.loginOpen);
 assert(calls.some(c=>c[0]==='navigateTo'&&c[1].url==='/pages/service-detail/service-detail?id=8'));
 assert.equal(app.globalData.loginReturnUrl,'');
 app.clearAuth();calls=[];
 for (const key of ['service-detail','provider-detail','providers','book','appointments','review','consult-list','chat','drug-detail','favorites','profile-edit']) {
  const p=page(key);pages=[p];p.onLoad({id:'42'});p.onShow();assert(p.data.accessBlocked,key);
  modal(false);assert(calls.some(c=>c[0]==='switchTab'&&c[1].url==='/pages/index/index'));
 }
 assert.throws(()=>app.setAuth({token:'bad',user:{}}));assert(!app.isLoggedIn());
 calls=[];app.ensureLogin('https://evil.invalid');modal(true);assert.equal(app.globalData.loginReturnUrl,'');
 console.log('PASS: public landing pages, single login prompt, 11 protected deep links, cancel, agreement, separate phone/login codes, duplicate submit, validated session, safe return without replay');
})().catch(e=>{console.error(e);process.exitCode=1});
