const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const listeners = new Map();
const state = {
  currentVideoDetails: { id: 'archive-fixture-a' }
};

const document = {
  hidden: false,
  addEventListener(name, callback) {
    listeners.set(name, callback);
  },
  querySelector() { return null; }
};

const context = {
  console,
  window: null,
  globalThis: null,
  document,
  navigator: { connection: { saveData: false } },
  performance,
  DOMException,
  Map,
  Set,
  Promise,
  Number,
  String,
  Math,
  URL,
  setTimeout,
  clearTimeout,
  fetch: async () => ({ arrayBuffer: async () => new ArrayBuffer(0) }),
  ArchivebateAppContext: { state },
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/performance.js', 'utf8'), context, { filename: 'performance.js' });

assert.ok(context.ArchivebatePerf, 'performance API should be exported');
assert.equal(typeof context.ArchivebatePerf.bridgeModalVideoIdentity, 'function');
assert.equal(typeof listeners.get('play'), 'function', 'modal identity bridge must subscribe before playing');

const modalVideo = { id: 'modalVideo', dataset: {} };
listeners.get('play')({ target: modalVideo });
assert.equal(state.currentVideoId, 'archive-fixture-a');
assert.equal(modalVideo.dataset.videoId, 'archive-fixture-a');

state.currentVideoDetails = { id: 'archive-fixture-b' };
listeners.get('play')({ target: modalVideo });
assert.equal(state.currentVideoId, 'archive-fixture-b', 'switching modal video must refresh active storyboard id');
assert.equal(modalVideo.dataset.videoId, 'archive-fixture-b');

const otherVideo = { id: 'mainPlayer', dataset: {} };
listeners.get('play')({ target: otherVideo });
assert.equal(otherVideo.dataset.videoId, undefined, 'bridge is modal-specific');

const fallbackSource = fs.readFileSync('static/v43-timeline-fallback-v7.js', 'utf8');
assert.doesNotThrow(() => new vm.Script(fallbackSource, { filename: 'v43-timeline-fallback-v7.js' }));
assert.match(fallbackSource, /coordinator_version:\s*7/);
assert.match(fallbackSource, /currentVideoDetails\?\.id/,
  'modal details id must be the source of truth, not a stale compatibility id');
assert.match(fallbackSource, /showExact/,
  'V7 must render cached exact frames itself instead of depending on listener ordering');
assert.match(fallbackSource, /getExactCached/,
  'exact 1-fps segments remain authoritative');
assert.match(fallbackSource, /ensureInteractiveCoarse/,
  'real hover must be able to start one persistent coarse overview');
assert.match(fallbackSource, /COARSE_EXACT_DEFER_MS = 3500/,
  'cold coarse generation gets only a bounded head start');
assert.match(fallbackSource, /coordinatedRequestSegment/,
  'V7 must coordinate exact requests with the one-time coarse build');
assert.match(fallbackSource, /deferExact/);
assert.match(fallbackSource, /hover_coarse_starts/);
assert.match(fallbackSource, /source_video_id:\s*latestVideoId/,
  'diagnostics must expose the real active source id');
assert.match(fallbackSource, /INTERACTIVE_BUFFER_SECONDS = 3\.0/,
  'interactive coarse work requires a useful primary playback buffer');
assert.match(fallbackSource, /PREWARM_BUFFER_SECONDS = 5\.0/,
  'background coarse work remains more conservative than interactive hover');
assert.match(fallbackSource, /LOW_BUFFER_CANCEL_SECONDS = 2\.0/,
  'coarse work must yield when the playback buffer becomes fragile');
assert.match(fallbackSource, /showPosterFallback/,
  'cold hover must preserve a meaningful poster instead of a black tooltip');
assert.match(fallbackSource, /previewImg\.style\.display = 'block'/,
  'poster image remains visible until a valid dynamic frame exists');
assert.match(fallbackSource, /mainVideo\.addEventListener\('waiting'/);
assert.match(fallbackSource, /mainVideo\.addEventListener\('stalled'/);
assert.match(fallbackSource, /media_seek_enabled:\s*false/);
assert.match(fallbackSource, /black_fallback_enabled:\s*false/);
assert.doesNotMatch(fallbackSource, /cancelCoarse\(videoId, 'exact_hover'\)/,
  'pointerenter may no longer kill the coarse build before it has a chance to finish');
assert.doesNotMatch(fallbackSource, /\/api\/video\/stream\?id=.*owner=preview/,
  'timeline must never seek a second full-resolution MP4');
assert.doesNotMatch(fallbackSource, /previewVideo\.currentTime\s*=/,
  'timeline pointer motion must not trigger media seeks');

const runtimeSource = fs.readFileSync('runtime_app.py', 'utf8');
assert.match(runtimeSource, /v43-timeline-fallback-v7\.js\?v=7/);
assert.match(runtimeSource, /timeline_coordinator_version["']:\s*7/);
assert.match(runtimeSource, /QUICK_FRAME_COUNT = 8/);
assert.match(runtimeSource, /QUICK_PARALLELISM = 2/);
assert.match(runtimeSource, /media_seek_fallback["']:\s*False/);

console.log('PASS V4.3 MODAL IDENTITY + COARSE-FIRST V7 TIMELINE');