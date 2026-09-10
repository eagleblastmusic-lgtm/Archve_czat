const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element() {
  return {
    style: {}, children: [], innerHTML: '', innerText: '', className: '',
    classList: { add() {}, remove() {} },
    appendChild(child) { this.children.push(child); },
    querySelectorAll(selector) { return this.children.filter(c => `.${c.className}` === selector); },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    addEventListener() {}, pause() {}, load() {}, play() { return Promise.resolve(); },
    removeAttribute(name) { this[name] = ''; }, getAttribute(name) { return this[name] || ''; }
  };
}

const state = {
  mode: 'home', currentPage: 1, videos: [], gridCardMap: new Map(),
  lastAppliedFeedRevision: -1, lastAppliedVideosCount: 0,
  lastAppliedFeedUpdatedAt: 0, lastAppliedFeedVideoCount: -1,
  feedSpecKey: null, feedSnapshotId: null, catalogRevision: null,
  viewGeneration: 0, gridGeneration: 0
};
const dom = {
  videoGrid: element(), videoCount: element(), viewTitle: element(),
  contentHeader: element(), tagsSection: element(), homeStatsBar: element(),
  pageJumpInput: element(), pageJumpInputTop: element(),
  statPageVideos: element(), statCatalogVideos: element(), statCatalogVideosLbl: element()
};
const context = {
  AbortController, URL, performance, console, setTimeout, clearTimeout,
  location: { href: 'http://localhost/' },
  ArchivebateAppContext: { state, dom },
  document: { body: { style: {} }, documentElement: { scrollTop: 0 }, getElementById: () => null, createElement: element, querySelectorAll: () => [] },
  ArchivebatePerf: { measure() {}, setPlaybackBusy() {} },
  ArchivebateVideoGrid: {
    reconcilePage(videos) { dom.videoGrid.children = videos.map(() => element()); },
    deduplicateVideos(videos) { return videos; }
  },
  ArchivebatePagination: { render() {} }
};
context.window = context;
let eventSource;
context.EventSource = class {
  constructor(url) { this.url = url; this.closed = false; this.onmessage = null; this.onerror = null; eventSource = this; }
  close() { this.closed = true; }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/video-views.js', 'utf8'), context);

const cards = Array.from({ length: 280 }, (_, i) => ({ id: `v${i}`, username: 'fixture' }));
context.ArchivebateAPI = { getJSON: async () => ({
  snapshot_id: '1', catalog_revision: 1, revision: 1, videos: cards,
  items: cards, complete: true, catalog_complete: false,
  video_count: 280, group_count: 280, page_count: 1, has_more: true, updated_at: 1
}) };

(async () => {
  await context.ArchivebateVideoViews.loadHomeVideos(1, true);
  assert(eventSource, 'partial catalog must open a progress stream');
  assert.equal(eventSource.closed, false);
  eventSource.onmessage({ data: JSON.stringify({
    snapshot_id: '1', catalog_revision: 1, revision: 1, videos: cards,
    items: cards, complete: true, catalog_complete: false,
    video_count: 560, group_count: 560, page_count: 2, has_more: true, updated_at: 2
  }) });
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(state.totalCatalogVideos, 560, 'same revision progress must update counters');
  eventSource.onmessage({ data: JSON.stringify({
    snapshot_id: '2', catalog_revision: 2, revision: 2, videos: cards,
    items: cards, complete: true, catalog_complete: true,
    video_count: 600, group_count: 600, page_count: 3, has_more: false, updated_at: 3
  }) });
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(state.catalogRevision, 2, 'published revision must replace partial revision');
  assert.equal(state.catalogComplete, true);
  assert.equal(eventSource.closed, true);
  console.log('PASS: partial catalog cards stream progress and switch to published revision');
})().catch(error => { console.error(error); process.exitCode = 1; });
