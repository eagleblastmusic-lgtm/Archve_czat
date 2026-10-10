/**
 * Reproduction test for Priority P0 issues:
 * 1. P0-1: Critical timeline callback recursion & listener explosion.
 * 2. P0-2: Uncontrolled polling and HTTP requests on pointermove when storyboard is complete.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function setupTestEnvironment() {
  const code = fs.readFileSync(path.join(__dirname, '..', 'static', 'timeline-preview.js'), 'utf8');
  
  const fetchCalls = [];
  const fakeFetch = async (url, opts = {}) => {
    fetchCalls.push({ url: String(url), method: opts.method || 'GET' });
    if (url.includes('/api/storyboard/v2/build')) {
      return { ok: true, json: async () => ({ status: 'queued' }) };
    }
    if (url.includes('/api/storyboard/v2')) {
      return {
        ok: true,
        json: async () => ({
          version: 11,
          rev: 2,
          complete: true,
          sheets: [{ index: 0, url: '/api/storyboard/v2/sheet?id=test&sheet=0&v=2' }],
          frames: [[0.0, 0, 0], [150.0, 0, 1], [300.0, 0, 2]],
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const sandbox = {
    console,
    Date,
    Math,
    Set,
    Map,
    Promise,
    setTimeout,
    clearTimeout,
    AbortController,
    fetch: fakeFetch,
    document: {
      hidden: false,
      querySelector: () => null,
      createElement: () => ({ style: {}, dataset: {} }),
    },
    Image: class {
      constructor() {
        this.src = '';
      }
      decode() {
        return Promise.resolve();
      }
    },
    global: {},
    globalThis: {},
    window: {},
  };

  sandbox.window = sandbox;
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  vm.runInContext(code, context);

  return { context, fetchCalls };
}

function testP0_1_recursion_reproduction() {
  console.log('--- TESTING P0-1 RECURSION REPRODUCTION ---');
  const { context } = setupTestEnvironment();
  const manager = context.ArchivebateTimelinePreview.manager;

  const key = 'archivebate:id:test_rec';
  const entry = manager.getOrCreateEntry(key);

  // Pre-populate entry with ready manifest and sheet
  entry.manifest = {
    version: 11,
    rev: 2,
    complete: true,
    sheets: [{ index: 0, url: 'sheet_0.jpg' }],
    frames: [[0, 0, 0]],
  };
  entry.sheets.set(0, { src: 'sheet_0.jpg' });

  let recursionDepth = 0;
  let caughtError = null;

  // Simulate modal-player-controls pattern where render calls pollManifest with callback calling render
  function simulatedRender() {
    recursionDepth++;
    if (recursionDepth > 5000) {
      throw new RangeError('Maximum call stack size exceeded (simulated limit)');
    }
    manager.pollManifest(key, 'test_rec', 100, 'archivebate', () => {
      simulatedRender();
    });
  }

  try {
    simulatedRender();
  } catch (err) {
    caughtError = err;
  }

  console.log(`Recursion depth reached: ${recursionDepth}`);
  console.log(`Number of listeners accumulated in entry: ${entry.listeners.size}`);
  if (caughtError) {
    console.log(`REPRODUCED P0-1: Caught recursion error: ${caughtError.message}`);
  } else {
    console.log('WARNING: Did not throw, but check depth/listeners');
  }

  return { caughtError, recursionDepth, listenerCount: entry.listeners.size };
}

async function testP0_2_uncontrolled_polling_reproduction() {
  console.log('\n--- TESTING P0-2 UNCONTROLLED POLLING REPRODUCTION ---');
  const { context, fetchCalls } = setupTestEnvironment();
  const manager = context.ArchivebateTimelinePreview.manager;

  const key = 'archivebate:id:test_poll';
  
  // 1. First poll loads and completes the manifest
  await manager.pollManifest(key, 'test_poll', 100, 'archivebate');
  const initialFetchCount = fetchCalls.length;
  console.log(`Initial fetch calls after load complete: ${initialFetchCount}`);

  const entry = manager.cache.get(key);
  console.log(`Entry manifest complete status: ${entry?.manifest?.complete}`);
  console.log(`Entry fetching status: ${entry?.fetching}`);

  // 2. Simulate 50 pointermove events calling pollManifest as occurs in current modal-player-controls.js
  const callsBeforeMoves = fetchCalls.length;
  for (let i = 0; i < 50; i++) {
    manager.pollManifest(key, 'test_poll', 100, 'archivebate', () => {});
  }

  // Allow microtasks/promises to settle
  await new Promise(r => setTimeout(r, 50));

  const callsAfterMoves = fetchCalls.length;
  const redundantCalls = callsAfterMoves - callsBeforeMoves;
  console.log(`Redundant HTTP fetch calls triggered by pointermove on COMPLETE storyboard: ${redundantCalls}`);
  console.log(`Total fetch calls: ${fetchCalls.length}`);

  if (redundantCalls > 0) {
    console.log(`REPRODUCED P0-2: ${redundantCalls} unnecessary network requests fired for an already complete storyboard!`);
  }

  return { redundantCalls, totalCalls: fetchCalls.length };
}

async function main() {
  const p0_1 = testP0_1_recursion_reproduction();
  const p0_2 = await testP0_2_uncontrolled_polling_reproduction();

  console.log('\n--- REPRODUCTION SUMMARY ---');
  console.log(`P0-1 Recursion reproduced: ${p0_1.caughtError ? 'YES' : 'NO'} (depth=${p0_1.recursionDepth}, leaked listeners=${p0_1.listenerCount})`);
  console.log(`P0-2 Uncontrolled polling reproduced: ${p0_2.redundantCalls > 0 ? 'YES' : 'NO'} (${p0_2.redundantCalls} redundant requests)`);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
