(() => {
  'use strict';

  // V4.3 playback-safe timeline strategy.
  //
  // 1. Exact 1-fps/30-second sprites remain authoritative and are requested by
  //    modal-player-controls after the existing 140 ms hover-intent gate.
  // 2. A tiny persistent coarse sprite may be prepared only when primary
  //    playback is healthy. Pointer movement NEVER starts that generator.
  // 3. While nothing is cached, keep the ordinary poster visible. Never replace
  //    it with a black box while background work is running.
  // 4. There is no auxiliary full-resolution <video> seek path in V4.3.
  const PREWARM_DELAY_MS = 2500;
  const PREWARM_BUFFER_SECONDS = 8.0;
  const LOW_BUFFER_CANCEL_SECONDS = 3.0;
  const RETRY_AFTER_HOVER_MS = 3500;
  const STATUS_WAIT_MS = 1200;
  const STATUS_TOTAL_TIMEOUT_MS = 14000;
  const QUICK_MEMORY_LIMIT = 24;

  let installed = false;
  let hoverActive = false;
  let latestVideoId = '';
  let latestDuration = 0;
  let latestTargetTime = 0;
  let pointerRaf = 0;
  let pendingClientX = 0;
  let prewarmTimer = null;
  let lowBufferVideoId = '';
  let lastFrameIdentity = '';

  const coarseBoards = new Map();
  const coarseBuilds = new Map();

  const metrics = {
    requests: 0,
    pointerUpdates: 0,
    frames: 0,
    posterFallbacks: 0,
    coarseBuilds: 0,
    coarseReady: 0,
    coarseCacheHits: 0,
    coarseReadyMs: [],
    coarseBuildAborts: 0,
    statusRequests: 0,
    startRequests: 0,
    demandRequests: 0,
    exactSkips: 0,
    existingCoarseHits: 0,
    prewarmStarts: 0,
    playbackProtectSkips: 0,
    lowBufferCancels: 0,
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

  function percentile(values, p) {
    if (!values.length) return 0;
    const ordered = [...values].sort((a, b) => a - b);
    const index = Math.min(ordered.length - 1, Math.max(0, Math.ceil(ordered.length * p) - 1));
    return Number(ordered[index].toFixed(1));
  }

  function bufferedAhead(video) {
    try {
      const current = Number(video?.currentTime) || 0;
      const ranges = video?.buffered;
      if (!ranges?.length) return 0;
      for (let i = 0; i < ranges.length; i += 1) {
        if (ranges.start(i) <= current + 0.1 && ranges.end(i) >= current) {
          return Math.max(0, ranges.end(i) - current);
        }
      }
    } catch (_) {}
    return 0;
  }

  function playbackSafe(video) {
    if (!video) return false;
    const ready = Number(video.readyState || 0);
    if (video.seeking) return false;
    if (video.paused) {
      // Do not compete with the initial click-to-first-frame path. Once a real
      // frame has been decoded (or the user paused after playback began), coarse
      // work is safe because it cannot stall active playback.
      return ready >= 3 || (ready >= 2 && Number(video.currentTime || 0) > 0.25);
    }
    return ready >= 3 && bufferedAhead(video) >= PREWARM_BUFFER_SECONDS;
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

  async function fetchQuickStatus(videoId, duration, signal, waitMs = STATUS_WAIT_MS) {
    metrics.requests += 1;
    metrics.statusRequests += 1;
    const response = await fetch(
      `/api/runtime/v43/storyboard/quick?id=${encodeURIComponent(videoId)}` +
      `&duration=${encodeURIComponent(duration)}&wait_ms=${encodeURIComponent(waitMs)}`,
      { cache: 'no-store', signal },
    );
    if (!response.ok) throw new Error(`QUICK status HTTP ${response.status}`);
    return response.json();
  }

  function demandUrl(videoId, consumer) {
    return `/api/storyboard/demand?id=${encodeURIComponent(videoId)}&consumer=${encodeURIComponent(consumer)}`;
  }

  async function acquireDemand(videoId, consumer, signal) {
    const url = demandUrl(videoId, consumer);
    metrics.requests += 1;
    metrics.demandRequests += 1;
    const response = await mutationRequest(url, { method: 'POST' });
    if (!response.ok) throw new Error(`QUICK demand HTTP ${response.status}`);
    if (signal?.aborted) {
      await releaseDemand(url);
      throw new DOMException('Aborted', 'AbortError');
    }
    return url;
  }

  function releaseDemand(url) {
    if (!url) return Promise.resolve();
    metrics.requests += 1;
    metrics.demandRequests += 1;
    return mutationRequest(url, { method: 'DELETE', keepalive: true }).catch(() => null);
  }

  function consumerToken(videoId) {
    return globalThis.crypto?.randomUUID?.() || `quick-${videoId}-${Date.now()}-${Math.random()}`;
  }

  function cancelCoarse(videoId, reason = 'cancelled') {
    if (!videoId) return false;
    let cancelled = false;
    for (const holder of coarseBuilds.values()) {
      if (holder.videoId !== videoId || holder.controller.signal.aborted) continue;
      holder.abortReason = reason;
      holder.controller.abort();
      cancelled = true;
    }
    return cancelled;
  }

  function cancelBuildsExcept(videoId) {
    for (const holder of coarseBuilds.values()) {
      if (holder.videoId === videoId || holder.controller.signal.aborted) continue;
      holder.abortReason = 'video_switched';
      holder.controller.abort();
    }
  }

  function rememberReadyBoard(videoId, duration, board) {
    const key = cacheKey(videoId, duration);
    lruSet(coarseBoards, key, board, QUICK_MEMORY_LIMIT);
    const state = appState();
    const current = currentIdentity(document.getElementById('modalVideo'), state);
    if (state && current.videoId === videoId && Math.abs(current.duration - duration) <= 1) {
      // Reuse the baseline renderer instead of maintaining a competing render
      // stack. modal-player-controls already gives exact segment > coarse >
      // poster priority in exactly this order.
      state.timelineSpriteBoard = board;
    }
  }

  function prewarmCoarse(videoId, duration, { reason = 'background' } = {}) {
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
    const holder = {
      videoId,
      duration,
      reason,
      controller,
      demand: '',
      abortReason: '',
      promise: null,
    };
    const started = now();
    metrics.coarseBuilds += 1;

    holder.promise = (async () => {
      try {
        // Fast cache probe. It returns immediately when no persistent sprite is
        // available; later status calls are server-side long polls.
        let data = await fetchQuickStatus(videoId, duration, controller.signal, 0);
        if (data.status !== 'ready' || !data.sprite_url) {
          holder.demand = await acquireDemand(videoId, consumerToken(videoId), controller.signal);
          metrics.requests += 1;
          metrics.startRequests += 1;
          const startResponse = await mutationRequest(
            `/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`,
            { method: 'POST', cache: 'no-store', signal: controller.signal },
          );
          if (!startResponse.ok) throw new Error(`QUICK start HTTP ${startResponse.status}`);
          data = await startResponse.json();
        }

        const deadline = now() + STATUS_TOTAL_TIMEOUT_MS;
        while ((data.status !== 'ready' || !data.sprite_url) && now() < deadline) {
          if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
          if (data.status === 'error') throw new Error(data.error || 'QUICK build error');
          data = await fetchQuickStatus(videoId, duration, controller.signal, STATUS_WAIT_MS);
        }
        if (data.status !== 'ready' || !data.sprite_url) throw new Error('QUICK build timeout');

        const board = await preloadBoard(data, controller.signal);
        rememberReadyBoard(videoId, duration, board);
        metrics.coarseReady += 1;
        metrics.coarseReadyMs.push(now() - started);
        if (metrics.coarseReadyMs.length > 32) metrics.coarseReadyMs.shift();
        return board;
      } catch (error) {
        if (error?.name === 'AbortError') metrics.coarseBuildAborts += 1;
        else metrics.buildErrors += 1;
        return null;
      } finally {
        await releaseDemand(holder.demand);
        if (coarseBuilds.get(key) === holder) coarseBuilds.delete(key);
      }
    })();

    coarseBuilds.set(key, holder);
    return holder.promise;
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

    function posterSource(state) {
      const raw = String(
        state?.currentVideoDetails?.thumbnail ||
        state?.currentVideoDetails?.poster ||
        mainVideo.poster ||
        ''
      ).trim();
      return raw ? raw.replace('.mp4', '.jpg') : '';
    }

    function showPosterFallback(state) {
      if (previewVideo) previewVideo.style.display = 'none';
      globalThis.ArchivebateYouTubeStoryboard?.clearFrame?.(sprite);
      const poster = posterSource(state);
      if (poster && previewImg) {
        try {
          const absolute = new URL(poster, globalThis.location?.href || 'http://127.0.0.1/').href;
          if (previewImg.src !== absolute) previewImg.src = poster;
        } catch (_) {
          if (previewImg.src !== poster) previewImg.src = poster;
        }
        previewImg.style.display = 'block';
      } else if (previewImg?.src) {
        // Preserve the last valid image rather than actively creating a black
        // rectangle when a rare record has no poster field.
        previewImg.style.display = 'block';
      }
      if (status) {
        status.textContent = 'Podgląd dokładny jest przygotowywany…';
        status.style.display = 'block';
      }
      metrics.posterFallbacks += 1;
    }

    function showCoarse(board, targetTime, duration) {
      if (!board || !hoverActive) return false;
      // Apply first. Only after the sprite has a valid frame do we hide the
      // poster. This ordering is the black-preview regression guard.
      const result = globalThis.ArchivebateYouTubeStoryboard.applyFrame(
        sprite,
        board,
        targetTime,
        { targetTime, duration },
      );
      if (!result?.ok) return false;
      if (previewVideo) previewVideo.style.display = 'none';
      if (previewImg) previewImg.style.display = 'none';
      if (status) status.style.display = 'none';
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

      // modal-player-controls rendered first in this animation frame. Exact
      // cached output is already correct; do not cover it.
      if (currentSegmentCached(videoId, duration, targetTime)) {
        metrics.exactSkips += 1;
        return;
      }

      const existingBoard = state.timelineSpriteBoard;
      if (existingBoard?.sprite_url) {
        metrics.existingCoarseHits += 1;
        if (!showCoarse(existingBoard, targetTime, duration)) showPosterFallback(state);
        return;
      }

      const board = lruGet(coarseBoards, cacheKey(videoId, duration));
      if (board) {
        metrics.coarseCacheHits += 1;
        state.timelineSpriteBoard = board;
        if (!showCoarse(board, targetTime, duration)) showPosterFallback(state);
        return;
      }

      // Critical V4.3 rule: pointer motion never starts background extraction.
      // The exact path may start after its existing intent gate; until something
      // is ready, retain a meaningful poster instead of a black tooltip.
      showPosterFallback(state);
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

    function schedulePrewarm(delay = PREWARM_DELAY_MS) {
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = setTimeout(() => {
        prewarmTimer = null;
        if (!modal?.classList?.contains?.('active') || hoverActive) {
          metrics.playbackProtectSkips += 1;
          return;
        }
        const state = appState();
        const { videoId, duration } = currentIdentity(mainVideo, state);
        if (!videoId || !duration) return;
        if (!playbackSafe(mainVideo)) {
          metrics.playbackProtectSkips += 1;
          return;
        }
        metrics.prewarmStarts += 1;
        prewarmCoarse(videoId, duration, { reason: mainVideo.paused ? 'paused' : 'buffered_playback' });
      }, Math.max(0, Number(delay) || 0));
    }

    function protectPlayback(reason) {
      const state = appState();
      const { videoId } = currentIdentity(mainVideo, state);
      if (videoId && cancelCoarse(videoId, reason)) metrics.playbackProtectSkips += 1;
    }

    timeline.addEventListener('pointerenter', event => {
      hoverActive = true;
      const state = appState();
      const { videoId } = currentIdentity(mainVideo, state);
      // Exact hover is interactive and always outranks speculative coarse work.
      if (videoId) cancelCoarse(videoId, 'exact_hover');
      schedulePointer(event);
    }, { passive: true });
    timeline.addEventListener('pointermove', schedulePointer, { passive: true });
    timeline.addEventListener('pointerleave', () => {
      hoverActive = false;
      lastFrameIdentity = '';
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
      pointerRaf = 0;
      schedulePrewarm(RETRY_AFTER_HOVER_MS);
    }, { passive: true });

    mainVideo.addEventListener('playing', () => schedulePrewarm(PREWARM_DELAY_MS));
    mainVideo.addEventListener('pause', () => {
      if (Number(mainVideo.currentTime || 0) > 0.25) schedulePrewarm(500);
    });
    mainVideo.addEventListener('waiting', () => protectPlayback('waiting'));
    mainVideo.addEventListener('stalled', () => protectPlayback('stalled'));
    mainVideo.addEventListener('seeking', () => protectPlayback('player_seek'));
    mainVideo.addEventListener('seeked', () => {
      if (!hoverActive) schedulePrewarm(PREWARM_DELAY_MS);
    });
    mainVideo.addEventListener('timeupdate', () => {
      if (mainVideo.paused || !latestVideoId) return;
      const state = appState();
      const { videoId } = currentIdentity(mainVideo, state);
      if (!videoId) return;
      if (coarseBuilds.size && bufferedAhead(mainVideo) < LOW_BUFFER_CANCEL_SECONDS) {
        if (lowBufferVideoId !== videoId && cancelCoarse(videoId, 'low_buffer')) {
          metrics.lowBufferCancels += 1;
          lowBufferVideoId = videoId;
        }
      } else if (bufferedAhead(mainVideo) >= LOW_BUFFER_CANCEL_SECONDS) {
        lowBufferVideoId = '';
      }
    });

    mainVideo.addEventListener('emptied', () => {
      const previous = latestVideoId;
      if (previous) cancelCoarse(previous, 'video_emptied');
      hoverActive = false;
      latestVideoId = '';
      latestDuration = 0;
      latestTargetTime = 0;
      lowBufferVideoId = '';
      lastFrameIdentity = '';
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = null;
    });
  }

  function scheduleInstall() {
    // modal-player-controls registers before this runtime script, so its RAF is
    // queued first and this layer can preserve poster/coarse output afterwards.
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
        poster_fallbacks: metrics.posterFallbacks,
        coarse_builds: metrics.coarseBuilds,
        coarse_ready: metrics.coarseReady,
        coarse_cache_hits: metrics.coarseCacheHits,
        coarse_ready_p50_ms: percentile(metrics.coarseReadyMs, 0.50),
        coarse_ready_p95_ms: percentile(metrics.coarseReadyMs, 0.95),
        coarse_build_aborts: metrics.coarseBuildAborts,
        status_requests: metrics.statusRequests,
        start_requests: metrics.startRequests,
        demand_requests: metrics.demandRequests,
        exact_skips: metrics.exactSkips,
        existing_coarse_hits: metrics.existingCoarseHits,
        prewarm_starts: metrics.prewarmStarts,
        playback_protect_skips: metrics.playbackProtectSkips,
        low_buffer_cancels: metrics.lowBufferCancels,
        build_errors: metrics.buildErrors,
        coarse_cache_entries: coarseBoards.size,
        coarse_inflight: coarseBuilds.size,
        last_target_time: Number(latestTargetTime.toFixed?.(3) ?? latestTargetTime),
        prewarm_delay_ms: PREWARM_DELAY_MS,
        prewarm_buffer_seconds: PREWARM_BUFFER_SECONDS,
        status_wait_ms: STATUS_WAIT_MS,
        media_seek_enabled: false,
        black_fallback_enabled: false,
      };
    },
    reset() {
      for (const holder of coarseBuilds.values()) {
        if (!holder.controller.signal.aborted) holder.controller.abort();
      }
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = null;
      hoverActive = false;
      latestVideoId = '';
      latestDuration = 0;
      latestTargetTime = 0;
      lastFrameIdentity = '';
    },
  };
})();
