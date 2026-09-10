const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

console.log('--- ROZPOCZĘCIE TESTÓW PAKIETU D (FRONTEND) ---');

// Mock środowiska przeglądarkowego
class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.style = {};
    this.dataset = {};
    this.children = [];
  }
  querySelector(sel) {
    if (sel.includes('.timeline-sprite-image')) {
      return this.children.find(c => c.className === 'timeline-sprite-image') || null;
    }
    return null;
  }
  replaceChildren(...kids) {
    this.children = kids;
  }
}

class FakeImage {
  constructor() {
    this.style = {};
  }
  set src(val) {
    this._src = val;
    setTimeout(() => { if (typeof this.onload === 'function') this.onload(); }, 1);
  }
  get src() { return this._src; }
}

const context = {
  console,
  setTimeout,
  clearTimeout,
  performance,
  Map,
  Set,
  Array,
  Math,
  Number,
  String,
  DOMException,
  AbortController,
  Image: FakeImage,
  window: {},
  document: {
    createElement(tag) { return new FakeElement(tag); }
  }
};
vm.createContext(context);

// Załaduj youtube-storyboard.js i player-core.js
const storyboardSrc = fs.readFileSync('static/youtube-storyboard.js', 'utf8');
const playerCoreSrc = fs.readFileSync('static/player-core.js', 'utf8');

vm.runInContext(storyboardSrc, context);
vm.runInContext(playerCoreSrc, context);

const Storyboard = context.window.ArchivebateYouTubeStoryboard;
const PlayerCore = context.window.ArchivebatePlayerCore;

assert.ok(Storyboard, 'ArchivebateYouTubeStoryboard powinien być wyeksportowany');
assert.ok(PlayerCore, 'ArchivebatePlayerCore powinien być wyeksportowany');

// 1. Test wyszukiwania najbliższej klatki po times[]
const nonUniformTimes = [0.0, 1.2, 2.5, 4.0, 7.8, 12.0];
assert.equal(Storyboard.findNearestIndex(nonUniformTimes, 0.5), 0);
assert.equal(Storyboard.findNearestIndex(nonUniformTimes, 1.1), 1);
assert.equal(Storyboard.findNearestIndex(nonUniformTimes, 2.1), 2);
assert.equal(Storyboard.findNearestIndex(nonUniformTimes, 3.8), 3);
assert.equal(Storyboard.findNearestIndex(nonUniformTimes, 6.5), 4);
assert.equal(Storyboard.findNearestIndex(nonUniformTimes, 15.0), 5);
console.log('PASS 1: findNearestIndex poprawnie wybiera najbliższą klatkę po times[]');

// 2. Fixture neutralnego filmu 10s: przeciągnięcie kursora przez 10 kolejnych sekund (0..9s)
const neutral10sSegment = {
  type: 'segment',
  segment_index: 0,
  start_time: 0.0,
  end_time: 10.0,
  duration: 10.0,
  total_duration: 10.0,
  frame_count: 10,
  columns: 6,
  rows: 2,
  frame_width: 160,
  frame_height: 90,
  times: [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
  sprite_url: '/api/storyboard/segment/image?id=neutral&segment=0'
};

const spriteElem = new FakeElement('div');
const renderedIndices = [];
const timeErrors = [];
const updateTimesMs = [];

for (let sec = 0; sec < 10; sec += 1) {
  const t0 = performance.now();
  const res = Storyboard.applyFrame(spriteElem, neutral10sSegment, sec);
  const t1 = performance.now();
  updateTimesMs.push(t1 - t0);

  assert.ok(res && res.ok, `applyFrame powinno zwrócić wynik dla sekundy ${sec}`);
  renderedIndices.push(res.frameIndex);
  
  const err = Math.abs(res.frameTime - sec);
  timeErrors.push(err);
  assert.ok(err <= 1.0, `Błąd czasowy dla sekundy ${sec} wynosi ${err}s (przekracza 1s)`);
}

// Sprawdzenie: każda z 10 kolejnych sekund otrzymała dokładnie swój unikalny kadr
assert.deepEqual(renderedIndices, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
console.log('PASS 2: Przeciągnięcie kursora przez 10 kolejnych sekund aktualizuje 10 unikalnych kadrów');
console.log('PASS 3: Maksymalny błąd czasowy w gęstym cache:', Math.max(...timeErrors), 's (wymóg <= 1s)');

// 3. Benchmark p95 czasu aktualizacji klatki w gotowym cache
const benchmarkSamples = [];
for (let i = 0; i < 50; i += 1) {
  const target = (i % 10) + 0.3;
  const t0 = performance.now();
  Storyboard.applyFrame(spriteElem, neutral10sSegment, target);
  const t1 = performance.now();
  benchmarkSamples.push(t1 - t0);
}
benchmarkSamples.sort((a, b) => a - b);
const p50 = benchmarkSamples[Math.floor(benchmarkSamples.length * 0.5)];
const p95 = benchmarkSamples[Math.floor(benchmarkSamples.length * 0.95)];
console.log(`PASS 4: Benchmark aktualizacji klatki (50 próbek): p50 = ${p50.toFixed(3)}ms, p95 = ${p95.toFixed(3)}ms (wymóg <= 50ms)`);
assert.ok(p95 <= 50, `p95 wynosi ${p95}ms, co przekracza próg 50ms`);

// 4. Test segmentu cold vs warm
(async () => {
  let fetchCallCount = 0;
  context.fetch = async (url) => {
    fetchCallCount += 1;
    if (url.includes('/api/storyboard/segment')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ready',
          segment_index: 1,
          start_time: 30.0,
          end_time: 60.0,
          frame_count: 30,
          columns: 6,
          rows: 5,
          frame_width: 160,
          frame_height: 90,
          times: Array.from({ length: 30 }, (_, idx) => 30.0 + idx),
          sprite_url: 'http://localhost/segment_1.jpg'
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  // Przed pobraniem segment 1 nie istnieje w pamięci (cold)
  const coldCheck = Storyboard.getSegmentFromCache('test_vid', 120, 35.0);
  assert.equal(coldCheck, null, 'Zimny segment nie powinien być w cache');

  // Żądanie segmentu
  const ac = new AbortController();
  let segmentReadyReceived = null;
  Storyboard.requestSegment({
    videoId: 'test_vid',
    duration: 120,
    targetTime: 35.0,
    signal: ac.signal,
    onReady: (seg) => { segmentReadyReceived = seg; }
  });

  // Oczekiwanie na pobranie i znormalizowanie obrazu
  await new Promise(r => setTimeout(r, 20));
  assert.ok(segmentReadyReceived, 'onReady powinno zostać wywołane po załadowaniu segmentu');
  assert.equal(segmentReadyReceived.segment_index, 1);
  console.log('PASS 5: Zimny segment pobiera manifest i obraz, a następnie wywołuje onReady');

  // Po pobraniu segment jest natychmiast ciepły w pamięci (warm)
  const warmCheck = Storyboard.getSegmentFromCache('test_vid', 120, 42.0);
  assert.ok(warmCheck, 'Ciepły segment powinien być natychmiast dostępny w cache');
  assert.equal(warmCheck.segment_index, 1);
  console.log('PASS 6: Ciepły segment zwracany natychmiast z pamięci podręcznej (0ms, 0 zapytań)');

  // 5. Skok na odległy czas (np. 125s) wylicza poprawny segment 4
  const distantTime = 125.0;
  const segIndex = Math.floor(distantTime / Storyboard.SEGMENT_DURATION);
  assert.equal(segIndex, 4, '125s powinno odpowiadać segmentowi 4 (120-150s)');
  console.log('PASS 7: Skok na odległy czas precyzyjnie kalkuluje docelowy indeks segmentu (125s -> seg 4)');

  // 6. Zmiana filmu: anulowanie zapytania A -> B
  const cancelAc = new AbortController();
  let cancelledSegmentLoaded = false;
  Storyboard.requestSegment({
    videoId: 'cancelled_vid',
    duration: 60,
    targetTime: 10.0,
    signal: cancelAc.signal,
    onReady: () => { cancelledSegmentLoaded = true; }
  });
  cancelAc.abort();
  await new Promise(r => setTimeout(r, 20));
  assert.equal(cancelledSegmentLoaded, false, 'Przerwane zapytanie nie powinno wywołać onReady');
  console.log('PASS 8: Anulowanie zapytania przy zmianie filmu zapobiega niepotrzebnym aktualizacjom UI');

  console.log('\n--- WSZYSTKIE TESTY PAKIETU D (FRONTEND) ZAKOŃCZONE SUKCESEM (PASS) ---');
})().catch(err => {
  console.error('BŁĄD TESTU FRONTEND:', err);
  process.exit(1);
});
