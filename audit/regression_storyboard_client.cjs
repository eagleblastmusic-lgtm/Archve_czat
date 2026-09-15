const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  const images = [];
  const documentEvents = new Map();
  class ImageMock {
    constructor() { this.style = {}; }
    set src(value) { this.url = value; images.push(this); }
    get src() { return this.url; }
  }

  const source = fs.readFileSync('static/youtube-storyboard.js', 'utf8');
  const context = {
    console,
    Image: ImageMock,
    DOMException,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    performance,
    Map,
    Set,
    Math,
    Number,
    String,
    URLSearchParams,
    decodeURIComponent,
    location: { search: '', pathname: '/watch/watch-fixture' },
    crypto: { randomUUID: () => `id-${Math.random()}` },
    document: {
      querySelector: () => null,
      createElement: () => ({ style: {} }),
      addEventListener(name, callback) { documentEvents.set(name, callback); }
    },
    fetch: async () => { throw new Error('unexpected fetch'); }
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'youtube-storyboard.js' });

  const api = context.window.ArchivebateYouTubeStoryboard;
  assert.ok(api, 'ArchivebateYouTubeStoryboard should be exported');

  let segmentStarts = 0;
  let demandPosts = 0;
  let demandDeletes = 0;
  context.ArchivebateAPI = {
    request: async (url, options = {}) => {
      if (String(url).includes('/api/storyboard/demand')) {
        if (String(options.method || 'GET').toUpperCase() === 'DELETE') demandDeletes += 1;
        else demandPosts += 1;
        return { ok: true, json: async () => ({ ok: true }) };
      }
      if (String(url).includes('/api/storyboard/segment')) {
        segmentStarts += 1;
        return {
          ok: true,
          json: async () => ({
            status: 'ready',
            type: 'segment',
            segment_index: 0,
            frame_count: 2,
            columns: 2,
            rows: 1,
            frame_width: 160,
            frame_height: 90,
            times: [0, 1],
            sprite_url: `/sprite-${segmentStarts}`
          })
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }
  };

  const firstAbort = new AbortController();
  const secondAbort = new AbortController();
  const first = api.prepareSegment({ videoId: 'fixture', duration: 30, segmentIndex: 0, signal: firstAbort.signal });
  const second = api.prepareSegment({ videoId: 'fixture', duration: 30, segmentIndex: 0, signal: secondAbort.signal });

  firstAbort.abort();
  await assert.rejects(first, { name: 'AbortError' });

  // Backend start and Image creation are asynchronous microtasks. Wait until the
  // surviving consumer has reached sprite preload instead of asserting in the
  // same tick as the abort.
  for (let i = 0; i < 50 && images.length === 0; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  assert.equal(segmentStarts, 1, 'same segment must share one backend start');
  assert.equal(images.length, 1, 'same sprite must share one Image load');
  images[0].onload();

  const board = await second;
  assert.equal(board.sprite_url, '/sprite-1');

  const warm = await api.prepareSegment({ videoId: 'fixture', duration: 30, segmentIndex: 0 });
  assert.equal(warm.sprite_url, '/sprite-1');
  assert.equal(segmentStarts, 1, 'warm cache must not restart backend work');

  // `attach` may inspect an already cached QUICK board, but pointer-enter/play
  // must not independently start an exact FFmpeg segment before playback has a
  // useful buffer. The global buffered-playing prewarm path owns that decision.
  let statusGets = 0;
  context.fetch = async (url) => {
    if (String(url).startsWith('/api/storyboard?')) {
      statusGets += 1;
      return { ok: true, json: async () => ({ status: 'missing' }) };
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
  const handlers = new Map();
  const fakeVideo = {
    duration: 90,
    currentTime: 10,
    paused: true,
    readyState: 0,
    addEventListener(name, callback) { handlers.set(`video:${name}`, callback); }
  };
  const fakeTimeline = {
    addEventListener(name, callback) { handlers.set(`timeline:${name}`, callback); }
  };
  const attachAbort = new AbortController();
  api.attach({
    video: fakeVideo,
    videoId: 'attach-fixture',
    timeline: fakeTimeline,
    signal: attachAbort.signal,
    onBoard: () => { throw new Error('missing QUICK board should not be delivered'); }
  });
  handlers.get('timeline:pointerenter')();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(statusGets, 1, 'attach may perform one cache-only QUICK status lookup');
  assert.equal(segmentStarts, 1, 'attach pointer-enter must not start exact segment work');
  attachAbort.abort();

  // The buffered-playing hook must resolve /watch/<id> from pathname. Without
  // this contract the standalone watch page would never receive the safe
  // post-buffer prewarm after removing attach's eager warm.
  context.ArchivebatePerf = { getBufferedAhead: () => 3.5 };
  const playingHandler = documentEvents.get('playing');
  assert.equal(typeof playingHandler, 'function', 'global playing prewarm hook must be installed');
  const watchVideo = {
    id: 'mainPlayer',
    dataset: {},
    duration: 30,
    currentTime: 3,
    paused: false,
    ended: false,
    readyState: 4,
  };
  playingHandler({ target: watchVideo });
  for (let i = 0; i < 80 && segmentStarts < 2; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.equal(segmentStarts, 2, 'buffered watch playback must prewarm exact segment using pathname video id');
  for (let i = 0; i < 20 && images.length < 2; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  assert.equal(images.length, 2, 'watch prewarm should reach shared sprite preload');
  images[1].onload();
  await new Promise(resolve => setTimeout(resolve, 5));

  // If playback remains weak after all retry checks, V4.3 must give up instead
  // of starting FFmpeg anyway. Accelerate only the 350/450 ms readiness timers;
  // keep the 15 s warm cancellation timer real so a buggy warm would be visible.
  const realSetTimeout = context.setTimeout;
  const realClearTimeout = context.clearTimeout;
  const fastTimerTokens = new Set();
  context.setTimeout = (callback, delay, ...args) => {
    if (delay === 350 || delay === 450) {
      const token = {};
      fastTimerTokens.add(token);
      Promise.resolve().then(() => {
        if (fastTimerTokens.delete(token)) callback(...args);
      });
      return token;
    }
    return realSetTimeout(callback, delay, ...args);
  };
  context.clearTimeout = (token) => {
    if (fastTimerTokens.delete(token)) return;
    return realClearTimeout(token);
  };
  context.ArchivebatePerf = { getBufferedAhead: () => 0.25 };
  const startsBeforeWeakPlayback = segmentStarts;
  playingHandler({
    target: {
      id: 'mainPlayer',
      dataset: { videoId: 'weak-buffer-fixture' },
      duration: 30,
      currentTime: 2,
      paused: false,
      ended: false,
      readyState: 2,
    }
  });
  await new Promise(resolve => realSetTimeout(resolve, 20));
  assert.equal(segmentStarts, startsBeforeWeakPlayback, 'weak playback must not start exact prewarm after retry budget expires');
  context.setTimeout = realSetTimeout;
  context.clearTimeout = realClearTimeout;

  // A real hover signal owns a persistent per-video lease in addition to the
  // exact segment lease. The exact entry may finish and release its lease, but
  // the hover lease must stay alive so directional backend prefetch is not
  // cancelled immediately. Pointerleave (signal abort) releases the final lease.
  context.ArchivebatePerf = { getBufferedAhead: () => 3.5 };
  const hoverAbort = new AbortController();
  let hoverReady = false;
  const postsBeforeHover = demandPosts;
  const deletesBeforeHover = demandDeletes;
  api.requestSegment({
    videoId: 'hover-lease-fixture',
    duration: 90,
    targetTime: 35,
    signal: hoverAbort.signal,
    onReady: () => { hoverReady = true; }
  });
  for (let i = 0; i < 60 && images.length < 3; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  assert.equal(images.length, 3, 'hover exact request should reach sprite preload');
  images[2].onload();
  for (let i = 0; i < 30 && !hoverReady; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  assert.equal(hoverReady, true, 'hover segment should become ready');
  assert.equal(demandPosts - postsBeforeHover, 2, 'hover uses one persistent target lease plus one exact-entry lease');
  assert.equal(demandDeletes - deletesBeforeHover, 1, 'exact-entry lease may close while hover target lease stays alive');
  assert.equal(api.stats().target_leases, 1, 'target lease must remain while pointer signal is alive');
  hoverAbort.abort();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(demandDeletes - deletesBeforeHover, 2, 'pointerleave releases the persistent hover lease');
  assert.equal(api.stats().target_leases, 0, 'no hover lease may leak after pointerleave');

  console.log('PASS: segment dedupe, safe prewarm, weak-buffer protection and persistent hover lease lifecycle work');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
