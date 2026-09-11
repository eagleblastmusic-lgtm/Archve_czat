const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeButton() {
  return { disabled: false, onclick: null };
}

// 1) Pagination routing: Next on search must request page 2 for the active query.
{
  const state = {
    mode: 'search',
    currentQuery: 'alice',
    currentPage: 1,
    lastPage: 3
  };
  const nextPageBtn = makeButton();
  const dom = {
    paginationSection: { style: {} },
    prevPageBtn: makeButton(),
    nextPageBtn,
    lastPageBtn: makeButton(),
    lastPageNumber: { innerText: '' },
    pageJumpInput: { value: 1, max: 1 },
    pageNumbersList: null,
    paginationSectionTop: null
  };
  const calls = [];
  const window = {
    ArchivebateAppContext: { state, dom },
    scrollTo() {}
  };
  const ctx = { window, globalThis: window, console };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/pagination.js', 'utf8'), ctx);
  window.ArchivebatePagination.init({
    loadHomeVideos() {},
    performSearch: (query, page) => calls.push({ query, page }),
    loadModelVideos() {},
    loadFavorites() {},
    loadHistory() {},
    loadFollowing() {}
  });
  window.ArchivebatePagination.render();
  assert.equal(nextPageBtn.disabled, false);
  nextPageBtn.onclick();
  assert.equal(state.currentPage, 2);
  assert.deepEqual(calls, [{ query: 'alice', page: 2 }]);
}

// 2) Search page >1 must tolerate slower live-source work and commit the requested page.
(async () => {
  const state = {
    mode: 'search',
    currentQuery: 'alice',
    currentPage: 1,
    lastPage: 4,
    sourceFilter: 'all',
    authorFilter: 'all',
    groupByAuthor: false,
    videos: [{ id: 'old' }],
    viewGeneration: 0,
    viewController: null,
    activeSearchSource: null
  };
  const dom = {
    accountPanelView: { style: {} },
    tagsSection: { style: {} },
    homeStatsBar: { style: {} },
    contentHeader: { style: {} },
    viewTitle: { innerText: '' },
    resetFilterBtn: { style: {} },
    pageJumpInput: { value: 1 },
    pageJumpInputTop: { value: 1 },
    paginationSection: { style: {} },
    paginationSectionTop: { style: {} },
    videoCount: { innerHTML: '', innerText: '' }
  };
  let requested = null;
  let rendered = null;
  let renderPaginationCalls = 0;
  const window = {
    ArchivebateAppContext: { state, dom },
    ArchivebateAPI: {
      getJSON: async (url, opts) => {
        requested = { url, opts };
        return { last_page: 4, total_videos: 900, videos: [{ id: 'page2' }] };
      }
    },
    ArchivebateVideoViews: {
      beginViewRequest() {
        state.viewGeneration += 1;
        state.viewController = { signal: { aborted: false } };
        return state.viewGeneration;
      },
      showSkeletons() {},
      updateBackButtonUI() {}
    },
    ArchivebateVideoGrid: {
      renderVideoGrid(videos) { rendered = videos; },
      reconcilePage() {},
      appendVideoBatch() {}
    },
    ArchivebateVideoPrefetch: { scheduleThumbnailWarmup() {} },
    ArchivebatePagination: { render() { renderPaginationCalls += 1; } },
    ArchivebateToast: { show() {} },
    document: { querySelector() { return null; } }
  };
  const ctx = { window, globalThis: window, document: window.document, console };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/search-results.js', 'utf8'), ctx);

  await window.ArchivebateSearchResults.performSearch('alice', 2);
  assert.ok(requested.url.includes('page=2'));
  assert.equal(requested.opts.timeoutMs, 60000);
  assert.equal(state.currentPage, 2);
  assert.deepEqual(state.videos, [{ id: 'page2' }]);
  assert.deepEqual(rendered, [{ id: 'page2' }]);
  assert.ok(renderPaginationCalls >= 2);

  // 3) A failed later page restores the previous page and its videos.
  state.currentPage = 2;
  state.videos = [{ id: 'page2' }];
  window.ArchivebateAPI.getJSON = async () => {
    const err = new Error('timeout');
    err.code = 'timeout';
    throw err;
  };
  await window.ArchivebateSearchResults.performSearch('alice', 3);
  assert.equal(state.currentPage, 2);
  assert.deepEqual(state.videos, [{ id: 'page2' }]);
  assert.equal(dom.pageJumpInput.value, 2);
  assert.equal(dom.pageJumpInputTop.value, 2);

  console.log('PASS: pagination routing, slow-page timeout budget, rollback on page-load failure');
})().catch(err => {
  console.error(err);
  process.exitCode = 1;
});


// 4) Top paginator owns its handlers exactly once, including jump/last.
{
  function btn() { return { disabled: false, onclick: null, onkeydown: null }; }
  const state = { mode: 'home', currentPage: 1, lastPage: 5 };
  const topNext = btn();
  const topPrev = btn();
  const topLast = btn();
  const topJump = btn();
  const topInput = { value: 1, max: 1, onkeydown: null };
  const dom = {
    paginationSection: null,
    paginationSectionTop: { style: {} },
    prevPageBtnTop: topPrev,
    nextPageBtnTop: topNext,
    lastPageBtnTop: topLast,
    lastPageNumberTop: { innerText: '' },
    pageJumpInputTop: topInput,
    pageJumpBtnTop: topJump,
    pageNumbersListTop: null
  };
  const calls = [];
  const window = { ArchivebateAppContext: { state, dom }, scrollTo() {} };
  const ctx = { window, globalThis: window, console, document: { createElement() { throw new Error('not needed'); } } };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/pagination.js', 'utf8'), ctx);
  window.ArchivebatePagination.init({
    loadHomeVideos: page => calls.push(page), performSearch() {}, loadModelVideos() {},
    loadFavorites() {}, loadHistory() {}, loadFollowing() {}
  });
  window.ArchivebatePagination.render();
  topNext.onclick();
  assert.equal(state.currentPage, 2);
  assert.deepEqual(calls, [2], 'one top Next click must trigger exactly one navigation');

  state.currentPage = 2;
  window.ArchivebatePagination.render();
  topInput.value = '4';
  topJump.onclick();
  assert.equal(state.currentPage, 4);
  assert.deepEqual(calls, [2, 4]);

  state.currentPage = 4;
  window.ArchivebatePagination.render();
  topLast.onclick();
  assert.equal(state.currentPage, 5);
  assert.deepEqual(calls, [2, 4, 5]);

  const eventsSource = fs.readFileSync('static/app-events.js', 'utf8');
  assert.doesNotMatch(eventsSource, /nextPageBtnTop\.addEventListener/, 'top paginator must not have a second event owner');
}
