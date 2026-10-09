/**
 * Benchmark Script for Timeline Preview V2 Performance Metrics.
 */
const fs = require('fs');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '..', 'static', 'timeline-preview.js'), 'utf8');

const mockGlobal = {
  document: {
    querySelector: () => null
  }
};

const fn = new Function('global', 'window', 'document', code);
fn(mockGlobal, mockGlobal, mockGlobal.document);

const TimelinePreview = mockGlobal.ArchivebateTimelinePreview;
const manager = TimelinePreview.manager;

const key = 'archivebate:id:bench_test';
const entry = manager.getOrCreateEntry(key);

// Populate 100 frames
const frames = [];
for (let i = 0; i < 100; i++) {
  frames.push([i * 36.0, 0, i]);
}

entry.manifest = {
  version: 11,
  rev: 2,
  duration: 3600.0,
  frames: frames
};

entry.sheets.set(0, {
  src: '/api/storyboard/v2/sheet?id=bench_test&sheet=0&v=2',
  complete: true
});

const spriteEl = {
  style: {},
  dataset: {},
  querySelector: () => null,
  replaceChildren: () => {}
};
const imgEl = { style: {} };
const timeTextEl = { innerText: '' };
const statusEl = { style: {}, textContent: '' };

// Measure 10,000 render calls across varying pointer positions
const iterations = 10000;
const start = performance.now();

for (let i = 0; i < iterations; i++) {
  const pos = (i % 1000) / 1000;
  const targetTime = pos * 3600.0;
  TimelinePreview.render({
    videoKey: key,
    videoId: 'bench_test',
    targetTime,
    duration: 3600.0,
    pos,
    spriteEl,
    imgEl,
    timeTextEl,
    statusEl
  });
}

const elapsed = performance.now() - start;
const avgUs = (elapsed / iterations) * 1000;
const fpsCapacity = Math.round(1000 / (elapsed / iterations));

console.log(JSON.stringify({
  iterations,
  total_time_ms: Math.round(elapsed * 100) / 100,
  avg_render_time_us: Math.round(avgUs * 10) / 10,
  avg_render_time_ms: Math.round((elapsed / iterations) * 10000) / 10000,
  fps_capacity: fpsCapacity,
  frame_budget_percent: Math.round(((elapsed / iterations) / 16.66) * 1000) / 10
}, null, 2));
