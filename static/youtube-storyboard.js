(() => {
  'use strict';

  // V4.5.2: exact-segment-first timeline preview with player-first QoS.
  // Dense 1-fps 30-second segments remain authoritative, but uncached FFmpeg
  // work is never allowed to compete with critical primary playback.
  const SEGMENT_DURATION = 30;
  const SAMPLE_INTERVAL = 10;
  const SEGMENT_MEMORY_LIMIT = 48;
  const QUICK_MEMORY_LIMIT = 24;
  const HOVER_INTENT_MS = 140;
  const QOS_RECHECK_MS = 180;
  const EXACT_BUFFER_SECONDS = 3.0;
  const BACKGROUND_BUFFER_SECONDS = 8.0;
  const SEGMENT_BUILD_TIMEOUT_MS = 60000;
  const preloadMemory = new Map();
  const quickMemory = new Map();
  const segmentMemory = new Map();
  const segmentInFlight = new Map();
  const activeTargetRequests = new Map();
  const targetLeases = new Map();
  const recentTargets = new Map();
  const previewStates = new Map();
  const warmInFlight = new Map();
  const playbackPrewarmTimers = new WeakMap();
  const videoPreparations = new Map();

  const metrics = {
    cacheHits: 0,
    cacheMisses: 0,
    requests: 0,
    abortedConsumers: 0,
    targetSwitches: 0,
    prewarmRequests: 0,
    intentScheduled: 0,
    intentCancelled: 0,
    hoverSessionCancels: 0,
    qosBlockedStarts: 0,
    qosWaitLoops: 0,
    qosPrewarmSkips: 0,
    exactReadyMs: [],
    imageReadyMs: [],
    qosWaitMs: [],
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

  async function mutationRequest(url, options = {}) {
    if (globalThis.ArchivebateAPI?.request) return globalThis.ArchivebateAPI.request(url, options);
    const headers = { ...(options.headers || {}) };
    const token = globalThis.document?.querySelector?.('meta[name="archivebate-mutation-token"]')?.content || '';
    if (token) headers['X-Archivebate-Mutation-Token'] = token;
    const controller = new AbortController();
    const unlink = linkAbort(options.signal, controller);
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      return await fetch(url, { ...options, headers, signal: controller.signal });
    } finally {
      clearTimeout(timer);
      unlink();
    }
  }

  function cacheKey(videoId, duration) {
    return `${videoId}:${Math.max(1, Math.round(Number(duration) || 0))}`;
  }

  function segmentKey(videoId, duration, segmentIndex) {
    return `${cacheKey(videoId, duration)}:seg_${Number(segmentIndex) || 0}`;
  }

  function segmentIndexFor(duration, targetTime) {
    const lastTime = Math.max(0, (Number(duration) || 0) - 0.001);
    const time = Math.max(0, Math.min(lastTime, Number(targetTime) || 0));
    return Math.floor(time / SEGMENT_DURATION);
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

  function bufferedAhead(video) {
    try {
      if (globalThis.ArchivebatePerf?.getBufferedAhead) {
        return Math.max(0, Number(globalThis.ArchivebatePerf.getBufferedAhead(video) || 0));
      }
      const current = Number(video?.currentTime) || 0;
      const ranges = video?.buffered;
      for (let i = 0; i < (ranges?.length || 0); i += 1) {
        if (ranges.start(i) <= current + 0.1 && ranges.end(i) >= current) {
          return Math.max(0, ranges.end(i) - current);
        }
      }
    } catch (_) {}
    return 0;
  }

  function resolvePlaybackVideoId(video) {
    if (!video) return '';
    if (video.id === 'modalVideo') {
      return String(
        globalThis.ArchivebateAppContext?.state?.currentVideoDetails?.id ||
        globalThis.ArchivebateAppContext?.state?.currentVideoId ||
        globalThis.state?.currentVideoDetails?.id ||
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

  function primaryPlaybackVideo(videoId) {
    const modal = globalThis.document?.getElementById?.('modalVideo') || null;
    const watch = globalThis.document?.getElementById?.('mainPlayer') || null;
    const candidates = [watch, modal].filter(Boolean);
    const active = candidates.find(video => !video.paused && !video.ended);
    if (active) return active;
    return candidates.find(video => resolvePlaybackVideoId(video) === String(videoId || '')) || null;
  }

  function playbackAllowsStoryboard(videoId, minimumBuffer = EXACT_BUFFER_SECONDS) {
    const video = primaryPlaybackVideo(videoId);
    if (!video || video.ended || video.paused) return true;
    if (video.seeking) return false;
    return Number(video.readyState || 0) >= 3 && bufferedAhead(video) >= Number(minimumBuffer || 0);
  }

  async function waitForPlaybackBudget(videoId, minimumBuffer, signal) {
    const started = now();
    let blocked = false;
    while (!playbackAllowsStoryboard(videoId, minimumBuffer)) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      if (!blocked) {
        blocked = true;
        metrics.qosBlockedStarts += 1;
      }
      metrics.qosWaitLoops += 1;
      await sleep(QOS_RECHECK_MS, signal);
    }
    if (blocked) rememberSample(metrics.qosWaitMs, now() - started);
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
    const controller = new AbortController();
    const unlink = linkAbort(signal, controller);
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(endpoint, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`Storyboard segment HTTP ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timer);
      unlink();
    }
  }

  async function normalizeReadyBoard(data, signal) {
    const img = await preload(data.sprite_url, signal);
    return { ...data, _image: img };
  }

  function createConsumerToken(prefix = 'seg') {
    return globalThis.crypto?.randomUUID?.() || `${prefix}-${Date.now()}-${Math.random()}`;
  }

  function leaseUrl(videoId, consumer) {
    return `/api/storyboard/demand?id=${encodeURIComponent(videoId)}&consumer=${encodeURIComponent(consumer)}`;
  }

  async function acquireLease(videoId, consumer, signal) {
    const url = leaseUrl(videoId, consumer);
    const response = await mutationRequest(url, { method: 'POST' });
    if (!response.ok) throw new Error(`Storyboard demand HTTP ${response.status}`);
    if (signal?.aborted) {
      await releaseLease(url);
      throw new DOMException('Aborted', 'AbortError');
    }
    return url;
  }

  function releaseLease(url) {
    if (!url) return Promise.resolve();
    return mutationRequest(url, { method: 'DELETE', keepalive: true }).catch(() => null);
  }

  function abortSegmentEntriesForVideo(videoId) {
    for (const entry of segmentInFlight.values()) {
      if (entry.videoId === videoId && !entry.controller.signal.aborted) entry.controller.abort();
    }
  }

  function cancelWarmForVideo(videoId) {
    for (const [key, holder] of warmInFlight.entries()) {
      if (holder.videoId !== videoId) continue;
      if (!holder.controller.signal.aborted) holder.controller.abort();
      warmInFlight.delete(key);
    }
  }

  function releaseTargetLease(videoId, holder = targetLeases.get(videoId)) {
    if (!holder || holder.released) return;
    holder.released = true;
    metrics.hoverSessionCancels += 1;
    holder.signal?.removeEventListener?.('abort', holder.release);
    if (holder.url) releaseLease(holder.url);
    if (targetLeases.get(videoId) === holder) targetLeases.delete(videoId);
  }

  function cancelHoverClientWork(videoId) {
    releaseTargetLease(videoId);
    cancelActiveTarget(videoId);
    cancelWarmForVideo(videoId);
    abortSegmentEntriesForVideo(videoId);
  }

  function cancelVideoClientWork(videoId) {
    videoPreparations.get(videoId)?.controller.abort();
    videoPreparations.delete(videoId);
    cancelHoverClientWork(videoId);
  }

  function ensureTargetLease(videoId, signal) {
    if (!videoId || !signal || signal.aborted) return;
    const previous = targetLeases.get(videoId);
    if (previous && previous.signal === signal && !previous.released) return;
    previous?.release?.();

    const holder = {
      signal,
      url: '',
      released: false,
      release: null,
    };
    const release = () => {
      if (holder.released) return;
      releaseTargetLease(videoId, holder);
      cancelHoverClientWork(videoId);
    };
    holder.release = release;
    targetLeases.set(videoId, holder);
    signal.addEventListener?.('abort', release, { once: true });

    acquireLease(videoId, createConsumerToken('hover'))
      .then(url => {
        holder.url = url;
        if (holder.released || signal.aborted) releaseLease(url);
      })
      .catch(() => {})
      .finally(() => {
        if (holder.released && targetLeases.get(videoId) === holder) targetLeases.delete(videoId);
      });
  }

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
    throw new Error('QUICK cold generation disabled in V4.5.2 client');
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
      const buildTimer = setTimeout(() => controller.abort(), SEGMENT_BUILD_TIMEOUT_MS);
      try {
        // Re-check immediately before demand/POST so a hover that became a
        // player stall while waiting at the intent gate cannot start FFmpeg.
        await waitForPlaybackBudget(videoId, EXACT_BUFFER_SECONDS, controller.signal);
        entry.leaseUrl = await acquireLease(videoId, consumer, controller.signal);
        const postUrl = `/api/storyboard/segment?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}&segment=${encodeURIComponent(segmentIndex)}&prefetch_next=false`;
        const startResponse = await mutationRequest(postUrl, {
          method: 'POST', cache: 'no-store', signal: controller.signal
        });
        if (!startResponse.ok) throw new Error(`Segment start HTTP ${startResponse.status}`);
        let data = await startResponse.json();
        if (data.status === 'error') throw new Error(data.error || 'Segment error');
        if (data.status === 'ready' && data.sprite_url) {
          const board = await normalizeReadyBoard(data, controller.signal);
          lruSet(segmentMemory, key, board, SEGMENT_MEMORY_LIMIT);
          rememberSample(metrics.exactReadyMs, now() - entry.startedAt);
          return board;
        }

        const deadline = now() + SEGMENT_BUILD_TIMEOUT_MS;
        let renewedAt = now();
        for (let attempt = 0; now() < deadline; attempt += 1) {
          await sleep(attempt < 6 ? 80 : 180, controller.signal);
          if (now() - renewedAt >= 15000) {
            await acquireLease(videoId, consumer, controller.signal);
            renewedAt = now();
          }
          data = await fetchSegmentStatus(videoId, duration, segmentIndex, controller.signal);
          if (data.status === 'ready' && data.sprite_url) {
            const board = await normalizeReadyBoard(data, controller.signal);
            lruSet(segmentMemory, key, board, SEGMENT_MEMORY_LIMIT);
            rememberSample(metrics.exactReadyMs, now() - entry.startedAt);
            return board;
          }
          if (data.status === 'error') throw new Error(data.error || 'Segment error');
          // A superseded/cancelled backend job may disappear while a new
          // consumer is already waiting. Restart only the still-live target.
          if (data.status === 'missing') {
            await waitForPlaybackBudget(videoId, EXACT_BUFFER_SECONDS, controller.signal);
            const retry = await mutationRequest(postUrl, { method: 'POST', cache: 'no-store', signal: controller.signal });
            if (!retry.ok) throw new Error(`Segment restart HTTP ${retry.status}`);
            data = await retry.json();
            if (data.status === 'error') throw new Error(data.error || 'Segment error');
          }
        }
        throw new Error('Przekroczono czas przygotowania segmentu');
      } finally {
        clearTimeout(buildTimer);
        await releaseLease(entry.leaseUrl);
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
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
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
    if (!entry || entry.controller.signal.aborted) entry = createSegmentEntry({ videoId, duration, segmentIndex });
    return consumeSegmentEntry(entry, signal);
  }

  function getSegmentFromCache(videoId, duration, targetTime) {
    const segmentIndex = segmentIndexFor(duration, targetTime);
    const cached = lruGet(segmentMemory, segmentKey(videoId, duration, segmentIndex));
    if (cached) metrics.cacheHits += 1;
    return cached;
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
    if (active.timer) {
      clearTimeout(active.timer);
      active.timer = null;
      if (!active.started) metrics.intentCancelled += 1;
    }
    active.parentSignal?.removeEventListener?.('abort', active.parentAbort);
    active.unlink?.();
    if (!active.controller.signal.aborted) active.controller.abort();
  }

  function previewStatusText(videoId, duration, targetTime) {
    const key = segmentKey(videoId, duration, segmentIndexFor(duration, targetTime));
    const phase = previewStates.get(key)?.phase;
    if (phase === 'waiting-buffer') return 'Podgląd czeka na bufor filmu. Wstrzymaj film, aby go przygotować.';
    if (phase === 'error') return 'Nie udało się pobrać podglądu. Najedź ponownie, aby spróbować.';
    const preparation = videoPreparations.get(videoId);
    if (preparation) return `Przygotowywanie podglądów filmu: ${Math.round(100 * preparation.ready / preparation.total)}%…`;
    return 'Przygotowywanie podglądu…';
  }

  function requestSegment({ videoId, duration, targetTime, signal, onReady, onStatus }) {
    const segmentIndex = segmentIndexFor(duration, targetTime);
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

    // Keep a failed preview visible instead of restarting it on every mouse move.
    const previous = previewStates.get(key);
    if (previous?.phase === 'error' && now() - previous.updated < 10000) {
      onStatus?.('error');
      return null;
    }

    let active = activeTargetRequests.get(videoId);
    if (active && active.segmentIndex === segmentIndex && !active.controller.signal.aborted) {
      active.onReady = typeof onReady === 'function' ? onReady : active.onReady;
      active.targetTime = Number(targetTime) || 0;
      active.onStatus = typeof onStatus === 'function' ? onStatus : active.onStatus;
      return null;
    }

    if (active) {
      metrics.targetSwitches += 1;
      cancelActiveTarget(videoId);
    }
    // Foreground hover has priority over advance preparation of other segments.
    cancelWarmForVideo(videoId);

    const controller = new AbortController();
    const unlink = linkAbort(signal, controller);
    active = {
      segmentIndex,
      targetTime: Number(targetTime) || 0,
      controller,
      unlink,
      parentSignal: signal || null,
      parentAbort: null,
      onStatus: typeof onStatus === 'function' ? onStatus : null,
      onReady: typeof onReady === 'function' ? onReady : null,
      promise: null,
      timer: null,
      started: false,
      qosBlocked: false,
      cacheChecked: false,
    };
    activeTargetRequests.set(videoId, active);
    metrics.intentScheduled += 1;

    const parentAbort = () => {
      if (activeTargetRequests.get(videoId) === active) cancelHoverClientWork(videoId);
    };
    active.parentAbort = parentAbort;
    signal?.addEventListener?.('abort', parentAbort, { once: true });
    if (signal?.aborted) {
      parentAbort();
      return null;
    }

    const setPhase = phase => {
      lruSet(previewStates, key, {phase, updated: now()}, SEGMENT_MEMORY_LIMIT);
      if (!controller.signal.aborted) active.onStatus?.(phase);
    };
    const finishActive = () => {
      active.parentSignal?.removeEventListener?.('abort', active.parentAbort);
      if (activeTargetRequests.get(videoId) === active) activeTargetRequests.delete(videoId);
      unlink();
    };
    const tryStart = () => {
      active.timer = null;
      if (controller.signal.aborted || activeTargetRequests.get(videoId) !== active) return;
      // A disk-cached sprite needs no decoder or remote video traffic. Read it
      // before the playback gate, including while the primary player buffers.
      if (!active.cacheChecked) {
        active.cacheChecked = true;
        active.promise = fetchSegmentStatus(videoId, duration, segmentIndex, controller.signal)
          .then(async data => {
            if (controller.signal.aborted) return;
            if (data.status === 'ready' && data.sprite_url) {
              const board = await normalizeReadyBoard(data, controller.signal);
              lruSet(segmentMemory, key, board, SEGMENT_MEMORY_LIMIT);
              metrics.cacheHits += 1;
              setPhase('ready');
              active.onReady?.(board);
              finishActive();
            } else tryStart();
          })
          .catch(() => { if (!controller.signal.aborted) setPhase('error'); finishActive(); });
        return;
      }
      if (!playbackAllowsStoryboard(videoId, EXACT_BUFFER_SECONDS)) {
        if (!active.qosBlocked) {
          active.qosBlocked = true;
          setPhase('waiting-buffer');
          metrics.qosBlockedStarts += 1;
        }
        metrics.qosWaitLoops += 1;
        active.timer = setTimeout(tryStart, QOS_RECHECK_MS);
        return;
      }
      ensureTargetLease(videoId, signal);
      active.started = true;
      setPhase('loading');
      active.promise = prepareSegment({ videoId, duration, segmentIndex, signal: controller.signal })
        .then(segment => {
          if (!controller.signal.aborted) {
            setPhase('ready');
            if (typeof active.onReady === 'function') active.onReady(segment);
          }
          return segment;
        })
        .catch(() => { if (!controller.signal.aborted) setPhase('error'); return null; })
        .finally(finishActive);
    };

    // One idle dwell per target. If playback is not healthy after the dwell,
    // retain the latest target and re-check QoS instead of starting FFmpeg.
    active.timer = setTimeout(tryStart, HOVER_INTENT_MS);
    return null;
  }

  function warm({ videoId, duration, targetTime = 0 }) {
    duration = Number(duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) return Promise.resolve(null);
    const segmentIndex = segmentIndexFor(duration, targetTime);
    const key = segmentKey(videoId, duration, segmentIndex);
    const cached = lruGet(segmentMemory, key);
    if (cached) return Promise.resolve(cached);
    const existing = warmInFlight.get(key);
    if (existing) return existing.promise;
    if (!playbackAllowsStoryboard(videoId, BACKGROUND_BUFFER_SECONDS)) {
      metrics.qosPrewarmSkips += 1;
      return Promise.resolve(null);
    }

    metrics.prewarmRequests += 1;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SEGMENT_BUILD_TIMEOUT_MS);
    const holder = { videoId, controller, promise: null };
    holder.promise = waitForPlaybackBudget(videoId, BACKGROUND_BUFFER_SECONDS, controller.signal)
      .then(() => prepareSegment({ videoId, duration, segmentIndex, signal: controller.signal }))
      .catch(() => null)
      .finally(() => {
        clearTimeout(timer);
        if (warmInFlight.get(key) === holder) warmInFlight.delete(key);
      });
    warmInFlight.set(key, holder);
    return holder.promise;
  }

  function schedulePlaybackPrewarm(video) {
    const videoId = resolvePlaybackVideoId(video);
    const duration = Number(video?.duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) return;
    if (getSegmentFromCache(videoId, duration, Number(video.currentTime) || 0)) return;

    const oldTimer = playbackPrewarmTimers.get(video);
    if (oldTimer) clearTimeout(oldTimer);
    let attempts = 0;
    const check = () => {
      playbackPrewarmTimers.delete(video);
      if (video.paused || video.ended) return;
      attempts += 1;
      const ready = Number(video.readyState || 0) >= 3 && bufferedAhead(video) >= BACKGROUND_BUFFER_SECONDS;
      if (!ready) {
        if (attempts < 8) {
          const timer = setTimeout(check, 400);
          playbackPrewarmTimers.set(video, timer);
        } else {
          playbackPrewarmTimers.delete(video);
          metrics.qosPrewarmSkips += 1;
        }
        return;
      }
      playbackPrewarmTimers.delete(video);
      warm({ videoId, duration, targetTime: Number(video.currentTime) || 0 })
        .then(() => prepareVideoAhead(video));
    };
    const timer = setTimeout(check, 650);
    playbackPrewarmTimers.set(video, timer);
  }

  globalThis.document?.addEventListener?.('playing', event => {
    const video = event?.target;
    if (video?.id === 'modalVideo' || video?.id === 'mainPlayer') schedulePlaybackPrewarm(video);
  }, true);

  globalThis.document?.addEventListener?.('progress', event => {
    const video = event?.target;
    if ((video?.id === 'modalVideo' || video?.id === 'mainPlayer') && !video.paused &&
        !playbackPrewarmTimers.has(video)) schedulePlaybackPrewarm(video);
  }, true);

  function prepareVideoAhead(video) {
    const videoId = resolvePlaybackVideoId(video);
    const duration = Number(video?.duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0 || videoPreparations.has(videoId)) return;
    const controller = new AbortController();
    const holder = { controller, ready: 0, total: Math.ceil(duration / SEGMENT_DURATION) };
    videoPreparations.set(videoId, holder);
    holder.promise = (async () => {
      // Fill the existing disk/memory segment cache before the pointer gets
      // there. No second media element or alternate storyboard format.
      for (let index = 0; index < holder.total && !controller.signal.aborted;) {
        if (video.isConnected === false || resolvePlaybackVideoId(video) !== videoId) break;
        if (activeTargetRequests.has(videoId) || !playbackAllowsStoryboard(videoId, BACKGROUND_BUFFER_SECONDS) ||
            globalThis.document?.hidden) {
          await sleep(500, controller.signal);
          continue;
        }
        const targetTime = index * SEGMENT_DURATION;
        let board = getSegmentFromCache(videoId, duration, targetTime);
        let interrupted = false;
        if (!board) {
          const task = warm({ videoId, duration, targetTime });
          const warming = warmInFlight.get(segmentKey(videoId, duration, index));
          board = await task;
          interrupted = !!warming?.controller.signal.aborted;
        }
        if (controller.signal.aborted) break;
        if (board) { holder.ready += 1; index += 1; }
        else {
          // A failed source must not block all other parts or create a retry storm.
          if (!interrupted && !activeTargetRequests.has(videoId) && playbackAllowsStoryboard(videoId, BACKGROUND_BUFFER_SECONDS)) index += 1;
          await sleep(1000, controller.signal);
        }
      }
    })().catch(() => {}).finally(() => {
      if (videoPreparations.get(videoId) === holder) videoPreparations.delete(videoId);
    });
  }

  globalThis.document?.addEventListener?.('loadedmetadata', event => {
    const video = event?.target;
    if (video?.id === 'modalVideo' || video?.id === 'mainPlayer') prepareVideoAhead(video);
  }, true);
  globalThis.document?.addEventListener?.('pause', event => {
    const video = event?.target;
    if (video?.id === 'modalVideo' || video?.id === 'mainPlayer') prepareVideoAhead(video);
  }, true);
  globalThis.document?.addEventListener?.('emptied', event => {
    const video = event?.target;
    if (video?.id === 'modalVideo' || video?.id === 'mainPlayer') cancelVideoClientWork(resolvePlaybackVideoId(video));
  }, true);
  globalThis.addEventListener?.('pagehide', () => {
    for (const videoId of videoPreparations.keys()) cancelVideoClientWork(videoId);
  }, { once: true });

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
      const atlasWidth = (Number(board.columns) || 1) * (Number(board.frame_width) || 160);
      const atlasHeight = (Number(board.rows) || 1) * (Number(board.frame_height) || 90);
      img.width = atlasWidth;
      img.height = atlasHeight;
      // HTMLImageElement.width/height read the previous CSS-rendered size.
      // Use manifest dimensions when replacing a small QUICK with a dense atlas.
      img.style.width = `${atlasWidth}px`;
      img.style.height = `${atlasHeight}px`;
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
    if (Array.isArray(board.times) && board.times.length) {
      const sampleTime = Number(board.sample_interval) > 1
        ? Math.floor(targetTime / Number(board.sample_interval)) * Number(board.sample_interval) : targetTime;
      index = findNearestIndex(board.times, sampleTime);
    }
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
    element.dataset.frameTime = String(frameTime);
    return {
      ok: true,
      frameIndex: index,
      frameTime,
      targetTime,
      isExact: board.approximate !== true && Array.isArray(board.times) && Math.abs(Number(frameTime) - targetTime) <= 1.0,
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
      intent_scheduled: metrics.intentScheduled,
      intent_cancelled_before_start: metrics.intentCancelled,
      hover_session_cancels: metrics.hoverSessionCancels,
      qos_blocked_starts: metrics.qosBlockedStarts,
      qos_wait_loops: metrics.qosWaitLoops,
      qos_prewarm_skips: metrics.qosPrewarmSkips,
      qos_wait_p95_ms: percentile(metrics.qosWaitMs, 0.95),
      hover_intent_ms: HOVER_INTENT_MS,
      qos_recheck_ms: QOS_RECHECK_MS,
      exact_buffer_seconds: EXACT_BUFFER_SECONDS,
      background_buffer_seconds: BACKGROUND_BUFFER_SECONDS,
      segment_cache_entries: segmentMemory.size,
      segment_inflight: segmentInFlight.size,
      warm_inflight: warmInFlight.size,
      videos_preparing_ahead: videoPreparations.size,
      active_target_requests: activeTargetRequests.size,
      target_leases: targetLeases.size,
      preload_entries: preloadMemory.size,
      exact_ready_p50_ms: percentile(metrics.exactReadyMs, 0.50),
      exact_ready_p95_ms: percentile(metrics.exactReadyMs, 0.95),
      image_ready_p95_ms: percentile(metrics.imageReadyMs, 0.95),
      full_upgrade_enabled: false,
      cold_quick_generation_enabled: false,
      player_qos_guard: true,
    };
  }

  const api = {
    SAMPLE_INTERVAL,
    warm,
    prepare,
    applyFrame,
    clearFrame,
    attach,
    findNearestIndex,
    getSegmentFromCache,
    requestSegment,
    previewStatusText,
    prepareSegment,
    cancelActiveTarget,
    cancelVideoClientWork,
    stats,
    playbackAllowsStoryboard,
    SEGMENT_DURATION,
    HOVER_INTENT_MS,
    EXACT_BUFFER_SECONDS,
    BACKGROUND_BUFFER_SECONDS,
  };

  window.ArchivebateYouTubeStoryboard = api;
})();
