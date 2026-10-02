const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  let finishSegment;
  let segmentStarts = 0;
  let demandDeletes = 0;
  const images = [];
  const segmentReady = new Promise(resolve => { finishSegment = resolve; });
  class ImageMock {
    set src(value) { this.url = value; images.push(this); }
  }
  const context = {
    AbortController, DOMException, Map, Set, Math, Number, String, URLSearchParams,
    Date, Promise, console, setTimeout, clearTimeout, performance, Image: ImageMock,
    location: { search: '', pathname: '/watch/fixture' },
    crypto: { randomUUID: () => 'watcher-fixture' },
    document: { getElementById: () => null },
    ArchivebateAPI: {
      request: async (url, options = {}) => {
        const method = String(options.method || 'GET').toUpperCase();
        if (url.includes('/api/storyboard/demand') && method === 'POST') return { ok: true };
        if (url.includes('/api/storyboard/demand') && method === 'DELETE') {
          demandDeletes += 1;
          return { ok: true };
        }
        if (url.includes('/api/storyboard/segment') && method === 'POST') {
          segmentStarts += 1;
          return segmentReady;
        }
        throw new Error(`Unexpected storyboard request: ${method} ${url}`);
      }
    },
    fetch: async () => { throw new Error('unexpected fetch'); }
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/youtube-storyboard.js', 'utf8'), context,
    { filename: 'static/youtube-storyboard.js' });

  const api = context.ArchivebateYouTubeStoryboard;
  assert.equal(typeof api?.prepareSegment, 'function', 'test uses the current exported segment API');
  const first = new AbortController();
  const second = new AbortController();
  const firstResult = api.prepareSegment({ videoId: 'shared-watch', duration: 60, segmentIndex: 0, signal: first.signal });
  const secondResult = api.prepareSegment({ videoId: 'shared-watch', duration: 60, segmentIndex: 0, signal: second.signal });
  first.abort();
  await assert.rejects(firstResult, { name: 'AbortError' });
  assert.equal(segmentStarts, 1, 'overlapping consumers start one exact segment request');
  assert.equal(demandDeletes, 0, 'one cancelled consumer cannot release the surviving consumer lease');

  finishSegment({ ok: true, json: async () => ({
    status: 'ready', type: 'segment', segment_index: 0,
    frame_count: 2, columns: 2, rows: 1,
    frame_width: 160, frame_height: 90, times: [0, 1], sprite_url: '/shared-watch.jpg'
  }) });
  for (let i = 0; i < 50 && images.length === 0; i += 1) await new Promise(resolve => setTimeout(resolve, 2));
  assert.equal(images.length, 1, 'the surviving consumer reaches the shared sprite load');
  images[0].onload();
  const board = await secondResult;
  assert.equal(board.sprite_url, '/shared-watch.jpg');
  assert.equal(demandDeletes, 1, 'settled shared work releases its backend lease once');
  assert.equal(api.stats().segment_inflight, 0, 'settled watcher state is removed');

  // Aborted work can still be releasing its lease when a fresh hover arrives.
  // That consumer must not attach to the old, already-aborted controller.
  let restartStarts = 0;
  let finishOldDelete;
  const oldDelete = new Promise(resolve => { finishOldDelete = resolve; });
  context.ArchivebateAPI.request = async (url, options = {}) => {
    if (url.includes('/api/storyboard/demand')) {
      if (options.method === 'DELETE' && restartStarts === 1) await oldDelete;
      return {ok:true};
    }
    restartStarts += 1;
    if (restartStarts === 1) return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted','AbortError')), {once:true});
    });
    return {ok:true,json:async()=>({...board,sprite_url:'/shared-watch.jpg'})};
  };
  const oldHover = new AbortController();
  const oldResult = api.prepareSegment({videoId:'restart-watch',duration:60,segmentIndex:0,signal:oldHover.signal});
  for (let i=0; i<50 && restartStarts===0; i++) await new Promise(resolve=>setTimeout(resolve,2));
  oldHover.abort();
  await assert.rejects(oldResult,{name:'AbortError'});
  const restarted = api.prepareSegment({videoId:'restart-watch',duration:60,segmentIndex:0});
  assert.equal((await restarted).sprite_url,'/shared-watch.jpg');
  assert.equal(restartStarts,2,'new consumer must start fresh work while old lease deletion is pending');
  finishOldDelete();
  await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal(api.stats().segment_inflight,0);

  // A ready disk-cache segment is usable even with an empty playback buffer.
  const player = {id:'mainPlayer',dataset:{videoId:'disk-watch'},paused:false,ended:false,seeking:false,
    readyState:1,currentTime:0,buffered:{length:0}};
  context.document.getElementById = id => id==='mainPlayer' ? player : null;
  context.fetch = async url => {
    assert(url.startsWith('/api/storyboard/segment?'));
    return {ok:true,json:async()=>({...board,status:'ready',sprite_url:'/disk-cache.jpg'})};
  };
  const diskHover = new AbortController();
  let diskReady = null;
  api.requestSegment({videoId:'disk-watch',duration:60,targetTime:1,signal:diskHover.signal,onReady:value=>{diskReady=value;}});
  for (let i=0;i<150 && images.length<2;i++) await new Promise(resolve=>setTimeout(resolve,2));
  assert.equal(images.length,2);
  images[1].onload();
  for (let i=0;i<50 && !diskReady;i++) await new Promise(resolve=>setTimeout(resolve,2));
  assert.equal(diskReady?.sprite_url,'/disk-cache.jpg','disk cache must bypass the buffer gate');
  assert.equal(restartStarts,2,'cached presentation must not start any decoder or demand');
  diskHover.abort();
  console.log('PASS: current storyboard API shares one exact segment and isolates consumer cancellation');
})().catch(error => { console.error(error); process.exitCode = 1; });
