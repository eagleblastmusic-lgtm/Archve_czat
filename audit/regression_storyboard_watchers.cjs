const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/youtube-storyboard.js', 'utf8');
let calls = 0;
let finish;
const pending = new Promise(resolve => { finish = resolve; });
const context = {
  AbortController, console, Map, Set,
  upgradeWatchers: new Map(), memory: new Map(),
  sleep: async () => {},
  fetchStatus: async () => { calls++; return pending; },
  normalizeReadyBoard: async data => data,
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('  function startUpgradeWatcher'), source.indexOf('  async function prepare')), context);
(async () => {
  const first = new AbortController();
  const second = new AbortController();
  const delivered = [];
  const base = { videoId: 'fixture', duration: 10, key: 'fixture:10' };
  context.startUpgradeWatcher({ ...base, signal: first.signal, onUpgrade: () => delivered.push('first') });
  context.startUpgradeWatcher({ ...base, signal: second.signal, onUpgrade: () => delivered.push('second') });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  const shared = context.upgradeWatchers.get(base.key);
  first.abort();
  assert.equal(shared.controller.signal.aborted, false);
  finish({ status: 'ready', quality: 'full', sprite_url: 'fixture.jpg' });
  await shared.promise;
  assert.deepEqual(delivered, ['second']);
  assert.equal(context.upgradeWatchers.size, 0);
  console.log('PASS: one upgrade poll for two consumers; independent abort; watcher cleanup');
})().catch(error => { console.error(error); process.exitCode = 1; });
