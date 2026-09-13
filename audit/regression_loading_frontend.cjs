const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element() {
  const e = {style: {}, children: [], innerHTML: '', innerText: '', className: '',
    classList: {add(){},remove(){}},
    appendChild(child){ this.children.push(child); child.remove = () => this.children.splice(this.children.indexOf(child),1); },
    querySelectorAll(selector){ return this.children.filter(c => '.'+c.className === selector); },
    querySelector(selector){ return this.querySelectorAll(selector)[0] || null; },
    addEventListener(){}, removeEventListener(){}, pause(){}, load(){}, play(){return Promise.resolve();},
    setAttribute(name, value){this[name] = String(value);},
    removeAttribute(name){this[name]='';}, getAttribute(name){return this[name] || '';}
  };
  return e;
}
function env(state, dom) {
  const c = {AbortController, URL, performance, console, setTimeout, clearTimeout,
    location:{href:'http://localhost/'}, ArchivebateAppContext:{state,dom},
    document:{body:{style:{}},getElementById:()=>null,createElement:element,querySelectorAll:()=>[]},
    ArchivebatePerf:{measure(){},setPlaybackBusy(){}},
    ArchivebateVideoGrid:{
      renderVideoGrid(v){dom.videoGrid.children=[];v.forEach(()=>dom.videoGrid.appendChild(element()));},
      reconcilePage(v){dom.videoGrid.children=[];v.forEach(()=>dom.videoGrid.appendChild(element()));}
    },
  };
  c.window=c;
  vm.createContext(c);
  return c;
}
const load=(c,file)=>vm.runInContext(fs.readFileSync('static/'+file,'utf8'),c);
(async()=>{
  const state={videos:[],currentPage:1};
  const dom={videoGrid:element(),videoCount:element(),statPageVideos:element()};
  const c=env(state,dom);
  c.ArchivebateAPI={getJSON:async()=>{throw new Error('HTTP 500');}};
  load(c,'video-views.js');
  await c.ArchivebateVideoViews.loadHomeVideos(1);
  assert.equal(dom.videoGrid.querySelectorAll('.skeleton-card').length,0);
  assert(dom.videoCount.innerText.includes('Ponów'));
  const retry=dom.videoGrid.children.find(e=>e.className.includes('feed-retry'));
  assert(retry);
  c.ArchivebateAPI.getJSON=async()=>({snapshot_id:'ok',revision:1,videos:[{id:'one'}],complete:true});
  await retry.onclick();
  assert.equal(state.videos.length,1);
  assert.equal(dom.videoGrid.children.length,1);
  c.ArchivebateAPI.getJSON=async()=>{throw Object.assign(new Error('expired'),{status:409});};
  let calls=0;const get=c.ArchivebateAPI.getJSON;
  c.ArchivebateAPI.getJSON=(...a)=>{calls++;return get(...a);};
  await c.ArchivebateVideoViews.loadHomeVideos(1);
  assert.equal(calls,2,'A permanently expired snapshot must not recurse forever');

  // A busy but healthy backend may exceed the former 12s full-page budget.
  // The home handshake must accept a bounded first batch, then use the
  // existing stream to replace it with the complete page.
  const slowState={videos:[],currentPage:1,gridCardMap:new Map()};
  const slowDom={videoGrid:element(),videoCount:element(),statPageVideos:element()};
  const slow=env(slowState,slowDom);
  let requestedTimeout=0; let feedStream=null;
  const homeRequests=[];
  slow.ArchivebateAPI={getJSON:async(url,options)=>{
    homeRequests.push(url);
    requestedTimeout=options.timeoutMs;
    await new Promise(resolve=>setTimeout(resolve,20));
    return {
      snapshot_id:'1',catalog_revision:1,revision:1,
      videos:Array.from({length:16},(_,i)=>({id:`first_${i}`})),
      complete:true,catalog_complete:true,page_complete:false,
      video_count:560,group_count:560,page_count:2,has_more:true,updated_at:1
    };
  }};
  slow.EventSource=class {
    constructor(url){this.url=url;this.closed=false;feedStream=this;}
    close(){this.closed=true;}
  };
  load(slow,'video-views.js');
  await slow.ArchivebateVideoViews.loadHomeVideos(1);
  assert(requestedTimeout>12000,'home handshake must not reuse the old 12s timeout');
  assert.equal(slowState.videos.length,16,'first bounded batch should render immediately');
  assert.equal(homeRequests.length,1,'next-page prefetch must wait for the visible page to finish');
  assert(!slowDom.videoCount.innerText.includes('Nie udało się załadować'),'healthy delayed response must not enter feed error state');
  assert(feedStream && feedStream.url.includes('initial_items=16'),'home must continue through the existing feed stream');
  feedStream.onmessage({data:JSON.stringify({
    snapshot_id:'1',catalog_revision:1,revision:1,
    videos:Array.from({length:280},(_,i)=>({id:`full_${i}`})),
    complete:true,catalog_complete:true,page_complete:true,
    video_count:560,group_count:560,page_count:2,has_more:true,updated_at:2
  })});
  await new Promise(resolve=>setTimeout(resolve,25));
  assert.equal(slowState.videos.length,280,'stream must replace the bounded batch with the full page');
  assert.equal(feedStream.closed,true);
  assert(homeRequests.some(url=>url.includes('page=2')),'complete page should resume next-page prefetch');

  // Transport failure after visible data is an error, but it must preserve
  // the usable cards instead of replacing them with a false empty-view error.
  feedStream=null;
  await slow.ArchivebateVideoViews.loadHomeVideos(1,true);
  assert(feedStream);
  feedStream.onerror();
  assert.equal(slowState.videos.length,16);
  assert(slowDom.videoCount.innerText.includes('Nie udało się odświeżyć'));
  assert(!slowDom.videoCount.innerText.includes('Nie udało się załadować'));

  // A status timeout is availability information, not proof of an auth
  // failure. It must not be rendered as the misleading "Błąd sesji" label.
  const statusState={};
  const statusDom={userEmail:element(),statusDot:element()};
  const statusCtx={global:null,window:null,ArchivebateAppContext:{state:statusState,dom:statusDom},ArchivebateAPI:{getJSON:async()=>{throw Object.assign(new Error('slow'),{code:'timeout',status:408});}},setTimeout:fn=>{statusCtx._timers.push(fn);return fn;},clearTimeout(){},console,_timers:[]};
  statusCtx.global=statusCtx; statusCtx.window=statusCtx;
  vm.createContext(statusCtx); load(statusCtx,'account.js');
  await statusCtx.ArchivebateAccount.initUserStatus();
  for(let i=0;i<3;i++) await statusCtx._timers.shift()();
  assert.equal(statusDom.userEmail.innerText,'Status chwilowo niedostępny');
  assert.notEqual(statusDom.userEmail.innerText,'Błąd sesji');

  // Run the full modal module: immediate playback precedes slow details,
  // details do not restart it, retry bypasses stale prefetch data.
  const mState={};
  const video=element();let plays=0;video.play=()=>{plays++;return Promise.resolve();};
  const mDom={modalVideo:video,videoModal:element(),videoLoader:element(),modalCenterPlay:element()};
  mDom.videoLoader.innerHTML='spinner';
  const m=env(mState,mDom);let resolveDetails;const requests=[];
  m.ArchivebateAPI={getJSON:url=>{requests.push(url);return new Promise(r=>{resolveDetails=r;});},postJSON:async()=>({})};
  load(m,'video-modal.js');
  const opened=m.ArchivebateVideoModal.open({id:'a',username:'Fixture'});
  assert.equal(plays,1,'Playback starts without waiting for metadata');
  resolveDetails({id:'a',proxy_stream_url:'/api/video/stream?id=a',direct_url:'https://fixture/a'});
  await opened;assert.equal(plays,1,'Metadata must not reset the stream');
  m.ArchivebateVideoPrefetch={detailsCache:new Map([['a',{direct_url:'stale'}]]),prefetchVideoDetails(){throw new Error('Retry used stale prefetch');}};
  mDom.videoLoader.innerHTML='previous error';
  const retried=m.ArchivebateVideoModal.open({id:'a',username:'Fixture'},{forceRefresh:true});
  assert.equal(mDom.videoLoader.innerHTML,'spinner');
  assert(requests.at(-1).includes('force_refresh=true'));
  assert.equal(plays,1,'Retry waits for refreshed URL');
  resolveDetails({id:'a',proxy_stream_url:'/api/video/stream?id=a'});
  await retried;assert.equal(plays,2);assert(video.src.includes('retry='));
  const closed=mState.playerController;
  m.ArchivebateVideoModal.close();assert(closed.signal.aborted);

  // Expired-host quarantine is per VIDEO, never per grouped card/author.
  // If the representative of a 3-video group expires, the group must remain
  // and the next known member becomes its representative. Lazy groups with no
  // members loaded locally must also stay visible instead of losing 99 good videos.
  const prefStorage=new Map();
  const dead={id:'dead',source:'archivebate',username:'group'};
  const live1={id:'live1',source:'archivebate',username:'group',poster:'live1.jpg'};
  const live2={id:'live2',source:'archivebate',username:'group',poster:'live2.jpg'};
  const grouped={...dead,is_grouped:true,_isGrouped:true,group_count:3,_groupCount:3,
    grouped_videos:[dead,live1,live2],_groupedVideos:[dead,live1,live2]};
  const lazyGrouped={id:'lazy-dead',source:'archivebate',username:'lazy',is_grouped:true,_isGrouped:true,
    group_count:100,_groupCount:100,group_members_lazy:true,group_members_url:'/api/group/lazy'};
  const solo={id:'solo-dead',source:'archivebate',username:'solo'};
  const prefState={videos:[grouped,lazyGrouped,solo],gridCardMap:new Map()};
  const groupCard={dataset:{source:'archivebate',videoId:'dead'},_videoData:grouped,_cardKey:'group:author:group',_cardIndex:0,removed:false,
    remove(){this.removed=true;},_updateCard(v){this._videoData=v;}};
  const lazyCard={dataset:{source:'archivebate',videoId:'lazy-dead'},_videoData:lazyGrouped,_cardKey:'group:author:lazy',_cardIndex:1,removed:false,
    remove(){this.removed=true;},_updateCard(v){this._videoData=v;}};
  const soloCard={dataset:{source:'archivebate',videoId:'solo-dead'},_videoData:solo,_cardKey:'archivebate:id:solo-dead',removed:false,
    remove(){this.removed=true;}};
  const prefCtx={AbortController,console,setTimeout,clearTimeout,ArchivebateAppContext:{state:prefState,dom:{statPageVideos:{textContent:''}}},
    ArchivebateAPI:{getJSON:async()=>null},ArchivebatePerf:undefined,
    localStorage:{getItem:k=>prefStorage.get(k)||null,setItem:(k,v)=>prefStorage.set(k,String(v))},
    document:{querySelectorAll:sel=>sel==='.video-card'?[groupCard,lazyCard,soloCard]:[]},
    dispatchEvent(){},CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}}};
  prefCtx.window=prefCtx; prefCtx.globalThis=prefCtx;
  vm.createContext(prefCtx); load(prefCtx,'video-prefetch.js');
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('dead');
  assert.equal(groupCard.removed,false,'one expired representative must not remove the whole group');
  assert.equal(groupCard.dataset.videoId,'live1','next loaded member should be promoted as representative');
  assert.equal(grouped.group_count,2);
  assert.equal(grouped.grouped_videos.map(v=>v.id).join(','),'live1,live2');
  assert.equal(prefState.videos.includes(grouped),true);
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('lazy-dead');
  assert.equal(lazyCard.removed,false,'lazy 100-video group must survive an expired representative');
  assert.equal(lazyGrouped.group_count,99,'only the confirmed dead member is subtracted');
  assert.equal(prefState.videos.includes(lazyGrouped),true);
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('solo-dead');
  assert.equal(soloCard.removed,true,'ordinary expired video card should still disappear');
  assert.equal(prefState.videos.some(v=>v.id==='solo-dead'),false);

  // Stats arriving after a filtered feed must not replace its counts.
  const s=env({mode:'home',lastAppliedFeedRevision:1,videos:[{}]},
    {statPageVideos:element(),statCatalogVideos:{innerText:'7'},statCatalogVideosLbl:{innerText:'filtered'}});
  s.fetch=async()=>({ok:true,json:async()=>({catalog_videos:999,catalog_pages:4})});
  load(s,'home-stats.js');await s.ArchivebateHomeStats.update();
  assert.equal(s.ArchivebateAppContext.dom.statPageVideos.innerText,'1');
  assert.equal(s.ArchivebateAppContext.dom.statCatalogVideos.innerText,'7');

  load(m,'player-core.js');
  let cancelled=0;let frame;
  const frameVideo={requestVideoFrameCallback(cb){frame=cb;return 1;},cancelVideoFrameCallback(){cancelled++;}};
  const ac=new AbortController();
  const pending=m.ArchivebatePlayerCore.waitForPresentedFrame(frameVideo,undefined,30000,ac.signal);
  ac.abort();assert.equal(await pending,false);assert.equal(cancelled,1);
  assert.equal(await m.ArchivebatePlayerCore.waitForPresentedFrame(frameVideo,undefined,2),false,'Timeout is not a presented frame');
  const presented=m.ArchivebatePlayerCore.waitForPresentedFrame(frameVideo);frame();assert.equal(await presented,true);
  console.log('PASS: full feed error/retry, bounded 409, full modal immediate start/no reset/forced retry/close, grouped unavailable-video isolation, filtered stats, first-frame timeout and cancellation');
})().catch(e=>{console.error(e);process.exitCode=1;});
