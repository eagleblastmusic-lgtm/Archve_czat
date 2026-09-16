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
    addEventListener() {},
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
  assert.strictEqual(fetchCalls.length, 0, `uncached hover issued ${fetchCalls.length} network calls during critical playback`);
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

  console.log('PASS V4.5.2 FRONTEND PLAYER-QOS GATE');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
