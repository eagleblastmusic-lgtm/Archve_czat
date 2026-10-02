const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function classList() { return { add() {}, remove() {} }; }
function button() {
  return {
    classList: classList(),
    handler: null,
    addEventListener(name, fn) { if (name === 'click') this.handler = fn; },
    click() { if (this.handler) this.handler(); }
  };
}

const state = {
  mode: 'search', currentQuery: '#trans', currentPage: 4,
  sourceFilter: 'all', authorFilter: 'all', groupByAuthor: false
};
const sourceBtn = button();
const authorBtn = button();
const dom = {
  toggleCamwhoresBtn: sourceBtn,
  toggleAuthorFilterBtn: authorBtn,
  toggleGroupBtn: null,
  camwhoresToggleLabel: { innerText: '' }, sourceToggleIcon: { className: '' },
  authorFilterLabel: { innerText: '' }, authorFilterIcon: { className: '' }
};
const calls = [];
const window = {
  ArchivebateAppContext: { state, dom },
  localStorage: { setItem() {} },
  document: { body: { classList: classList() } }
};
const ctx = { window, globalThis: window, document: window.document, localStorage: window.localStorage, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/filters.js', 'utf8'), ctx);
window.ArchivebateFilters.init({
  showToast() {}, loadHomeVideos() {}, performSearch: (q, p) => calls.push([q, p])
});

sourceBtn.click();
assert.equal(state.sourceFilter, 'only-camwhores');
assert.deepEqual(calls, [['#trans', 1]]);

authorBtn.click();
assert.equal(state.authorFilter, 'only_fav');
assert.deepEqual(calls, [['#trans', 1], ['#trans', 1]]);

const searchSource = fs.readFileSync('static/search-results.js', 'utf8');
const indexSource = fs.readFileSync('static/index.html', 'utf8');
assert.match(indexSource, /id="searchScopeSelect"/, 'search must expose an explicit online/local scope');
assert.match(searchSource, /dom\.searchScopeSelect\?\.value === 'local'/, 'local search must be selected explicitly');
assert.match(searchSource, /\/api\/search\/local\?q=/, 'local search must use the local catalog endpoint');
assert.match(searchSource, /wyników lokalnych/, 'local results must be labelled as metadata-only');
console.log('PASS: search source/author filters rerun active query from page 1');

// Exercise real SSE callbacks and deadlines, including a silent connection and
// responses delivered after cancellation. These paths do not use JSON fetch.
function searchHarness() {
  const timers = new Map(), streams = [];
  let timerId = 0;
  const node = () => ({ style: {}, children: [], innerText: '',
    set innerHTML(value) { if (!value) this.children = []; },
    appendChild(child) { this.children.push(child); },
    append(...items) { this.children.push(...items); },
    replaceChildren() { this.children = []; }, setAttribute() {},
    addEventListener(name, fn) { this[name] = fn; } });
  const searchState = { currentPage: 7, lastPage: 9, videos: [{id:'old'}], groupByAuthor: true };
  const searchDom = { videoGrid: node(), videoCount: node(), searchScopeSelect: {value:'online'} };
  const context = { console: {error(){}}, AbortController,
    document: {querySelectorAll:()=>[], createElement:node},
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, {fn,ms}); return id; },
    clearTimeout(id) { timers.delete(id); },
    ArchivebateAppContext: {state:searchState,dom:searchDom},
    ArchivebateVideoViews: {
      beginViewRequest() {
        searchState.viewController?.abort();
        searchState.viewController = new AbortController();
        return searchState.viewGeneration = (searchState.viewGeneration || 0) + 1;
      },
      showSkeletons() { searchDom.videoGrid.children = ['skeleton']; }
    },
    ArchivebateVideoGrid: {
      reconcilePage(items) { searchDom.videoGrid.children = items.slice(); },
      renderVideoGrid(items) { searchDom.videoGrid.children = items.slice(); }
    },
    EventSource: class {
      constructor(url) { this.url = url; streams.push(this); }
      close() { this.closed = true; }
      send(payload) { this.onmessage({data:JSON.stringify(payload)}); }
    }
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(searchSource, context);
  return {context, state:searchState, dom:searchDom, timers, streams,
    start:q=>context.ArchivebateSearchResults.performSearch(q),
    tick(ms) { const entry = [...timers].find(([,t])=>t.ms===ms); assert(entry); timers.delete(entry[0]); entry[1].fn(); }
  };
}
(async () => {
  const h = searchHarness();
  await h.start('a"[query]'); // Free text must never become a CSS selector.
  assert.equal(h.state.videos.length, 0);
  assert.equal(h.state.lastPage, 1);
  assert.equal(h.state.isLoading, true);
  h.tick(5000);
  assert.match(h.dom.videoCount.innerText, /trwa dłużej/);
  h.tick(30000);
  assert.equal(h.streams[0].closed, true);
  assert.equal(h.state.isLoading, false);
  assert.equal(h.timers.size, 0);
  assert.equal(h.dom.videoGrid.children.length, 1);
  assert.equal(h.dom.videoGrid.children[0].textContent, 'Ponów wyszukiwanie');
  h.dom.videoGrid.children[0].click();
  assert.equal(h.streams.length, 2);
  const active = h.streams[1];
  active.send({type:'videos',videos:[{id:'partial'}]});
  active.onerror();
  assert.equal(h.state.videos[0].id, 'partial');
  assert.match(h.dom.videoCount.innerText, /niepełna/);
  assert.equal(h.dom.videoGrid.children[0].id, 'partial');
  assert.equal(h.timers.size, 0);

  await h.start('next');
  const cancelled = h.streams[2];
  await h.start('final');
  assert.equal(cancelled.closed, true);
  cancelled.send({type:'videos',videos:[{id:'stale'}]});
  assert.equal(h.state.videos.length, 0);
  const finalStream = h.streams[3];
  finalStream.send({type:'done',total_videos:0});
  assert.equal(h.state.activeSearchSource, null);
  assert.equal(h.state.lastPage, 1);
  assert.equal(h.state.isLoading, false);
  assert.equal(h.timers.size, 0);
  await h.start('malformed');
  h.streams[4].onmessage({data:'{'});
  assert.equal(h.streams[4].closed, true);
  assert.match(h.dom.videoCount.innerText, /Nie udało/);
  assert.equal(h.timers.size, 0);
  console.log('PASS: search silence, partial failure, retry, malformed payload, stale events and cleanup');
})().catch(error => {console.error(error); process.exitCode = 1;});
