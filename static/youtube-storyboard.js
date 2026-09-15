(() => {
  'use strict';

  // V4.3: exact-segment-first timeline preview. Dense 1-fps 30-second
  // segments are the precise hover source. The old QUICK -> FULL upgrade path
  // is intentionally absent from the active timeline.
  const SEGMENT_DURATION = 30;
  const SEGMENT_MEMORY_LIMIT = 48;
  const QUICK_MEMORY_LIMIT = 24;
  const preloadMemory = new Map();
  const quickMemory = new Map();
  const segmentMemory = new Map();
  const segmentInFlight = new Map();
  const activeTargetRequests = new Map();
  const recentTargets = new Map();
  const warmInFlight = new Map();
  const playbackPrewarmTimers = new WeakMap();

  const metrics = {
    cacheHits: 0,
    cacheMisses: 0,
    requests: 0,
    abortedConsumers: 0,
    targetSwitches: 0,
    prewarmRequests: 0,
    exactReadyMs: [],
    imageReadyMs: [],
  };

  function now() {
    return globalThis.performance?.now?.() ?? Date.now();
  }

  function rememberSample(bucket, value) {
    bucket.push(Math.max(0, Number(value) || 0));
    if (bucket.length > 128) bucket.splice(0, bucket.length - 128);
  }

  function percentile(values, p) {
    if (!values.length) return 0;
    const ordered = [...values].sort((a, b) => a - b);
    const index = Math.min(ordered.length - 1, Math.max(0, Math.ceil(ordered.length * p) - 1));
    return Number(ordered[index].toFixed(2));
  }

  function mutationRequest(url, options = {}) {
    if (globalThis.ArchivebateAPI?.request) return globalThis.ArchivebateAPI.request(url, options);
    const headers = { ...(options.headers || {}) };
    const token = globalThis.document?.querySelector?.('meta[name="archivebate-mutation-token"]')?.content || '';
    if (token) headers['X-Archivebate-Mutation-Token'] = token;
    return fetch(url, { ...options, headers });
  }

  function cacheKey(videoId, duration) {
    return `${videoId}:${Math.max(1, Math.round(Number(duration) || 0))}`;
  }

  function segmentKey(videoId, duration, segmentIndex) {
    return `${cacheKey(videoId, duration)}:seg_${Number(segmentIndex) || 0}`;
  }

  function lruGet(map, key) {
    if (!map.has(key)) return null;
    const value = map.get(key);
    map.delete(key);
    map.set(key, value);
    return value;
  }

  function lruSet(map, key, value, limit) {
    if (map.has(key)) map.delete(key);
    map.set(key, value);
    while (map.size > limit) map.delete(map.keys().next().value);
    return value;
  }

  function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
      let timer = null;
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      timer = setTimeout(() => {
        signal?.removeEventListener?.('abort', abort);
        resolve();
      }, ms);
      signal?.addEventListener?.('abort', abort, { once: true });
    });
  }

  function findNearestIndex(times, targetTime) {
    if (!Array.isArray(times) || !times.length) return 0;
    const target = Number(targetTime) || 0;
    let low = 0;
    let high = times.length - 1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      const diff = Number(times[mid]) - target;
      if (Math.abs(diff) < 0.001) return mid;
      if (diff < 0) low = mid + 1;
      else high = mid - 1;
    }
    if (low >= times.length) return times.length - 1;
    if (high < 0) return 0;
    return Math.abs(Number(times[low]) - target) < Math.abs(Number(times[high]) - target) ? low : high;
  }

  function consume(promise, signal) {
    if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
    return new Promise((resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'));
      signal?.addEventListener?.('abort', abort, { once: true });
      promise.then(resolve, reject).finally(() => signal?.removeEventListener?.('abort', abort));
    });
  }

  async function preload(url, signal) {
    if (!url) throw new Error('Brak sprite_url');
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const started = now();
    let promise = preloadMemory.get(url);
    if (!promise) {
      promise = new Promise((resolve, reject) => {
        const img = new Image();
        const timer = setTimeout(() => finish(new Error('Sprite timeout')), 15000);
        const finish = error => {
          clearTimeout(timer);
          img.onload = img.onerror = null;
          error ? reject(error) : resolve(img);
        };
        img.onload = async () => {
          try {
            if (typeof img.decode === 'function') await img.decode();
          } catch (_) {}
          finish();
        };
        img.onerror = () => finish(new Error('Sprite load failed'));
        img.src = url;
      });
      preloadMemory.set(url, promise);
      promise.catch(() => {
        if (preloadMemory.get(url) === promise) preloadMemory.delete(url);
      });
      while (preloadMemory.size > 64) preloadMemory.delete(preloadMemory.keys().next().value);
    }
    const image = await consume(promise, signal);
    rememberSample(metrics.imageReadyMs, now() - started);
    return image;
  }

  async function fetchStatus(videoId, duration, signal) {
    const endpoint = `/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`;
    const response = await fetch(endpoint, { cache: 'no-store', signal });
    if (!response.ok) throw new Error(`Storyboard HTTP ${response.status}`);
    return response.json();
  }

  async function fetchSegmentStatus(videoId, duration, segmentIndex, signal) {
    const endpoint = `/api/storyboard/segment?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}&segment=${encodeURIComponent(segmentIndex)}`;
    const response = await fetch(endpoint, { cache: 'no-store', signal });
    if (!response.ok) throw new Error(`Storyboard segment HTTP ${response.status}`);
    return response.json();
  }

  async function normalizeReadyBoard(data, signal) {
    const img = await preload(data.sprite_url, signal);
    return { ...data, _image: img };
  }

  function createConsumerToken(prefix = 'seg') {
    return globalThis.crypto?.randomUUID?.() || `${prefix}-${Date.now()}-${Math.random()}`;
  }

  async function acquireLease(videoId, consumer, signal) {
    const url = `/api/storyboard/demand?id=${encodeURIComponent(videoId)}&consumer=${encodeURIComponent(consumer)}`;
    const response = await mutationRequest(url, { method: 'POST', signal });
    if (!response.ok) throw new Error(`Storyboard demand HTTP ${response.status}`);
    return url;
  }

  function releaseLease(url) {
    if (!url) return;
    mutationRequest(url, { method: 'DELETE', keepalive: true }).catch(() => {});
  }

  // QUICK is now cache-only from the UI. Cards may use an already prepared
  // approximate sprite, but hovering a card can no longer start eight FFmpeg
  // seeks and compete with playback/exact timeline work.
  async function prepare({ videoId, duration, signal, onStatus }) {
    duration = Number(duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) throw new Error('Brak ID lub długości filmu');
    const key = cacheKey(videoId, duration);
    const cached = lruGet(quickMemory, key);
    if (cached) return cached;
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    onStatus?.('cache-check');
    const data = await fetchStatus(videoId, duration, signal);
    if (data.status === 'ready' && data.sprite_url) {
      const board = await normalizeReadyBoard(data, signal);
      lruSet(quickMemory, key, board, QUICK_MEMORY_LIMIT);
      onStatus?.('ready');
      return board;
    }
    throw new Error('QUICK cold generation disabled in V4.3');
  }

  function createSegmentEntry({ videoId, duration, segmentIndex }) {
    const key = segmentKey(videoId, duration, segmentIndex);
    const controller = new AbortController();
    const entry = {
      key,
      videoId,
      duration,
      segmentIndex,
      controller,
      consumers: 0,
      leaseUrl: '',
      startedAt: now(),
      promise: null,
    };

    entry.promise = (async () => {
      metrics.requests += 1;
      const consumer = createConsumerToken(`seg-${segmentIndex}`);
      try {
        entry.leaseUrl = await acquireLease(videoId, consumer, controller.signal);
        const postUrl = `/api/storyboard/segment?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}&segment=${encodeURIComponent(segmentIndex)}&prefetch_next=false`;
        const startResponse = await mutationRequest(postUrl, {
          method: 'POST', cache: 'no-store', signal: controller.signal
        });
        if (!startResponse.ok) throw new Error(`Segment start HTTP ${startResponse.status}`);
        let data = await startResponse.json();
        if (data.status === 'ready' && data.sprite_url) {
          const board = await normalizeReadyBoard(data, controller.signal);
          lruSet(segmentMemory, key, board, SEGMENT_MEMORY_LIMIT);
          rememberSample(metrics.exactReadyMs, now() - entry.startedAt);
          return board;
        }

        // Shared and fully abortable status wait. Backend V4.3 may hold each GET
        // briefly while a segment is building, so this loop does not hammer it.
        for (let attempt = 0; attempt < 70; attempt += 1) {
          await sleep(attempt < 6 ? 80 : 180, controller.signal);
          data = await fetchSegmentStatus(videoId, duration, segmentIndex, controller.signal);
          if (data.status === 'ready' && data.sprite_url) {
            const board = await normalizeReadyBoard(data, controller.signal);
            lruSet(segmentMemory, key, board, SEGMENT_MEMORY_LIMIT);
            rememberSample(metrics.exactReadyMs, now() - entry.startedAt);
            return board;
          }
          if (data.status === 'error') throw new Error(data.error || 'Segment error');
        }
        throw new Error('Przekroczono czas przygotowania segmentu');
      } finally {
        releaseLease(entry.leaseUrl);
        if (segmentInFlight.get(key) === entry) segmentInFlight.delete(key);
      }
    })();

    segmentInFlight.set(key, entry);
    return entry;
  }

  function consumeSegmentEntry(entry, signal) {
    if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
    entry.consumers += 1;
    let settled = false;
    return new Promise((resolve, reject) => {
      const finish = () => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener?.('abort', onAbort);
        entry.consumers = Math.max(0, entry.consumers - 1);
      };
      const onAbort = () => {
        if (settled) return;
        metrics.abortedConsumers += 1;
        finish();
        if (entry.consumers === 0 && !segmentMemory.has(entry.key)) entry.controller.abort();
        reject(new DOMException('Aborted', 'AbortError'));
      };
      signal?.addEventListener?.('abort', onAbort, { once: true });
      entry.promise.then(
        value => { finish(); resolve(value); },
        error => { finish(); reject(error); }
      );
    });
  }

  async function prepareSegment({ videoId, duration, segmentIndex, signal }) {
    duration = Number(duration);
    segmentIndex = Number(segmentIndex) || 0;
    if (!videoId || !Number.isFinite(duration) || duration <= 0 || segmentIndex < 0) {
      throw new Error('Niepoprawne parametry segmentu');
    }
    const key = segmentKey(videoId, duration, segmentIndex);
    const cached = lruGet(segmentMemory, key);
    if (cached) {
      metrics.cacheHits += 1;
      return cached;
    }
    metrics.cacheMisses += 1;
    let entry = segmentInFlight.get(key);
    if (!entry) entry = createSegmentEntry({ videoId, duration, segmentIndex });
    return consumeSegmentEntry(entry, signal);
  }

  function getSegmentFromCache(videoId, duration, targetTime) {
    const segmentIndex = Math.max(0, Math.floor((Number(targetTime) || 0) / SEGMENT_DURATION));
    return lruGet(segmentMemory, segmentKey(videoId, duration, segmentIndex));
  }

  function noteTarget(videoId, targetTime) {
    const time = Number(targetTime) || 0;
    const previous = recentTargets.get(videoId);
    const stamp = now();
    let direction = 0;
    if (previous && stamp - previous.stamp < 900) {
      const delta = time - previous.time;
      if (Math.abs(delta) >= 0.25) direction = delta > 0 ? 1 : -1;
    }
    recentTargets.set(videoId, { time, stamp, direction });
    return direction;
  }

  function linkAbort(parentSignal, childController) {
    if (!parentSignal) return () => {};
    if (parentSignal.aborted) {
      childController.abort();
      return () => {};
    }
    const abort = () => childController.abort();
    parentSignal.addEventListener('abort', abort, { once: true });
    return () => parentSignal.removeEventListener('abort', abort);
  }

  function cancelActiveTarget(videoId) {
    const active = activeTargetRequests.get(videoId);
    if (!active) return;
    activeTargetRequests.delete(videoId);
    active.unlink?.();
    if (!active.controller.signal.aborted) active.controller.abort();
  }

  function requestSegment({ videoId, duration, targetTime, signal, onReady }) {
    const segmentIndex = Math.max(0, Math.floor((Number(targetTime) || 0) / SEGMENT_DURATION));
    const key = segmentKey(videoId, duration, segmentIndex);
    noteTarget(videoId, targetTime);

    const cached = lruGet(segmentMemory, key);
    if (cached) {
      metrics.cacheHits += 1;
      const active = activeTargetRequests.get(videoId);
      if (active && active.segmentIndex !== segmentIndex) {
        metrics.targetSwitches += 1;
        cancelActiveTarget(videoId);
      }
      if (typeof onReady === 'function') onReady(cached);
      return cached;
    }

    let active = activeTargetRequests.get(videoId);
    if (active && active.segmentIndex === segmentIndex && !active.controller.signal.aborted) {
      active.onReady = typeof onReady === 'function' ? onReady : active.onReady;
      return null;
    }

    if (active) {
      metrics.targetSwitches += 1;
      cancelActiveTarget(videoId);
    }

    const controller = new AbortController();
    const unlink = linkAbort(signal, controller);
    active = {
      segmentIndex,
      controller,
      unlink,
      onReady: typeof onReady === 'function' ? onReady : null,
      promise: null,
    };
    activeTargetRequests.set(videoId, active);
    active.promise = prepareSegment({ videoId, duration, segmentIndex, signal: controller.signal })
      .then(segment => {
        if (!controller.signal.aborted && typeof active.onReady === 'function') active.onReady(segment);
        return segment;
      })
      .catch(() => null)
      .finally(() => {
        if (activeTargetRequests.get(videoId) === active) activeTargetRequests.delete(videoId);
        unlink();
      });
    return null;
  }

  // Prewarm exact current segment only after playback has enough data. This
  // avoids competing with click-to-first-frame while still making the first
  // timeline hover much more likely to be a memory/disk hit.
  function warm({ videoId, duration, targetTime = 0 }) {
    duration = Number(duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) return Promise.resolve(null);
    const segmentIndex = Math.max(0, Math.floor((Number(targetTime) || 0) / SEGMENT_DURATION));
    const key = segmentKey(videoId, duration, segmentIndex);
    const cached = lruGet(segmentMemory, key);
    if (cached) return Promise.resolve(cached);
    if (warmInFlight.has(key)) return warmInFlight.get(key);

    metrics.prewarmRequests += 1;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    const promise = prepareSegment({ videoId, duration, segmentIndex, signal: controller.signal })
      .catch(() => null)
      .finally(() => {
        clearTimeout(timer);
        if (warmInFlight.get(key) === promise) warmInFlight.delete(key);
      });
    warmInFlight.set(key, promise);
    return promise;
  }

  function resolvePlaybackVideoId(video) {
    if (!video) return '';
    if (video.id === 'modalVideo') {
      return String(
        globalThis.ArchivebateAppContext?.state?.currentVideoId ||
        globalThis.state?.currentVideoId ||
        video.dataset?.videoId ||
        ''
      ).trim();
    }
    if (video.id === 'mainPlayer') {
      const fromData = String(video.dataset?.videoId || '').trim();
      if (fromData) return fromData;
      try {
        const fromQuery = String(new URLSearchParams(globalThis.location?.search || '').get('id') || '').trim();
        if (fromQuery) return fromQuery;
        const parts = String(globalThis.location?.pathname || '').split('/').filter(Boolean);
        if (parts.length >= 2 && parts[0].toLowerCase() === 'watch') {
          return decodeURIComponent(parts[parts.length - 1] || '').trim();
        }
      } catch (_) {}
      return '';
    }
    return '';
  }

  function schedulePlaybackPrewarm(video) {
    const videoId = resolvePlaybackVideoId(video);
    const duration = Number(video?.duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) return;

    const oldTimer = playbackPrewarmTimers.get(video);
    if (oldTimer) clearTimeout(oldTimer);
    let attempts = 0;
    const check = () => {
      if (video.paused || video.ended) return;
      attempts += 1;
      const bufferedAhead = Number(globalThis.ArchivebatePerf?.getBufferedAhead?.(video) || 0);
      const ready = Number(video.readyState || 0) >= 3 && bufferedAhead >= 2.0;
      if (!ready) {
        if (attempts < 6) {
          const timer = setTimeout(check, 350);
          playbackPrewarmTimers.set(video, timer);
        } else {
          playbackPrewarmTimers.delete(video);
        }
        return;
      }
      playbackPrewarmTimers.delete(video);
      warm({ videoId, duration, targetTime: Number(video.currentTime) || 0 });
    };
    const timer = setTimeout(check, 450);
    playbackPrewarmTimers.set(video, timer);
  }

  globalThis.document?.addEventListener?.('playing', event => {
    const video = event?.target;
    if (video?.id === 'modalVideo' || video?.id === 'mainPlayer') schedulePlaybackPrewarm(video);
  }, true);

  function ensureSpriteImage(element, board) {
    if (!element || !board || !board.sprite_url) return null;
    let img = element.querySelector?.(':scope > .timeline-sprite-image');
    const identity = `${board.sprite_url}|${board.frame_width}|${board.frame_height}|${board.columns}|${board.rows}`;
    if (!img) {
      img = document.createElement('img');
      img.className = 'timeline-sprite-image';
      img.alt = '';
      img.draggable = false;
      element.replaceChildren(img);
    }
    if (element.dataset.boardIdentity !== identity) {
      element.dataset.boardIdentity = identity;
      element.dataset.frameIndex = '-1';
      img.src = board.sprite_url;
      img.width = (Number(board.columns) || 1) * (Number(board.frame_width) || 160);
      img.height = (Number(board.rows) || 1) * (Number(board.frame_height) || 90);
      img.style.width = `${img.width}px`;
      img.style.height = `${img.height}px`;
      img.style.transform = 'translate3d(0,0,0)';
    }
    return img;
  }

  function applyFrame(element, board, posOrTime, options = {}) {
    if (!element || !board || !board.sprite_url) return false;
    const count = Number(board.frame_count) || 0;
    const columns = Number(board.columns) || 1;
    const frameWidth = Number(board.frame_width) || 160;
    const frameHeight = Number(board.frame_height) || 90;
    if (!count) return false;

    let targetTime;
    if (options.targetTime !== undefined) targetTime = Number(options.targetTime);
    else if (board.type === 'segment' || board.segment_index !== undefined) targetTime = Number(posOrTime) || 0;
    else if (typeof posOrTime === 'number' && posOrTime <= 1 && Number(board.duration || options.duration) > 1) {
      targetTime = Number(posOrTime) * Number(board.duration || options.duration);
    } else targetTime = Number(posOrTime) || 0;

    let index;
    if (Array.isArray(board.times) && board.times.length) index = findNearestIndex(board.times, targetTime);
    else {
      const clamped = Math.max(0, Math.min(1, Number(posOrTime) || 0));
      index = Math.min(count - 1, Math.floor(clamped * count));
    }

    const img = ensureSpriteImage(element, board);
    if (!img) return false;
    element.style.display = 'block';
    if (Number(element.dataset.frameIndex) !== index) {
      element.dataset.frameIndex = String(index);
      const col = index % columns;
      const row = Math.floor(index / columns);
      img.style.transform = `translate3d(${-col * frameWidth}px, ${-row * frameHeight}px, 0)`;
    }
    const frameTime = board.times?.[index] ?? (count > 1 ? (index / (count - 1)) * Number(board.duration || 0) : 0);
    return {
      ok: true,
      frameIndex: index,
      frameTime,
      targetTime,
      isExact: Array.isArray(board.times) && Math.abs(Number(frameTime) - targetTime) <= 1.0,
    };
  }

  function clearFrame(element) {
    if (element) element.style.display = 'none';
  }

  function attach({ video, videoId, timeline, signal, onBoard }) {
    let started = false;
    const begin = () => {
      if (started || signal?.aborted || !Number.isFinite(video.duration) || video.duration <= 0) return;
      started = true;
      // Do not start exact FFmpeg work here. Exact prewarm is deliberately
      // scheduled by the global `playing` hook only after useful media buffer
      // exists, while pointer hover requests its exact target explicitly.
      if (typeof onBoard === 'function') {
        prepare({ videoId, duration: video.duration, signal }).then(board => {
          if (!signal?.aborted) onBoard(board);
        }).catch(() => {});
      }
    };
    video.addEventListener('playing', begin, { once: true, signal });
    timeline?.addEventListener?.('pointerenter', begin, { once: true, signal });
    if (!video.paused && video.readyState >= 2) begin();
  }

  function stats() {
    return {
      cache_hits: metrics.cacheHits,
      cache_misses: metrics.cacheMisses,
      requests: metrics.requests,
      aborted_consumers: metrics.abortedConsumers,
      target_switches: metrics.targetSwitches,
      prewarm_requests: metrics.prewarmRequests,
      segment_cache_entries: segmentMemory.size,
      segment_inflight: segmentInFlight.size,
      active_target_requests: activeTargetRequests.size,
      preload_entries: preloadMemory.size,
      exact_ready_p50_ms: percentile(metrics.exactReadyMs, 0.50),
      exact_ready_p95_ms: percentile(metrics.exactReadyMs, 0.95),
      image_ready_p95_ms: percentile(metrics.imageReadyMs, 0.95),
      full_upgrade_enabled: false,
      cold_quick_generation_enabled: false,
    };
  }

  const api = {
    warm,
    prepare,
    applyFrame,
    clearFrame,
    attach,
    findNearestIndex,
    getSegmentFromCache,
    requestSegment,
    prepareSegment,
    cancelActiveTarget,
    stats,
    SEGMENT_DURATION,
  };

  window.ArchivebateYouTubeStoryboard = api;
})();
