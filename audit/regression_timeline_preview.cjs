/**
 * Regression test for static/timeline-preview.js (Frontend V2 Timeline Preview)
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '..', 'static', 'timeline-preview.js'), 'utf8');

// Synthetic environment
const mockGlobal = {
  document: {
    querySelector: (sel) => {
      if (sel.includes('archivebate-mutation-token')) {
        return { getAttribute: () => 'mock_token_123' };
      }
      return null;
    }
  }
};

const fn = new Function('global', 'window', 'document', code);
fn(mockGlobal, mockGlobal, mockGlobal.document);

const TimelinePreview = mockGlobal.ArchivebateTimelinePreview;
assert(TimelinePreview, 'ArchivebateTimelinePreview must be exported');

// 1. Test binarySearchNearest
const sampleFrames = [
  [0.0, 0, 0],
  [10.0, 0, 1],
  [20.0, 0, 2],
  [30.0, 0, 3],
  [40.0, 0, 4]
];

assert.deepStrictEqual(TimelinePreview.binarySearchNearest(sampleFrames, 0), [0.0, 0, 0]);
assert.deepStrictEqual(TimelinePreview.binarySearchNearest(sampleFrames, -5), [0.0, 0, 0]);
assert.deepStrictEqual(TimelinePreview.binarySearchNearest(sampleFrames, 11), [10.0, 0, 1]);
assert.deepStrictEqual(TimelinePreview.binarySearchNearest(sampleFrames, 18), [20.0, 0, 2]);
assert.deepStrictEqual(TimelinePreview.binarySearchNearest(sampleFrames, 50), [40.0, 0, 4]);
console.log('PASS: binarySearchNearest logic');

// 2. Test Layer 0 native prefix rendering
const imgEl = { src: '', style: {} };
const spriteEl = { style: {} };
const timeTextEl = { innerText: '' };
const statusEl = { textContent: '', style: {} };

const renderedNative = TimelinePreview.render({
  videoKey: 'camwhores:id:cw_123',
  videoId: 'cw_123',
  targetTime: 15.0,
  duration: 60.0,
  pos: 0.25,
  spriteEl,
  imgEl,
  timeTextEl,
  statusEl,
  nativePrefix: 'https://thumbs.cdn/preview/',
  nativeCount: 15,
});

assert.strictEqual(renderedNative, true, 'Layer 0 native prefix must return true');
assert(imgEl.src.includes('https://thumbs.cdn/preview/'), 'Image src must be set from native prefix');
assert.strictEqual(imgEl.style.display, 'block');
assert.strictEqual(spriteEl.style.display, 'none');
assert.strictEqual(timeTextEl.innerText, '0:15');
console.log('PASS: Layer 0 native prefix rendering');

// 3. Test Progressive V2 Sprite Sheet Rendering
const testKey = 'archivebate:id:test_ab_123';
const manager = TimelinePreview.manager;
const entry = manager.getOrCreateEntry(testKey);

entry.manifest = {
  version: 11,
  rev: 2,
  duration: 100.0,
  frames: [
    [0.0, 0, 0],
    [50.0, 0, 1],
    [100.0, 0, 2]
  ]
};

// Mock preloaded sheet image
entry.sheets.set(0, {
  src: '/api/storyboard/v2/sheet?id=test_ab_123&sheet=0&v=2',
  complete: true
});

const renderedSprite = TimelinePreview.render({
  videoKey: testKey,
  videoId: 'test_ab_123',
  targetTime: 49.0,
  duration: 100.0,
  pos: 0.49,
  spriteEl,
  imgEl,
  timeTextEl,
  statusEl,
});

assert.strictEqual(renderedSprite, true, 'V2 sprite rendering must return true when sheet is ready');
assert.strictEqual(spriteEl.style.display, 'block');
assert.strictEqual(imgEl.style.display, 'none');
assert(spriteEl.style.backgroundImage.includes('sheet=0'));
assert.strictEqual(spriteEl.style.backgroundPosition, '-160px -0px'); // cell 1: col 1 (160px), row 0
console.log('PASS: Progressive V2 sprite sheet rendering');

// 4. Test attach and dispose event lifecycle
let moveHandler = null;
let leaveHandler = null;
const mockTimeline = {
  addEventListener: (ev, handler) => {
    if (ev === 'pointermove') moveHandler = handler;
    if (ev === 'pointerleave') leaveHandler = handler;
  },
  removeEventListener: (ev, handler) => {
    if (ev === 'pointermove' && moveHandler === handler) moveHandler = null;
    if (ev === 'pointerleave' && leaveHandler === handler) leaveHandler = null;
  },
  getBoundingClientRect: () => ({ left: 0, width: 1000 })
};

const handle = TimelinePreview.attach({
  timeline: mockTimeline,
  tooltip: { style: { setProperty: () => {} } },
  spriteEl,
  imgEl,
  timeTextEl,
  statusEl,
  getVideoId: () => 'vid_123',
  getDuration: () => 120.0
});

assert(moveHandler !== null, 'pointermove listener must be attached');
assert(leaveHandler !== null, 'pointerleave listener must be attached');

handle.dispose();
assert(moveHandler === null, 'pointermove listener must be detached on dispose');
assert(leaveHandler === null, 'pointerleave listener must be detached on dispose');
console.log('PASS: attach/dispose event lifecycle');

// 5. Test canonicalKey and source scoping
assert.strictEqual(TimelinePreview.canonicalKey('123', 'archivebate'), 'archivebate:id:123');
assert.strictEqual(TimelinePreview.canonicalKey('cw_123', 'camwhores'), 'camwhores:id:123');
assert.strictEqual(TimelinePreview.canonicalKey('camwhores:id:cw_123'), 'camwhores:id:123');
assert.strictEqual(TimelinePreview.canonicalKey('cw_999'), 'camwhores:id:999');
console.log('PASS: canonicalKey normalization');

// 6. Test stopPolling
const testPollKey = 'archivebate:id:poll_test_456';
const pollEntry = manager.getOrCreateEntry(testPollKey);
pollEntry.controller = new (class { constructor() { this.aborted = false; } abort() { this.aborted = true; } })();
pollEntry.fetching = true;
let notified = false;
pollEntry.listeners.add(() => { notified = true; });

TimelinePreview.stopPolling(testPollKey);
assert.strictEqual(pollEntry.controller, null, 'controller must be cleared on stopPolling');
assert.strictEqual(pollEntry.fetching, false, 'fetching must be false on stopPolling');
assert.strictEqual(pollEntry.listeners.size, 0, 'listeners must be cleared on stopPolling');
console.log('PASS: stopPolling lifecycle cleanup');

console.log('\nALL FRONTEND TIMELINE PREVIEW V2 TESTS PASSED.');
