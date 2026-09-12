const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const state = {
  mode: 'home',
  currentPage: 1,
  currentQuery: '',
  sourceFilter: 'all',
  authorFilter: 'all',
  groupByAuthor: false,
  videos: [],
  viewGeneration: 0,
  viewController: null,
  activeSearchSource: null
};

const style = () => ({ display: '', });
const dom = {
  searchScopeSelect: { value: 'local' },
  searchInput: { value: 'fixture' },
  videoGrid: { innerHTML: '', replaceChildren() { this.innerHTML = ''; } },
  accountPanelView: { style: style() },
  tagsSection: { style: style() },
  homeStatsBar: { style: style() },
  contentHeader: { style: style() },
  viewTitle: { innerText: '' },
  videoCount: { innerHTML: '', innerText: '' },
  resetFilterBtn: { style: style() },
  pageJumpInput: { value: '' },
  pageJumpInputTop: { value: '' },
  paginationSection: { style: style() },
  paginationSectionTop: { style: style() },
  matchedProfiles: { style: style() },
  profilesList: { innerHTML: '' }
};

const localItems = [{ id: 'local-1', source: 'archivebate', username: 'fixture', title: 'Local metadata' }];
const calls = [];
const context = {
  window: null,
  globalThis: null,
  document: {
    querySelector() { return null; },
    createElement() { return { style: {}, className: '', append() {}, appendChild() {}, setAttribute() {}, textContent: '' }; }
  },
  console,
  AbortController,
  ArchivebateAppContext: { state, dom },
  ArchivebateVideoViews: {
    beginViewRequest() {
      state.viewGeneration += 1;
      state.viewController = new AbortController();
      return state.viewGeneration;
    },
    showSkeletons() {}
  },
  ArchivebateVideoGrid: {
    renderVideoGrid(items) { calls.push(['render', items]); },
    reconcilePage() {}
  },
  ArchivebateVideoPrefetch: { scheduleThumbnailWarmup() {} },
  ArchivebatePagination: { render() {} },
  ArchivebateAPI: {
    async getJSON(url) {
      calls.push(['api', url]);
      assert.match(url, /^\/api\/search\/local\?/);
      assert.doesNotMatch(url, /\/api\/search\/stream/);
      return { items: localItems, total: 1, page_count: 1, catalog_revision: 7 };
    }
  }
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/search-results.js', 'utf8'), context);

(async () => {
  await context.ArchivebateSearchResults.performSearch('fixture', 1);
  assert.equal(state.catalogRevision, 7);
  assert.deepEqual(state.videos, localItems);
  assert.equal(calls.filter(([kind]) => kind === 'api').length, 1);
  assert.equal(calls.filter(([kind]) => kind === 'render').length, 1);
  assert.match(dom.videoCount.innerText, /wyników lokalnych/);
  console.log('PASS: explicit local search uses catalog JSON, pins revision metadata, and never opens SSE');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
