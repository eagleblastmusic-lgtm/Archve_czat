const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// V4.3 removed QUICK→FULL cross-tab polling entirely. This regression now
// protects the replacement invariant: one exact-segment backend operation is
// shared by concurrent consumers, and aborting one consumer does not kill the
// work while another consumer is still waiting.
const source = fs.readFileSync('static/youtube-storyboard.js', 'utf8');
let demandPosts = 0;
let segmentPosts = 0;
let demandDeletes = 0;
let imageLoads = 0;

class FakeImage {
  constructor() { this.style = {}; }
  set src(value) {
    this._src = value;
    imageLoads += 1;
    setTimeout(() => this.onload?.(), 5);
  }
  get src() { return this._src; }
}

const context = {
  console,
  Map,
  Set,
  AbortController,
  DOMException,
  Image: FakeImage,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  performance,
  crypto: { randomUUID: () => `uuid-${Math.random()}` },
  document: {
    querySelector() { return null; },
    createElement() { return { style: {}, dataset: {}, querySelector: () => null, replaceChildren() {} }; }
  }
};
context.window = context;
context.ArchivebateAPI = {
  request: async (url, options = {}) => {
    const method = String(options.method || 'GET').toUpperCase();
    if (String(url).includes('/api/storyboard/demand')) {
      if (method === 'DELETE') demandDeletes += 1;
      else demandPosts += 1;
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (String(url).includes('/api/storyboard/segment')) {
      segmentPosts += 1;
      await new Promise(resolve => setTimeout(resolve, 10));
      return {
        ok: true,
        json: async () => ({
          status: 'ready',
          type: 'segment',
          segment_index: 4,
          frame_count: 3,
          columns: 3,
          rows: 1,
          frame_width: 160,
          frame_height: 90,
          times: [120, 121, 122],
          sprite_url: '/fixture/segment-4.jpg'
        })
      };
    }
    throw new Error(`Unexpected request ${method} ${url}`);
  }
};
context.fetch = async url => { throw new Error(`Unexpected GET ${url}`); };

vm.createContext(context);
vm.runInContext(source, context, { filename: 'youtube-storyboard.js' });

(async () => {
  const api = context.window.ArchivebateYouTubeStoryboard;
  assert.ok(api);
  assert.equal(api.stats().full_upgrade_enabled, false, 'FULL upgrade must remain disabled');

  const a = new AbortController();
  const b = new AbortController();
  const first = api.prepareSegment({ videoId: 'shared-fixture', duration: 180, segmentIndex: 4, signal: a.signal });
  const second = api.prepareSegment({ videoId: 'shared-fixture', duration: 180, segmentIndex: 4, signal: b.signal });

  a.abort();
  await assert.rejects(first, { name: 'AbortError' });
  const board = await second;
  assert.equal(board.segment_index, 4);
  assert.equal(demandPosts, 1, 'concurrent consumers should share one lease');
  assert.equal(segmentPosts, 1, 'concurrent consumers should share one segment start');
  assert.equal(imageLoads, 1, 'concurrent consumers should share one sprite load');

  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(demandDeletes, 1, 'shared lease should be released exactly once');
  assert.equal(api.stats().segment_inflight, 0, 'in-flight registry should be empty after completion');

  console.log('PASS: V4.3 shared exact-segment operation survives one consumer abort and cleans up once');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
