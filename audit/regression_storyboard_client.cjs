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
  context.ArchivebateAPI = {
    request: async (url) => {
      if (String(url).includes('/api/storyboard/demand')) {
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

  console.log('PASS: shared segment abort/dedupe, attach gating and buffered watch-route prewarm work');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
