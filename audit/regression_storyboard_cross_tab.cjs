const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

// Dwie izolowane karty współdzielą kanał i localStorage. Test sprawdza, że
// tylko właściciel dzierżawy odpytuje status, a druga karta dostaje ten sam
// gotowy manifest i obraz.
const source = fs.readFileSync('static/youtube-storyboard.js', 'utf8');
const channelGroups = new Map();
const storageData = new Map();

class FakeBroadcastChannel {
  constructor(name) {
    this.name = name;
    this.listeners = new Set();
    if (!channelGroups.has(name)) channelGroups.set(name, new Set());
    channelGroups.get(name).add(this);
  }
  addEventListener(type, listener) {
    if (type === 'message') this.listeners.add(listener);
  }
  removeEventListener(type, listener) {
    if (type === 'message') this.listeners.delete(listener);
  }
  postMessage(data) {
    for (const peer of channelGroups.get(this.name) || []) {
      if (peer === this) continue;
      setTimeout(() => {
        for (const listener of peer.listeners) listener({ data });
      }, 0);
    }
  }
  close() { channelGroups.get(this.name)?.delete(this); }
}

const sharedStorage = {
  getItem(key) { return storageData.has(key) ? storageData.get(key) : null; },
  setItem(key, value) { storageData.set(key, String(value)); },
  removeItem(key) { storageData.delete(key); }
};

let statusCalls = 0;
function makeImageClass() {
  return class FakeImage {
    set src(value) {
      this._src = value;
      setTimeout(() => this.onload?.(), 0);
    }
    get src() { return this._src; }
  };
}

function makeContext() {
  const context = {
    console,
    Map,
    Set,
    AbortController,
    BroadcastChannel: FakeBroadcastChannel,
    localStorage: sharedStorage,
    Image: makeImageClass(),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    queueMicrotask,
    performance,
    crypto: { randomUUID: () => `${Math.random()}-${Date.now()}` },
    __ARCHIVEBATE_STORYBOARD_TEST__: true,
    fetch: async (url) => {
      if (String(url).includes('/api/storyboard?')) {
        statusCalls += 1;
        return {
          ok: true,
          json: async () => statusCalls < 2
            ? { status: 'building', quality: 'quick' }
            : { status: 'ready', quality: 'full', sprite_url: '/fixture/full.jpg', frame_count: 8 }
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'youtube-storyboard.js' });
  return context;
}

(async () => {
  const first = makeContext();
  const second = makeContext();
  const a = new AbortController();
  const b = new AbortController();
  const delivered = [0, 0];
  const base = { videoId: 'cross-tab-fixture', duration: 30, key: 'cross-tab-fixture:30' };

  first.window.ArchivebateYouTubeStoryboard.__startUpgradeWatcher({
    ...base, signal: a.signal, onUpgrade: () => { delivered[0] += 1; }
  });
  second.window.ArchivebateYouTubeStoryboard.__startUpgradeWatcher({
    ...base, signal: b.signal, onUpgrade: () => { delivered[1] += 1; }
  });

  await new Promise(resolve => setTimeout(resolve, 2200));
  assert.equal(statusCalls, 2, 'one tab should poll pending then ready');
  assert.deepEqual(delivered, [1, 1], 'both tabs should receive the same full board');
  a.abort();
  b.abort();
  console.log('PASS: one QUICK→FULL status watcher shared across two tabs; manifest delivered to both');
})().catch(error => { console.error(error); process.exitCode = 1; });
