// Offline diagnostics with small DOM/fetch mocks; no browser or external HTTP.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const result = {};

const scheduled = [];
const children = [];
const grid = {
  set innerHTML(value) { children.length = 0; },
  appendChild(fragment) { children.push(...fragment.children); }
};
const context = {
  dom: { videoGrid: grid }, state: { groupByAuthor: false, mode: 'home' },
  deduplicateVideos: xs => xs,
  createVideoCard: v => v.id,
  document: { createDocumentFragment: () => ({ children: [], appendChild(v) { this.children.push(v); } }) },
  updateCheckpointUI() {}, checkAndHighlightCheckpoint() {},
  window: { requestIdleCallback: true },
  requestIdleCallback: callback => scheduled.push(callback),
  setTimeout: callback => scheduled.push(callback)
};
vm.createContext(context);
vm.runInContext(app.slice(app.indexOf('function renderVideoGrid('), app.indexOf('// STOPNIOWE DOKŁADANIE')), context);
context.oldItems = Array.from({length: 64}, (_, i) => ({id: `old-${i}`}));
context.newItems = [{id: 'new-0'}];
vm.runInContext('renderVideoGrid(oldItems); renderVideoGrid(newItems);', context);
scheduled.splice(0).forEach(callback => callback());
result.render_race = {new_items: children.filter(x => x.startsWith('new')).length,
                      obsolete_items_appended: children.filter(x => x.startsWith('old')).length};
if (result.render_race.obsolete_items_appended !== 32) throw Error('Unexpected reviewed behavior');

(async () => {
  let bodyReads = 0;
  let fetched = 0;
  const perfContext = { window: {}, fetch: async () => {
    fetched++;
    return { arrayBuffer: () => { bodyReads++; return new Promise(() => {}); } };
  }};
  vm.createContext(perfContext);
  vm.runInContext(fs.readFileSync(path.join(root, 'static/performance.js'), 'utf8'), perfContext);
  await perfContext.window.ArchivebatePerf.prefetchUrls(Array.from({length: 12}, (_, i) => `/fixture/${i}`), {concurrency: 4});
  result.prefetch_body_completion = {fetches: fetched, bodies_awaited: bodyReads};

  let clearedTimers = 0;
  const apiContext = { window: {}, AbortController, navigator: {onLine: true},
    setTimeout: () => 1, clearTimeout: () => clearedTimers++,
    fetch: async () => ({ok: true, json: () => new Promise(() => {})}) };
  vm.createContext(apiContext);
  vm.runInContext(fs.readFileSync(path.join(root, 'static/api-client.js'), 'utf8'), apiContext);
  let settled = false;
  apiContext.window.ArchivebateAPI.getJSON('/fixture').finally(() => settled = true);
  await new Promise(resolve => setImmediate(resolve));
  result.json_timeout_after_headers = {timer_cleared: clearedTimers > 0, body_still_pending: !settled};

  fs.writeFileSync(path.join(__dirname, 'frontend_results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
})().catch(err => { console.error(err); process.exitCode = 1; });
