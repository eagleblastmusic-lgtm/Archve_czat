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
  assert.ok(api.HOVER_INTENT_MS >= 100, 'hover intent gate should suppress fly-over segments');

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
        const held = String(url).includes('cleanup-fixture');
        return {
          ok: true,
          json: async () => held ? { status: 'building' } : ({
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
  const hitsBeforeDirectLookup = api.stats().cache_hits;
  assert.ok(api.getSegmentFromCache('fixture', 30, 0), 'prepared segment should be available to timeline lookup');
  assert.equal(api.stats().cache_hits, hitsBeforeDirectLookup + 1, 'timeline cache lookup must count a cache hit');

  // `attach` may inspect an already cached QUICK board, but pointer-enter/play
  // must not independently start an exact FFmpeg segment before playback has a
  // useful buffer. The global buffered-playing prewarm path owns that decision.
  let statusGets = 0;
  context.fetch = async (url) => {
    if (String(url).startsWith('/api/storyboard?')) {
      statusGets += 1;
      return { ok: true, json: async () => ({ status: 'missing' }) };
    }
    if (String(url).startsWith('/api/storyboard/segment?')) {
      return { ok: true, json: async () => ({ status: 'building' }) };
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
  // the hover lease stays alive until pointerleave.
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
  for (let i = 0; i < 120 && images.length < 3; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 3));
  }
  assert.equal(images.length, 3, 'settled hover intent should reach sprite preload');
  images[2].onload();
  for (let i = 0; i < 40 && !hoverReady; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 3));
  }
  assert.equal(hoverReady, true, 'hover segment should become ready');
  assert.equal(demandPosts - postsBeforeHover, 2, 'hover uses one persistent target lease plus one exact-entry lease');
  assert.equal(demandDeletes - deletesBeforeHover, 1, 'exact-entry lease may close while hover target lease stays alive');
  assert.equal(api.stats().target_leases, 1, 'target lease must remain while pointer signal is alive');
  hoverAbort.abort();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(demandDeletes - deletesBeforeHover, 2, 'pointerleave releases the persistent hover lease');
  assert.equal(api.stats().target_leases, 0, 'no hover lease may leak after pointerleave');

  // Rapid fly-over across many 30-second segments must not translate into one
  // FFmpeg start per crossed segment. Only the final settled intent may start.
  const rapidAbort = new AbortController();
  const startsBeforeRapid = segmentStarts;
  const cancelledBeforeRapid = api.stats().intent_cancelled_before_start;
  for (let i = 0; i < 20; i += 1) {
    api.requestSegment({
      videoId: 'rapid-fixture',
      duration: 900,
      targetTime: i * 30 + 1,
      signal: rapidAbort.signal,
      onReady: () => {}
    });
  }
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(segmentStarts, startsBeforeRapid, 'fly-over intents must not start backend work before dwell threshold');
  for (let i = 0; i < 80 && segmentStarts === startsBeforeRapid; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 3));
  }
  assert.equal(segmentStarts, startsBeforeRapid + 1, 'only final settled fly-over segment may start backend work');
  const rapidImage = images[images.length - 1];
  rapidImage.onload();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.ok(api.stats().intent_cancelled_before_start >= cancelledBeforeRapid + 19, 'crossed segments must be cancelled before FFmpeg starts');
  rapidAbort.abort();
  await new Promise(resolve => setTimeout(resolve, 10));

  // Pointerleave must also cancel an independent playback prewarm for the same
  // video; otherwise that extra lease can keep stale hover FFmpeg alive server-side.
  const cleanupStartsBefore = segmentStarts;
  const cleanupWarm = api.warm({ videoId: 'cleanup-fixture', duration: 90, targetTime: 1 });
  for (let i = 0; i < 50 && segmentStarts === cleanupStartsBefore; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  assert.equal(segmentStarts, cleanupStartsBefore + 1, 'cleanup fixture prewarm should start one backend segment');
  const cleanupHover = new AbortController();
  api.requestSegment({
    videoId: 'cleanup-fixture',
    duration: 90,
    targetTime: 35,
    signal: cleanupHover.signal,
    onReady: () => {}
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  cleanupHover.abort();
  await cleanupWarm;
  for (let i = 0; i < 40 && (api.stats().segment_inflight || api.stats().warm_inflight || api.stats().target_leases); i += 1) {
    await new Promise(resolve => setTimeout(resolve, 3));
  }
  const cleanupStats = api.stats();
  assert.equal(cleanupStats.segment_inflight, 0, 'pointerleave must abort all exact segment consumers for the video');
  assert.equal(cleanupStats.warm_inflight, 0, 'pointerleave must abort playback prewarm for the video');
  assert.equal(cleanupStats.target_leases, 0, 'pointerleave must leave no target lease');
  assert.equal(cleanupStats.active_target_requests, 0, 'pointerleave must leave no pending hover intent');

  console.log('PASS: debounce suppresses fly-over FFmpeg starts, cache telemetry works and pointerleave drains client storyboard work');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
