const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const listeners = new Map();
const state = { currentVideoDetails: { id: 'archive-fixture-a' } };
const document = {
  hidden: false,
  addEventListener(name, callback) { listeners.set(name, callback); },
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
assert.equal(state.currentVideoId, 'archive-fixture-b');
assert.equal(modalVideo.dataset.videoId, 'archive-fixture-b');

const fallbackSource = fs.readFileSync('static/v43-timeline-fallback-v7.js', 'utf8');
assert.doesNotThrow(() => new vm.Script(fallbackSource, { filename: 'v43-timeline-fallback-v7.js' }));
assert.match(fallbackSource, /coordinator_version:\s*452/);
assert.match(fallbackSource, /currentVideoDetails\?\.id/);
assert.match(fallbackSource, /showExact/);
assert.match(fallbackSource, /getExactCached/);
assert.match(fallbackSource, /ensureInteractiveCoarse/);
assert.match(fallbackSource, /EXACT_IDLE_MS = 260/);
assert.match(fallbackSource, /EXACT_MIN_BUFFER_SECONDS = 3\.0/);
assert.match(fallbackSource, /requestSoftProtect/);
assert.match(fallbackSource, /requestHardCancel/);
assert.match(fallbackSource, /\/api\/runtime\/v452\/storyboard\/protect/);
assert.match(fallbackSource, /\/api\/runtime\/v452\/storyboard\/cancel/);
assert.match(fallbackSource, /__v452PlayerQoSCoordinator/);
assert.match(fallbackSource, /clearIdleExact\(\{ cancelActive: true \}\)/);
assert.match(fallbackSource, /playbackSafe\(mainVideo, EXACT_MIN_BUFFER_SECONDS\)/,
  'idle exact must re-check live playback health immediately before backend work');
assert.match(fallbackSource, /source_video_id:\s*latestVideoId/);
assert.match(fallbackSource, /INTERACTIVE_BUFFER_SECONDS = 3\.0/);
assert.match(fallbackSource, /PREWARM_BUFFER_SECONDS = 5\.0/);
assert.match(fallbackSource, /LOW_BUFFER_CANCEL_SECONDS = 2\.0/);
assert.match(fallbackSource, /exact_idle_scheduled/);
assert.match(fallbackSource, /player_qos_coordinator:\s*true/);
assert.match(fallbackSource, /media_seek_enabled:\s*false/);
assert.match(fallbackSource, /black_fallback_enabled:\s*false/);
assert.doesNotMatch(fallbackSource, /previewVideo\.currentTime\s*=/,
  'modal timeline pointer motion must not seek an auxiliary video');

const youtubeSource = fs.readFileSync('static/youtube-storyboard.js', 'utf8');
assert.doesNotThrow(() => new vm.Script(youtubeSource, { filename: 'youtube-storyboard.js' }));
assert.match(youtubeSource, /EXACT_BUFFER_SECONDS = 3\.0/);
assert.match(youtubeSource, /BACKGROUND_BUFFER_SECONDS = 8\.0/);
assert.match(youtubeSource, /QOS_RECHECK_MS = 180/);
assert.match(youtubeSource, /playbackAllowsStoryboard/);
assert.match(youtubeSource, /waitForPlaybackBudget/);
assert.match(youtubeSource, /player_qos_guard:\s*true/);
assert.match(youtubeSource, /currentVideoDetails\?\.id/,
  'exact client must prefer the modal details identity over stale compatibility state');

const qosSource = fs.readFileSync('static/v452-player-qos.js', 'utf8');
assert.doesNotThrow(() => new vm.Script(qosSource, { filename: 'v452-player-qos.js' }));
assert.match(qosSource, /droppedVideoFrames/);
assert.match(qosSource, /totalVideoFrames/);
assert.match(qosSource, /stall_count/);
assert.match(qosSource, /video_width/);
assert.match(qosSource, /display_width/);
assert.match(qosSource, /\/api\/runtime\/v452\/playback\/status/);
assert.match(qosSource, /\/api\/runtime\/v452\/storyboard\/cancel/);

const runtimeSource = fs.readFileSync('runtime_app.py', 'utf8');
assert.match(runtimeSource, /v43-timeline-fallback-v7\.js\?v=452/);
assert.match(runtimeSource, /v452-player-qos\.js\?v=452/);
assert.match(runtimeSource, /timeline_coordinator_version["']:\s*452/);
assert.match(runtimeSource, /timeline_scheduler["']:\s*["']single-idle-exact-v452["']/);
assert.match(runtimeSource, /QUICK_FRAME_COUNT = 4/);
assert.match(runtimeSource, /QUICK_PARALLELISM = 2/);
assert.match(runtimeSource, /QUICK_MIN_SUCCESS = 3/);
assert.match(runtimeSource, /player_qos_stabilization["']:\s*True/);
assert.match(runtimeSource, /_remove_watch_aux_stream/);
assert.match(runtimeSource, /const warmWatch = \(\) =>/,
  'runtime must explicitly recognize and strip the legacy /watch auxiliary media path');
assert.match(runtimeSource, /media_seek_fallback["']:\s*False/);
assert.match(runtimeSource, /RUNTIME_ID = ["']v4\.3-fast2["']/,
  'launcher compatibility runtime id must remain stable');

console.log('PASS V4.5.2 MODAL IDENTITY + PLAYER QOS + RUNTIME WATCH GUARD');
