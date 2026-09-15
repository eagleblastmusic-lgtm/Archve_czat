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
assert.match(fallbackSource, /createPreviewSeeker\(previewVideo/);
assert.match(fallbackSource, /owner=preview&priority=low&reason=timeline_preview/);
assert.match(fallbackSource, /getSegmentFromCache/);
assert.match(fallbackSource, /Number\(mainVideo\.readyState \|\| 0\) < 2/);
assert.match(fallbackSource, /timeline\.addEventListener\('pointerleave'/);
assert.match(fallbackSource, /keepPreviewComposited\(previewVideo, false\)/);
assert.match(fallbackSource, /opacity = visible \? '1' : '0\.001'/);
assert.match(fallbackSource, /hasDynamicFrame/);
assert.match(fallbackSource, /pointerRaf = requestAnimationFrame/,
  'fallback must run after modal-player-controls RAF so the poster renderer cannot hide it afterwards');
assert.match(fallbackSource, /previewVideo\.addEventListener\('seeked'/);
assert.match(fallbackSource, /seekedFallbackFrames/,
  'paused auxiliary video needs a seeked+RAF fallback when requestVideoFrameCallback does not arrive');
assert.doesNotMatch(fallbackSource, /meta\.isLatest === false/);
assert.doesNotMatch(fallbackSource, /requestSegment\s*\(/);
assert.doesNotMatch(fallbackSource, /\/api\/storyboard\/segment/);

const runtimeSource = fs.readFileSync('runtime_app.py', 'utf8');
assert.match(runtimeSource, /v43-timeline-fallback\.js\?v=3/);
assert.match(runtimeSource, /dynamic_timeline_fallback["']:\s*True/);

console.log('PASS V4.3 MODAL IDENTITY + RAF-ORDERED TIMELINE FALLBACK');
