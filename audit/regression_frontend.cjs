const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const app=fs.readFileSync('static/app.js','utf8');
const scheduled=[],children=[];
const ctx={AbortController,state:{},dom:{videoGrid:{set innerHTML(v){children.length=0},appendChild(f){children.push(...f.children)}}},deduplicateVideos:x=>x,createVideoCard:v=>v.id,document:{createDocumentFragment:()=>({children:[],appendChild(v){this.children.push(v)}})},updateCheckpointUI(){},checkAndHighlightCheckpoint(){},window:{requestIdleCallback:true},requestIdleCallback:cb=>scheduled.push(cb),setTimeout:cb=>scheduled.push(cb)};
vm.createContext(ctx);
vm.runInContext(app.slice(app.indexOf('function renderVideoGrid('),app.indexOf('// STOPNIOWE DOKŁADANIE')),ctx);
ctx.renderVideoGrid(Array.from({length:64},(_,id)=>({id:'old'+id})));
ctx.renderVideoGrid([{id:'new'}]);scheduled.forEach(cb=>cb());assert.deepEqual(children,['new']);
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
    vm.runInContext(app.slice(app.indexOf('function renderVideoGrid('), app.indexOf('// STOPNIOWE DOKŁADANIE')), gridCtx);

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
