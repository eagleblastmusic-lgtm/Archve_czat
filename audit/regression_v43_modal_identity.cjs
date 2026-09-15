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
assert.match(fallbackSource, /\/api\/storyboard\?id=/,
  'cold timeline must build one persistent QUICK sprite');
assert.match(fallbackSource, /ArchivebateYouTubeStoryboard\.applyFrame/,
  'pointer motion must select frames locally from the sprite');
assert.match(fallbackSource, /getSegmentFromCache/,
  'exact 1-fps segment remains above the coarse fallback');
assert.match(fallbackSource, /frame_width:\s*160/);
assert.match(fallbackSource, /frame_height:\s*90/);
assert.match(fallbackSource, /media_seek_enabled:\s*false/);
assert.match(fallbackSource, /PREWARM_BUFFER_SECONDS = 3\.0/,
  'coarse generation must wait until primary playback has a useful buffer');
assert.match(fallbackSource, /timeline\.addEventListener\('pointermove'/);
assert.doesNotMatch(fallbackSource, /\/api\/video\/stream\?id=.*owner=preview/,
  'timeline pointer motion must not seek a second full-resolution MP4');
assert.doesNotMatch(fallbackSource, /previewVideo\.currentTime\s*=/,
  'timeline pointer motion must be network-free once the sprite is ready');
assert.doesNotMatch(fallbackSource, /requestSegment\s*\(/,
  'fallback itself must not duplicate exact-segment scheduling');

const runtimeSource = fs.readFileSync('runtime_app.py', 'utf8');
assert.match(runtimeSource, /v43-timeline-fallback\.js\?v=5/);
assert.match(runtimeSource, /parallel_quick_storyboard["']:\s*bool/);
assert.match(runtimeSource, /fast_storyboard_quick/);

console.log('PASS V4.3 MODAL IDENTITY + YOUTUBE-STYLE COARSE SPRITE FALLBACK');
