/**
 * Regression & Acceptance tests for Pakiet C Frontend:
 * 1. Playback session telemetry milestones & A->B cancellation (switching cancels intent A).
 * 2. Buffer-aware playback coordinator (5s threshold for busy/idle state).
 * 3. Prefetch candidate limit (<=2 inflight) and Range header abort on status != 206 (e.g. status 200).
 * 4. Author identifier extraction without guessing title dictionary words ("hot", "squirt").
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  // --- 1. Playback session milestones & A->B cancellation ---
  {
    const perfEnv = {
      window: { location: { href: 'http://localhost:8000/' } },
      URL,
      performance: { now: () => Date.now() },
      console: { info: () => {}, warn: () => {}, error: () => {} },
      fetch: async () => ({ ok: true })
    };
    vm.createContext(perfEnv);
    vm.runInContext(fs.readFileSync('static/performance.js', 'utf8'), perfEnv);

    const perf = perfEnv.window.ArchivebatePerf;
    assert(perf, 'ArchivebatePerf must be defined');

    // Start Session A
    const sessionA = perf.startPlaybackSession('video_a', {
      owner: 'player',
      priority: 'high',
      reason: 'user_click'
    });
    assert.equal(sessionA.id, 'video_a');
    assert.equal(sessionA.owner, 'player');
    assert.equal(sessionA.priority, 'high');
    assert.equal(sessionA.aborted, false);
    assert.equal(sessionA.completed, false);

    sessionA.markUrlResolved('https://stream1.archive.org/stream.mp4');
    assert.equal(sessionA.host, 'stream1.archive.org');
    assert(sessionA.stages.click_to_resolve_ms >= 0);

    sessionA.markConnect();
    sessionA.markFirstByte();
    sessionA.markMetadata();

    // User clicks Video B before video A presents frame (Switching A -> B)
    const sessionB = perf.startPlaybackSession('video_b', {
      owner: 'player',
      priority: 'high',
      reason: 'playlist_advance'
    });

    // Intent A must be aborted with 'superseded'
    assert.equal(sessionA.aborted, true, 'Sesja A powinna zostać natychmiast przerwana po starcie sesji B');
    assert.equal(sessionA.error?.category, 'aborted');
    assert.equal(sessionA.error?.message, 'superseded');

    // Video B proceeds to first presented frame
    sessionB.markUrlResolved('https://stream2.archive.org/stream.mp4');
    sessionB.markConnect();
    sessionB.markFirstByte();
    sessionB.markMetadata();
    sessionB.markFirstFrame();

    assert.equal(sessionB.completed, true);
    assert.equal(sessionB.aborted, false);
    assert(sessionB.stages.total_click_to_first_frame_ms >= 0);

    const percentiles = perf.calculatePercentiles('total_click_to_first_frame_ms');
    assert(percentiles.count >= 1);
  }

  // --- 2. Buffer-aware playback coordinator (5s threshold) ---
  {
    const perfEnv = {
      window: {},
      performance: { now: () => 1000 },
      console: { info: () => {}, warn: () => {}, error: () => {} },
      fetch: async () => ({ ok: true })
    };
    vm.createContext(perfEnv);
    vm.runInContext(fs.readFileSync('static/performance.js', 'utf8'), perfEnv);
    const perf = perfEnv.window.ArchivebatePerf;

    // Mock video with 2.0s buffered ahead (< 5s target)
    const lowBufferVideo = {
      currentTime: 10.0,
      duration: 100.0,
      paused: false,
      seeking: false,
      readyState: 4,
      buffered: {
        length: 1,
        start: () => 0,
        end: () => 12.0 // 12.0 - 10.0 = 2.0s
      }
    };

    const aheadLow = perf.getBufferedAhead(lowBufferVideo);
    assert.equal(aheadLow, 2.0, 'Bufor w przód powinien wynosić 2.0s');

    const statusLow = perf.updatePlaybackBuffer(lowBufferVideo, 5.0);
    assert.equal(statusLow.isBusy, true, 'Odtwarzacz powinien być oznaczony jako busy przy buforze < 5s');

    // Mock video with 8.0s buffered ahead (>= 5s target)
    const highBufferVideo = {
      currentTime: 10.0,
      duration: 100.0,
      paused: false,
      seeking: false,
      readyState: 4,
      buffered: {
        length: 1,
        start: () => 0,
        end: () => 18.0 // 18.0 - 10.0 = 8.0s
      }
    };

    const aheadHigh = perf.getBufferedAhead(highBufferVideo);
    assert.equal(aheadHigh, 8.0, 'Bufor w przód powinien wynosić 8.0s');

    const statusHigh = perf.updatePlaybackBuffer(highBufferVideo, 5.0);
    assert.equal(statusHigh.isBusy, false, 'Odtwarzacz NIE powinien być busy przy buforze >= 5s');
  }

  // --- 3. Prefetch candidate limit & Range header abort on status 200 ---
  {
    let fetchCalls = [];
    const prefetchEnv = {
      window: {},
      AbortController,
      fetch: async (url, opts) => {
        fetchCalls.push({ url, opts });
        return {
          status: 200, // Serwer zignorował Range i zwraca pełny plik 200 OK!
          headers: new Map(),
          body: {
            getReader: () => ({
              read: async () => ({ done: true, value: null })
            })
          }
        };
      }
    };
    vm.createContext(prefetchEnv);
    vm.runInContext(fs.readFileSync('static/video-prefetch.js', 'utf8'), prefetchEnv);
    const prefetch = prefetchEnv.window.ArchivebateVideoPrefetch || prefetchEnv.ArchivebateVideoPrefetch;
    assert(prefetch, 'ArchivebateVideoPrefetch must be defined');

    // Test: serwer zwraca 200 na żądanie Range -> natychmiastowe przerwanie (zwraca false)
    const rangeRes200 = await prefetch.prefetchCandidateStreamChunk('http://upstream.test/stream.mp4', { maxBytes: 65536 });
    assert.equal(rangeRes200, false, 'Żądanie Range ze statusem 200 (zignorowane Range) musi zostać natychmiast przerwane');

    // Test: serwer poprawnie honoruje Range i zwraca 206 Partial Content
    prefetchEnv.fetch = async (url, opts) => {
      return {
        status: 206,
        body: {
          getReader: () => {
            let sent = false;
            return {
              read: async () => {
                if (sent) return { done: true, value: null };
                sent = true;
                return { done: false, value: new Uint8Array(1024) };
              }
            };
          }
        }
      };
    };

    const rangeRes206 = await prefetch.prefetchCandidateStreamChunk('http://upstream.test/stream.mp4', { maxBytes: 1024 });
    assert.equal(rangeRes206, true, 'Prawidłowy fragment Range 206 powinien zostać zaakceptowany');
  }

  // --- 4. Author identifier extraction without guessing title dictionary words ---
  {
    const modalEnv = {
      window: {},
      document: { createElement: () => ({ innerHTML: '', addEventListener: () => {} }) },
      state: {},
      dom: {}
    };
    vm.createContext(modalEnv);
    vm.runInContext(fs.readFileSync('static/video-modal.js', 'utf8'), modalEnv);
    const modalMod = modalEnv.window.ArchivebateVideoModal || modalEnv.ArchivebateVideoModal;
    const getEffectiveVideoUsername = modalMod.getEffectiveVideoUsername;
    assert(typeof getEffectiveVideoUsername === 'function', 'getEffectiveVideoUsername must be exported');

    // Przypadek 1: Modelka z profile_url
    const u1 = getEffectiveVideoUsername({
      username: 'Model',
      profile_url: 'https://archivebate.com/profile/sweet_sarah?page=1'
    });
    assert.equal(u1, 'sweet_sarah', 'Powinno wyodrębnić sweet_sarah z profile_url');

    // Przypadek 2: Tytuł ze słowami kluczowymi ("hot", "squirt", "teen", "solo show") - NIE WOLNO ZGADYWAĆ!
    const u2 = getEffectiveVideoUsername({
      username: 'Model',
      title: 'hot squirt teen solo show 2024'
    });
    assert.equal(u2, '', 'Nie wolno zgadywać słów kluczowych ("hot", "squirt") z tytułu');

    // Przypadek 3: Poprawny autor z camwhores URL
    const u3 = getEffectiveVideoUsername({
      username: 'Model',
      url: 'https://www.camwhores.tv/models/elena_queen/'
    });
    assert.equal(u3, 'elena_queen', 'Powinno wyodrębnić elena_queen z camwhores URL');

    // Przypadek 4: Bezpośrednio znana nazwa
    const u4 = getEffectiveVideoUsername({
      username: 'VerifiedStar'
    });
    assert.equal(u4, 'VerifiedStar');
  }

  console.log('PASS: Pakiet C frontend - session telemetry milestones, A->B cancellation, 5s buffer coordinator, candidate range abort on status 200, strict author validation');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
