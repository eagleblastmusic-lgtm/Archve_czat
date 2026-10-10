/**
 * Real production TimelinePreviewManager contract, without network dependencies.
 * Ensures nonproductive 200 responses and failing build admission stop polling.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const production = fs.readFileSync(path.join(__dirname, '..', 'static', 'timeline-preview.js'), 'utf8');

function fixture(buildStatus, timeStep = 100) {
  const counts = { build: 0, manifest: 0, cancel: 0 };
  let now = 100000;
  const fakeDate = { now: () => (now += timeStep) };
  const manifest = { version: 12, rev: 1, complete: false, frames: [], sheets: [], native: {frames: []} };
  const sandbox = {
    console, AbortController, Promise, setTimeout, clearTimeout, Math, Date: fakeDate,
    document: { hidden: false, querySelector: () => null },
    fetch: async (url, opts = {}) => {
      if (String(url).includes('/build?')) {
        counts.build++;
        return { ok: true, json: async () => ({ build_status: buildStatus }) };
      }
      if (String(url).includes('/storyboard/v2?')) {
        counts.manifest++;
        return { ok: true, json: async () => manifest };
      }
      if (String(url).includes('/storyboard/cancel?')) {
        counts.cancel++;
        return { ok: true, json: async () => ({ok: true}) };
      }
      return { ok: true, json: async () => ({ok: true}) };
    },
  };
  sandbox.window = sandbox;
  sandbox.global = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(production, sandbox, {filename: 'timeline-preview.js'});
  return { manager: sandbox.ArchivebateTimelinePreview.manager, counts };
}

async function run() {
  const terminal = fixture('terminal_error');
  await Promise.race([
    terminal.manager.ensureStoryboard('archivebate:id:failed', 'failed', 60, 'archivebate'),
    new Promise((_, reject) => setTimeout(() => reject(new Error('Terminal build did not stop')), 2500)),
  ]);
  const failed = terminal.manager.getOrCreateEntry('archivebate:id:failed', 'archivebate');
  assert.equal(failed.terminalFailure, true);
  assert.equal(failed.lifecycle, 'ERROR');
  assert.ok(terminal.counts.manifest <= 6, 'Terminal error must not repeatedly poll manifest');
  const countBefore = terminal.counts.manifest;
  await terminal.manager.ensureStoryboard('archivebate:id:failed', 'failed', 60, 'archivebate');
  assert.equal(terminal.counts.manifest, countBefore, 'Error state must block automatic restarts');

  const stalled = fixture('queued', 501);
  stalled.manager.noProgressTimeoutMs = 1000;
  await Promise.race([
    stalled.manager.ensureStoryboard('archivebate:id:stalled', 'stalled', 60, 'archivebate'),
    new Promise((_, reject) => setTimeout(() => reject(new Error('No-progress watchdog did not stop')), 2500)),
  ]);
  const entry = stalled.manager.getOrCreateEntry('archivebate:id:stalled', 'archivebate');
  assert.equal(entry.lifecycle, 'ERROR');
  assert.equal(entry.terminalFailure, true);
  assert.ok(stalled.counts.manifest <= 8, 'No-progress polling must be bounded');
  assert.equal(stalled.counts.cancel, 1, 'Terminal no-progress must release the lease');
  console.log('PASS: production V2 client handles terminal build and no-progress without infinite polling');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
