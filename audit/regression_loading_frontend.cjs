const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element() {
  const e = {style: {}, children: [], innerHTML: '', innerText: '', className: '',
    classList: {add(){},remove(){}},
    appendChild(child){ this.children.push(child); child.remove = () => this.children.splice(this.children.indexOf(child),1); },
    querySelectorAll(selector){ return this.children.filter(c => '.'+c.className === selector); },
    querySelector(selector){ return this.querySelectorAll(selector)[0] || null; },
    addEventListener(){}, pause(){}, load(){}, play(){return Promise.resolve();},
    removeAttribute(name){this[name]='';}, getAttribute(name){return this[name] || '';}
  };
  return e;
}
function env(state, dom) {
  const c = {AbortController, URL, performance, console, setTimeout, clearTimeout,
    location:{href:'http://localhost/'}, ArchivebateAppContext:{state,dom},
    document:{body:{style:{}},getElementById:()=>null,createElement:element,querySelectorAll:()=>[]},
    ArchivebatePerf:{measure(){},setPlaybackBusy(){}},
    ArchivebateVideoGrid:{reconcilePage(v){dom.videoGrid.children=[];v.forEach(()=>dom.videoGrid.appendChild(element()));}},
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
  console.log('PASS: full feed error/retry, bounded 409, full modal immediate start/no reset/forced retry/close, filtered stats, first-frame timeout and cancellation');
})().catch(e=>{console.error(e);process.exitCode=1;});
