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
