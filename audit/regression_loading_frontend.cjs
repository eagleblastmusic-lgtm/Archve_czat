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
  const apiCalls=[];
  const apiContext={AbortController,URL,setTimeout,clearTimeout,navigator:{onLine:true},document:{querySelector:()=>({content:'fixture-mutation-token'})},fetch:async(url,init)=>{
    apiCalls.push({url,init});
    return {ok:true,status:200,json:async()=>({id:'method-fixture'})};
  }};
  apiContext.window=apiContext;vm.createContext(apiContext);load(apiContext,'api-client.js');
  await apiContext.ArchivebateAPI.refreshVideoDetails('id with space');
  assert.equal(apiCalls[0].init.method,'POST');
  assert(apiCalls[0].url.includes('/api/video/details/refresh?id=id%20with%20space'));
  assert.equal(apiCalls[0].init.headers['X-Archivebate-Mutation-Token'],'fixture-mutation-token');
  assert.equal(apiCalls[0].init.body,'{}');

  // Metadata consumers share work without inheriting a card's cancellation.
  const detailRequests=[];
  const detailsContext=env({},{});
  delete detailsContext.ArchivebatePerf;
  detailsContext.ArchivebateAPI={getJSON:(url,options)=>new Promise((resolve,reject)=>{
    detailRequests.push({url,signal:options.signal,resolve,reject});
    options.signal.addEventListener('abort',()=>reject(new Error('cancelled')),{once:true});
  })};
  load(detailsContext,'video-prefetch.js');
  const detailsAPI=detailsContext.ArchivebateVideoPrefetch;
  const cardSignal=new AbortController();
  const playerSignal=new AbortController();
  const cardDetails=detailsAPI.prefetchVideoDetails('shared-details',{signal:cardSignal.signal});
  const playerDetails=detailsAPI.prefetchVideoDetails('shared-details',{signal:playerSignal.signal});
  cardSignal.abort();
  assert.equal(await cardDetails,null);
  assert.equal(detailRequests.length,1);
  assert.equal(detailRequests[0].signal.aborted,false,'closing a card cannot cancel the active player');
  detailRequests[0].resolve({id:'shared-details',availability:'available',direct_url:'/fixture.mp4'});
  assert.equal((await playerDetails).id,'shared-details');
  const abandoned=new AbortController();
  const abandonedDetails=detailsAPI.prefetchVideoDetails('reopen-details',{signal:abandoned.signal});
  abandoned.abort();
  const reopenDetails=detailsAPI.prefetchVideoDetails('reopen-details');
  assert.equal(detailRequests.length,3,'reopening immediately must create fresh work after the last consumer aborts');
  assert.equal(detailRequests[1].signal.aborted,true);
  detailRequests[2].resolve({id:'reopen-details',availability:'available',direct_url:'/fixture.mp4'});
  assert.equal(await abandonedDetails,null);
  assert.equal((await reopenDetails).id,'reopen-details');
  assert.equal(detailsAPI.isDetailsInflight('reopen-details'),false);

  // The card module's actual group-members API shares one exact URL request;
  // cancelling one drawer leaves the other consumer alive, while cancelling
  // the final consumer aborts the underlying request.
  const memberRequests=[];
  const memberContext={AbortController,DOMException,URL,location:{href:'http://localhost/'},console,
    ArchivebateAPI:{getJSON:(url,options)=>new Promise((resolve,reject)=>{
      const request={url,signal:options.signal,resolve,reject};
      memberRequests.push(request);
      options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
    })}};
  memberContext.window=memberContext;vm.createContext(memberContext);load(memberContext,'video-card.js');
  const groupUrl='/api/catalog/groups/sharedmodel/members?source=only-archivebate&revision=9&author_filter=exclude_fav';
  const firstDrawer=new AbortController(),secondDrawer=new AbortController();
  const firstMembers=memberContext.ArchivebateVideoCard.getGroupMembers(groupUrl,firstDrawer.signal);
  const secondMembers=memberContext.ArchivebateVideoCard.getGroupMembers(groupUrl,secondDrawer.signal);
  assert.equal(memberRequests.length,1,'two open cards for the same scoped group share one request');
  firstDrawer.abort();
  await assert.rejects(firstMembers,{name:'AbortError'});
  assert.equal(memberRequests[0].signal.aborted,false,'one drawer closing cannot cancel another drawer consumer');
  const scopedMembers={revision:9,items:[{id:'member-1',source:'archivebate'}],total:1};
  memberRequests[0].resolve(scopedMembers);
  assert.equal(await secondMembers,scopedMembers,'the remaining drawer receives the matching scoped member page');
  const lastDrawer=new AbortController();
  const lastMembers=memberContext.ArchivebateVideoCard.getGroupMembers(groupUrl,lastDrawer.signal);
  assert.equal(memberRequests.length,2,'a settled request is removed so a later drawer can refresh');
  lastDrawer.abort();
  await assert.rejects(lastMembers,{name:'AbortError'});
  assert.equal(memberRequests[1].signal.aborted,true,'closing the final consumer cancels its underlying request');

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
  assert.equal(calls,3,'An expired snapshot gets one status read and one bounded feed retry');

  // Refresh follows the revision token returned by POST even when the first
  // GET sees an older publication. Its retry reloads that same token without
  // starting another catalog rebuild and the old visible cards stay in place.
  const refreshState={videos:[{id:'old-revision'}],currentPage:1,lastPage:1,groupByAuthor:false,sourceFilter:'all',authorFilter:'all',
    catalogRevision:8,feedSnapshotId:'8',feedSpecKey:'all|all|0|prefs:0',preferencesVersion:0,gridCardMap:new Map([['old',{}]]),
    lastAppliedFeedRevision:8,lastAppliedFeedUpdatedAt:1,lastAppliedFeedVideoCount:1,lastAppliedFeedPageComplete:true};
  const refreshDom={videoGrid:element(),videoCount:element(),statPageVideos:element(),contentHeader:element(),viewTitle:element(),refreshCatalogBtn:element()};
  refreshDom.videoGrid.appendChild(element());
  const refresh=env(refreshState,refreshDom);
  const refreshPosts=[];const refreshGets=[];
  refresh.ArchivebateAPI={
    postJSON:async(url,body,options)=>{refreshPosts.push({url,body,options});return {success:true,refresh_revision:9,refresh_pending:true};},
    getJSON:async(url)=>{
      refreshGets.push(url);
      if(refreshGets.length===1)return {catalog_revision:8,revision:8,snapshot_id:'8',videos:[{id:'old-revision'}],complete:true,catalog_complete:true,page_complete:true,video_count:1,group_count:1,page_count:1,preferences_version:0};
      return {catalog_revision:9,revision:9,snapshot_id:'9',videos:[{id:'new-revision'}],complete:true,catalog_complete:true,page_complete:true,video_count:1,group_count:1,page_count:1,updated_at:2,preferences_version:0};
    }
  };
  load(refresh,'video-views.js');
  await refresh.ArchivebateVideoViews.refreshCatalog(1);
  assert.equal(refreshPosts.length,1,'manual catalog refresh uses one POST');
  assert.equal(refreshState.requestedCatalogRevision,9,'the POST token remains tracked after a stale GET');
  assert.equal(refreshState.videos[0].id,'old-revision','a stale response cannot replace the visible revision');
  assert(refreshGets[0].includes('revision=9'),'the first GET explicitly follows the POST revision');
  const refreshRetry=refreshDom.videoGrid.children.find(child=>child.className.includes('feed-retry'));
  assert(refreshRetry && refreshRetry.textContent==='Ponów ładowanie','transport retry stays distinct from a new rebuild');
  await refreshRetry.onclick();
  assert.equal(refreshPosts.length,1,'retrying revision 9 must not POST a second rebuild');
  assert(refreshGets[1].includes('revision=9'),'the retry continues to follow revision 9');
  assert.equal(refreshState.catalogRevision,9);
  assert.equal(refreshState.requestedCatalogRevision,null);
  assert.equal(refreshState.videos[0].id,'new-revision');

  // A prefetch that resolves after a committed preference change cannot refill
  // the new cache generation with results from the old block/favorite scope.
  const cacheState={videos:[],currentPage:1,lastPage:2,groupByAuthor:false,sourceFilter:'all',authorFilter:'all',catalogRevision:null,
    feedSnapshotId:null,feedSpecKey:null,preferencesVersion:0,gridCardMap:new Map()};
  const cacheDom={videoGrid:element(),videoCount:element(),statPageVideos:element(),contentHeader:element(),viewTitle:element()};
  const cacheCtx=env(cacheState,cacheDom);
  let resolveStalePrefetch;let pageTwoCalls=0;
  cacheCtx.ArchivebateAPI={getJSON:async url=>{
    if(url.includes('page=1'))return {catalog_revision:1,revision:1,snapshot_id:'1',videos:[{id:'page-one'}],complete:true,catalog_complete:true,page_complete:true,video_count:2,group_count:2,page_count:2,preferences_version:0};
    pageTwoCalls++;
    if(pageTwoCalls===1)return new Promise(resolve=>{resolveStalePrefetch=resolve;});
    return {catalog_revision:1,revision:1,snapshot_id:'1',videos:[{id:'fresh-page-two'}],complete:true,catalog_complete:true,page_complete:true,video_count:2,group_count:2,page_count:2,preferences_version:1};
  }};
  load(cacheCtx,'video-views.js');
  await cacheCtx.ArchivebateVideoViews.loadHomeVideos(1);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(pageTwoCalls,1,'the adjacent page prefetch started once');
  cacheCtx.ArchivebateVideoViews.preferencesChanged(1);
  resolveStalePrefetch({catalog_revision:1,revision:1,snapshot_id:'1',videos:[{id:'stale-page-two'}],complete:true,catalog_complete:true,page_complete:true,video_count:2,group_count:2,page_count:2,preferences_version:0});
  await new Promise(resolve=>setImmediate(resolve));
  await cacheCtx.ArchivebateVideoViews.loadHomeVideos(2);
  assert.equal(pageTwoCalls,2,'page two is fetched again after preferences invalidate the old prefetch');
  assert.equal(cacheState.videos[0].id,'fresh-page-two','a late old-scope response never becomes the rendered page');

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
  await slow.ArchivebateVideoViews.loadHomeVideos(1,{retryLoad:true});
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
  m.ArchivebateAPI={
    getJSON:url=>{requests.push({method:'GET',url});return new Promise(r=>{resolveDetails=r;});},
    postJSON:async(url)=>{requests.push({method:'POST',url});return {id:'a',proxy_stream_url:'/api/video/stream?id=a'};},
    refreshVideoDetails:(id)=>{requests.push({method:'POST',url:`/api/video/details/refresh?id=${encodeURIComponent(id)}`});return Promise.resolve({id:'a',proxy_stream_url:'/api/video/stream?id=a'});}
  };
  load(m,'video-modal.js');
  const opened=m.ArchivebateVideoModal.open({id:'a',username:'Fixture'});
  assert.equal(plays,1,'Playback starts without waiting for metadata');
  resolveDetails({id:'a',proxy_stream_url:'/api/video/stream?id=a',direct_url:'https://fixture/a'});
  await opened;assert.equal(plays,1,'Metadata must not reset the stream');
  m.ArchivebateVideoPrefetch={detailsCache:new Map([['a',{direct_url:'stale'}]]),prefetchVideoDetails(){throw new Error('Retry used stale prefetch');}};
  mDom.videoLoader.innerHTML='previous error';
  const retried=m.ArchivebateVideoModal.open({id:'a',username:'Fixture'},{forceRefresh:true});
  assert.equal(mDom.videoLoader.innerHTML,'spinner');
  assert.equal(requests.at(-1).method,'POST');
  assert(requests.at(-1).url.includes('/api/video/details/refresh?id=a'));
  assert.equal(plays,1,'Retry waits for refreshed URL');
  resolveDetails({id:'a',proxy_stream_url:'/api/video/stream?id=a'});
  await retried;assert.equal(plays,2);assert(video.src.includes('retry='));
  const closed=mState.playerController;
  m.ArchivebateVideoModal.close();assert(closed.signal.aborted);

  // Expired-host quarantine is per VIDEO, never per grouped card/author.
  // If the representative of a 3-video group expires, the group must remain
  // and the next known member becomes its representative. Lazy groups with no
  // members loaded locally must also stay visible instead of losing 99 good videos.
  const nowMs=Date.now();
  const prefStorage=new Map([
    ['archivebate_unavailable_videos_v1',JSON.stringify({'archivebate:id:legacy-dead':nowMs+3600000})],
    ['archivebate_unavailable_videos_v2',JSON.stringify({version:2,entries:{
      'archivebate:id:unproven':{reason:'source_page_not_found',checked_at:null,attempted_at:nowMs,expires_at:nowMs+3600000},
      'archivebate:id:expired':{reason:'source_page_not_found',checked_at:nowMs,attempted_at:nowMs-5000,expires_at:nowMs-1},
      'camwhores:id:good':{reason:'source_page_not_found',checked_at:nowMs,attempted_at:nowMs,expires_at:nowMs+3600000}
    }})]
  ]);
  const dead={id:'dead',source:'archivebate',username:'group'};
  const live1={id:'live1',source:'archivebate',username:'group',poster:'live1.jpg'};
  const live2={id:'live2',source:'archivebate',username:'group',poster:'live2.jpg'};
  const grouped={...dead,is_grouped:true,_isGrouped:true,group_count:3,_groupCount:3,
    grouped_videos:[dead,live1,live2],_groupedVideos:[dead,live1,live2]};
  const lazyGrouped={id:'lazy-dead',source:'archivebate',username:'lazy',is_grouped:true,_isGrouped:true,
    group_count:100,_groupCount:100,group_members_lazy:true,group_members_url:'/api/group/lazy'};
  const twoToOne={id:'two-dead',source:'archivebate',username:'two',is_grouped:true,_isGrouped:true,
    group_count:2,_groupCount:2,group_members_lazy:true,group_members_url:'/api/group/two',revision:4,
    grouped_videos:[{id:'two-dead',source:'archivebate'},{id:'two-live',source:'archivebate'}]};
  const foreignLazy={id:'foreign-leader',source:'archivebate',username:'foreign',is_grouped:true,
    group_count:100,_groupCount:100,group_members_lazy:true,group_members_url:'/api/group/foreign'};
  const sourceCollision={id:'dead',source:'camwhores',username:'cam',is_grouped:true,group_count:2,
    grouped_videos:[{id:'dead',source:'camwhores'},{id:'cw-live',source:'camwhores'}]};
  const solo={id:'solo-dead',source:'archivebate',username:'solo'};
  const prefState={videos:[grouped,lazyGrouped,twoToOne,foreignLazy,sourceCollision,solo],gridCardMap:new Map()};
  const groupCard={dataset:{source:'archivebate',videoId:'dead'},_videoData:grouped,_cardKey:'group:author:group',_cardIndex:0,removed:false,
    remove(){this.removed=true;},_updateCard(v){this._videoData=v;}};
  const lazyCard={dataset:{source:'archivebate',videoId:'lazy-dead'},_videoData:lazyGrouped,_cardKey:'group:author:lazy',_cardIndex:1,removed:false,
    remove(){this.removed=true;},_updateCard(v){this._videoData=v;}};
  const soloCard={dataset:{source:'archivebate',videoId:'solo-dead'},_videoData:solo,_cardKey:'archivebate:id:solo-dead',removed:false,
    remove(){this.removed=true;}};
  const foreignCard={dataset:{source:'archivebate',videoId:'foreign-leader'},_videoData:foreignLazy,_cardKey:'group:author:foreign',removed:false,
    remove(){this.removed=true;},_updateCard(v){this._videoData=v;}};
  const collisionCard={dataset:{source:'camwhores',videoId:'dead'},_videoData:sourceCollision,_cardKey:'group:author:cam',removed:false,
    remove(){this.removed=true;},_updateCard(v){this._videoData=v;}};
  const prefCtx={AbortController,console,setTimeout,clearTimeout,ArchivebateAppContext:{state:prefState,dom:{statPageVideos:{textContent:''}}},
    ArchivebateAPI:{getJSON:async()=>null,postJSON:async(_url,_body)=>({id:'unused',availability:'available',availability_reason:'direct_stream',checked_at:Date.now()/1000,direct_url:'https://fixture.invalid/live',source:'archivebate'})},ArchivebatePerf:undefined,
    localStorage:{getItem:k=>prefStorage.get(k)||null,setItem:(k,v)=>prefStorage.set(k,String(v)),removeItem:k=>prefStorage.delete(k)},
    document:{querySelectorAll:sel=>sel==='.video-card'?[groupCard,lazyCard,soloCard,foreignCard,collisionCard]:[]},
    dispatchEvent(){},CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}}};
  prefCtx.window=prefCtx; prefCtx.globalThis=prefCtx;
  vm.createContext(prefCtx); load(prefCtx,'video-prefetch.js');
  const proof=(id,source='archivebate',checkedAt=Date.now()/1000)=>({id,source,availability:'unavailable',availability_reason:'source_page_not_found',checked_at:checkedAt,retryable:false});
  assert.equal(prefStorage.has('archivebate_unavailable_videos_v1'),false,'v1 registry must be invalidated');
  assert(prefStorage.has('archivebate_unavailable_v1_diagnostic')===false,'migration uses the versioned diagnostic key');
  assert(prefStorage.has('archivebate_unavailable_videos_v1_diagnostic'),'old quarantine is preserved only as a diagnostic export');
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo('legacy-dead'),false,'unverified v1 entries must not hide cards');
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo({id:'unproven',source:'archivebate'}),false,'v2 entries without source-check evidence must not hide cards');
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo({id:'expired',source:'archivebate'}),false,'expired quarantine entries must be ignored');
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo({id:'good',source:'camwhores'}),true,'a well-formed source-scoped v2 proof remains active');
  assert.equal(prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('dead',{...proof('dead')}),true);
  assert.equal(groupCard.removed,false,'one expired representative must not remove the whole group');
  assert.equal(groupCard.dataset.videoId,'live1','next loaded member should be promoted as representative');
  assert.equal(grouped.group_count,2);
  assert.equal(grouped.grouped_videos.map(v=>v.id).join(','),'live1,live2');
  assert.equal(prefState.videos.includes(grouped),true);
  assert.equal(sourceCollision.id,'dead','same raw ID in a different source is untouched');
  assert.equal(sourceCollision.group_count,2);
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('lazy-dead',proof('lazy-dead'));
  assert.equal(lazyCard.removed,false,'lazy 100-video group must survive an expired representative');
  assert.equal(lazyGrouped.group_count,99,'only the confirmed dead member is subtracted');
  assert.equal(prefState.videos.includes(lazyGrouped),true);
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('foreign-other',proof('foreign-other'));
  assert.equal(foreignLazy.group_count,100,'an unrelated ID is a strict no-op for a lazy group');
  assert.equal(foreignLazy._unavailableMemberKeys,undefined);
  assert.equal(foreignCard.removed,false);
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('two-dead',proof('two-dead'));
  assert.equal(twoToOne.group_count,1,'2 -> 1 retains the remaining member');
  assert.equal(twoToOne.is_grouped,true,'aggregate identity survives 2 -> 1');
  load(prefCtx,'video-grid.js');
  assert(prefCtx.ArchivebateVideoGrid.filterKnownUnavailableVideos([twoToOne]).includes(twoToOne),'render filtering must preserve lazy aggregate with one member');
  assert.equal(twoToOne.id,'two-live','group leader becomes the remaining verified member');
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('two-dead',proof('two-dead', 'archivebate', Date.now()/1000 + 1));
  assert.equal(twoToOne.group_count,1,'repeated quarantine event cannot subtract twice');
  assert.equal(sourceCollision.id,'dead','Archivebate quarantine cannot mutate Camwhores identity');
  assert.equal(sourceCollision.group_count,2);
  const lateSse={id:'two-dead',source:'archivebate',username:'two',group_count:2,_groupCount:2,is_grouped:true,group_members_lazy:true,
    grouped_videos:[{id:'two-dead',source:'archivebate'},{id:'two-live',source:'archivebate'}]};
  assert(prefCtx.ArchivebateVideoGrid.filterKnownUnavailableVideos([lateSse]).includes(lateSse),'late feed reconciliation must retain a group after its leader quarantine');
  assert.equal(lateSse.id,'two-live','late SSE group promotes a member excluded by the local quarantine');
  assert.equal(lateSse.group_count,1,'late SSE subtracts the dead member once');
  prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('solo-dead',proof('solo-dead'));
  assert.equal(soloCard.removed,true,'ordinary expired video card should still disappear');
  assert.equal(prefState.videos.some(v=>v.id==='solo-dead'),false);

  const embedProof = {...proof('embed-dead'), availability_reason:'embed_file_not_found'};
  assert(prefCtx.ArchivebateVideoPrefetch.detailsAreUnavailable(embedProof));
  assert(prefCtx.ArchivebateVideoPrefetch.markUnavailableVideo('embed-dead',embedProof));
  load(prefCtx,'video-prefetch.js');
  assert(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo('embed-dead'), 'embed removal must survive page reload');

  // Confirmation makes two fresh POST reads with time separation; a failed
  // second transmission or a late negative response after a positive read is ignored.
  const unavailable=(id,checkedAt)=>({...proof(id,'archivebate',checkedAt)});
  let confirmedPosts=0;
  const confirmedTimes=[];
  prefCtx.ArchivebateAPI.postJSON=async url=>{
    assert(url.includes('/api/video/details/refresh?id='));
    confirmedPosts++;
    confirmedTimes.push(Date.now());
    return unavailable('confirm-dead',Date.now()/1000 + confirmedPosts);
  };
  const confirmedResult=await prefCtx.ArchivebateVideoPrefetch.confirmUnavailableVideo('confirm-dead',unavailable('confirm-dead',Date.now()/1000));
  assert.equal(confirmedPosts,2);
  assert(confirmedTimes[1]-confirmedTimes[0]>=1400,'proof attempts must be separated in time');
  assert.equal(confirmedResult.availability,'unavailable');
  assert(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo({id:'confirm-dead',source:'archivebate'}));
  const storedProof=JSON.parse(prefStorage.get('archivebate_unavailable_videos_v2'));
  assert(!JSON.stringify(storedProof).includes('fixture.invalid/live'),'persistent quarantine data must not retain stream URLs');

  let resolveLate;
  prefCtx.ArchivebateAPI.postJSON=()=>new Promise(resolve=>{resolveLate=resolve;});
  const lateCheck=prefCtx.ArchivebateVideoPrefetch.confirmUnavailableVideo('race-dead',unavailable('race-dead',Date.now()/1000));
  prefCtx.ArchivebateVideoPrefetch.setVideoDetails('race-dead',{id:'race-dead',source:'archivebate',availability:'available',direct_url:'https://fixture.invalid/live'});
  resolveLate(unavailable('race-dead',Date.now()/1000 + 1));
  await lateCheck;
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo('race-dead'),false,'a late negative result cannot override positive availability');

  let resolveCancelled;let cancelledPosts=0;
  prefCtx.ArchivebateAPI.postJSON=()=>{cancelledPosts++;return new Promise(resolve=>{resolveCancelled=resolve;});};
  const cancelledController=new AbortController();
  const cancelledCheck=prefCtx.ArchivebateVideoPrefetch.confirmUnavailableVideo('cancelled-dead',unavailable('cancelled-dead',Date.now()/1000),cancelledController.signal);
  cancelledController.abort();
  resolveCancelled(unavailable('cancelled-dead',Date.now()/1000+1));
  await cancelledCheck;
  assert.equal(cancelledPosts,1,'cancellation must stop the confirmation before a second POST');
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo({id:'cancelled-dead',source:'archivebate'}),false,'a cancelled response cannot quarantine a video');

  let secondAttempt=0;
  prefCtx.ArchivebateAPI.postJSON=async()=>{
    secondAttempt++;
    if(secondAttempt===1)return unavailable('second-fails',Date.now()/1000);
    throw new Error('second transmission failed');
  };
  await prefCtx.ArchivebateVideoPrefetch.confirmUnavailableVideo('second-fails',unavailable('second-fails',Date.now()/1000));
  assert.equal(secondAttempt,2);
  assert.equal(prefCtx.ArchivebateVideoPrefetch.isKnownUnavailableVideo({id:'second-fails',source:'archivebate'}),false,'a second transmission failure cannot confirm the first result');

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
  // A slow online profile must show the local author index first. Changing
  // profile invalidates both late local and remote responses from the old view.
  const profileState={mode:'home',videos:[],currentPage:1,lastPage:1,preferencesVersion:1};
  const profileDom={videoGrid:element(),viewTitle:element(),videoCount:element(),contentHeader:element()};
  const profiles=env(profileState,profileDom);
  profiles.document.documentElement={scrollTop:0};
  const profileRequests=[];
  profiles.ArchivebateAPI={getJSON:(url,options)=>new Promise(resolve=>profileRequests.push({url,signal:options.signal,resolve}))};
  load(profiles,'video-views.js');
  const firstProfile=profiles.ArchivebateVideoViews.loadModelVideos('first',1);
  assert.equal(profileRequests.length,2);
  profileRequests[0].resolve({videos:[{id:'saved-first'}]});
  await Promise.resolve(); await Promise.resolve();
  assert.equal(profileState.videos[0].id,'saved-first','local cards are visible while the provider request is pending');
  const secondProfile=profiles.ArchivebateVideoViews.loadModelVideos('second',1);
  assert(profileRequests[1].signal.aborted,'switching profile cancels the old provider work');
  profileRequests[1].resolve({videos:[{id:'late-first'}]});
  await firstProfile;
  assert(!profileState.videos.some(v=>v.id==='late-first'),'a late old profile cannot overwrite the new view');
  profileRequests[3].resolve({videos:[{id:'online-second'}]});
  await secondProfile;
  profileRequests[2].resolve({videos:[{id:'late-saved-second'}]});
  await Promise.resolve(); await Promise.resolve();
  assert.equal(profileState.videos[0].id,'online-second','late local cache cannot replace the completed online result');
  const thirdProfile=profiles.ArchivebateVideoViews.loadModelVideos('second',1);
  assert.equal(profileState.videos[0].id,'online-second','reopening the same profile paints from memory synchronously');
  assert.equal(profileRequests.length,5,'a warm profile only needs the online refresh');
  profileRequests[4].resolve({videos:[{id:'refreshed-second'}]}); await thirdProfile;
  const forgedStreamMissing={id:'unverified-file',availability:'unavailable',availability_reason:'stream_file_not_found',retryable:false,checked_at:Date.now()/1000};
  assert.equal(detailsAPI.detailsAreUnavailable(forgedStreamMissing),false,'stream removal requires independent server probes');
  assert.equal(detailsAPI.detailsAreUnavailable({...forgedStreamMissing,stream_missing_confirmations:2}),true);
  console.log('PASS: loading, cancellation, profile cache ordering, native stream-removal proof, quarantine, first-frame and retry contracts');
})().catch(e=>{console.error(e);process.exitCode=1;});
