// Checks the native templates against their actual page controllers, without network calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');global.wx={getStorageSync:()=>'',getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}})};
let current;global.Page=p=>current=p;
let bindings=0,assets=0;
for(const route of require('../app.json').pages){
 current=null;require(path.join(root,route+'.js'));assert(current,route+' must register Page');
 const xml=fs.readFileSync(path.join(root,route+'.wxml'),'utf8');
 for(const [,name] of xml.matchAll(/(?:bind\w+|catch\w+)="([^"{]+)"/g)){assert.equal(typeof current[name],'function',route+': missing '+name);bindings++}
 for(const [,src] of xml.matchAll(/src="(\/assets\/[^"{]+)"/g)){assert(fs.existsSync(path.join(root,src)),route+': missing '+src);assets++}
 if(['pages/my/my','pages/profile-edit/profile-edit'].includes(route)) assert(xml.includes('<privacy-consent'),route+': privacy component must remain mounted');
 assert(!/class="[^"\n]*(?:cell-arrow|ph-arrow|ph-caret|ph-caret-right)/.test(xml),route+': arrow icons forbidden');
}
console.log(JSON.stringify({routes:19,bindings,staticAssets:assets,arrows:0}));
