const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  const images = [];
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
    crypto: { randomUUID: () => `id-${Math.random()}` },
    document: { querySelector: () => null, createElement: () => ({ style: {} }) },
    fetch: async () => { throw new Error('unexpected fetch'); }
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'youtube-storyboard.js' });

  // The same sprite URL must have one underlying Image load even when one
  // consumer aborts. A surviving consumer must still resolve successfully.
  const api = context.window.ArchivebateYouTubeStoryboard;
  assert.ok(api, 'ArchivebateYouTubeStoryboard should be exported');

  // Access preload indirectly through two ready segment loads so this test stays
  // on the public contract instead of slicing private functions out of the file.
  let segmentStarts = 0;
  context.ArchivebateAPI = {
    request: async (url, options = {}) => {
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
            sprite_url: '/sprite'
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
  assert.equal(segmentStarts, 1, 'same segment must share one backend start');
  assert.equal(images.length, 1, 'same sprite must share one Image load');
  images[0].onload();
  const board = await second;
  assert.equal(board.sprite_url, '/sprite');

  const warm = await api.prepareSegment({ videoId: 'fixture', duration: 30, segmentIndex: 0 });
  assert.equal(warm.sprite_url, '/sprite');
  assert.equal(segmentStarts, 1, 'warm cache must not restart backend work');

  console.log('PASS: shared segment survives one consumer abort; image/backend work are deduplicated');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
