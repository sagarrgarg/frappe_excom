var Ot=Object.defineProperty,Mt=Object.defineProperties;var Pt=Object.getOwnPropertyDescriptors;var ke=Object.getOwnPropertySymbols;var Lt=Object.prototype.hasOwnProperty,Bt=Object.prototype.propertyIsEnumerable;var De=(t,e,n)=>e in t?Ot(t,e,{enumerable:!0,configurable:!0,writable:!0,value:n}):t[e]=n,m=(t,e)=>{for(var n in e||(e={}))Lt.call(e,n)&&De(t,n,e[n]);if(ke)for(var n of ke(e))Bt.call(e,n)&&De(t,n,e[n]);return t},T=(t,e)=>Mt(t,Pt(e));var c=(t,e,n)=>new Promise((r,s)=>{var i=l=>{try{o(n.next(l))}catch(h){s(h)}},a=l=>{try{o(n.throw(l))}catch(h){s(h)}},o=l=>l.done?r(l.value):Promise.resolve(l.value).then(i,a);o((n=n.apply(t,e)).next())});try{self["workbox:core:7.3.0"]&&_()}catch(t){}const xt=(t,...e)=>{let n=t;return e.length>0&&(n+=` :: ${JSON.stringify(e)}`),n},Ut=xt;class p extends Error{constructor(e,n){const r=Ut(e,n);super(r),this.name=e,this.details=n}}const y={googleAnalytics:"googleAnalytics",precache:"precache-v2",prefix:"workbox",runtime:"runtime",suffix:typeof registration!="undefined"?registration.scope:""},X=t=>[y.prefix,t,y.suffix].filter(e=>e&&e.length>0).join("-"),$t=t=>{for(const e of Object.keys(y))t(e)},q={updateDetails:t=>{$t(e=>{typeof t[e]=="string"&&(y[e]=t[e])})},getGoogleAnalyticsName:t=>t||X(y.googleAnalytics),getPrecacheName:t=>t||X(y.precache),getPrefix:()=>y.prefix,getRuntimeName:t=>t||X(y.runtime),getSuffix:()=>y.suffix};function ve(t,e){const n=e();return t.waitUntil(n),n}try{self["workbox:precaching:7.3.0"]&&_()}catch(t){}const Ft="__WB_REVISION__";function Ht(t){if(!t)throw new p("add-to-cache-list-unexpected-type",{entry:t});if(typeof t=="string"){const i=new URL(t,location.href);return{cacheKey:i.href,url:i.href}}const{revision:e,url:n}=t;if(!n)throw new p("add-to-cache-list-unexpected-type",{entry:t});if(!e){const i=new URL(n,location.href);return{cacheKey:i.href,url:i.href}}const r=new URL(n,location.href),s=new URL(n,location.href);return r.searchParams.set(Ft,e),{cacheKey:r.href,url:s.href}}class Kt{constructor(){this.updatedURLs=[],this.notUpdatedURLs=[],this.handlerWillStart=r=>c(this,[r],function*({request:e,state:n}){n&&(n.originalRequest=e)}),this.cachedResponseWillBeUsed=s=>c(this,[s],function*({event:e,state:n,cachedResponse:r}){if(e.type==="install"&&n&&n.originalRequest&&n.originalRequest instanceof Request){const i=n.originalRequest.url;r?this.notUpdatedURLs.push(i):this.updatedURLs.push(i)}return r})}}class jt{constructor({precacheController:e}){this.cacheKeyWillBeUsed=s=>c(this,[s],function*({request:n,params:r}){const i=(r==null?void 0:r.cacheKey)||this._precacheController.getCacheKeyForURL(n.url);return i?new Request(i,{headers:n.headers}):n}),this._precacheController=e}}let L;function Vt(){if(L===void 0){const t=new Response("");if("body"in t)try{new Response(t.body),L=!0}catch(e){L=!1}L=!1}return L}function Wt(t,e){return c(this,null,function*(){let n=null;if(t.url&&(n=new URL(t.url).origin),n!==self.location.origin)throw new p("cross-origin-copy-response",{origin:n});const r=t.clone(),i={headers:new Headers(r.headers),status:r.status,statusText:r.statusText},a=Vt()?r.body:yield r.blob();return new Response(a,i)})}const qt=t=>new URL(String(t),location.href).href.replace(new RegExp(`^${location.origin}`),"");function Ne(t,e){const n=new URL(t);for(const r of e)n.searchParams.delete(r);return n.href}function zt(t,e,n,r){return c(this,null,function*(){const s=Ne(e.url,n);if(e.url===s)return t.match(e,r);const i=Object.assign(Object.assign({},r),{ignoreSearch:!0}),a=yield t.keys(e,i);for(const o of a){const l=Ne(o.url,n);if(s===l)return t.match(o,r)}})}let Gt=class{constructor(){this.promise=new Promise((e,n)=>{this.resolve=e,this.reject=n})}};const Jt=new Set;function Yt(){return c(this,null,function*(){for(const t of Jt)yield t()})}function Xt(t){return new Promise(e=>setTimeout(e,t))}try{self["workbox:strategies:7.3.0"]&&_()}catch(t){}function F(t){return typeof t=="string"?new Request(t):t}class Qt{constructor(e,n){this._cacheKeys={},Object.assign(this,n),this.event=n.event,this._strategy=e,this._handlerDeferred=new Gt,this._extendLifetimePromises=[],this._plugins=[...e.plugins],this._pluginStateMap=new Map;for(const r of this._plugins)this._pluginStateMap.set(r,{});this.event.waitUntil(this._handlerDeferred.promise)}fetch(e){return c(this,null,function*(){const{event:n}=this;let r=F(e);if(r.mode==="navigate"&&n instanceof FetchEvent&&n.preloadResponse){const a=yield n.preloadResponse;if(a)return a}const s=this.hasCallback("fetchDidFail")?r.clone():null;try{for(const a of this.iterateCallbacks("requestWillFetch"))r=yield a({request:r.clone(),event:n})}catch(a){if(a instanceof Error)throw new p("plugin-error-request-will-fetch",{thrownErrorMessage:a.message})}const i=r.clone();try{let a;a=yield fetch(r,r.mode==="navigate"?void 0:this._strategy.fetchOptions);for(const o of this.iterateCallbacks("fetchDidSucceed"))a=yield o({event:n,request:i,response:a});return a}catch(a){throw s&&(yield this.runCallbacks("fetchDidFail",{error:a,event:n,originalRequest:s.clone(),request:i.clone()})),a}})}fetchAndCachePut(e){return c(this,null,function*(){const n=yield this.fetch(e),r=n.clone();return this.waitUntil(this.cachePut(e,r)),n})}cacheMatch(e){return c(this,null,function*(){const n=F(e);let r;const{cacheName:s,matchOptions:i}=this._strategy,a=yield this.getCacheKey(n,"read"),o=Object.assign(Object.assign({},i),{cacheName:s});r=yield caches.match(a,o);for(const l of this.iterateCallbacks("cachedResponseWillBeUsed"))r=(yield l({cacheName:s,matchOptions:i,cachedResponse:r,request:a,event:this.event}))||void 0;return r})}cachePut(e,n){return c(this,null,function*(){const r=F(e);yield Xt(0);const s=yield this.getCacheKey(r,"write");if(!n)throw new p("cache-put-with-no-response",{url:qt(s.url)});const i=yield this._ensureResponseSafeToCache(n);if(!i)return!1;const{cacheName:a,matchOptions:o}=this._strategy,l=yield self.caches.open(a),h=this.hasCallback("cacheDidUpdate"),u=h?yield zt(l,s.clone(),["__WB_REVISION__"],o):null;try{yield l.put(s,h?i.clone():i)}catch(f){if(f instanceof Error)throw f.name==="QuotaExceededError"&&(yield Yt()),f}for(const f of this.iterateCallbacks("cacheDidUpdate"))yield f({cacheName:a,oldResponse:u,newResponse:i.clone(),request:s,event:this.event});return!0})}getCacheKey(e,n){return c(this,null,function*(){const r=`${e.url} | ${n}`;if(!this._cacheKeys[r]){let s=e;for(const i of this.iterateCallbacks("cacheKeyWillBeUsed"))s=F(yield i({mode:n,request:s,event:this.event,params:this.params}));this._cacheKeys[r]=s}return this._cacheKeys[r]})}hasCallback(e){for(const n of this._strategy.plugins)if(e in n)return!0;return!1}runCallbacks(e,n){return c(this,null,function*(){for(const r of this.iterateCallbacks(e))yield r(n)})}*iterateCallbacks(e){for(const n of this._strategy.plugins)if(typeof n[e]=="function"){const r=this._pluginStateMap.get(n);yield i=>{const a=Object.assign(Object.assign({},i),{state:r});return n[e](a)}}}waitUntil(e){return this._extendLifetimePromises.push(e),e}doneWaiting(){return c(this,null,function*(){for(;this._extendLifetimePromises.length;){const e=this._extendLifetimePromises.splice(0),r=(yield Promise.allSettled(e)).find(s=>s.status==="rejected");if(r)throw r.reason}})}destroy(){this._handlerDeferred.resolve(null)}_ensureResponseSafeToCache(e){return c(this,null,function*(){let n=e,r=!1;for(const s of this.iterateCallbacks("cacheWillUpdate"))if(n=(yield s({request:this.request,response:n,event:this.event}))||void 0,r=!0,!n)break;return r||n&&n.status!==200&&(n=void 0),n})}}class Zt{constructor(e={}){this.cacheName=q.getRuntimeName(e.cacheName),this.plugins=e.plugins||[],this.fetchOptions=e.fetchOptions,this.matchOptions=e.matchOptions}handle(e){const[n]=this.handleAll(e);return n}handleAll(e){e instanceof FetchEvent&&(e={event:e,request:e.request});const n=e.event,r=typeof e.request=="string"?new Request(e.request):e.request,s="params"in e?e.params:void 0,i=new Qt(this,{event:n,request:r,params:s}),a=this._getResponse(i,r,n),o=this._awaitComplete(a,i,r,n);return[a,o]}_getResponse(e,n,r){return c(this,null,function*(){yield e.runCallbacks("handlerWillStart",{event:r,request:n});let s;try{if(s=yield this._handle(n,e),!s||s.type==="error")throw new p("no-response",{url:n.url})}catch(i){if(i instanceof Error){for(const a of e.iterateCallbacks("handlerDidError"))if(s=yield a({error:i,event:r,request:n}),s)break}if(!s)throw i}for(const i of e.iterateCallbacks("handlerWillRespond"))s=yield i({event:r,request:n,response:s});return s})}_awaitComplete(e,n,r,s){return c(this,null,function*(){let i,a;try{i=yield e}catch(o){}try{yield n.runCallbacks("handlerDidRespond",{event:s,request:r,response:i}),yield n.doneWaiting()}catch(o){o instanceof Error&&(a=o)}if(yield n.runCallbacks("handlerDidComplete",{event:s,request:r,response:i,error:a}),n.destroy(),a)throw a})}}class C extends Zt{constructor(e={}){e.cacheName=q.getPrecacheName(e.cacheName),super(e),this._fallbackToNetwork=e.fallbackToNetwork!==!1,this.plugins.push(C.copyRedirectedCacheableResponsesPlugin)}_handle(e,n){return c(this,null,function*(){const r=yield n.cacheMatch(e);return r||(n.event&&n.event.type==="install"?yield this._handleInstall(e,n):yield this._handleFetch(e,n))})}_handleFetch(e,n){return c(this,null,function*(){let r;const s=n.params||{};if(this._fallbackToNetwork){const i=s.integrity,a=e.integrity,o=!a||a===i;r=yield n.fetch(new Request(e,{integrity:e.mode!=="no-cors"?a||i:void 0})),i&&o&&e.mode!=="no-cors"&&(this._useDefaultCacheabilityPluginIfNeeded(),yield n.cachePut(e,r.clone()))}else throw new p("missing-precache-entry",{cacheName:this.cacheName,url:e.url});return r})}_handleInstall(e,n){return c(this,null,function*(){this._useDefaultCacheabilityPluginIfNeeded();const r=yield n.fetch(e);if(!(yield n.cachePut(e,r.clone())))throw new p("bad-precaching-response",{url:e.url,status:r.status});return r})}_useDefaultCacheabilityPluginIfNeeded(){let e=null,n=0;for(const[r,s]of this.plugins.entries())s!==C.copyRedirectedCacheableResponsesPlugin&&(s===C.defaultPrecacheCacheabilityPlugin&&(e=r),s.cacheWillUpdate&&n++);n===0?this.plugins.push(C.defaultPrecacheCacheabilityPlugin):n>1&&e!==null&&this.plugins.splice(e,1)}}C.defaultPrecacheCacheabilityPlugin={cacheWillUpdate(e){return c(this,arguments,function*({response:t}){return!t||t.status>=400?null:t})}};C.copyRedirectedCacheableResponsesPlugin={cacheWillUpdate(e){return c(this,arguments,function*({response:t}){return t.redirected?yield Wt(t):t})}};class en{constructor({cacheName:e,plugins:n=[],fallbackToNetwork:r=!0}={}){this._urlsToCacheKeys=new Map,this._urlsToCacheModes=new Map,this._cacheKeysToIntegrities=new Map,this._strategy=new C({cacheName:q.getPrecacheName(e),plugins:[...n,new jt({precacheController:this})],fallbackToNetwork:r}),this.install=this.install.bind(this),this.activate=this.activate.bind(this)}get strategy(){return this._strategy}precache(e){this.addToCacheList(e),this._installAndActiveListenersAdded||(self.addEventListener("install",this.install),self.addEventListener("activate",this.activate),this._installAndActiveListenersAdded=!0)}addToCacheList(e){const n=[];for(const r of e){typeof r=="string"?n.push(r):r&&r.revision===void 0&&n.push(r.url);const{cacheKey:s,url:i}=Ht(r),a=typeof r!="string"&&r.revision?"reload":"default";if(this._urlsToCacheKeys.has(i)&&this._urlsToCacheKeys.get(i)!==s)throw new p("add-to-cache-list-conflicting-entries",{firstEntry:this._urlsToCacheKeys.get(i),secondEntry:s});if(typeof r!="string"&&r.integrity){if(this._cacheKeysToIntegrities.has(s)&&this._cacheKeysToIntegrities.get(s)!==r.integrity)throw new p("add-to-cache-list-conflicting-integrities",{url:i});this._cacheKeysToIntegrities.set(s,r.integrity)}if(this._urlsToCacheKeys.set(i,s),this._urlsToCacheModes.set(i,a),n.length>0){const o=`Workbox is precaching URLs without revision info: ${n.join(", ")}
This is generally NOT safe. Learn more at https://bit.ly/wb-precache`;console.warn(o)}}}install(e){return ve(e,()=>c(this,null,function*(){const n=new Kt;this.strategy.plugins.push(n);for(const[i,a]of this._urlsToCacheKeys){const o=this._cacheKeysToIntegrities.get(a),l=this._urlsToCacheModes.get(i),h=new Request(i,{integrity:o,cache:l,credentials:"same-origin"});yield Promise.all(this.strategy.handleAll({params:{cacheKey:a},request:h,event:e}))}const{updatedURLs:r,notUpdatedURLs:s}=n;return{updatedURLs:r,notUpdatedURLs:s}}))}activate(e){return ve(e,()=>c(this,null,function*(){const n=yield self.caches.open(this.strategy.cacheName),r=yield n.keys(),s=new Set(this._urlsToCacheKeys.values()),i=[];for(const a of r)s.has(a.url)||(yield n.delete(a),i.push(a.url));return{deletedURLs:i}}))}getURLsToCacheKeys(){return this._urlsToCacheKeys}getCachedURLs(){return[...this._urlsToCacheKeys.keys()]}getCacheKeyForURL(e){const n=new URL(e,location.href);return this._urlsToCacheKeys.get(n.href)}getIntegrityForCacheKey(e){return this._cacheKeysToIntegrities.get(e)}matchPrecache(e){return c(this,null,function*(){const n=e instanceof Request?e.url:e,r=this.getCacheKeyForURL(n);if(r)return(yield self.caches.open(this.strategy.cacheName)).match(r)})}createHandlerBoundToURL(e){const n=this.getCacheKeyForURL(e);if(!n)throw new p("non-precached-url",{url:e});return r=>(r.request=new Request(e),r.params=Object.assign({cacheKey:n},r.params),this.strategy.handle(r))}}let Q;const Ge=()=>(Q||(Q=new en),Q);try{self["workbox:routing:7.3.0"]&&_()}catch(t){}const Je="GET",H=t=>t&&typeof t=="object"?t:{handle:t};class x{constructor(e,n,r=Je){this.handler=H(n),this.match=e,this.method=r}setCatchHandler(e){this.catchHandler=H(e)}}class tn extends x{constructor(e,n,r){const s=({url:i})=>{const a=e.exec(i.href);if(a&&!(i.origin!==location.origin&&a.index!==0))return a.slice(1)};super(s,n,r)}}class nn{constructor(){this._routes=new Map,this._defaultHandlerMap=new Map}get routes(){return this._routes}addFetchListener(){self.addEventListener("fetch",(e=>{const{request:n}=e,r=this.handleRequest({request:n,event:e});r&&e.respondWith(r)}))}addCacheListener(){self.addEventListener("message",(e=>{if(e.data&&e.data.type==="CACHE_URLS"){const{payload:n}=e.data,r=Promise.all(n.urlsToCache.map(s=>{typeof s=="string"&&(s=[s]);const i=new Request(...s);return this.handleRequest({request:i,event:e})}));e.waitUntil(r),e.ports&&e.ports[0]&&r.then(()=>e.ports[0].postMessage(!0))}}))}handleRequest({request:e,event:n}){const r=new URL(e.url,location.href);if(!r.protocol.startsWith("http"))return;const s=r.origin===location.origin,{params:i,route:a}=this.findMatchingRoute({event:n,request:e,sameOrigin:s,url:r});let o=a&&a.handler;const l=e.method;if(!o&&this._defaultHandlerMap.has(l)&&(o=this._defaultHandlerMap.get(l)),!o)return;let h;try{h=o.handle({url:r,request:e,event:n,params:i})}catch(f){h=Promise.reject(f)}const u=a&&a.catchHandler;return h instanceof Promise&&(this._catchHandler||u)&&(h=h.catch(f=>c(this,null,function*(){if(u)try{return yield u.handle({url:r,request:e,event:n,params:i})}catch(b){b instanceof Error&&(f=b)}if(this._catchHandler)return this._catchHandler.handle({url:r,request:e,event:n});throw f}))),h}findMatchingRoute({url:e,sameOrigin:n,request:r,event:s}){const i=this._routes.get(r.method)||[];for(const a of i){let o;const l=a.match({url:e,sameOrigin:n,request:r,event:s});if(l)return o=l,(Array.isArray(o)&&o.length===0||l.constructor===Object&&Object.keys(l).length===0||typeof l=="boolean")&&(o=void 0),{route:a,params:o}}return{}}setDefaultHandler(e,n=Je){this._defaultHandlerMap.set(n,H(e))}setCatchHandler(e){this._catchHandler=H(e)}registerRoute(e){this._routes.has(e.method)||this._routes.set(e.method,[]),this._routes.get(e.method).push(e)}unregisterRoute(e){if(!this._routes.has(e.method))throw new p("unregister-route-but-not-found-with-method",{method:e.method});const n=this._routes.get(e.method).indexOf(e);if(n>-1)this._routes.get(e.method).splice(n,1);else throw new p("unregister-route-route-not-registered")}}let B;const rn=()=>(B||(B=new nn,B.addFetchListener(),B.addCacheListener()),B);function sn(t,e,n){let r;if(typeof t=="string"){const i=new URL(t,location.href),a=({url:o})=>o.href===i.href;r=new x(a,e,n)}else if(t instanceof RegExp)r=new tn(t,e,n);else if(typeof t=="function")r=new x(t,e,n);else if(t instanceof x)r=t;else throw new p("unsupported-route-type",{moduleName:"workbox-routing",funcName:"registerRoute",paramName:"capture"});return rn().registerRoute(r),r}function an(t,e=[]){for(const n of[...t.searchParams.keys()])e.some(r=>r.test(n))&&t.searchParams.delete(n);return t}function*on(t,{ignoreURLParametersMatching:e=[/^utm_/,/^fbclid$/],directoryIndex:n="index.html",cleanURLs:r=!0,urlManipulation:s}={}){const i=new URL(t,location.href);i.hash="",yield i.href;const a=an(i,e);if(yield a.href,n&&a.pathname.endsWith("/")){const o=new URL(a.href);o.pathname+=n,yield o.href}if(r){const o=new URL(a.href);o.pathname+=".html",yield o.href}if(s){const o=s({url:i});for(const l of o)yield l.href}}class cn extends x{constructor(e,n){const r=({request:s})=>{const i=e.getURLsToCacheKeys();for(const a of on(s.url,n)){const o=i.get(a);if(o){const l=e.getIntegrityForCacheKey(o);return{cacheKey:o,integrity:l}}}};super(r,e.strategy)}}function ln(t){const e=Ge(),n=new cn(e,t);sn(n)}const un="-precache-",hn=(n,...r)=>c(null,[n,...r],function*(t,e=un){const i=(yield self.caches.keys()).filter(a=>a.includes(e)&&a.includes(self.registration.scope)&&a!==t);return yield Promise.all(i.map(a=>self.caches.delete(a))),i});function fn(){self.addEventListener("activate",(t=>{const e=q.getPrecacheName();t.waitUntil(hn(e).then(n=>{}))}))}function dn(t){Ge().precache(t)}function pn(t,e){dn(t),ln(e)}function gn(){self.addEventListener("activate",()=>self.clients.claim())}const mn=()=>{};var Oe={};/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Ye=function(t){const e=[];let n=0;for(let r=0;r<t.length;r++){let s=t.charCodeAt(r);s<128?e[n++]=s:s<2048?(e[n++]=s>>6|192,e[n++]=s&63|128):(s&64512)===55296&&r+1<t.length&&(t.charCodeAt(r+1)&64512)===56320?(s=65536+((s&1023)<<10)+(t.charCodeAt(++r)&1023),e[n++]=s>>18|240,e[n++]=s>>12&63|128,e[n++]=s>>6&63|128,e[n++]=s&63|128):(e[n++]=s>>12|224,e[n++]=s>>6&63|128,e[n++]=s&63|128)}return e},bn=function(t){const e=[];let n=0,r=0;for(;n<t.length;){const s=t[n++];if(s<128)e[r++]=String.fromCharCode(s);else if(s>191&&s<224){const i=t[n++];e[r++]=String.fromCharCode((s&31)<<6|i&63)}else if(s>239&&s<365){const i=t[n++],a=t[n++],o=t[n++],l=((s&7)<<18|(i&63)<<12|(a&63)<<6|o&63)-65536;e[r++]=String.fromCharCode(55296+(l>>10)),e[r++]=String.fromCharCode(56320+(l&1023))}else{const i=t[n++],a=t[n++];e[r++]=String.fromCharCode((s&15)<<12|(i&63)<<6|a&63)}}return e.join("")},Xe={byteToCharMap_:null,charToByteMap_:null,byteToCharMapWebSafe_:null,charToByteMapWebSafe_:null,ENCODED_VALS_BASE:"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",get ENCODED_VALS(){return this.ENCODED_VALS_BASE+"+/="},get ENCODED_VALS_WEBSAFE(){return this.ENCODED_VALS_BASE+"-_."},HAS_NATIVE_SUPPORT:typeof atob=="function",encodeByteArray(t,e){if(!Array.isArray(t))throw Error("encodeByteArray takes an array as a parameter");this.init_();const n=e?this.byteToCharMapWebSafe_:this.byteToCharMap_,r=[];for(let s=0;s<t.length;s+=3){const i=t[s],a=s+1<t.length,o=a?t[s+1]:0,l=s+2<t.length,h=l?t[s+2]:0,u=i>>2,f=(i&3)<<4|o>>4;let b=(o&15)<<2|h>>6,$=h&63;l||($=64,a||(b=64)),r.push(n[u],n[f],n[b],n[$])}return r.join("")},encodeString(t,e){return this.HAS_NATIVE_SUPPORT&&!e?btoa(t):this.encodeByteArray(Ye(t),e)},decodeString(t,e){return this.HAS_NATIVE_SUPPORT&&!e?atob(t):bn(this.decodeStringToByteArray(t,e))},decodeStringToByteArray(t,e){this.init_();const n=e?this.charToByteMapWebSafe_:this.charToByteMap_,r=[];for(let s=0;s<t.length;){const i=n[t.charAt(s++)],o=s<t.length?n[t.charAt(s)]:0;++s;const h=s<t.length?n[t.charAt(s)]:64;++s;const f=s<t.length?n[t.charAt(s)]:64;if(++s,i==null||o==null||h==null||f==null)throw new wn;const b=i<<2|o>>4;if(r.push(b),h!==64){const $=o<<4&240|h>>2;if(r.push($),f!==64){const Nt=h<<6&192|f;r.push(Nt)}}}return r},init_(){if(!this.byteToCharMap_){this.byteToCharMap_={},this.charToByteMap_={},this.byteToCharMapWebSafe_={},this.charToByteMapWebSafe_={};for(let t=0;t<this.ENCODED_VALS.length;t++)this.byteToCharMap_[t]=this.ENCODED_VALS.charAt(t),this.charToByteMap_[this.byteToCharMap_[t]]=t,this.byteToCharMapWebSafe_[t]=this.ENCODED_VALS_WEBSAFE.charAt(t),this.charToByteMapWebSafe_[this.byteToCharMapWebSafe_[t]]=t,t>=this.ENCODED_VALS_BASE.length&&(this.charToByteMap_[this.ENCODED_VALS_WEBSAFE.charAt(t)]=t,this.charToByteMapWebSafe_[this.ENCODED_VALS.charAt(t)]=t)}}};class wn extends Error{constructor(){super(...arguments),this.name="DecodeBase64StringError"}}const yn=function(t){const e=Ye(t);return Xe.encodeByteArray(e,!0)},Qe=function(t){return yn(t).replace(/\./g,"")},_n=function(t){try{return Xe.decodeString(t,!0)}catch(e){console.error("base64Decode failed: ",e)}return null};/**
 * @license
 * Copyright 2022 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function In(){if(typeof self!="undefined")return self;if(typeof window!="undefined")return window;if(typeof global!="undefined")return global;throw new Error("Unable to locate global object.")}/**
 * @license
 * Copyright 2022 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const En=()=>In().__FIREBASE_DEFAULTS__,Cn=()=>{if(typeof process=="undefined"||typeof Oe=="undefined")return;const t=Oe.__FIREBASE_DEFAULTS__;if(t)return JSON.parse(t)},Sn=()=>{if(typeof document=="undefined")return;let t;try{t=document.cookie.match(/__FIREBASE_DEFAULTS__=([^;]+)/)}catch(n){return}const e=t&&_n(t[1]);return e&&JSON.parse(e)},Tn=()=>{try{return mn()||En()||Cn()||Sn()}catch(t){console.info(`Unable to get __FIREBASE_DEFAULTS__ due to: ${t}`);return}},Ze=()=>{var t;return(t=Tn())==null?void 0:t.config};/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */class Rn{constructor(){this.reject=()=>{},this.resolve=()=>{},this.promise=new Promise((e,n)=>{this.resolve=e,this.reject=n})}wrapCallback(e){return(n,r)=>{n?this.reject(n):this.resolve(r),typeof e=="function"&&(this.promise.catch(()=>{}),e.length===1?e(n):e(n,r))}}}function et(){try{return typeof indexedDB=="object"}catch(t){return!1}}function tt(){return new Promise((t,e)=>{try{let n=!0;const r="validate-browser-context-for-indexeddb-analytics-module",s=self.indexedDB.open(r);s.onsuccess=()=>{s.result.close(),n||self.indexedDB.deleteDatabase(r),t(!0)},s.onupgradeneeded=()=>{n=!1},s.onerror=()=>{var i;e(((i=s.error)==null?void 0:i.message)||"")}}catch(n){e(n)}})}/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const An="FirebaseError";class P extends Error{constructor(e,n,r){super(n),this.code=e,this.customData=r,this.name=An,Object.setPrototypeOf(this,P.prototype),Error.captureStackTrace&&Error.captureStackTrace(this,z.prototype.create)}}class z{constructor(e,n,r){this.service=e,this.serviceName=n,this.errors=r}create(e,...n){const r=n[0]||{},s=`${this.service}/${e}`,i=this.errors[e],a=i?kn(i,r):"Error",o=`${this.serviceName}: ${a} (${s}).`;return new P(s,o,r)}}function kn(t,e){return t.replace(Dn,(n,r)=>{const s=e[r];return s!=null?String(s):`<${r}?>`})}const Dn=/\{\$([^}]+)}/g;function le(t,e){if(t===e)return!0;const n=Object.keys(t),r=Object.keys(e);for(const s of n){if(!r.includes(s))return!1;const i=t[s],a=e[s];if(Me(i)&&Me(a)){if(!le(i,a))return!1}else if(i!==a)return!1}for(const s of r)if(!n.includes(s))return!1;return!0}function Me(t){return t!==null&&typeof t=="object"}/**
 * @license
 * Copyright 2021 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function nt(t){return t&&t._delegate?t._delegate:t}class k{constructor(e,n,r){this.name=e,this.instanceFactory=n,this.type=r,this.multipleInstances=!1,this.serviceProps={},this.instantiationMode="LAZY",this.onInstanceCreated=null}setInstantiationMode(e){return this.instantiationMode=e,this}setMultipleInstances(e){return this.multipleInstances=e,this}setServiceProps(e){return this.serviceProps=e,this}setInstanceCreatedCallback(e){return this.onInstanceCreated=e,this}}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const R="[DEFAULT]";/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */class vn{constructor(e,n){this.name=e,this.container=n,this.component=null,this.instances=new Map,this.instancesDeferred=new Map,this.instancesOptions=new Map,this.onInitCallbacks=new Map}get(e){const n=this.normalizeInstanceIdentifier(e);if(!this.instancesDeferred.has(n)){const r=new Rn;if(this.instancesDeferred.set(n,r),this.isInitialized(n)||this.shouldAutoInitialize())try{const s=this.getOrInitializeService({instanceIdentifier:n});s&&r.resolve(s)}catch(s){}}return this.instancesDeferred.get(n).promise}getImmediate(e){var s;const n=this.normalizeInstanceIdentifier(e==null?void 0:e.identifier),r=(s=e==null?void 0:e.optional)!=null?s:!1;if(this.isInitialized(n)||this.shouldAutoInitialize())try{return this.getOrInitializeService({instanceIdentifier:n})}catch(i){if(r)return null;throw i}else{if(r)return null;throw Error(`Service ${this.name} is not available`)}}getComponent(){return this.component}setComponent(e){if(e.name!==this.name)throw Error(`Mismatching Component ${e.name} for Provider ${this.name}.`);if(this.component)throw Error(`Component for ${this.name} has already been provided`);if(this.component=e,!!this.shouldAutoInitialize()){if(On(e))try{this.getOrInitializeService({instanceIdentifier:R})}catch(n){}for(const[n,r]of this.instancesDeferred.entries()){const s=this.normalizeInstanceIdentifier(n);try{const i=this.getOrInitializeService({instanceIdentifier:s});r.resolve(i)}catch(i){}}}}clearInstance(e=R){this.instancesDeferred.delete(e),this.instancesOptions.delete(e),this.instances.delete(e)}delete(){return c(this,null,function*(){const e=Array.from(this.instances.values());yield Promise.all([...e.filter(n=>"INTERNAL"in n).map(n=>n.INTERNAL.delete()),...e.filter(n=>"_delete"in n).map(n=>n._delete())])})}isComponentSet(){return this.component!=null}isInitialized(e=R){return this.instances.has(e)}getOptions(e=R){return this.instancesOptions.get(e)||{}}initialize(e={}){const{options:n={}}=e,r=this.normalizeInstanceIdentifier(e.instanceIdentifier);if(this.isInitialized(r))throw Error(`${this.name}(${r}) has already been initialized`);if(!this.isComponentSet())throw Error(`Component ${this.name} has not been registered yet`);const s=this.getOrInitializeService({instanceIdentifier:r,options:n});for(const[i,a]of this.instancesDeferred.entries()){const o=this.normalizeInstanceIdentifier(i);r===o&&a.resolve(s)}return s}onInit(e,n){var a;const r=this.normalizeInstanceIdentifier(n),s=(a=this.onInitCallbacks.get(r))!=null?a:new Set;s.add(e),this.onInitCallbacks.set(r,s);const i=this.instances.get(r);return i&&e(i,r),()=>{s.delete(e)}}invokeOnInitCallbacks(e,n){const r=this.onInitCallbacks.get(n);if(r)for(const s of r)try{s(e,n)}catch(i){}}getOrInitializeService({instanceIdentifier:e,options:n={}}){let r=this.instances.get(e);if(!r&&this.component&&(r=this.component.instanceFactory(this.container,{instanceIdentifier:Nn(e),options:n}),this.instances.set(e,r),this.instancesOptions.set(e,n),this.invokeOnInitCallbacks(r,e),this.component.onInstanceCreated))try{this.component.onInstanceCreated(this.container,e,r)}catch(s){}return r||null}normalizeInstanceIdentifier(e=R){return this.component?this.component.multipleInstances?e:R:e}shouldAutoInitialize(){return!!this.component&&this.component.instantiationMode!=="EXPLICIT"}}function Nn(t){return t===R?void 0:t}function On(t){return t.instantiationMode==="EAGER"}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */class Mn{constructor(e){this.name=e,this.providers=new Map}addComponent(e){const n=this.getProvider(e.name);if(n.isComponentSet())throw new Error(`Component ${e.name} has already been registered with ${this.name}`);n.setComponent(e)}addOrOverwriteComponent(e){this.getProvider(e.name).isComponentSet()&&this.providers.delete(e.name),this.addComponent(e)}getProvider(e){if(this.providers.has(e))return this.providers.get(e);const n=new vn(e,this);return this.providers.set(e,n),n}getProviders(){return Array.from(this.providers.values())}}/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */var d;(function(t){t[t.DEBUG=0]="DEBUG",t[t.VERBOSE=1]="VERBOSE",t[t.INFO=2]="INFO",t[t.WARN=3]="WARN",t[t.ERROR=4]="ERROR",t[t.SILENT=5]="SILENT"})(d||(d={}));const Pn={debug:d.DEBUG,verbose:d.VERBOSE,info:d.INFO,warn:d.WARN,error:d.ERROR,silent:d.SILENT},Ln=d.INFO,Bn={[d.DEBUG]:"log",[d.VERBOSE]:"log",[d.INFO]:"info",[d.WARN]:"warn",[d.ERROR]:"error"},xn=(t,e,...n)=>{if(e<t.logLevel)return;const r=new Date().toISOString(),s=Bn[e];if(s)console[s](`[${r}]  ${t.name}:`,...n);else throw new Error(`Attempted to log a message with an invalid logType (value: ${e})`)};class Un{constructor(e){this.name=e,this._logLevel=Ln,this._logHandler=xn,this._userLogHandler=null}get logLevel(){return this._logLevel}set logLevel(e){if(!(e in d))throw new TypeError(`Invalid value "${e}" assigned to \`logLevel\``);this._logLevel=e}setLogLevel(e){this._logLevel=typeof e=="string"?Pn[e]:e}get logHandler(){return this._logHandler}set logHandler(e){if(typeof e!="function")throw new TypeError("Value assigned to `logHandler` must be a function");this._logHandler=e}get userLogHandler(){return this._userLogHandler}set userLogHandler(e){this._userLogHandler=e}debug(...e){this._userLogHandler&&this._userLogHandler(this,d.DEBUG,...e),this._logHandler(this,d.DEBUG,...e)}log(...e){this._userLogHandler&&this._userLogHandler(this,d.VERBOSE,...e),this._logHandler(this,d.VERBOSE,...e)}info(...e){this._userLogHandler&&this._userLogHandler(this,d.INFO,...e),this._logHandler(this,d.INFO,...e)}warn(...e){this._userLogHandler&&this._userLogHandler(this,d.WARN,...e),this._logHandler(this,d.WARN,...e)}error(...e){this._userLogHandler&&this._userLogHandler(this,d.ERROR,...e),this._logHandler(this,d.ERROR,...e)}}const $n=(t,e)=>e.some(n=>t instanceof n);let Pe,Le;function Fn(){return Pe||(Pe=[IDBDatabase,IDBObjectStore,IDBIndex,IDBCursor,IDBTransaction])}function Hn(){return Le||(Le=[IDBCursor.prototype.advance,IDBCursor.prototype.continue,IDBCursor.prototype.continuePrimaryKey])}const rt=new WeakMap,ue=new WeakMap,st=new WeakMap,Z=new WeakMap,me=new WeakMap;function Kn(t){const e=new Promise((n,r)=>{const s=()=>{t.removeEventListener("success",i),t.removeEventListener("error",a)},i=()=>{n(I(t.result)),s()},a=()=>{r(t.error),s()};t.addEventListener("success",i),t.addEventListener("error",a)});return e.then(n=>{n instanceof IDBCursor&&rt.set(n,t)}).catch(()=>{}),me.set(e,t),e}function jn(t){if(ue.has(t))return;const e=new Promise((n,r)=>{const s=()=>{t.removeEventListener("complete",i),t.removeEventListener("error",a),t.removeEventListener("abort",a)},i=()=>{n(),s()},a=()=>{r(t.error||new DOMException("AbortError","AbortError")),s()};t.addEventListener("complete",i),t.addEventListener("error",a),t.addEventListener("abort",a)});ue.set(t,e)}let he={get(t,e,n){if(t instanceof IDBTransaction){if(e==="done")return ue.get(t);if(e==="objectStoreNames")return t.objectStoreNames||st.get(t);if(e==="store")return n.objectStoreNames[1]?void 0:n.objectStore(n.objectStoreNames[0])}return I(t[e])},set(t,e,n){return t[e]=n,!0},has(t,e){return t instanceof IDBTransaction&&(e==="done"||e==="store")?!0:e in t}};function Vn(t){he=t(he)}function Wn(t){return t===IDBDatabase.prototype.transaction&&!("objectStoreNames"in IDBTransaction.prototype)?function(e,...n){const r=t.call(ee(this),e,...n);return st.set(r,e.sort?e.sort():[e]),I(r)}:Hn().includes(t)?function(...e){return t.apply(ee(this),e),I(rt.get(this))}:function(...e){return I(t.apply(ee(this),e))}}function qn(t){return typeof t=="function"?Wn(t):(t instanceof IDBTransaction&&jn(t),$n(t,Fn())?new Proxy(t,he):t)}function I(t){if(t instanceof IDBRequest)return Kn(t);if(Z.has(t))return Z.get(t);const e=qn(t);return e!==t&&(Z.set(t,e),me.set(e,t)),e}const ee=t=>me.get(t);function G(t,e,{blocked:n,upgrade:r,blocking:s,terminated:i}={}){const a=indexedDB.open(t,e),o=I(a);return r&&a.addEventListener("upgradeneeded",l=>{r(I(a.result),l.oldVersion,l.newVersion,I(a.transaction),l)}),n&&a.addEventListener("blocked",l=>n(l.oldVersion,l.newVersion,l)),o.then(l=>{i&&l.addEventListener("close",()=>i()),s&&l.addEventListener("versionchange",h=>s(h.oldVersion,h.newVersion,h))}).catch(()=>{}),o}function te(t,{blocked:e}={}){const n=indexedDB.deleteDatabase(t);return e&&n.addEventListener("blocked",r=>e(r.oldVersion,r)),I(n).then(()=>{})}const zn=["get","getKey","getAll","getAllKeys","count"],Gn=["put","add","delete","clear"],ne=new Map;function Be(t,e){if(!(t instanceof IDBDatabase&&!(e in t)&&typeof e=="string"))return;if(ne.get(e))return ne.get(e);const n=e.replace(/FromIndex$/,""),r=e!==n,s=Gn.includes(n);if(!(n in(r?IDBIndex:IDBObjectStore).prototype)||!(s||zn.includes(n)))return;const i=function(a,...o){return c(this,null,function*(){const l=this.transaction(a,s?"readwrite":"readonly");let h=l.store;return r&&(h=h.index(o.shift())),(yield Promise.all([h[n](...o),s&&l.done]))[0]})};return ne.set(e,i),i}Vn(t=>T(m({},t),{get:(e,n,r)=>Be(e,n)||t.get(e,n,r),has:(e,n)=>!!Be(e,n)||t.has(e,n)}));/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */class Jn{constructor(e){this.container=e}getPlatformInfoString(){return this.container.getProviders().map(n=>{if(Yn(n)){const r=n.getImmediate();return`${r.library}/${r.version}`}else return null}).filter(n=>n).join(" ")}}function Yn(t){const e=t.getComponent();return(e==null?void 0:e.type)==="VERSION"}const fe="@firebase/app",xe="0.14.10";/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const E=new Un("@firebase/app"),Xn="@firebase/app-compat",Qn="@firebase/analytics-compat",Zn="@firebase/analytics",er="@firebase/app-check-compat",tr="@firebase/app-check",nr="@firebase/auth",rr="@firebase/auth-compat",sr="@firebase/database",ir="@firebase/data-connect",ar="@firebase/database-compat",or="@firebase/functions",cr="@firebase/functions-compat",lr="@firebase/installations",ur="@firebase/installations-compat",hr="@firebase/messaging",fr="@firebase/messaging-compat",dr="@firebase/performance",pr="@firebase/performance-compat",gr="@firebase/remote-config",mr="@firebase/remote-config-compat",br="@firebase/storage",wr="@firebase/storage-compat",yr="@firebase/firestore",_r="@firebase/ai",Ir="@firebase/firestore-compat",Er="firebase";/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const de="[DEFAULT]",Cr={[fe]:"fire-core",[Xn]:"fire-core-compat",[Zn]:"fire-analytics",[Qn]:"fire-analytics-compat",[tr]:"fire-app-check",[er]:"fire-app-check-compat",[nr]:"fire-auth",[rr]:"fire-auth-compat",[sr]:"fire-rtdb",[ir]:"fire-data-connect",[ar]:"fire-rtdb-compat",[or]:"fire-fn",[cr]:"fire-fn-compat",[lr]:"fire-iid",[ur]:"fire-iid-compat",[hr]:"fire-fcm",[fr]:"fire-fcm-compat",[dr]:"fire-perf",[pr]:"fire-perf-compat",[gr]:"fire-rc",[mr]:"fire-rc-compat",[br]:"fire-gcs",[wr]:"fire-gcs-compat",[yr]:"fire-fst",[Ir]:"fire-fst-compat",[_r]:"fire-vertex","fire-js":"fire-js",[Er]:"fire-js-all"};/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const K=new Map,Sr=new Map,pe=new Map;function Ue(t,e){try{t.container.addComponent(e)}catch(n){E.debug(`Component ${e.name} failed to register with FirebaseApp ${t.name}`,n)}}function M(t){const e=t.name;if(pe.has(e))return E.debug(`There were multiple attempts to register component ${e}.`),!1;pe.set(e,t);for(const n of K.values())Ue(n,t);for(const n of Sr.values())Ue(n,t);return!0}function be(t,e){const n=t.container.getProvider("heartbeat").getImmediate({optional:!0});return n&&n.triggerHeartbeat(),t.container.getProvider(e)}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Tr={"no-app":"No Firebase App '{$appName}' has been created - call initializeApp() first","bad-app-name":"Illegal App name: '{$appName}'","duplicate-app":"Firebase App named '{$appName}' already exists with different options or config","app-deleted":"Firebase App named '{$appName}' already deleted","server-app-deleted":"Firebase Server App has been deleted","no-options":"Need to provide options, when not being deployed to hosting via source.","invalid-app-argument":"firebase.{$appName}() takes either no argument or a Firebase App instance.","invalid-log-argument":"First argument to `onLog` must be null or a function.","idb-open":"Error thrown when opening IndexedDB. Original error: {$originalErrorMessage}.","idb-get":"Error thrown when reading from IndexedDB. Original error: {$originalErrorMessage}.","idb-set":"Error thrown when writing to IndexedDB. Original error: {$originalErrorMessage}.","idb-delete":"Error thrown when deleting from IndexedDB. Original error: {$originalErrorMessage}.","finalization-registry-not-supported":"FirebaseServerApp deleteOnDeref field defined but the JS runtime does not support FinalizationRegistry.","invalid-server-app-environment":"FirebaseServerApp is not for use in browser environments."},S=new z("app","Firebase",Tr);/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */class Rr{constructor(e,n,r){this._isDeleted=!1,this._options=m({},e),this._config=m({},n),this._name=n.name,this._automaticDataCollectionEnabled=n.automaticDataCollectionEnabled,this._container=r,this.container.addComponent(new k("app",()=>this,"PUBLIC"))}get automaticDataCollectionEnabled(){return this.checkDestroyed(),this._automaticDataCollectionEnabled}set automaticDataCollectionEnabled(e){this.checkDestroyed(),this._automaticDataCollectionEnabled=e}get name(){return this.checkDestroyed(),this._name}get options(){return this.checkDestroyed(),this._options}get config(){return this.checkDestroyed(),this._config}get container(){return this._container}get isDeleted(){return this._isDeleted}set isDeleted(e){this._isDeleted=e}checkDestroyed(){if(this.isDeleted)throw S.create("app-deleted",{appName:this._name})}}function it(t,e={}){let n=t;typeof e!="object"&&(e={name:e});const r=m({name:de,automaticDataCollectionEnabled:!0},e),s=r.name;if(typeof s!="string"||!s)throw S.create("bad-app-name",{appName:String(s)});if(n||(n=Ze()),!n)throw S.create("no-options");const i=K.get(s);if(i){if(le(n,i.options)&&le(r,i.config))return i;throw S.create("duplicate-app",{appName:s})}const a=new Mn(s);for(const l of pe.values())a.addComponent(l);const o=new Rr(n,r,a);return K.set(s,o),o}function Ar(t=de){const e=K.get(t);if(!e&&t===de&&Ze())return it();if(!e)throw S.create("no-app",{appName:t});return e}function O(t,e,n){var a;let r=(a=Cr[t])!=null?a:t;n&&(r+=`-${n}`);const s=r.match(/\s|\//),i=e.match(/\s|\//);if(s||i){const o=[`Unable to register library "${r}" with version "${e}":`];s&&o.push(`library name "${r}" contains illegal characters (whitespace or "/")`),s&&i&&o.push("and"),i&&o.push(`version name "${e}" contains illegal characters (whitespace or "/")`),E.warn(o.join(" "));return}M(new k(`${r}-version`,()=>({library:r,version:e}),"VERSION"))}/**
 * @license
 * Copyright 2021 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const kr="firebase-heartbeat-database",Dr=1,U="firebase-heartbeat-store";let re=null;function at(){return re||(re=G(kr,Dr,{upgrade:(t,e)=>{switch(e){case 0:try{t.createObjectStore(U)}catch(n){console.warn(n)}}}}).catch(t=>{throw S.create("idb-open",{originalErrorMessage:t.message})})),re}function vr(t){return c(this,null,function*(){try{const n=(yield at()).transaction(U),r=yield n.objectStore(U).get(ot(t));return yield n.done,r}catch(e){if(e instanceof P)E.warn(e.message);else{const n=S.create("idb-get",{originalErrorMessage:e==null?void 0:e.message});E.warn(n.message)}}})}function $e(t,e){return c(this,null,function*(){try{const r=(yield at()).transaction(U,"readwrite");yield r.objectStore(U).put(e,ot(t)),yield r.done}catch(n){if(n instanceof P)E.warn(n.message);else{const r=S.create("idb-set",{originalErrorMessage:n==null?void 0:n.message});E.warn(r.message)}}})}function ot(t){return`${t.name}!${t.options.appId}`}/**
 * @license
 * Copyright 2021 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Nr=1024,Or=30;class Mr{constructor(e){this.container=e,this._heartbeatsCache=null;const n=this.container.getProvider("app").getImmediate();this._storage=new Lr(n),this._heartbeatsCachePromise=this._storage.read().then(r=>(this._heartbeatsCache=r,r))}triggerHeartbeat(){return c(this,null,function*(){var e,n;try{const s=this.container.getProvider("platform-logger").getImmediate().getPlatformInfoString(),i=Fe();if(((e=this._heartbeatsCache)==null?void 0:e.heartbeats)==null&&(this._heartbeatsCache=yield this._heartbeatsCachePromise,((n=this._heartbeatsCache)==null?void 0:n.heartbeats)==null)||this._heartbeatsCache.lastSentHeartbeatDate===i||this._heartbeatsCache.heartbeats.some(a=>a.date===i))return;if(this._heartbeatsCache.heartbeats.push({date:i,agent:s}),this._heartbeatsCache.heartbeats.length>Or){const a=Br(this._heartbeatsCache.heartbeats);this._heartbeatsCache.heartbeats.splice(a,1)}return this._storage.overwrite(this._heartbeatsCache)}catch(r){E.warn(r)}})}getHeartbeatsHeader(){return c(this,null,function*(){var e;try{if(this._heartbeatsCache===null&&(yield this._heartbeatsCachePromise),((e=this._heartbeatsCache)==null?void 0:e.heartbeats)==null||this._heartbeatsCache.heartbeats.length===0)return"";const n=Fe(),{heartbeatsToSend:r,unsentEntries:s}=Pr(this._heartbeatsCache.heartbeats),i=Qe(JSON.stringify({version:2,heartbeats:r}));return this._heartbeatsCache.lastSentHeartbeatDate=n,s.length>0?(this._heartbeatsCache.heartbeats=s,yield this._storage.overwrite(this._heartbeatsCache)):(this._heartbeatsCache.heartbeats=[],this._storage.overwrite(this._heartbeatsCache)),i}catch(n){return E.warn(n),""}})}}function Fe(){return new Date().toISOString().substring(0,10)}function Pr(t,e=Nr){const n=[];let r=t.slice();for(const s of t){const i=n.find(a=>a.agent===s.agent);if(i){if(i.dates.push(s.date),He(n)>e){i.dates.pop();break}}else if(n.push({agent:s.agent,dates:[s.date]}),He(n)>e){n.pop();break}r=r.slice(1)}return{heartbeatsToSend:n,unsentEntries:r}}class Lr{constructor(e){this.app=e,this._canUseIndexedDBPromise=this.runIndexedDBEnvironmentCheck()}runIndexedDBEnvironmentCheck(){return c(this,null,function*(){return et()?tt().then(()=>!0).catch(()=>!1):!1})}read(){return c(this,null,function*(){if(yield this._canUseIndexedDBPromise){const n=yield vr(this.app);return n!=null&&n.heartbeats?n:{heartbeats:[]}}else return{heartbeats:[]}})}overwrite(e){return c(this,null,function*(){var r;if(yield this._canUseIndexedDBPromise){const s=yield this.read();return $e(this.app,{lastSentHeartbeatDate:(r=e.lastSentHeartbeatDate)!=null?r:s.lastSentHeartbeatDate,heartbeats:e.heartbeats})}else return})}add(e){return c(this,null,function*(){var r;if(yield this._canUseIndexedDBPromise){const s=yield this.read();return $e(this.app,{lastSentHeartbeatDate:(r=e.lastSentHeartbeatDate)!=null?r:s.lastSentHeartbeatDate,heartbeats:[...s.heartbeats,...e.heartbeats]})}else return})}}function He(t){return Qe(JSON.stringify({version:2,heartbeats:t})).length}function Br(t){if(t.length===0)return-1;let e=0,n=t[0].date;for(let r=1;r<t.length;r++)t[r].date<n&&(n=t[r].date,e=r);return e}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function xr(t){M(new k("platform-logger",e=>new Jn(e),"PRIVATE")),M(new k("heartbeat",e=>new Mr(e),"PRIVATE")),O(fe,xe,t),O(fe,xe,"esm2020"),O("fire-js","")}xr("");var Ur="firebase",$r="12.11.0";/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */O(Ur,$r,"app");const ct="@firebase/installations",we="0.6.21";/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const lt=1e4,ut=`w:${we}`,ht="FIS_v2",Fr="https://firebaseinstallations.googleapis.com/v1",Hr=3600*1e3,Kr="installations",jr="Installations";/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Vr={"missing-app-config-values":'Missing App configuration value: "{$valueName}"',"not-registered":"Firebase Installation is not registered.","installation-not-found":"Firebase Installation not found.","request-failed":'{$requestName} request failed with error "{$serverCode} {$serverStatus}: {$serverMessage}"',"app-offline":"Could not process request. Application offline.","delete-pending-registration":"Can't delete installation while there is a pending registration request."},D=new z(Kr,jr,Vr);function ft(t){return t instanceof P&&t.code.includes("request-failed")}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function dt({projectId:t}){return`${Fr}/projects/${t}/installations`}function pt(t){return{token:t.token,requestStatus:2,expiresIn:qr(t.expiresIn),creationTime:Date.now()}}function gt(t,e){return c(this,null,function*(){const r=(yield e.json()).error;return D.create("request-failed",{requestName:t,serverCode:r.code,serverMessage:r.message,serverStatus:r.status})})}function mt({apiKey:t}){return new Headers({"Content-Type":"application/json",Accept:"application/json","x-goog-api-key":t})}function Wr(t,{refreshToken:e}){const n=mt(t);return n.append("Authorization",zr(e)),n}function bt(t){return c(this,null,function*(){const e=yield t();return e.status>=500&&e.status<600?t():e})}function qr(t){return Number(t.replace("s","000"))}function zr(t){return`${ht} ${t}`}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function Gr(r,s){return c(this,arguments,function*({appConfig:t,heartbeatServiceProvider:e},{fid:n}){const i=dt(t),a=mt(t),o=e.getImmediate({optional:!0});if(o){const f=yield o.getHeartbeatsHeader();f&&a.append("x-firebase-client",f)}const l={fid:n,authVersion:ht,appId:t.appId,sdkVersion:ut},h={method:"POST",headers:a,body:JSON.stringify(l)},u=yield bt(()=>fetch(i,h));if(u.ok){const f=yield u.json();return{fid:f.fid||n,registrationStatus:2,refreshToken:f.refreshToken,authToken:pt(f.authToken)}}else throw yield gt("Create Installation",u)})}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function wt(t){return new Promise(e=>{setTimeout(e,t)})}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function Jr(t){return btoa(String.fromCharCode(...t)).replace(/\+/g,"-").replace(/\//g,"_")}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Yr=/^[cdef][\w-]{21}$/,ge="";function Xr(){try{const t=new Uint8Array(17);(self.crypto||self.msCrypto).getRandomValues(t),t[0]=112+t[0]%16;const n=Qr(t);return Yr.test(n)?n:ge}catch(t){return ge}}function Qr(t){return Jr(t).substr(0,22)}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function J(t){return`${t.appName}!${t.appId}`}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const yt=new Map;function _t(t,e){const n=J(t);It(n,e),Zr(n,e)}function It(t,e){const n=yt.get(t);if(n)for(const r of n)r(e)}function Zr(t,e){const n=es();n&&n.postMessage({key:t,fid:e}),ts()}let A=null;function es(){return!A&&"BroadcastChannel"in self&&(A=new BroadcastChannel("[Firebase] FID Change"),A.onmessage=t=>{It(t.data.key,t.data.fid)}),A}function ts(){yt.size===0&&A&&(A.close(),A=null)}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const ns="firebase-installations-database",rs=1,v="firebase-installations-store";let se=null;function ye(){return se||(se=G(ns,rs,{upgrade:(t,e)=>{switch(e){case 0:t.createObjectStore(v)}}})),se}function j(t,e){return c(this,null,function*(){const n=J(t),s=(yield ye()).transaction(v,"readwrite"),i=s.objectStore(v),a=yield i.get(n);return yield i.put(e,n),yield s.done,(!a||a.fid!==e.fid)&&_t(t,e.fid),e})}function Et(t){return c(this,null,function*(){const e=J(t),r=(yield ye()).transaction(v,"readwrite");yield r.objectStore(v).delete(e),yield r.done})}function Y(t,e){return c(this,null,function*(){const n=J(t),s=(yield ye()).transaction(v,"readwrite"),i=s.objectStore(v),a=yield i.get(n),o=e(a);return o===void 0?yield i.delete(n):yield i.put(o,n),yield s.done,o&&(!a||a.fid!==o.fid)&&_t(t,o.fid),o})}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function _e(t){return c(this,null,function*(){let e;const n=yield Y(t.appConfig,r=>{const s=ss(r),i=is(t,s);return e=i.registrationPromise,i.installationEntry});return n.fid===ge?{installationEntry:yield e}:{installationEntry:n,registrationPromise:e}})}function ss(t){const e=t||{fid:Xr(),registrationStatus:0};return Ct(e)}function is(t,e){if(e.registrationStatus===0){if(!navigator.onLine){const s=Promise.reject(D.create("app-offline"));return{installationEntry:e,registrationPromise:s}}const n={fid:e.fid,registrationStatus:1,registrationTime:Date.now()},r=as(t,n);return{installationEntry:n,registrationPromise:r}}else return e.registrationStatus===1?{installationEntry:e,registrationPromise:os(t)}:{installationEntry:e}}function as(t,e){return c(this,null,function*(){try{const n=yield Gr(t,e);return j(t.appConfig,n)}catch(n){throw ft(n)&&n.customData.serverCode===409?yield Et(t.appConfig):yield j(t.appConfig,{fid:e.fid,registrationStatus:0}),n}})}function os(t){return c(this,null,function*(){let e=yield Ke(t.appConfig);for(;e.registrationStatus===1;)yield wt(100),e=yield Ke(t.appConfig);if(e.registrationStatus===0){const{installationEntry:n,registrationPromise:r}=yield _e(t);return r||n}return e})}function Ke(t){return Y(t,e=>{if(!e)throw D.create("installation-not-found");return Ct(e)})}function Ct(t){return cs(t)?{fid:t.fid,registrationStatus:0}:t}function cs(t){return t.registrationStatus===1&&t.registrationTime+lt<Date.now()}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function ls(r,s){return c(this,arguments,function*({appConfig:t,heartbeatServiceProvider:e},n){const i=us(t,n),a=Wr(t,n),o=e.getImmediate({optional:!0});if(o){const f=yield o.getHeartbeatsHeader();f&&a.append("x-firebase-client",f)}const l={installation:{sdkVersion:ut,appId:t.appId}},h={method:"POST",headers:a,body:JSON.stringify(l)},u=yield bt(()=>fetch(i,h));if(u.ok){const f=yield u.json();return pt(f)}else throw yield gt("Generate Auth Token",u)})}function us(t,{fid:e}){return`${dt(t)}/${e}/authTokens:generate`}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function Ie(t,e=!1){return c(this,null,function*(){let n;const r=yield Y(t.appConfig,i=>{if(!St(i))throw D.create("not-registered");const a=i.authToken;if(!e&&ds(a))return i;if(a.requestStatus===1)return n=hs(t,e),i;{if(!navigator.onLine)throw D.create("app-offline");const o=gs(i);return n=fs(t,o),o}});return n?yield n:r.authToken})}function hs(t,e){return c(this,null,function*(){let n=yield je(t.appConfig);for(;n.authToken.requestStatus===1;)yield wt(100),n=yield je(t.appConfig);const r=n.authToken;return r.requestStatus===0?Ie(t,e):r})}function je(t){return Y(t,e=>{if(!St(e))throw D.create("not-registered");const n=e.authToken;return ms(n)?T(m({},e),{authToken:{requestStatus:0}}):e})}function fs(t,e){return c(this,null,function*(){try{const n=yield ls(t,e),r=T(m({},e),{authToken:n});return yield j(t.appConfig,r),n}catch(n){if(ft(n)&&(n.customData.serverCode===401||n.customData.serverCode===404))yield Et(t.appConfig);else{const r=T(m({},e),{authToken:{requestStatus:0}});yield j(t.appConfig,r)}throw n}})}function St(t){return t!==void 0&&t.registrationStatus===2}function ds(t){return t.requestStatus===2&&!ps(t)}function ps(t){const e=Date.now();return e<t.creationTime||t.creationTime+t.expiresIn<e+Hr}function gs(t){const e={requestStatus:1,requestTime:Date.now()};return T(m({},t),{authToken:e})}function ms(t){return t.requestStatus===1&&t.requestTime+lt<Date.now()}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function bs(t){return c(this,null,function*(){const e=t,{installationEntry:n,registrationPromise:r}=yield _e(e);return r?r.catch(console.error):Ie(e).catch(console.error),n.fid})}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function ws(t,e=!1){return c(this,null,function*(){const n=t;return yield ys(n),(yield Ie(n,e)).token})}function ys(t){return c(this,null,function*(){const{registrationPromise:e}=yield _e(t);e&&(yield e)})}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function _s(t){if(!t||!t.options)throw ie("App Configuration");if(!t.name)throw ie("App Name");const e=["projectId","apiKey","appId"];for(const n of e)if(!t.options[n])throw ie(n);return{appName:t.name,projectId:t.options.projectId,apiKey:t.options.apiKey,appId:t.options.appId}}function ie(t){return D.create("missing-app-config-values",{valueName:t})}/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Tt="installations",Is="installations-internal",Es=t=>{const e=t.getProvider("app").getImmediate(),n=_s(e),r=be(e,"heartbeat");return{app:e,appConfig:n,heartbeatServiceProvider:r,_delete:()=>Promise.resolve()}},Cs=t=>{const e=t.getProvider("app").getImmediate(),n=be(e,Tt).getImmediate();return{getId:()=>bs(n),getToken:s=>ws(n,s)}};function Ss(){M(new k(Tt,Es,"PUBLIC")),M(new k(Is,Cs,"PRIVATE"))}Ss();O(ct,we);O(ct,we,"esm2020");/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Rt="BDOU99-h67HcA6JeFXHbSNMu7e2yNNu3RzoMj8TM4W88jITfq7ZmPvIM1Iv-4_l2LxQcYwhqby2xGpWwzjfAnG4",Ts="https://fcmregistrations.googleapis.com/v1",At="FCM_MSG",Rs="google.c.a.c_id",As=3,ks=1;var V;(function(t){t[t.DATA_MESSAGE=1]="DATA_MESSAGE",t[t.DISPLAY_NOTIFICATION=3]="DISPLAY_NOTIFICATION"})(V||(V={}));/**
 * @license
 * Copyright 2018 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
 * in compliance with the License. You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under the License
 * is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
 * or implied. See the License for the specific language governing permissions and limitations under
 * the License.
 */var W;(function(t){t.PUSH_RECEIVED="push-received",t.NOTIFICATION_CLICKED="notification-clicked"})(W||(W={}));/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function w(t){const e=new Uint8Array(t);return btoa(String.fromCharCode(...e)).replace(/=/g,"").replace(/\+/g,"-").replace(/\//g,"_")}function Ds(t){const e="=".repeat((4-t.length%4)%4),n=(t+e).replace(/\-/g,"+").replace(/_/g,"/"),r=atob(n),s=new Uint8Array(r.length);for(let i=0;i<r.length;++i)s[i]=r.charCodeAt(i);return s}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const ae="fcm_token_details_db",vs=5,Ve="fcm_token_object_Store";function Ns(t){return c(this,null,function*(){if("databases"in indexedDB&&!(yield indexedDB.databases()).map(i=>i.name).includes(ae))return null;let e=null;return(yield G(ae,vs,{upgrade:(r,s,i,a)=>c(null,null,function*(){var h;if(s<2||!r.objectStoreNames.contains(Ve))return;const o=a.objectStore(Ve),l=yield o.index("fcmSenderId").get(t);if(yield o.clear(),!!l){if(s===2){const u=l;if(!u.auth||!u.p256dh||!u.endpoint)return;e={token:u.fcmToken,createTime:(h=u.createTime)!=null?h:Date.now(),subscriptionOptions:{auth:u.auth,p256dh:u.p256dh,endpoint:u.endpoint,swScope:u.swScope,vapidKey:typeof u.vapidKey=="string"?u.vapidKey:w(u.vapidKey)}}}else if(s===3){const u=l;e={token:u.fcmToken,createTime:u.createTime,subscriptionOptions:{auth:w(u.auth),p256dh:w(u.p256dh),endpoint:u.endpoint,swScope:u.swScope,vapidKey:w(u.vapidKey)}}}else if(s===4){const u=l;e={token:u.fcmToken,createTime:u.createTime,subscriptionOptions:{auth:w(u.auth),p256dh:w(u.p256dh),endpoint:u.endpoint,swScope:u.swScope,vapidKey:w(u.vapidKey)}}}}})})).close(),yield te(ae),yield te("fcm_vapid_details_db"),yield te("undefined"),Os(e)?e:null})}function Os(t){if(!t||!t.subscriptionOptions)return!1;const{subscriptionOptions:e}=t;return typeof t.createTime=="number"&&t.createTime>0&&typeof t.token=="string"&&t.token.length>0&&typeof e.auth=="string"&&e.auth.length>0&&typeof e.p256dh=="string"&&e.p256dh.length>0&&typeof e.endpoint=="string"&&e.endpoint.length>0&&typeof e.swScope=="string"&&e.swScope.length>0&&typeof e.vapidKey=="string"&&e.vapidKey.length>0}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Ms="firebase-messaging-database",Ps=1,N="firebase-messaging-store";let oe=null;function Ee(){return oe||(oe=G(Ms,Ps,{upgrade:(t,e)=>{switch(e){case 0:t.createObjectStore(N)}}})),oe}function Ce(t){return c(this,null,function*(){const e=Te(t),r=yield(yield Ee()).transaction(N).objectStore(N).get(e);if(r)return r;{const s=yield Ns(t.appConfig.senderId);if(s)return yield Se(t,s),s}})}function Se(t,e){return c(this,null,function*(){const n=Te(t),s=(yield Ee()).transaction(N,"readwrite");return yield s.objectStore(N).put(e,n),yield s.done,e})}function Ls(t){return c(this,null,function*(){const e=Te(t),r=(yield Ee()).transaction(N,"readwrite");yield r.objectStore(N).delete(e),yield r.done})}function Te({appConfig:t}){return t.appId}/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const Bs={"missing-app-config-values":'Missing App configuration value: "{$valueName}"',"only-available-in-window":"This method is available in a Window context.","only-available-in-sw":"This method is available in a service worker context.","permission-default":"The notification permission was not granted and dismissed instead.","permission-blocked":"The notification permission was not granted and blocked instead.","unsupported-browser":"This browser doesn't support the API's required to use the Firebase SDK.","indexed-db-unsupported":"This browser doesn't support indexedDb.open() (ex. Safari iFrame, Firefox Private Browsing, etc)","failed-service-worker-registration":"We are unable to register the default service worker. {$browserErrorMessage}","token-subscribe-failed":"A problem occurred while subscribing the user to FCM: {$errorInfo}","token-subscribe-no-token":"FCM returned no token when subscribing the user to push.","token-unsubscribe-failed":"A problem occurred while unsubscribing the user from FCM: {$errorInfo}","token-update-failed":"A problem occurred while updating the user from FCM: {$errorInfo}","token-update-no-token":"FCM returned no token when updating the user to push.","use-sw-after-get-token":"The useServiceWorker() method may only be called once and must be called before calling getToken() to ensure your service worker is used.","invalid-sw-registration":"The input to useServiceWorker() must be a ServiceWorkerRegistration.","invalid-bg-handler":"The input to setBackgroundMessageHandler() must be a function.","invalid-vapid-key":"The public VAPID key must be a string.","use-vapid-key-after-get-token":"The usePublicVapidKey() method may only be called once and must be called before calling getToken() to ensure your VAPID key is used."},g=new z("messaging","Messaging",Bs);/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function xs(t,e){return c(this,null,function*(){const n=yield Ae(t),r=Dt(e),s={method:"POST",headers:n,body:JSON.stringify(r)};let i;try{i=yield(yield fetch(Re(t.appConfig),s)).json()}catch(a){throw g.create("token-subscribe-failed",{errorInfo:a==null?void 0:a.toString()})}if(i.error){const a=i.error.message;throw g.create("token-subscribe-failed",{errorInfo:a})}if(!i.token)throw g.create("token-subscribe-no-token");return i.token})}function Us(t,e){return c(this,null,function*(){const n=yield Ae(t),r=Dt(e.subscriptionOptions),s={method:"PATCH",headers:n,body:JSON.stringify(r)};let i;try{i=yield(yield fetch(`${Re(t.appConfig)}/${e.token}`,s)).json()}catch(a){throw g.create("token-update-failed",{errorInfo:a==null?void 0:a.toString()})}if(i.error){const a=i.error.message;throw g.create("token-update-failed",{errorInfo:a})}if(!i.token)throw g.create("token-update-no-token");return i.token})}function kt(t,e){return c(this,null,function*(){const r={method:"DELETE",headers:yield Ae(t)};try{const i=yield(yield fetch(`${Re(t.appConfig)}/${e}`,r)).json();if(i.error){const a=i.error.message;throw g.create("token-unsubscribe-failed",{errorInfo:a})}}catch(s){throw g.create("token-unsubscribe-failed",{errorInfo:s==null?void 0:s.toString()})}})}function Re({projectId:t}){return`${Ts}/projects/${t}/registrations`}function Ae(n){return c(this,arguments,function*({appConfig:t,installations:e}){const r=yield e.getToken();return new Headers({"Content-Type":"application/json",Accept:"application/json","x-goog-api-key":t.apiKey,"x-goog-firebase-installations-auth":`FIS ${r}`})})}function Dt({p256dh:t,auth:e,endpoint:n,vapidKey:r}){const s={web:{endpoint:n,auth:e,p256dh:t}};return r!==Rt&&(s.web.applicationPubKey=r),s}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const $s=10080*60*1e3;function Fs(t){return c(this,null,function*(){const e=yield Ks(t.swRegistration,t.vapidKey),n={vapidKey:t.vapidKey,swScope:t.swRegistration.scope,endpoint:e.endpoint,auth:w(e.getKey("auth")),p256dh:w(e.getKey("p256dh"))},r=yield Ce(t.firebaseDependencies);if(r){if(js(r.subscriptionOptions,n))return Date.now()>=r.createTime+$s?Hs(t,{token:r.token,createTime:Date.now(),subscriptionOptions:n}):r.token;try{yield kt(t.firebaseDependencies,r.token)}catch(s){console.warn(s)}return qe(t.firebaseDependencies,n)}else return qe(t.firebaseDependencies,n)})}function We(t){return c(this,null,function*(){const e=yield Ce(t.firebaseDependencies);e&&(yield kt(t.firebaseDependencies,e.token),yield Ls(t.firebaseDependencies));const n=yield t.swRegistration.pushManager.getSubscription();return n?n.unsubscribe():!0})}function Hs(t,e){return c(this,null,function*(){try{const n=yield Us(t.firebaseDependencies,e),r=T(m({},e),{token:n,createTime:Date.now()});return yield Se(t.firebaseDependencies,r),n}catch(n){throw n}})}function qe(t,e){return c(this,null,function*(){const r={token:yield xs(t,e),createTime:Date.now(),subscriptionOptions:e};return yield Se(t,r),r.token})}function Ks(t,e){return c(this,null,function*(){const n=yield t.pushManager.getSubscription();return n||t.pushManager.subscribe({userVisibleOnly:!0,applicationServerKey:Ds(e)})})}function js(t,e){const n=e.vapidKey===t.vapidKey,r=e.endpoint===t.endpoint,s=e.auth===t.auth,i=e.p256dh===t.p256dh;return n&&r&&s&&i}/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function Vs(t){const e={from:t.from,collapseKey:t.collapse_key,messageId:t.fcmMessageId};return Ws(e,t),qs(e,t),zs(e,t),e}function Ws(t,e){if(!e.notification)return;t.notification={};const n=e.notification.title;n&&(t.notification.title=n);const r=e.notification.body;r&&(t.notification.body=r);const s=e.notification.image;s&&(t.notification.image=s);const i=e.notification.icon;i&&(t.notification.icon=i)}function qs(t,e){e.data&&(t.data=e.data)}function zs(t,e){var s,i,a,o,l;if(!e.fcmOptions&&!((s=e.notification)!=null&&s.click_action))return;t.fcmOptions={};const n=(o=(i=e.fcmOptions)==null?void 0:i.link)!=null?o:(a=e.notification)==null?void 0:a.click_action;n&&(t.fcmOptions.link=n);const r=(l=e.fcmOptions)==null?void 0:l.analytics_label;r&&(t.fcmOptions.analyticsLabel=r)}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function Gs(t){return typeof t=="object"&&!!t&&Rs in t}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function Js(t){return new Promise(e=>{setTimeout(e,t)})}function Ys(t,e){return c(this,null,function*(){const n=Xs(e,yield t.firebaseDependencies.installations.getId());Qs(t,n,e.productId)})}function Xs(t,e){var r,s;const n={};return t.from&&(n.project_number=t.from),t.fcmMessageId&&(n.message_id=t.fcmMessageId),n.instance_id=e,t.notification?n.message_type=V.DISPLAY_NOTIFICATION.toString():n.message_type=V.DATA_MESSAGE.toString(),n.sdk_platform=As.toString(),n.package_name=self.origin.replace(/(^\w+:|^)\/\//,""),t.collapse_key&&(n.collapse_key=t.collapse_key),n.event=ks.toString(),(r=t.fcmOptions)!=null&&r.analytics_label&&(n.analytics_label=(s=t.fcmOptions)==null?void 0:s.analytics_label),n}function Qs(t,e,n){const r={};r.event_time_ms=Math.floor(Date.now()).toString(),r.source_extension_json_proto3=JSON.stringify({messaging_client_event:e}),n&&(r.compliance_data=Zs(n)),t.logEvents.push(r)}function Zs(t){return{privacy_context:{prequest:{origin_associated_product_id:t}}}}/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function ei(t,e){return c(this,null,function*(){var s,i;const{newSubscription:n}=t;if(!n){yield We(e);return}const r=yield Ce(e.firebaseDependencies);yield We(e),e.vapidKey=(i=(s=r==null?void 0:r.subscriptionOptions)==null?void 0:s.vapidKey)!=null?i:Rt,yield Fs(e)})}function ti(t,e){return c(this,null,function*(){const n=si(t);if(!n)return;e.deliveryMetricsExportedToBigQueryEnabled&&(yield Ys(e,n));const r=yield vt();if(ai(r))return oi(r,n);if(n.notification&&(yield ci(ri(n))),!!e&&e.onBackgroundMessageHandler){const s=Vs(n);typeof e.onBackgroundMessageHandler=="function"?yield e.onBackgroundMessageHandler(s):e.onBackgroundMessageHandler.next(s)}})}function ni(t){return c(this,null,function*(){var a,o;const e=(o=(a=t.notification)==null?void 0:a.data)==null?void 0:o[At];if(e){if(t.action)return}else return;t.stopImmediatePropagation(),t.notification.close();const n=li(e);if(!n)return;const r=new URL(n,self.location.href),s=new URL(self.location.origin);if(r.host!==s.host)return;let i=yield ii(r);if(i?i=yield i.focus():(i=yield self.clients.openWindow(n),yield Js(3e3)),!!i)return e.messageType=W.NOTIFICATION_CLICKED,e.isFirebaseMessaging=!0,i.postMessage(e)})}function ri(t){const e=m({},t.notification);return e.data={[At]:t},e}function si({data:t}){if(!t)return null;try{return t.json()}catch(e){return null}}function ii(t){return c(this,null,function*(){const e=yield vt();for(const n of e){const r=new URL(n.url,self.location.href);if(t.host===r.host)return n}return null})}function ai(t){return t.some(e=>e.visibilityState==="visible"&&!e.url.startsWith("chrome-extension://"))}function oi(t,e){e.isFirebaseMessaging=!0,e.messageType=W.PUSH_RECEIVED;for(const n of t)n.postMessage(e)}function vt(){return self.clients.matchAll({type:"window",includeUncontrolled:!0})}function ci(t){var r;const{actions:e}=t,{maxActions:n}=Notification;return e&&n&&e.length>n&&console.warn(`This browser only supports ${n} actions. The remaining actions will not be displayed.`),self.registration.showNotification((r=t.title)!=null?r:"",t)}function li(t){var n,r,s;const e=(s=(n=t.fcmOptions)==null?void 0:n.link)!=null?s:(r=t.notification)==null?void 0:r.click_action;return e||(Gs(t.data)?self.location.origin:null)}/**
 * @license
 * Copyright 2019 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function ui(t){if(!t||!t.options)throw ce("App Configuration Object");if(!t.name)throw ce("App Name");const e=["projectId","apiKey","appId","messagingSenderId"],{options:n}=t;for(const r of e)if(!n[r])throw ce(r);return{appName:t.name,projectId:n.projectId,apiKey:n.apiKey,appId:n.appId,senderId:n.messagingSenderId}}function ce(t){return g.create("missing-app-config-values",{valueName:t})}/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */class hi{constructor(e,n,r){this.deliveryMetricsExportedToBigQueryEnabled=!1,this.onBackgroundMessageHandler=null,this.onMessageHandler=null,this.logEvents=[],this.isLogServiceStarted=!1;const s=ui(e);this.firebaseDependencies={app:e,appConfig:s,installations:n,analyticsProvider:r}}_delete(){return Promise.resolve()}}/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */const fi=t=>{const e=new hi(t.getProvider("app").getImmediate(),t.getProvider("installations-internal").getImmediate(),t.getProvider("analytics-internal"));return self.addEventListener("push",n=>{n.waitUntil(ti(n,e))}),self.addEventListener("pushsubscriptionchange",n=>{n.waitUntil(ei(n,e))}),self.addEventListener("notificationclick",n=>{n.waitUntil(ni(n))}),e};function di(){M(new k("messaging-sw",fi,"PUBLIC"))}/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function pi(){return c(this,null,function*(){return et()&&(yield tt())&&"PushManager"in self&&"Notification"in self&&ServiceWorkerRegistration.prototype.hasOwnProperty("showNotification")&&PushSubscription.prototype.hasOwnProperty("getKey")})}/**
 * @license
 * Copyright 2020 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function gi(t,e){if(self.document!==void 0)throw g.create("only-available-in-sw");return t.onBackgroundMessageHandler=e,()=>{t.onBackgroundMessageHandler=null}}/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */function mi(t=Ar()){return pi().then(e=>{if(!e)throw g.create("unsupported-browser")},e=>{throw g.create("indexed-db-unsupported")}),be(nt(t),"messaging-sw").getImmediate()}function bi(t,e){return t=nt(t),gi(t,e)}/**
 * @license
 * Copyright 2017 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */di();pn([{"revision":"34a2d60143d67f0213e3e9f0d95f896e","url":"index.html"},{"revision":"3cf99278b3f5d95cb37c68ce8af4debf","url":"frappe-push-notification.js"},{"revision":null,"url":"assets/index-DJ0zCELB.js"},{"revision":null,"url":"assets/index-BmmtojrS.css"},{"revision":"e0ed5c22464fa6b1e9ae957f068a9c81","url":"manifest.webmanifest"}]);fn();const ze=new URL(location).searchParams.get("config");try{if(ze){const t=it(JSON.parse(ze)),e=mi(t);bi(e,n=>{var i,a,o,l,h;const r=((i=n.notification)==null?void 0:i.title)||"New message",s={body:((a=n.notification)==null?void 0:a.body)||"",icon:"/assets/excom/excom/manifest/android-chrome-192x192.png",tag:((o=n.data)==null?void 0:o.thread_id)||"excom",data:{url:(l=n.data)!=null&&l.base_url?`${n.data.base_url}/excom`:"/excom"}};(h=n.data)!=null&&h.image&&(s.icon=n.data.image),self.registration.showNotification(r,s)})}}catch(t){console.log("Firebase init skipped or failed",t)}self.addEventListener("notificationclick",t=>{var n;t.notification.close();const e=((n=t.notification.data)==null?void 0:n.url)||"/excom";t.waitUntil(clients.openWindow(e))});self.skipWaiting();gn();
