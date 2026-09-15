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

const fallbackSource = fs.readFileSync('static/v43-timeline-fallback.js', 'utf8');
assert.doesNotThrow(() => new vm.Script(fallbackSource, { filename: 'v43-timeline-fallback.js' }));
assert.match(fallbackSource, /ArchivebateYouTubeStoryboard\.applyFrame/,
  'pointer motion must select cached sprite frames locally');
assert.match(fallbackSource, /getSegmentFromCache/,
  'exact 1-fps segment remains above the coarse fallback');
assert.match(fallbackSource, /\/api\/runtime\/v43\/storyboard\/quick/,
  'QUICK state must use the V4.3 long-poll endpoint instead of browser busy polling');
assert.match(fallbackSource, /PREWARM_DELAY_MS = 2500/,
  'coarse work must not begin in the click-to-first-frame window');
assert.match(fallbackSource, /PREWARM_BUFFER_SECONDS = 8\.0/,
  'playing video needs a substantial buffer before speculative coarse work');
assert.match(fallbackSource, /LOW_BUFFER_CANCEL_SECONDS = 3\.0/,
  'coarse work must yield again when playback buffer becomes fragile');
assert.match(fallbackSource, /showPosterFallback/,
  'cold hover must preserve a meaningful poster instead of a black tooltip');
assert.match(fallbackSource, /previewImg\.style\.display = 'block'/,
  'poster image must remain visible while nothing dynamic is ready');
assert.match(fallbackSource, /Apply first[\s\S]*Only after the sprite has a valid frame do we hide the[\s\S]*poster/,
  'poster may be hidden only after a valid sprite frame is applied');
assert.match(fallbackSource, /timeline\.addEventListener\('pointerenter'[\s\S]*cancelCoarse\(videoId, 'exact_hover'\)/,
  'interactive exact hover must cancel speculative coarse generation');
assert.match(fallbackSource, /mainVideo\.addEventListener\('waiting'/);
assert.match(fallbackSource, /mainVideo\.addEventListener\('stalled'/);
assert.match(fallbackSource, /low_buffer_cancels/);
assert.match(fallbackSource, /media_seek_enabled:\s*false/);
assert.match(fallbackSource, /black_fallback_enabled:\s*false/);
assert.doesNotMatch(fallbackSource, /\/api\/video\/stream\?id=.*owner=preview/,
  'timeline must never seek a second full-resolution MP4');
assert.doesNotMatch(fallbackSource, /previewVideo\.currentTime\s*=/,
  'timeline pointer motion must not trigger media seeks');
assert.doesNotMatch(fallbackSource, /createPreviewSeeker\(previewVideo/);
assert.doesNotMatch(fallbackSource, /requestSegment\s*\(/,
  'fallback itself must not duplicate exact-segment scheduling');

const runtimeSource = fs.readFileSync('runtime_app.py', 'utf8');
assert.match(runtimeSource, /v43-timeline-fallback\.js\?v=6/);
assert.match(runtimeSource, /\/api\/runtime\/v43\/storyboard\/quick/);
assert.match(runtimeSource, /playback_safe_quick_storyboard/);
assert.match(runtimeSource, /media_seek_fallback["']:\s*False/);

console.log('PASS V4.3 MODAL IDENTITY + NO-BLACK PLAYBACK-SAFE TIMELINE');
