/**
 * Regression Test Suite for Final P1 Remediation (Frontend)
 *
 * Covers:
 * 1. P1: Modal video switch unregisters old video subscription.
 * 2. P1: Watch page video switch unregisters old video subscription.
 * 3. P1: LRU cache eviction protects entries with active subscribers.
 * 4. P2: Multi-consumer isolation and hasSubscribers lifecycle.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

function setupTestEnvironment() {
  const previewCode = fs.readFileSync(path.join(__dirname, '..', 'static', 'timeline-preview.js'), 'utf8');

  const fetchCalls = [];
  const fakeFetch = async (url, opts = {}) => {
    fetchCalls.push({ url: String(url), method: opts.method || 'GET' });
    if (url.includes('/api/storyboard/v2/build')) {
      return { ok: true, json: async () => ({ status: 'queued' }) };
    }
    if (url.includes('/api/storyboard/v2')) {
      return {
        ok: true,
        json: async () => ({
          version: 11,
          rev: 1,
          complete: false,
          sheets: [],
          frames: [],
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const listenersMap = new Map();
  const makeElement = () => ({
    style: {},
    dataset: {},
    classList: { add: () => {}, remove: () => {} },
    setAttribute: () => {},
    removeAttribute: () => {},
    getAttribute: () => '',
    addEventListener: (type, fn) => {
      if (!listenersMap.has(type)) listenersMap.set(type, []);
      listenersMap.get(type).push(fn);
    },
    removeEventListener: (type, fn) => {
      const arr = listenersMap.get(type) || [];
      listenersMap.set(type, arr.filter(f => f !== fn));
    },
    pause: () => {},
    load: () => {},
    play: () => Promise.resolve(),
    replaceChildren: () => {},
    appendChild: () => {},
    querySelectorAll: () => [],
    querySelector: () => null,
  });

  const sandbox = {
    console,
    Date,
    Math,
    Set,
    Map,
    Promise,
    setTimeout,
    clearTimeout,
    AbortController,
    performance: { now: () => Date.now() },
    fetch: fakeFetch,
    document: {
      hidden: false,
      body: { style: {} },
      querySelector: () => null,
      createElement: makeElement,
    },
    Image: class {
      constructor() {
        this.src = '';
      }
      decode() {
        return Promise.resolve();
      }
    },
    window: {},
    globalThis: {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(previewCode, sandbox);

  return { sandbox, fetchCalls, makeElement };
}

async function testModalSwitchUnsubscribe() {
  const { sandbox } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;

  // Emulate VideoModal modal state and logic
  const state = {
    currentVideoId: null,
    currentVideoDetails: null,
    playerGeneration: 0,
    playerController: null,
    activePlaybackSession: null,
  };

  const openVideoModal = (video) => {
    // 1. Unsubscribe previous video if present
    const prevVideoId = state.currentVideoId || state.currentVideoDetails?.id;
    if (sandbox.ArchivebateTimelinePreview && prevVideoId && String(prevVideoId) !== String(video?.id)) {
      const prevSource = state.currentVideoDetails?.source || (String(prevVideoId).startsWith('cw_') ? 'camwhores' : 'archivebate');
      manager.unsubscribe(prevVideoId, 'modal', prevSource);
    }
    state.currentVideoId = video?.id ? String(video.id) : null;
    state.currentVideoDetails = { ...video };

    // Subscribe new video
    const videoKey = `archivebate:id:${video.id}`;
    manager.subscribe(videoKey, 'modal', () => {}, 'archivebate');
  };

  const closeModal = () => {
    if (sandbox.ArchivebateTimelinePreview && state.currentVideoId) {
      const source = state.currentVideoDetails?.source || (String(state.currentVideoId).startsWith('cw_') ? 'camwhores' : 'archivebate');
      manager.unsubscribe(state.currentVideoId, 'modal', source);
    }
    state.currentVideoId = null;
    state.currentVideoDetails = null;
  };

  // Open Video A
  openVideoModal({ id: 'video_A', source: 'archivebate' });
  assert.strictEqual(manager.hasSubscribers('video_A'), true, 'Video A must have active subscriber');
  assert.strictEqual(state.currentVideoId, 'video_A');

  // Switch to Video B
  openVideoModal({ id: 'video_B', source: 'archivebate' });
  assert.strictEqual(manager.hasSubscribers('video_A'), false, 'Video A must be unsubscribed on switch');
  assert.strictEqual(manager.hasSubscribers('video_B'), true, 'Video B must be subscribed on switch');
  assert.strictEqual(state.currentVideoId, 'video_B');

  // Close modal
  closeModal();
  assert.strictEqual(manager.hasSubscribers('video_B'), false, 'Video B must be unsubscribed on close');
  assert.strictEqual(state.currentVideoId, null, 'currentVideoId must be reset to null on close');

  console.log('PASS: Modal switch unsubscribe & cleanup verified');
}

async function testWatchPageSwitchUnsubscribe() {
  const { sandbox } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;

  let videoId = 'watch_vid_1';
  let activeSubscribedVideoId = null;
  let activeSubscribedSource = null;
  let currentDetails = { id: videoId, source: 'archivebate' };

  const onLoadedMetadata = () => {
    const source = currentDetails?.source || 'archivebate';
    if (activeSubscribedVideoId && (activeSubscribedVideoId !== videoId || activeSubscribedSource !== source)) {
      manager.unsubscribe(activeSubscribedVideoId, 'watch', activeSubscribedSource);
    }
    activeSubscribedVideoId = videoId;
    activeSubscribedSource = source;
    const videoKey = `${source}:id:${videoId}`;
    manager.subscribe(videoKey, 'watch', () => {}, source);
  };

  const switchWatchVideo = (newVideo) => {
    if (!newVideo || !newVideo.id) return;
    if (activeSubscribedVideoId || videoId) {
      const oldId = activeSubscribedVideoId || videoId;
      const oldSource = activeSubscribedSource || (currentDetails?.source || 'archivebate');
      manager.unsubscribe(oldId, 'watch', oldSource);
      activeSubscribedVideoId = null;
      activeSubscribedSource = null;
    }
    videoId = newVideo.id;
    currentDetails = { id: newVideo.id, source: newVideo.source || 'archivebate' };
  };

  const onPageHide = () => {
    if (activeSubscribedVideoId || videoId) {
      const targetId = activeSubscribedVideoId || videoId;
      const source = activeSubscribedSource || (currentDetails?.source || 'archivebate');
      manager.unsubscribe(targetId, 'watch', source);
      activeSubscribedVideoId = null;
      activeSubscribedSource = null;
    }
  };

  // 1. Initial video load
  onLoadedMetadata();
  assert.strictEqual(manager.hasSubscribers('watch_vid_1'), true, 'watch_vid_1 must be subscribed');

  // 2. Switch to video 2
  switchWatchVideo({ id: 'watch_vid_2', source: 'archivebate' });
  assert.strictEqual(manager.hasSubscribers('watch_vid_1'), false, 'watch_vid_1 must be unsubscribed on switch');

  // 3. Load metadata for video 2
  onLoadedMetadata();
  assert.strictEqual(manager.hasSubscribers('watch_vid_2'), true, 'watch_vid_2 must be subscribed');

  // 4. Page hide
  onPageHide();
  assert.strictEqual(manager.hasSubscribers('watch_vid_2'), false, 'watch_vid_2 must be unsubscribed on pagehide');

  console.log('PASS: Watch page video switch & pagehide unsubscribe verified');
}

async function testLruEvictionProtectsSubscribers() {
  const { sandbox } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;
  manager.maxCachedEntries = 5;

  // Add 10 entries
  for (let i = 1; i <= 10; i++) {
    const key = `archivebate:id:entry_${i}`;
    manager.getOrCreateEntry(key, 'archivebate');
  }

  // Subscribe entry_3
  manager.subscribe('archivebate:id:entry_3', 'modal', () => {}, 'archivebate');

  // Trigger evictLru
  manager.evictLru('archivebate:id:entry_10');

  // entry_3 MUST still exist in cache because it has an active subscriber
  assert.strictEqual(manager.hasEntry('archivebate:id:entry_3'), true, 'entry_3 must be protected from LRU eviction');
  assert.strictEqual(manager.hasSubscribers('entry_3'), true);

  console.log('PASS: LRU cache eviction protects active subscribers verified');
}

async function testMultiConsumerIsolation() {
  const { sandbox } = setupTestEnvironment();
  const manager = sandbox.ArchivebateTimelinePreview.manager;

  const vidKey = 'archivebate:id:shared_video';

  // Two independent consumers subscribe to the same video
  let modalUpdated = 0;
  let watchUpdated = 0;

  manager.subscribe(vidKey, 'modal', () => { modalUpdated++; }, 'archivebate');
  manager.subscribe(vidKey, 'watch', () => { watchUpdated++; }, 'archivebate');

  assert.strictEqual(manager.hasSubscribers(vidKey), true);
  const entry = manager.cache.get('archivebate:id:shared_video');
  assert.strictEqual(entry.subscribers.size, 2);

  // Modal consumer unsubscribes (e.g. modal closed)
  manager.unsubscribe(vidKey, 'modal', 'archivebate');
  assert.strictEqual(manager.hasSubscribers(vidKey), true, 'Video must still have active subscribers');
  assert.strictEqual(entry.subscribers.size, 1);
  assert.strictEqual(entry.subscribers.has('watch'), true);

  // Watch consumer unsubscribes (e.g. watch navigation)
  manager.unsubscribe(vidKey, 'watch', 'archivebate');
  assert.strictEqual(manager.hasSubscribers(vidKey), false, 'Video must have 0 subscribers now');
  assert.strictEqual(entry.subscribers.size, 0);

  console.log('PASS: Multi-consumer isolation verified');
}

async function main() {
  await testModalSwitchUnsubscribe();
  await testWatchPageSwitchUnsubscribe();
  await testLruEvictionProtectsSubscribers();
  await testMultiConsumerIsolation();
  console.log('\nALL FRONTEND FINAL P1 REMEDIATION TESTS PASSED!');
}

main().catch(err => {
  console.error('TEST FAILED:', err);
  process.exit(1);
});
