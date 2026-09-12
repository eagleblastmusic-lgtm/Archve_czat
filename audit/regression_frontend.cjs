const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const scheduled=[],children=[];
const ctx={AbortController,state:{mode:'home',groupByAuthor:false,gridCardMap:new Map(),gridGeneration:0},dom:{videoGrid:{children,childNodes:children,set innerHTML(v){if(v==='')children.length=0},appendChild(f){(f.children||[]).forEach(v=>children.push(v))},querySelectorAll(){return[]}}},document:{createDocumentFragment:()=>({children:[],childNodes:[],appendChild(v){this.children.push(v);this.childNodes.push(v)}})},updateCheckpointUI(){},checkAndHighlightCheckpoint(){},window:null,setTimeout:cb=>{const timer={cb};scheduled.push(timer);return timer},clearTimeout() {}};
ctx.window=ctx;
ctx.ArchivebateAppContext={state:ctx.state,dom:ctx.dom};
ctx.createVideoCard=(v,idx)=>({_videoData:{...v},_cardIndex:idx,classList:{add(){},remove(){},toggle(){}},querySelectorAll(){return[]},remove(){const i=children.indexOf(this);if(i>=0)children.splice(i,1)},_updateCard(next,nextIdx){Object.assign(this._videoData,next);this._cardIndex=nextIdx}});
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/video-grid.js','utf8'),ctx);
ctx.ArchivebateVideoGrid.init({createVideoCard:ctx.createVideoCard});
ctx.renderVideoGrid(Array.from({length:64},(_,id)=>({id:'old'+id,source:'archivebate'})));
ctx.renderVideoGrid([{id:'new',source:'archivebate'}]);scheduled.splice(0).forEach(timer=>timer.cb());assert.deepEqual(children.map(card=>card._videoData.id),['new']);
const firstCard=children[0];
ctx.reconcilePage([{id:'new',source:'archivebate',views:'updated'}]);
assert.equal(children[0],firstCard);assert.equal(firstCard._videoData.views,'updated');
(async()=>{
 let active=0,peak=0,reads=0;
 const perf={window:{},fetch:async()=>{active++;peak=Math.max(peak,active);return{arrayBuffer:async()=>{reads++;await new Promise(r=>setTimeout(r,3));active--;}}}};
 vm.createContext(perf);vm.runInContext(fs.readFileSync('static/performance.js','utf8'),perf);
 await perf.window.ArchivebatePerf.prefetchUrls(Array.from({length:12},(_,i)=>'/'+i),{concurrency:4});assert.equal(peak,4);assert.equal(reads,12);assert.equal(active,0);
 const api={window:{},AbortController,navigator:{onLine:true},setTimeout,clearTimeout,fetch:async(url,{signal})=>({ok:true,json:()=>new Promise((resolve,reject)=>{if(signal.aborted)reject(Error('aborted'));else signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true})})})};
 vm.createContext(api);vm.runInContext(fs.readFileSync('static/api-client.js','utf8'),api);
 await assert.rejects(api.window.ArchivebateAPI.getJSON('/slow',{timeoutMs:10}),e=>e.code==='timeout');
 const ac=new AbortController();const pending=api.window.ArchivebateAPI.getJSON('/cancel',{signal:ac.signal});ac.abort();await assert.rejects(pending,e=>e.code==='cancelled');
 
  // Test Pakiet A: brak migotania, tożsamość DOM, stabilny img.src, podgląd, unieważnienie filtra
  {
    let gridClears = 0;
    const gridChildren = [];
    const gridCtx = {
      AbortController,
      state: { viewGeneration: 1 },
      dom: {
        videoGrid: {
          children: gridChildren,
          childNodes: gridChildren,
          set innerHTML(v) {
            if (v === '') {
              gridClears++;
              gridChildren.length = 0;
            }
          },
          appendChild(f) {
            if (f && f.children) gridChildren.push(...f.children);
            else if (f) gridChildren.push(f);
          },
          querySelectorAll() { return []; }
        }
      },
      deduplicateVideos: x => x,
      document: {
        createDocumentFragment: () => ({
          children: [],
          childNodes: [],
          appendChild(v) { this.children.push(v); this.childNodes.push(v); }
        })
      },
      updateCheckpointUI() {},
      checkAndHighlightCheckpoint() {},
      window: { requestIdleCallback: cb => cb() },
      createVideoCard: (v, idx) => {
        let src = v.poster || '/thumb.jpg';
        let srcSets = 0;
        const card = {
          id: v.id,
          _cardKey: v.id,
          _videoData: { ...v },
          _cardIndex: idx,
          isHoverActive: false,
          previewVideo: { isPlaying: false, paused: true },
          img: {
            get src() { return src; },
            set src(val) { srcSets++; src = val; },
            get srcSets() { return srcSets; },
            getAttribute(a) { return a === 'src' ? src : null; }
          },
          querySelector(sel) {
            if (sel === '.thumbnail-img') return this.img;
            return null;
          },
          _updateCard(newV, newIdx) {
            Object.assign(this._videoData, newV);
            if (newIdx !== undefined) this._cardIndex = newIdx;
            const targetPoster = newV.poster || '/thumb.jpg';
            if (this.img.src !== targetPoster) {
              this.img.src = targetPoster;
            }
          }
        };
        return card;
      }
    };

    vm.createContext(gridCtx);
    gridCtx.window = gridCtx;
    gridCtx.ArchivebateAppContext = { state: gridCtx.state, dom: gridCtx.dom };
    gridCtx.setTimeout = cb => { scheduled.push(cb); return cb; };
    gridCtx.clearTimeout = () => {};
    vm.runInContext(fs.readFileSync('static/video-grid.js', 'utf8'), gridCtx);
    gridCtx.ArchivebateVideoGrid.init({ createVideoCard: gridCtx.createVideoCard });

    // Inicjalna strona: 10 kart
    const initialVideos = Array.from({ length: 10 }, (_, i) => ({ id: `card_${i}`, source: 'archivebate', poster: '/thumb.jpg' }));
    gridCtx.reconcilePage(initialVideos);
    assert.equal(gridChildren.length, 10);
    const firstCard = gridChildren[0];
    firstCard.isHoverActive = true;
    firstCard.previewVideo.isPlaying = true;
    firstCard.previewVideo.paused = false;

    const initialClears = gridClears;
    const initialSrcSets = firstCard.img.srcSets;

    // Symulacja 20 porcji (w tym duplikaty i aktualizacja jednej karty)
    for (let batchIdx = 1; batchIdx <= 20; batchIdx++) {
      const batchVideos = Array.from({ length: 10 + Math.min(batchIdx, 10) }, (_, i) => ({
        id: `card_${i}`,
        source: 'archivebate',
        poster: '/thumb.jpg'
      }));

      if (batchIdx === 7) {
        batchVideos[0].views = '9999';
        batchVideos[0].is_favorite = true;
      }

      gridCtx.reconcilePage(batchVideos);

      assert.equal(gridChildren[0], firstCard, `Karta 0 utraciła tożsamość DOM w porcji ${batchIdx}`);
      assert.equal(firstCard.img.srcSets, initialSrcSets, `img.src zostało niepotrzebnie nadpisane w porcji ${batchIdx}`);
      assert.equal(firstCard.previewVideo.isPlaying, true, `Aktywny podgląd został przerwany w porcji ${batchIdx}`);
    }

    assert.equal(gridClears, initialClears, 'Podczas porcji wystąpiło niepożądane czyszczenie całej siatki (innerHTML="")');
    assert.equal(firstCard._videoData.views, '9999', 'Model powiązany z kartą nie został zaktualizowany');
    assert.equal(firstCard._videoData.is_favorite, true, 'Stan ulubionych nie został zaktualizowany');

    // Zmiana filtra
    gridCtx.state.viewGeneration++;
    gridCtx.renderVideoGrid([{ id: 'filtered_0', source: 'archivebate', poster: '/thumb_filt.jpg' }]);
    assert.equal(gridClears, initialClears + 1, 'Zmiana filtra powinna wyczyścić siatkę i zastąpić widok');
    assert.equal(gridChildren.length, 1);
    assert.equal(gridChildren[0].id, 'filtered_0');
  }

  console.log('PASS: obsolete render, full-body transfer limit, body timeout, cancellation, pakiet A zero-flash reconcile');
})().catch(e=>{console.error(e);process.exitCode=1});


// Runtime UI ownership / long-page loading regressions.
{
  const homeStatsSource = fs.readFileSync('static/home-stats.js', 'utf8');
  assert.doesNotMatch(homeStatsSource, /!hasFeedCounters/, 'global catalog card must not freeze behind feed counters');
  assert.match(homeStatsSource, /catalog_complete === false/, 'partial catalog must schedule live stats polling');

  const viewsSource = fs.readFileSync('static/video-views.js', 'utf8');
  assert.doesNotMatch(viewsSource, /statCatalogVideos\.innerText = totalVids/, 'feed snapshot must not overwrite global catalog card');

  const prefetchSource = fs.readFileSync('static/video-prefetch.js', 'utf8');
  assert.match(prefetchSource, /img\.loading = 'lazy'/, 'lazy thumbnails must use native browser loading');
  assert.match(prefetchSource, /img\.src = src/, 'lazy thumbnail must always receive a real src');

  const gridSource = fs.readFileSync('static/video-grid.js', 'utf8');
  assert.doesNotMatch(gridSource, /requestIdleCallback\(appendNextChunk/, 'long-grid completion must not depend on idle callbacks');
  assert.match(gridSource, /function scheduleChunk|setTimeout\(\(\) =>/, 'long-grid chunks must have a deterministic scheduler');
  assert.match(gridSource, /scheduleChunk\(appendNextChunk\)/, 'long-grid chunks must use the shared scheduler');
  assert.match(gridSource, /INITIAL_BATCH = 16/, 'first paint must stay bounded to a small synchronous card batch');
  const viewsPerfSource = fs.readFileSync('static/video-views.js', 'utf8');
  assert.match(viewsPerfSource, /HOME_PAGE_CACHE_LIMIT = 3/, 'home pagination needs a bounded three-page cache');
  assert.match(viewsPerfSource, /prefetchHomePage\(current \+ 1/, 'home view must prefetch the likely next page');
  assert.match(viewsPerfSource, /isInitial && !isSamePageRefresh && !renderedFromPageCache/, 'first page and page transitions must use chunked replacement');
}
