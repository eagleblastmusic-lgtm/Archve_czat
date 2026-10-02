const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const prefetch = fs.readFileSync('static/video-prefetch.js', 'utf8');
assert.match(prefetch, /lazyThumbObserver\.observe\(img\)/, 'offscreen thumbnails must be observer-driven');
assert.match(prefetch, /global\.lazyThumbObserver = lazyThumbObserver/, 'view changes must be able to disconnect stale thumbnail observers');
assert.match(prefetch, /const warmCount = Math\.min\(Math\.max\(0, count\), 12\)/, 'thumbnail warmup must remain bounded');
assert.match(prefetch, /generationController\.signal\.aborted \|\| thumbnailWarmupController !== generationController/, 'delayed warmup must be owned by the generation that scheduled it');
assert.match(prefetch, /if \(lazyThumbObserver\)[\s\S]*?observe\(img\)[\s\S]*?return;[\s\S]*?img\.src = src/, 'immediate src assignment must be fallback-only when IntersectionObserver is unavailable');

const runtime = fs.readFileSync('runtime_app.py', 'utf8');
const resilience = fs.readFileSync('static/lazy-thumbnail-resilience.js', 'utf8');
assert.match(runtime, /lazy-thumbnail-resilience\.js\?v=1/, 'release runtime must inject thumbnail scroll resilience');
assert.match(runtime, /"lazy_thumbnail_scroll_resilience": True/, 'runtime marker must expose thumbnail resilience');
assert.match(resilience, /\.thumbnail-img\[data-src\]/, 'resilience layer must target only pending lazy thumbnails');
assert.match(resilience, /getBoundingClientRect\(\)/, 'thumbnail rescue must remain viewport-bounded');
assert.match(resilience, /PRELOAD_MARGIN_PX = 900/, 'thumbnail rescue must preload only a bounded viewport margin');
assert.match(resilience, /requestAnimationFrame/, 'scroll recovery must be frame-throttled');
assert.match(resilience, /addEventListener\?\.\('scroll'/, 'scrolling must trigger a lazy-thumbnail recovery scan');
assert.match(resilience, /MutationObserver/, 'dynamically appended feed cards must also be recovered');
assert.doesNotMatch(resilience, /querySelectorAll\?\.\('\.thumbnail-img'\)(?!\[data-src\])/, 'resilience must not eagerly promote every thumbnail');

const stats = fs.readFileSync('static/home-stats.js', 'utf8');
assert.match(stats, /Indeksowanie trwa/, 'incomplete catalog must clearly say indexing is still running');
assert.match(stats, /Gotowe/, 'complete catalog must clearly say indexing has finished');
assert.match(stats, /ostatni zapis/, 'stale incomplete indexing should expose last-write age');

// Execute the actual warmup API with deferred idle callbacks. Replacing view A
// with B before idle must cancel A before it starts any thumbnail request.
const idleCallbacks=[];
const warmed=[];
const warmContext={
  AbortController,URL,Map,Set,Date,Math,Promise,console,
  setTimeout,clearTimeout:()=>{},
  ArchivebatePerf:{
    LRUCache:class{constructor(){this.values=new Map();}get(k){return this.values.get(k);}set(k,v){this.values.set(k,v);}has(k){return this.values.has(k);}delete(k){return this.values.delete(k);}},
    idle(callback){idleCallbacks.push(callback);return 987654;},
    prefetchUrls(urls,options){warmed.push({urls,signal:options.signal});return Promise.resolve();}
  },
  localStorage:{getItem(){return null;},setItem(){},removeItem(){}}
};
warmContext.window=warmContext;
vm.createContext(warmContext);
vm.runInContext(prefetch,warmContext);
const thumbnailSet=prefix=>Array.from({length:16},(_,i)=>({id:`${prefix}-${i}`,source:'archivebate',poster:`https://fixture.invalid/${prefix}-${i}.jpg`}));
warmContext.ArchivebateVideoPrefetch.scheduleThumbnailWarmup(thumbnailSet('view-a'),0,12);
warmContext.ArchivebateVideoPrefetch.scheduleThumbnailWarmup(thumbnailSet('view-b'),0,12);
idleCallbacks.forEach(callback=>callback());
assert.equal(warmed.length,1,'only the current view may start its warmup');
assert(warmed[0].urls.every(url=>url.includes('view-b-')),'an idle callback from a stale view must not fetch its thumbnails');

// A blank src is recoverable. Promoting only intersecting cards must actually
// start the image instead of applying a second native lazy-loading gate.
let onIntersection;
const observed = new Set();
const imageContext = {...warmContext, IntersectionObserver: class {
  constructor(callback, options) { if (options?.rootMargin) onIntersection = callback; }
  observe(img) { observed.add(img); }
  unobserve(img) { observed.delete(img); }
}};
imageContext.window = imageContext;
vm.createContext(imageContext);
vm.runInContext(prefetch,imageContext);
const pending = {dataset:{src:'/fixture.jpg'},src:'',getAttribute(){return this.src;}};
imageContext.ArchivebateVideoPrefetch.armLazyThumbnail(pending);
assert.equal(pending.src,'');
onIntersection([{target:pending,isIntersecting:false}],imageContext.lazyThumbObserver);
assert.equal(pending.src,'','offscreen thumbnails stay deferred');
onIntersection([{target:pending,isIntersecting:true}],imageContext.lazyThumbObserver);
assert.equal(pending.src,'/fixture.jpg');
assert.equal(pending.loading,'eager');
assert.equal(pending.dataset.src,undefined);
assert.equal(observed.size,0);

console.log('PASS: home first-paint thumbnails stay lazy, scroll recovery is resilient, and catalog completion state is explicit');
