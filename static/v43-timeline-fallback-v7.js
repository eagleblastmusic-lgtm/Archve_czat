(() => {
  'use strict';

  // V4.5.2 modal timeline coordinator.
  // Pointer motion renders only local cache/coarse/poster. One exact target is
  // retained and may enter the exact client only after 260 ms of real pointer
  // idle and a fresh playback-health check.
  const PREWARM_DELAY_MS = 1800;
  const PREWARM_BUFFER_SECONDS = 5.0;
  const INTERACTIVE_BUFFER_SECONDS = 3.0;
  const EXACT_MIN_BUFFER_SECONDS = 3.0;
  const LOW_BUFFER_CANCEL_SECONDS = 2.0;
  const EXACT_IDLE_MS = 260;
  const EXACT_RECHECK_MS = 180;
  const RETRY_AFTER_HOVER_MS = 2200;
  const STATUS_WAIT_MS = 1200;
  const STATUS_TOTAL_TIMEOUT_MS = 16000;
  const QUICK_MEMORY_LIMIT = 24;

  let installed = false;
  let hoverActive = false;
  let latestVideoId = '';
  let latestDuration = 0;
  let latestTargetTime = 0;
  let pointerRaf = 0;
  let pendingClientX = 0;
  let prewarmTimer = null;
  let exactIdleTimer = null;
  let idleExact = null;
  let lowBufferVideoId = '';
  let lastFrameIdentity = '';
  let renderLatestBoard = null;
  let originalRequestSegment = null;
  let modalObserver = null;

  const coarseBoards = new Map();
  const coarseBuilds = new Map();

  const metrics = {
    requests: 0,
    pointerUpdates: 0,
    frames: 0,
    exactFrames: 0,
    posterFallbacks: 0,
    coarseBuilds: 0,
    coarseReady: 0,
    coarseCacheHits: 0,
    coarseReadyMs: [],
    coarseBuildAborts: 0,
    statusRequests: 0,
    startRequests: 0,
    demandRequests: 0,
    exactCacheHits: 0,
    existingCoarseHits: 0,
    prewarmStarts: 0,
    hoverCoarseStarts: 0,
    playbackProtectSkips: 0,
    lowBufferCancels: 0,
    buildErrors: 0,
    exactIdleScheduled: 0,
    exactIdleResets: 0,
    exactIdleStarts: 0,
    exactIdleBlocked: 0,
    exactIdleCancelled: 0,
    softProtectRequests: 0,
    hardCancelRequests: 0,
    videoSwitchCancels: 0,
    identityRepairs: 0,
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
    const detailsId = String(state?.currentVideoDetails?.id || '').trim();
    const compatibilityId = String(state?.currentVideoId || '').trim();
    const datasetId = String(mainVideo?.dataset?.videoId || '').trim();
    const videoId = detailsId || compatibilityId || datasetId;
    const duration = Number.isFinite(Number(mainVideo?.duration)) && Number(mainVideo.duration) > 0
      ? Number(mainVideo.duration)
      : parseDuration(state?.currentVideoDetails?.duration);
    return { videoId, duration };
  }

  function synchronizeIdentity(mainVideo, state) {
    if (!state) return { videoId: '', duration: 0 };
    const identity = currentIdentity(mainVideo, state);
    if (!identity.videoId) return identity;
    if (String(state.currentVideoId || '') !== identity.videoId) {
      state.currentVideoId = identity.videoId;
      metrics.identityRepairs += 1;
    }
    if (mainVideo?.dataset && String(mainVideo.dataset.videoId || '') !== identity.videoId) {
      mainVideo.dataset.videoId = identity.videoId;
    }
    return identity;
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

  function playbackSafe(video, minimumBuffer) {
    if (!video || video.seeking || video.ended) return false;
    const ready = Number(video.readyState || 0);
    if (video.paused) {
      return ready >= 3 || (ready >= 2 && Number(video.currentTime || 0) > 0.25);
    }
    return ready >= 3 && bufferedAhead(video) >= Number(minimumBuffer || 0);
  }

  function getExactCached(videoId, duration, targetTime) {
    return globalThis.ArchivebateYouTubeStoryboard?.getSegmentFromCache?.(videoId, duration, targetTime) || null;
  }

  function mutationRequest(url, options = {}) {
    if (globalThis.ArchivebateAPI?.request) return globalThis.ArchivebateAPI.request(url, options);
    const headers = { ...(options.headers || {}) };
    const token = document.querySelector?.('meta[name="archivebate-mutation-token"]')?.content || '';
    if (token) headers['X-Archivebate-Mutation-Token'] = token;
    return fetch(url, { ...options, headers });
  }

  function postRuntime(url) {
    return mutationRequest(url, { method: 'POST', keepalive: true }).catch(() => null);
  }

  function requestSoftProtect(reason = 'timeline') {
    metrics.softProtectRequests += 1;
    return postRuntime(`/api/runtime/v452/storyboard/protect?reason=${encodeURIComponent(reason)}`);
  }

  function requestHardCancel(videoId, reason = 'lifecycle') {
    if (!videoId) return Promise.resolve(null);
    metrics.hardCancelRequests += 1;
    return postRuntime(
      `/api/runtime/v452/storyboard/cancel?id=${encodeURIComponent(videoId)}` +
      `&reason=${encodeURIComponent(reason)}`,
    );
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
    lruSet(coarseBoards, cacheKey(videoId, duration), board, QUICK_MEMORY_LIMIT);
    const state = appState();
    const mainVideo = document.getElementById('modalVideo');
    const current = synchronizeIdentity(mainVideo, state);
    if (state && current.videoId === videoId && Math.abs(current.duration - duration) <= 1) {
      state.timelineSpriteBoard = board;
      if (hoverActive && typeof renderLatestBoard === 'function') {
        renderLatestBoard(board, latestTargetTime, duration);
      }
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

  function clearIdleExact({ cancelActive = false } = {}) {
    if (exactIdleTimer) clearTimeout(exactIdleTimer);
    exactIdleTimer = null;
    const entry = idleExact;
    idleExact = null;
    if (entry) metrics.exactIdleCancelled += 1;
    if (cancelActive && entry?.videoId) {
      globalThis.ArchivebateYouTubeStoryboard?.cancelActiveTarget?.(entry.videoId);
    }
  }

  function scheduleIdleExact(args, mainVideo) {
    const videoId = String(args?.videoId || '').trim();
    if (!videoId || !originalRequestSegment) return null;
    if (exactIdleTimer) {
      clearTimeout(exactIdleTimer);
      metrics.exactIdleResets += 1;
    }
    const previous = idleExact;
    if (previous?.videoId && previous.videoId !== videoId) {
      globalThis.ArchivebateYouTubeStoryboard?.cancelVideoClientWork?.(previous.videoId);
    }
    idleExact = { ...args, videoId, scheduledAt: now() };
    metrics.exactIdleScheduled += 1;

    const attempt = () => {
      exactIdleTimer = null;
      const entry = idleExact;
      if (!entry || !hoverActive || entry.signal?.aborted) {
        clearIdleExact();
        return;
      }
      const current = synchronizeIdentity(mainVideo, appState());
      if (!current.videoId || current.videoId !== entry.videoId) {
        clearIdleExact({ cancelActive: true });
        return;
      }
      if (!playbackSafe(mainVideo, EXACT_MIN_BUFFER_SECONDS)) {
        metrics.exactIdleBlocked += 1;
        exactIdleTimer = setTimeout(attempt, EXACT_RECHECK_MS);
        return;
      }
      idleExact = null;
      metrics.exactIdleStarts += 1;
      originalRequestSegment(entry);
    };

    exactIdleTimer = setTimeout(attempt, EXACT_IDLE_MS);
    return null;
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
    const storyboard = globalThis.ArchivebateYouTubeStoryboard;
    if (!timeline || !mainVideo || !sprite || !storyboard?.applyFrame) return;

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
      storyboard.clearFrame?.(sprite);
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
        previewImg.style.display = 'block';
      }
      if (status) {
        status.textContent = 'Przygotowywanie podglądu…';
        status.style.display = 'block';
      }
      metrics.posterFallbacks += 1;
    }

    function applyBoard(board, targetTime, duration, kind) {
      if (!board || !hoverActive) return false;
      const result = storyboard.applyFrame(sprite, board, targetTime, { targetTime, duration });
      if (!result?.ok) return false;
      if (previewVideo) previewVideo.style.display = 'none';
      if (previewImg) previewImg.style.display = 'none';
      if (status) status.style.display = 'none';
      const identity = `${board.sprite_url}|${result.frameIndex}`;
      if (identity !== lastFrameIdentity) {
        lastFrameIdentity = identity;
        metrics.frames += 1;
        if (kind === 'exact') metrics.exactFrames += 1;
      }
      return true;
    }

    function showExact(board, targetTime, duration) {
      return applyBoard(board, targetTime, duration, 'exact');
    }

    function showCoarse(board, targetTime, duration) {
      return applyBoard(board, targetTime, duration, 'coarse');
    }

    renderLatestBoard = showCoarse;

    function ensureInteractiveCoarse(videoId, duration) {
      const key = cacheKey(videoId, duration);
      const state = appState();
      const existing = state?.timelineSpriteBoard?.sprite_url
        ? state.timelineSpriteBoard
        : lruGet(coarseBoards, key);
      if (existing) {
        if (state && !state.timelineSpriteBoard) state.timelineSpriteBoard = existing;
        return Promise.resolve(existing);
      }
      const inflight = coarseBuilds.get(key);
      if (inflight) return inflight.promise;
      if (!playbackSafe(mainVideo, INTERACTIVE_BUFFER_SECONDS)) return null;
      metrics.hoverCoarseStarts += 1;
      return prewarmCoarse(videoId, duration, { reason: 'interactive_hover' });
    }

    if (typeof storyboard.requestSegment === 'function' && !storyboard.__v452PlayerQoSCoordinator) {
      originalRequestSegment = storyboard.requestSegment.bind(storyboard);
      storyboard.requestSegment = function coordinatedRequestSegment(args = {}) {
        const state = appState();
        const identity = synchronizeIdentity(mainVideo, state);
        const videoId = String(args.videoId || identity.videoId || '').trim();
        const duration = Number(args.duration || identity.duration || 0);
        const targetTime = Number(args.targetTime || 0);
        if (!videoId || !Number.isFinite(duration) || duration <= 0) {
          return originalRequestSegment(args);
        }

        const normalized = { ...args, videoId, duration, targetTime };
        const exact = getExactCached(videoId, duration, targetTime);
        if (exact) {
          metrics.exactCacheHits += 1;
          return originalRequestSegment(normalized);
        }

        // Cached/local presentation stays immediate. Uncached EXACT is only the
        // latest idle target; pointer churn never directly starts backend work.
        ensureInteractiveCoarse(videoId, duration);
        return scheduleIdleExact(normalized, mainVideo);
      };
      storyboard.__v452PlayerQoSCoordinator = true;
    }

    function handleVideoSwitch(nextVideoId) {
      const next = String(nextVideoId || '').trim();
      if (latestVideoId && next && latestVideoId !== next) {
        const previous = latestVideoId;
        metrics.videoSwitchCancels += 1;
        clearIdleExact({ cancelActive: true });
        cancelCoarse(previous, 'video_switched');
        storyboard.cancelVideoClientWork?.(previous);
        requestHardCancel(previous, 'video_switched');
      }
      if (next) latestVideoId = next;
    }

    function requestFromClientX(clientX) {
      if (!modal?.classList?.contains?.('active')) return;
      const state = appState();
      if (!state || state.currentTimelinePrefix) return;
      const { videoId, duration } = synchronizeIdentity(mainVideo, state);
      if (!videoId || !Number.isFinite(duration) || duration <= 0) return;
      handleVideoSwitch(videoId);

      const rect = timeline.getBoundingClientRect?.();
      if (!rect?.width) return;
      const pos = Math.max(0, Math.min(1, (Number(clientX) - rect.left) / rect.width));
      const targetTime = pos * duration;
      latestDuration = duration;
      latestTargetTime = targetTime;
      hoverActive = true;
      metrics.pointerUpdates += 1;

      const exact = getExactCached(videoId, duration, targetTime);
      if (exact) {
        metrics.exactCacheHits += 1;
        if (showExact(exact, targetTime, duration)) return;
      }

      const existingBoard = state.timelineSpriteBoard;
      if (existingBoard?.sprite_url) {
        metrics.existingCoarseHits += 1;
        if (showCoarse(existingBoard, targetTime, duration)) return;
      }

      const board = lruGet(coarseBoards, cacheKey(videoId, duration));
      if (board) {
        metrics.coarseCacheHits += 1;
        state.timelineSpriteBoard = board;
        if (showCoarse(board, targetTime, duration)) return;
      }

      ensureInteractiveCoarse(videoId, duration);
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
        if (!modal?.classList?.contains?.('active') || hoverActive) return;
        const state = appState();
        const { videoId, duration } = synchronizeIdentity(mainVideo, state);
        handleVideoSwitch(videoId);
        if (!videoId || !duration) return;
        if (!playbackSafe(mainVideo, PREWARM_BUFFER_SECONDS)) {
          metrics.playbackProtectSkips += 1;
          return;
        }
        metrics.prewarmStarts += 1;
        prewarmCoarse(videoId, duration, { reason: mainVideo.paused ? 'paused' : 'buffered_playback' });
      }, Math.max(0, Number(delay) || 0));
    }

    function protectPlayback(reason) {
      const state = appState();
      const { videoId } = synchronizeIdentity(mainVideo, state);
      clearIdleExact({ cancelActive: true });
      if (videoId && cancelCoarse(videoId, reason)) metrics.playbackProtectSkips += 1;
      requestSoftProtect(reason);
    }

    timeline.addEventListener('pointerenter', event => {
      hoverActive = true;
      const state = appState();
      const { videoId } = synchronizeIdentity(mainVideo, state);
      handleVideoSwitch(videoId);
      schedulePointer(event);
    }, { passive: true });

    timeline.addEventListener('pointermove', event => {
      // Any real movement resets the single EXACT idle timer and cancels an
      // already-running target-specific exact client request.
      clearIdleExact({ cancelActive: true });
      schedulePointer(event);
    }, { passive: true });

    timeline.addEventListener('pointerleave', () => {
      hoverActive = false;
      lastFrameIdentity = '';
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
      pointerRaf = 0;
      clearIdleExact({ cancelActive: true });
      schedulePrewarm(RETRY_AFTER_HOVER_MS);
    }, { passive: true });

    mainVideo.addEventListener('loadedmetadata', () => {
      const { videoId } = synchronizeIdentity(mainVideo, appState());
      handleVideoSwitch(videoId);
    });
    mainVideo.addEventListener('playing', () => {
      const { videoId } = synchronizeIdentity(mainVideo, appState());
      handleVideoSwitch(videoId);
      schedulePrewarm(PREWARM_DELAY_MS);
    });
    mainVideo.addEventListener('pause', () => {
      const { videoId } = synchronizeIdentity(mainVideo, appState());
      handleVideoSwitch(videoId);
      if (Number(mainVideo.currentTime || 0) > 0.25) schedulePrewarm(350);
    });
    mainVideo.addEventListener('waiting', () => protectPlayback('waiting'));
    mainVideo.addEventListener('stalled', () => protectPlayback('stalled'));
    mainVideo.addEventListener('seeking', () => protectPlayback('seeking'));
    mainVideo.addEventListener('seeked', () => {
      if (!hoverActive) schedulePrewarm(PREWARM_DELAY_MS);
    });
    mainVideo.addEventListener('timeupdate', () => {
      if (mainVideo.paused) return;
      const state = appState();
      const { videoId } = synchronizeIdentity(mainVideo, state);
      handleVideoSwitch(videoId);
      if (!videoId) return;
      const ahead = bufferedAhead(mainVideo);
      if (ahead < LOW_BUFFER_CANCEL_SECONDS) {
        if (lowBufferVideoId !== videoId) {
          clearIdleExact({ cancelActive: true });
          if (cancelCoarse(videoId, 'low_buffer')) metrics.lowBufferCancels += 1;
          requestSoftProtect('low_buffer');
          lowBufferVideoId = videoId;
        }
      } else {
        lowBufferVideoId = '';
      }
    });

    mainVideo.addEventListener('emptied', () => {
      const previous = latestVideoId || String(mainVideo.dataset?.videoId || '').trim();
      clearIdleExact({ cancelActive: true });
      if (previous) {
        cancelCoarse(previous, 'video_emptied');
        storyboard.cancelVideoClientWork?.(previous);
        requestHardCancel(previous, 'video_emptied');
      }
      hoverActive = false;
      latestVideoId = '';
      latestDuration = 0;
      latestTargetTime = 0;
      lowBufferVideoId = '';
      lastFrameIdentity = '';
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = null;
    });

    if (modal && globalThis.MutationObserver) {
      let wasActive = !!modal.classList?.contains?.('active');
      modalObserver = new MutationObserver(() => {
        const active = !!modal.classList?.contains?.('active');
        if (wasActive && !active) {
          const previous = latestVideoId || currentIdentity(mainVideo, appState()).videoId;
          clearIdleExact({ cancelActive: true });
          if (previous) {
            cancelCoarse(previous, 'modal_closed');
            storyboard.cancelVideoClientWork?.(previous);
            requestHardCancel(previous, 'modal_closed');
          }
          hoverActive = false;
        }
        wasActive = active;
      });
      modalObserver.observe(modal, { attributes: true, attributeFilter: ['class'] });
    }
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
        coordinator_version: 452,
        hover_active: hoverActive,
        source_video_id: latestVideoId,
        source_duration_s: Number(latestDuration.toFixed?.(3) ?? latestDuration),
        requests: metrics.requests,
        pointer_updates: metrics.pointerUpdates,
        frames: metrics.frames,
        exact_frames: metrics.exactFrames,
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
        exact_cache_hits: metrics.exactCacheHits,
        existing_coarse_hits: metrics.existingCoarseHits,
        prewarm_starts: metrics.prewarmStarts,
        hover_coarse_starts: metrics.hoverCoarseStarts,
        playback_protect_skips: metrics.playbackProtectSkips,
        low_buffer_cancels: metrics.lowBufferCancels,
        build_errors: metrics.buildErrors,
        exact_idle_scheduled: metrics.exactIdleScheduled,
        exact_idle_resets: metrics.exactIdleResets,
        exact_idle_starts: metrics.exactIdleStarts,
        exact_idle_blocked: metrics.exactIdleBlocked,
        exact_idle_cancelled: metrics.exactIdleCancelled,
        soft_protect_requests: metrics.softProtectRequests,
        hard_cancel_requests: metrics.hardCancelRequests,
        video_switch_cancels: metrics.videoSwitchCancels,
        identity_repairs: metrics.identityRepairs,
        exact_idle_active: !!idleExact,
        coarse_cache_entries: coarseBoards.size,
        coarse_inflight: coarseBuilds.size,
        last_target_time: Number(latestTargetTime.toFixed?.(3) ?? latestTargetTime),
        exact_idle_ms: EXACT_IDLE_MS,
        exact_min_buffer_seconds: EXACT_MIN_BUFFER_SECONDS,
        prewarm_delay_ms: PREWARM_DELAY_MS,
        prewarm_buffer_seconds: PREWARM_BUFFER_SECONDS,
        interactive_buffer_seconds: INTERACTIVE_BUFFER_SECONDS,
        status_wait_ms: STATUS_WAIT_MS,
        player_qos_coordinator: true,
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
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
      pointerRaf = 0;
      clearIdleExact({ cancelActive: true });
      hoverActive = false;
      latestVideoId = '';
      latestDuration = 0;
      latestTargetTime = 0;
      lastFrameIdentity = '';
      modalObserver?.disconnect?.();
      modalObserver = null;
    },
  };
})();
