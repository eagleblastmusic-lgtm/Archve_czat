(() => {
  'use strict';

  // YouTube-style V4.3 fallback: never chase the pointer by seeking a full-size
  // auxiliary MP4.  Build one tiny persistent 160x90 QUICK sprite per opened
  // video, preload it in the background, then change frames locally.  Exact
  // 1-fps/30s storyboard segments still win whenever they are ready.
  const PREWARM_DELAY_MS = 450;
  const PREWARM_BUFFER_SECONDS = 3.0;
  const STATUS_TIMEOUT_MS = 12000;
  const QUICK_MEMORY_LIMIT = 24;

  let installed = false;
  let hoverActive = false;
  let latestVideoId = '';
  let latestDuration = 0;
  let latestTargetTime = 0;
  let pointerRaf = 0;
  let pendingClientX = 0;
  let prewarmTimer = null;
  let lastFrameIdentity = '';

  const coarseBoards = new Map();
  const coarseBuilds = new Map();

  const metrics = {
    requests: 0,
    pointerUpdates: 0,
    frames: 0,
    coarseBuilds: 0,
    coarseReady: 0,
    coarseCacheHits: 0,
    coarseReadyMs: [],
    exactSkips: 0,
    existingCoarseHits: 0,
    prewarmStarts: 0,
    buildErrors: 0,
  };

  function now() {
    return globalThis.performance?.now?.() ?? Date.now();
  }

  function appState() {
    return globalThis.ArchivebateAppContext?.state || globalThis.state || null;
  }

  function parseDuration(value) {
    if (globalThis.ArchivebatePlayerCore?.parseDurationToSeconds) {
      return Number(globalThis.ArchivebatePlayerCore.parseDurationToSeconds(value)) || 0;
    }
    if (typeof value === 'number') return Number.isFinite(value) ? Math.max(0, value) : 0;
    const parts = String(value || '').trim().split(':').map(Number);
    if (!parts.length || parts.some(part => !Number.isFinite(part))) return 0;
    if (parts.length === 3) return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2]);
    if (parts.length === 2) return Math.max(0, parts[0] * 60 + parts[1]);
    return Math.max(0, parts[0] || 0);
  }

  function currentIdentity(mainVideo, state) {
    const videoId = String(
      state?.currentVideoId ||
      state?.currentVideoDetails?.id ||
      mainVideo?.dataset?.videoId ||
      ''
    ).trim();
    const duration = Number.isFinite(Number(mainVideo?.duration)) && Number(mainVideo?.duration) > 0
      ? Number(mainVideo.duration)
      : parseDuration(state?.currentVideoDetails?.duration);
    return { videoId, duration };
  }

  function cacheKey(videoId, duration) {
    return `${videoId}:${Math.max(1, Math.round(Number(duration) || 0))}`;
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

  function currentSegmentCached(videoId, duration, targetTime) {
    return Boolean(globalThis.ArchivebateYouTubeStoryboard?.getSegmentFromCache?.(videoId, duration, targetTime));
  }

  function mutationRequest(url, options = {}) {
    if (globalThis.ArchivebateAPI?.request) return globalThis.ArchivebateAPI.request(url, options);
    const headers = { ...(options.headers || {}) };
    const token = document.querySelector?.('meta[name="archivebate-mutation-token"]')?.content || '';
    if (token) headers['X-Archivebate-Mutation-Token'] = token;
    return fetch(url, { ...options, headers });
  }

  function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
      const timer = setTimeout(() => {
        signal?.removeEventListener?.('abort', abort);
        resolve();
      }, ms);
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      signal?.addEventListener?.('abort', abort, { once: true });
    });
  }

  async function preloadBoard(data, signal) {
    if (!data?.sprite_url) throw new Error('Brak sprite_url');
    const img = new Image();
    const loaded = new Promise((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error('QUICK sprite load failed'));
    });
    img.src = data.sprite_url;
    await Promise.race([
      loaded,
      sleep(8000, signal).then(() => { throw new Error('QUICK sprite timeout'); }),
    ]);
    try { await img.decode?.(); } catch (_) {}
    return { ...data, _image: img };
  }

  async function fetchQuickStatus(videoId, duration, signal) {
    metrics.requests += 1;
    const response = await fetch(
      `/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`,
      { cache: 'no-store', signal },
    );
    if (!response.ok) throw new Error(`QUICK status HTTP ${response.status}`);
    return response.json();
  }

  function demandUrl(videoId, consumer) {
    return `/api/storyboard/demand?id=${encodeURIComponent(videoId)}&consumer=${encodeURIComponent(consumer)}`;
  }

  async function acquireDemand(videoId, consumer) {
    const url = demandUrl(videoId, consumer);
    metrics.requests += 1;
    const response = await mutationRequest(url, { method: 'POST' });
    if (!response.ok) throw new Error(`QUICK demand HTTP ${response.status}`);
    return url;
  }

  function releaseDemand(url) {
    if (!url) return Promise.resolve();
    metrics.requests += 1;
    return mutationRequest(url, { method: 'DELETE', keepalive: true }).catch(() => null);
  }

  function consumerToken(videoId) {
    return globalThis.crypto?.randomUUID?.() || `quick-${videoId}-${Date.now()}-${Math.random()}`;
  }

  function cancelBuildsExcept(videoId) {
    for (const [key, holder] of coarseBuilds.entries()) {
      if (holder.videoId === videoId) continue;
      holder.controller.abort();
      coarseBuilds.delete(key);
    }
  }

  function prewarmCoarse(videoId, duration) {
    duration = Number(duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) return Promise.resolve(null);
    const key = cacheKey(videoId, duration);
    const cached = lruGet(coarseBoards, key);
    if (cached) {
      metrics.coarseCacheHits += 1;
      return Promise.resolve(cached);
    }
    const existing = coarseBuilds.get(key);
    if (existing) return existing.promise;

    cancelBuildsExcept(videoId);
    const controller = new AbortController();
    const holder = { videoId, controller, demand: '', promise: null };
    const started = now();
    metrics.coarseBuilds += 1;

    holder.promise = (async () => {
      try {
        let data = await fetchQuickStatus(videoId, duration, controller.signal);
        if (data.status !== 'ready' || !data.sprite_url) {
          holder.demand = await acquireDemand(videoId, consumerToken(videoId));
          metrics.requests += 1;
          const startResponse = await mutationRequest(
            `/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`,
            { method: 'POST', cache: 'no-store', signal: controller.signal },
          );
          if (!startResponse.ok) throw new Error(`QUICK start HTTP ${startResponse.status}`);
          data = await startResponse.json();
        }

        const deadline = now() + STATUS_TIMEOUT_MS;
        while ((data.status !== 'ready' || !data.sprite_url) && now() < deadline) {
          if (data.status === 'error') throw new Error(data.error || 'QUICK build error');
          await sleep(now() - started < 1800 ? 120 : 240, controller.signal);
          data = await fetchQuickStatus(videoId, duration, controller.signal);
        }
        if (data.status !== 'ready' || !data.sprite_url) throw new Error('QUICK build timeout');

        const board = await preloadBoard(data, controller.signal);
        lruSet(coarseBoards, key, board, QUICK_MEMORY_LIMIT);
        metrics.coarseReady += 1;
        metrics.coarseReadyMs.push(now() - started);
        if (metrics.coarseReadyMs.length > 32) metrics.coarseReadyMs.shift();
        return board;
      } catch (error) {
        if (error?.name !== 'AbortError') metrics.buildErrors += 1;
        return null;
      } finally {
        await releaseDemand(holder.demand);
        if (coarseBuilds.get(key) === holder) coarseBuilds.delete(key);
      }
    })();

    coarseBuilds.set(key, holder);
    return holder.promise;
  }

  function percentile(values, p) {
    if (!values.length) return 0;
    const ordered = [...values].sort((a, b) => a - b);
    const index = Math.min(ordered.length - 1, Math.max(0, Math.ceil(ordered.length * p) - 1));
    return Number(ordered[index].toFixed(1));
  }

  function install() {
    if (installed || !globalThis.document?.getElementById) return;
    const timeline = document.getElementById('modalTimelineContainer');
    const mainVideo = document.getElementById('modalVideo');
    const previewVideo = document.getElementById('modalTimelinePreviewVideo');
    const previewImg = document.getElementById('modalTimelinePreviewImg');
    const sprite = document.getElementById('modalTimelineSprite');
    const status = document.getElementById('modalTimelinePreviewStatus');
    const modal = document.getElementById('videoModal');
    if (!timeline || !mainVideo || !sprite || !globalThis.ArchivebateYouTubeStoryboard?.applyFrame) return;

    installed = true;

    function hideMediaFallback() {
      if (previewVideo) previewVideo.style.display = 'none';
      if (previewImg) previewImg.style.display = 'none';
    }

    function showCoarse(board, targetTime, duration) {
      if (!board || !hoverActive) return false;
      hideMediaFallback();
      if (status) status.style.display = 'none';
      const result = globalThis.ArchivebateYouTubeStoryboard.applyFrame(
        sprite,
        board,
        targetTime,
        { targetTime, duration },
      );
      if (!result?.ok) return false;
      const identity = `${board.sprite_url}|${result.frameIndex}`;
      if (identity !== lastFrameIdentity) {
        lastFrameIdentity = identity;
        metrics.frames += 1;
      }
      return true;
    }

    function requestFromClientX(clientX) {
      if (!modal?.classList?.contains?.('active')) return;
      const state = appState();
      if (!state || state.currentTimelinePrefix) return;
      const { videoId, duration } = currentIdentity(mainVideo, state);
      if (!videoId || !Number.isFinite(duration) || duration <= 0) return;

      const rect = timeline.getBoundingClientRect?.();
      if (!rect?.width) return;
      const pos = Math.max(0, Math.min(1, (Number(clientX) - rect.left) / rect.width));
      const targetTime = pos * duration;
      latestVideoId = videoId;
      latestDuration = duration;
      latestTargetTime = targetTime;
      hoverActive = true;
      metrics.pointerUpdates += 1;

      // Exact segment already rendered by modal-player-controls in the RAF that
      // precedes this handler.  Do not cover it with the coarse fallback.
      if (currentSegmentCached(videoId, duration, targetTime)) {
        metrics.exactSkips += 1;
        return;
      }

      const existingBoard = state.timelineSpriteBoard;
      if (existingBoard?.sprite_url) {
        metrics.existingCoarseHits += 1;
        showCoarse(existingBoard, targetTime, duration);
        return;
      }

      const key = cacheKey(videoId, duration);
      const board = lruGet(coarseBoards, key);
      if (board) {
        metrics.coarseCacheHits += 1;
        showCoarse(board, targetTime, duration);
        return;
      }

      // First encounter only: start the tiny persistent sprite once.  We do not
      // open a second full-resolution MP4 anymore, so pointer movement itself is
      // network-free after this one sprite is ready.
      hideMediaFallback();
      if (status) {
        status.textContent = 'Przygotowywanie szybkiego podglądu…';
        status.style.display = 'block';
      }
      prewarmCoarse(videoId, duration).then(readyBoard => {
        if (!readyBoard || !hoverActive) return;
        if (videoId !== latestVideoId || Math.abs(duration - latestDuration) > 1) return;
        if (currentSegmentCached(videoId, duration, latestTargetTime)) return;
        showCoarse(readyBoard, latestTargetTime, duration);
      });
    }

    function schedulePointer(event) {
      if (!event) return;
      pendingClientX = Number(event.clientX) || 0;
      if (pointerRaf) return;
      pointerRaf = requestAnimationFrame(() => {
        pointerRaf = 0;
        requestFromClientX(pendingClientX);
      });
    }

    function bufferedAhead(video) {
      const helper = Number(globalThis.ArchivebatePerf?.getBufferedAhead?.(video));
      if (Number.isFinite(helper) && helper >= 0) return helper;
      try {
        const nowTime = Number(video.currentTime) || 0;
        for (let i = 0; i < video.buffered.length; i += 1) {
          if (video.buffered.start(i) <= nowTime && video.buffered.end(i) >= nowTime) {
            return Math.max(0, video.buffered.end(i) - nowTime);
          }
        }
      } catch (_) {}
      return 0;
    }

    function schedulePrewarm() {
      if (prewarmTimer) clearTimeout(prewarmTimer);
      let attempts = 0;
      const check = () => {
        attempts += 1;
        if (!modal?.classList?.contains?.('active')) return;
        const state = appState();
        const { videoId, duration } = currentIdentity(mainVideo, state);
        if (!videoId || !duration) return;
        const ready = Number(mainVideo.readyState || 0) >= 3 && bufferedAhead(mainVideo) >= PREWARM_BUFFER_SECONDS;
        if (!ready && attempts < 7) {
          prewarmTimer = setTimeout(check, 300);
          return;
        }
        prewarmTimer = null;
        if (!ready) return;
        metrics.prewarmStarts += 1;
        prewarmCoarse(videoId, duration);
      };
      prewarmTimer = setTimeout(check, PREWARM_DELAY_MS);
    }

    timeline.addEventListener('pointerenter', schedulePointer, { passive: true });
    timeline.addEventListener('pointermove', schedulePointer, { passive: true });
    timeline.addEventListener('pointerleave', () => {
      hoverActive = false;
      lastFrameIdentity = '';
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
      pointerRaf = 0;
    }, { passive: true });

    mainVideo.addEventListener('playing', schedulePrewarm);
    mainVideo.addEventListener('emptied', () => {
      hoverActive = false;
      latestVideoId = '';
      latestDuration = 0;
      latestTargetTime = 0;
      lastFrameIdentity = '';
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = null;
      cancelBuildsExcept('');
    });
  }

  function scheduleInstall() {
    setTimeout(install, 0);
  }

  if (globalThis.document?.readyState === 'loading') {
    globalThis.document.addEventListener('DOMContentLoaded', scheduleInstall, { once: true });
  } else {
    scheduleInstall();
  }

  globalThis.ArchivebateV43TimelineFallback = {
    stats() {
      return {
        installed,
        hover_active: hoverActive,
        source_video_id: '',
        requests: metrics.requests,
        pointer_updates: metrics.pointerUpdates,
        frames: metrics.frames,
        coarse_builds: metrics.coarseBuilds,
        coarse_ready: metrics.coarseReady,
        coarse_cache_hits: metrics.coarseCacheHits,
        existing_coarse_hits: metrics.existingCoarseHits,
        exact_skips: metrics.exactSkips,
        prewarm_starts: metrics.prewarmStarts,
        build_errors: metrics.buildErrors,
        coarse_ready_p50_ms: percentile(metrics.coarseReadyMs, 0.50),
        coarse_ready_p95_ms: percentile(metrics.coarseReadyMs, 0.95),
        coarse_memory_entries: coarseBoards.size,
        coarse_inflight: coarseBuilds.size,
        media_seek_enabled: false,
        frame_width: 160,
        frame_height: 90,
      };
    },
    prewarm(videoId, duration) {
      return prewarmCoarse(String(videoId || ''), Number(duration));
    },
    reset() {
      hoverActive = false;
      lastFrameIdentity = '';
    },
  };
})();
