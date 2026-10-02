'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'static', 'youtube-storyboard.js'), 'utf8');

let bufferEnd = 1.4;
let fetchCalls = [];
const listeners = new Map();
const documentListeners = new Map();
const video = {
  id: 'modalVideo',
  dataset: { videoId: 'fixture' },
  paused: false,
  ended: false,
  seeking: false,
  readyState: 3,
  currentTime: 1,
  duration: 120,
  videoWidth: 1920,
  videoHeight: 1080,
  buffered: {
    get length() { return 1; },
    start() { return 0; },
    end() { return bufferEnd; },
  },
  addEventListener(name, fn) { listeners.set(name, fn); },
};

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  URLSearchParams,
  AbortController,
  DOMException,
  performance,
  crypto: globalThis.crypto,
  location: { pathname: '/', search: '' },
  ArchivebateAppContext: {
    state: {
      currentVideoId: 'fixture',
      currentVideoDetails: { id: 'fixture' },
    },
  },
  document: {
    addEventListener(name, fn) { documentListeners.set(name, fn); },
    querySelector() { return null; },
    getElementById(id) { return id === 'modalVideo' ? video : null; },
    createElement() { throw new Error('unexpected DOM allocation'); },
  },
  fetch: async (url, options = {}) => {
    fetchCalls.push({ url: String(url), method: String(options.method || 'GET') });
    return {
      ok: true,
      status: 200,
      json: async () => ({ status: 'building' }),
    };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'youtube-storyboard.js' });

const api = sandbox.ArchivebateYouTubeStoryboard;
assert(api, 'storyboard API missing');
assert.strictEqual(api.EXACT_BUFFER_SECONDS, 3);
assert.strictEqual(api.BACKGROUND_BUFFER_SECONDS, 8);
assert.strictEqual(typeof api.playbackAllowsStoryboard, 'function');

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const hover = new AbortController();
  api.requestSegment({
    videoId: 'fixture',
    duration: 120,
    targetTime: 44,
    signal: hover.signal,
    onReady() {},
  });

  // Intent delay has elapsed, but primary playback has <3 s buffered.
  await sleep(390);
  assert.strictEqual(fetchCalls.length, 1, 'one local disk-cache lookup is allowed during critical playback');
  assert(fetchCalls.every(item => item.method === 'GET' && item.url.includes('/api/storyboard/segment')),
    'critical playback must never start demand, remote video traffic or FFmpeg');
  let stats = api.stats();
  assert(stats.qos_blocked_starts >= 1, stats);
  assert.strictEqual(stats.target_leases, 0, stats);
  assert.strictEqual(stats.segment_inflight, 0, stats);

  // Once playback is healthy, the retained idle target may acquire demand and start.
  bufferEnd = 12;
  await sleep(260);
  assert(fetchCalls.length >= 1, 'QoS-gated hover did not resume after buffer recovery');
  assert(fetchCalls.some(item => item.url.includes('/api/storyboard/demand')), fetchCalls);
  hover.abort();
  await sleep(30);

  // Background exact prewarm is stricter: active playback below 8 s is skipped.
  fetchCalls = [];
  bufferEnd = 5.5; // currentTime=1 -> 4.5 s ahead
  const warmResult = await api.warm({ videoId: 'fixture', duration: 120, targetTime: 90 });
  assert.strictEqual(warmResult, null);
  assert.strictEqual(fetchCalls.length, 0, 'background prewarm touched network below 8 s buffer');
  stats = api.stats();
  assert(stats.qos_prewarm_skips >= 1, stats);
  assert.strictEqual(stats.player_qos_guard, true);

  // Exercise the actual modal coordinator with the actual shared segment client.
  const pointerListeners = new Map();
  const timeline = {addEventListener(name, fn) { pointerListeners.set(name, fn); },
    getBoundingClientRect() { return {left:0,width:120}; }};
  const nodes = {modalVideo:video, modalTimelineContainer:timeline,
    modalTimelineSprite:{style:{}}, modalTimelinePreviewImg:{style:{}},
    modalTimelinePreviewStatus:{style:{}}, videoModal:{classList:{contains:()=>true}}};
  sandbox.document.getElementById = id => nodes[id] || null;
  sandbox.requestAnimationFrame = fn => setTimeout(fn,0);
  sandbox.cancelAnimationFrame = clearTimeout;
  vm.runInContext(fs.readFileSync(path.join(ROOT,'static','v43-timeline-fallback-v7.js'),'utf8'),sandbox);
  await sleep(20);
  bufferEnd = 1.5;
  const coordinated = new AbortController();
  const beforeIntents = api.stats().intent_scheduled;
  pointerListeners.get('pointerenter')({clientX:44});
  api.requestSegment({videoId:'fixture',duration:120,targetTime:44,signal:coordinated.signal,onReady(){}});
  for (const targetTime of [45,46,47]) {
    pointerListeners.get('pointermove')({clientX:targetTime});
    api.requestSegment({videoId:'fixture',duration:120,targetTime,signal:coordinated.signal,onReady(){}});
  }
  assert.equal(api.stats().intent_scheduled,beforeIntents+1,'pixel movement must reuse the same segment intent');
  await sleep(300);
  assert.equal(api.stats().active_target_requests,1,'latest intent survives the coordinator while QoS waits');
  assert(api.previewStatusText('fixture',120,47).includes('bufor'));
  // A real stall cancels work, but must retain intent for stationary hover.
  listeners.get('waiting')();
  assert.equal(api.stats().active_target_requests,0);
  bufferEnd = 12;
  listeners.get('timeupdate')();
  assert.equal(api.stats().active_target_requests,1,'buffer recovery must restore the same target without pointer motion');
  pointerListeners.get('pointerleave')();
  assert.equal(api.stats().active_target_requests,0,'leaving timeline must cancel the retained target');
  coordinated.abort();
  sandbox.ArchivebateV43TimelineFallback.reset();
  // Metadata/play events may precede enough buffer. A later progress event
  // must restart prewarm even when playing is not emitted a second time.
  fetchCalls = [];
  bufferEnd = 12;
  documentListeners.get('progress')({target:video});
  await sleep(750);
  assert(fetchCalls.some(call=>call.url.includes('/storyboard/segment')), 'buffer recovery must resume ten-second segment preparation');
  assert(!fetchCalls.some(call=>call.method==='POST' && /\/storyboard\?/.test(call.url)), 'ten-second previews must not launch a redundant whole-video QUICK build');
  api.cancelVideoClientWork('fixture');
  sandbox.ArchivebateV43TimelineFallback.reset();

  // Provider/worker failure must become a visible terminal state, not an
  // endlessly displayed loading label. Keep demand responses valid.
  const failureCalls=[];
  sandbox.fetch = async (url, options={}) => {failureCalls.push(String(url)); return {ok:true,status:200,json:async()=>
    String(url).includes('/storyboard/segment') ? {status:'error',error:'fixture failure'} : {status:'ok'}};};
  const errorPhases=[];
  const failing = new AbortController();
  api.requestSegment({videoId:'fixture',duration:120,targetTime:90,signal:failing.signal,onStatus:p=>errorPhases.push(p)});
  await sleep(420);
  assert(errorPhases.includes('error'),JSON.stringify({errorPhases,failureCalls:failureCalls.slice(-8),stats:api.stats()}));
  assert(api.previewStatusText('fixture',120,90).includes('Nie udało'));
  failing.abort();
  sandbox.ArchivebateV43TimelineFallback.reset();
  console.log('PASS V4.5.2 FRONTEND PLAYER-QOS GATE');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
