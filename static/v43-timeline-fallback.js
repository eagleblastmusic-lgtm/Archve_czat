(() => {
  'use strict';

  // Dynamic Archivebate timeline fallback. Exact storyboard segments still win.
  // The media fallback is now latest-target-wins: a new pointer target supersedes
  // an older in-flight seek instead of waiting for every old seek to finish.
  const MOVE_SEEK_INTERVAL_MS = 100;
  const TARGET_STEP_SECONDS = 1.0;
  const PREWARM_DELAY_MS = 1400;

  let installed = false;
  let hoverActive = false;
  let sourceVideoId = '';
  let latestTargetTime = 0;
  let latestDuration = 0;
  let latestVideoId = '';
  let hasDynamicFrame = false;
  let lastFrameTargetTime = null;
  let lastPublishedMediaTime = null;
  let pointerRaf = 0;
  let pendingClientX = 0;
  let pendingSeekTarget = null;
  let seekTimer = null;
  let prewarmTimer = null;
  let lastSeekAt = 0;
  let lastSeekTarget = null;

  const metrics = {
    requests: 0,
    pointerUpdates: 0,
    seekDispatches: 0,
    supersededSeeks: 0,
    frames: 0,
    sourceLoads: 0,
    prewarmLoads: 0,
    exactSkips: 0,
    coarseSkips: 0,
    notReadySkips: 0,
    metadataEvents: 0,
    seekedEvents: 0,
    videoErrors: 0,
    seekedFallbackFrames: 0,
  };

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

  function currentSegmentCached(videoId, duration, targetTime) {
    if (!videoId || !duration || !globalThis.ArchivebateYouTubeStoryboard?.getSegmentFromCache) return false;
    return Boolean(globalThis.ArchivebateYouTubeStoryboard.getSegmentFromCache(videoId, duration, targetTime));
  }

  function keepPreviewComposited(previewVideo, visible = false) {
    if (!previewVideo) return;
    previewVideo.style.display = 'block';
    previewVideo.style.visibility = 'visible';
    previewVideo.style.opacity = visible ? '1' : '0.001';
    previewVideo.style.pointerEvents = 'none';
  }

  function hideDynamicVideo(previewVideo) {
    if (!previewVideo) return;
    previewVideo.style.display = 'none';
    previewVideo.style.opacity = '0.001';
  }

  function clearSeekTimer() {
    if (seekTimer) clearTimeout(seekTimer);
    seekTimer = null;
  }

  function resetHover(previewVideo, { clearSource = false } = {}) {
    hideDynamicVideo(previewVideo);
    hoverActive = false;
    latestTargetTime = 0;
    latestDuration = 0;
    latestVideoId = '';
    pendingSeekTarget = null;
    clearSeekTimer();
    if (pointerRaf) cancelAnimationFrame(pointerRaf);
    pointerRaf = 0;
    if (clearSource && previewVideo) {
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = null;
      try { previewVideo.pause?.(); } catch (_) {}
      previewVideo.removeAttribute?.('src');
      try { previewVideo.load?.(); } catch (_) {}
      if (previewVideo.dataset) delete previewVideo.dataset.v43PreviewVideoId;
      sourceVideoId = '';
      hasDynamicFrame = false;
      lastFrameTargetTime = null;
      lastPublishedMediaTime = null;
      lastSeekTarget = null;
      lastSeekAt = 0;
    }
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
    if (!timeline || !mainVideo || !previewVideo) return;

    installed = true;
    previewVideo.muted = true;
    previewVideo.playsInline = true;
    previewVideo.preload = 'metadata';

    function publishFrame(targetTime, { fromSeekedFallback = false } = {}) {
      if (!hoverActive || !modal?.classList?.contains?.('active')) return false;
      const state = appState();
      const { videoId, duration } = currentIdentity(mainVideo, state);
      if (!videoId || videoId !== latestVideoId || Math.abs(duration - latestDuration) > 1.0) return false;
      if (currentSegmentCached(videoId, duration, latestTargetTime)) {
        metrics.exactSkips += 1;
        hideDynamicVideo(previewVideo);
        return false;
      }
      if (state?.currentTimelinePrefix || state?.timelineSpriteBoard) {
        metrics.coarseSkips += 1;
        hideDynamicVideo(previewVideo);
        return false;
      }
      if (previewVideo.readyState < 2 || previewVideo.seeking) return false;

      const mediaTime = Number(previewVideo.currentTime) || 0;
      if (lastPublishedMediaTime !== null && Math.abs(mediaTime - lastPublishedMediaTime) < 0.01) {
        keepPreviewComposited(previewVideo, true);
        return true;
      }
      lastPublishedMediaTime = mediaTime;
      hasDynamicFrame = true;
      lastFrameTargetTime = Number(targetTime) || mediaTime;
      if (previewImg) previewImg.style.display = 'none';
      if (sprite && globalThis.ArchivebateYouTubeStoryboard?.clearFrame) {
        globalThis.ArchivebateYouTubeStoryboard.clearFrame(sprite);
      }
      if (status) status.style.display = 'none';
      keepPreviewComposited(previewVideo, true);
      metrics.frames += 1;
      if (fromSeekedFallback) metrics.seekedFallbackFrames += 1;
      return true;
    }

    function quantizeTarget(target, duration) {
      const maxTime = Math.max(0, Number(duration) - 0.05);
      const clamped = Math.max(0, Math.min(maxTime, Number(target) || 0));
      return Math.max(0, Math.min(maxTime, Math.round(clamped / TARGET_STEP_SECONDS) * TARGET_STEP_SECONDS));
    }

    function dispatchLatestSeek() {
      clearSeekTimer();
      if (pendingSeekTarget === null || previewVideo.readyState < 1 || !Number.isFinite(previewVideo.duration) || previewVideo.duration <= 0) return;

      const target = quantizeTarget(pendingSeekTarget, previewVideo.duration);
      pendingSeekTarget = null;
      const now = performance.now();
      const wait = MOVE_SEEK_INTERVAL_MS - (now - lastSeekAt);
      if (wait > 0) {
        pendingSeekTarget = target;
        seekTimer = setTimeout(dispatchLatestSeek, wait);
        return;
      }

      if (lastSeekTarget !== null && Math.abs(target - lastSeekTarget) < 0.5) return;
      if (previewVideo.seeking) metrics.supersededSeeks += 1;
      lastSeekAt = now;
      lastSeekTarget = target;
      metrics.seekDispatches += 1;
      metrics.requests = metrics.seekDispatches;

      try {
        // Setting currentTime while a previous seek is still active makes the
        // browser abandon the obsolete target and chase the newest pointer target.
        // The old shared seeker intentionally serialized seeks; that was correct
        // but visually sluggish on remote MP4 range streams.
        previewVideo.currentTime = target;
      } catch (_) {
        metrics.videoErrors += 1;
      }
    }

    function queueLatestSeek(targetTime) {
      pendingSeekTarget = targetTime;
      if (previewVideo.readyState < 1) return;
      const now = performance.now();
      const wait = Math.max(0, MOVE_SEEK_INTERVAL_MS - (now - lastSeekAt));
      clearSeekTimer();
      seekTimer = setTimeout(dispatchLatestSeek, wait);
    }

    previewVideo.addEventListener('loadedmetadata', () => {
      metrics.metadataEvents += 1;
      if (pendingSeekTarget !== null) dispatchLatestSeek();
    });
    previewVideo.addEventListener('seeked', () => {
      metrics.seekedEvents += 1;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        publishFrame(previewVideo.currentTime, { fromSeekedFallback: true });
        if (pendingSeekTarget !== null) dispatchLatestSeek();
      }));
    });
    previewVideo.addEventListener('error', () => { metrics.videoErrors += 1; });

    function ensurePreviewSource(videoId, { prewarm = false } = {}) {
      if (!videoId) return false;
      if (sourceVideoId === videoId && previewVideo.getAttribute?.('src')) return true;

      sourceVideoId = videoId;
      hasDynamicFrame = false;
      lastFrameTargetTime = null;
      lastPublishedMediaTime = null;
      lastSeekTarget = null;
      lastSeekAt = 0;
      const src = `/api/video/stream?id=${encodeURIComponent(videoId)}&owner=preview&priority=low&reason=timeline_preview`;
      if (previewVideo.dataset) previewVideo.dataset.v43PreviewVideoId = videoId;
      previewVideo.src = src;
      previewVideo.preload = 'metadata';
      if (prewarm) hideDynamicVideo(previewVideo);
      else keepPreviewComposited(previewVideo, false);
      try { previewVideo.load(); } catch (_) {}
      metrics.sourceLoads += 1;
      if (prewarm) metrics.prewarmLoads += 1;
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

      if (currentSegmentCached(videoId, duration, targetTime)) {
        metrics.exactSkips += 1;
        hideDynamicVideo(previewVideo);
        return;
      }
      if (state.timelineSpriteBoard) {
        metrics.coarseSkips += 1;
        hideDynamicVideo(previewVideo);
        return;
      }
      if (Number(mainVideo.readyState || 0) < 2) {
        metrics.notReadySkips += 1;
        return;
      }

      ensurePreviewSource(videoId);
      hoverActive = true;
      latestTargetTime = targetTime;
      latestDuration = duration;
      latestVideoId = videoId;
      metrics.pointerUpdates += 1;

      // modal-player-controls paints its cold poster first. This fallback RAF is
      // registered afterwards and restores the last decoded dynamic frame while
      // the newest seek is being chased.
      keepPreviewComposited(previewVideo, hasDynamicFrame);
      if (hasDynamicFrame) {
        if (previewImg) previewImg.style.display = 'none';
        if (status) status.style.display = 'none';
      } else if (status) {
        status.textContent = 'Pobieranie klatki…';
        status.style.display = 'block';
      }

      queueLatestSeek(targetTime);
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

    function schedulePrewarm() {
      if (prewarmTimer) clearTimeout(prewarmTimer);
      prewarmTimer = setTimeout(() => {
        prewarmTimer = null;
        if (!modal?.classList?.contains?.('active')) return;
        const state = appState();
        const { videoId } = currentIdentity(mainVideo, state);
        if (!videoId || Number(mainVideo.readyState || 0) < 3) return;
        const bufferedAhead = Number(globalThis.ArchivebatePerf?.getBufferedAhead?.(mainVideo) || 0);
        if (bufferedAhead < 2.0) return;
        ensurePreviewSource(videoId, { prewarm: true });
      }, PREWARM_DELAY_MS);
    }

    timeline.addEventListener('pointerenter', schedulePointer, { passive: true });
    timeline.addEventListener('pointermove', schedulePointer, { passive: true });
    timeline.addEventListener('pointerleave', () => resetHover(previewVideo), { passive: true });
    mainVideo.addEventListener('playing', schedulePrewarm);
    mainVideo.addEventListener('emptied', () => resetHover(previewVideo, { clearSource: true }));
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
      const previewVideo = globalThis.document?.getElementById?.('modalTimelinePreviewVideo');
      return {
        installed,
        hover_active: hoverActive,
        source_video_id: sourceVideoId,
        requests: metrics.requests,
        pointer_updates: metrics.pointerUpdates,
        seek_dispatches: metrics.seekDispatches,
        superseded_seeks: metrics.supersededSeeks,
        frames: metrics.frames,
        source_loads: metrics.sourceLoads,
        prewarm_loads: metrics.prewarmLoads,
        exact_skips: metrics.exactSkips,
        coarse_skips: metrics.coarseSkips,
        not_ready_skips: metrics.notReadySkips,
        metadata_events: metrics.metadataEvents,
        seeked_events: metrics.seekedEvents,
        seeked_fallback_frames: metrics.seekedFallbackFrames,
        video_errors: metrics.videoErrors,
        has_dynamic_frame: hasDynamicFrame,
        last_target_time: Number(latestTargetTime.toFixed?.(3) ?? latestTargetTime),
        last_frame_target_time: lastFrameTargetTime,
        preview_ready_state: Number(previewVideo?.readyState || 0),
        preview_duration: Number.isFinite(Number(previewVideo?.duration)) ? Number(previewVideo.duration) : 0,
        preview_current_time: Number(previewVideo?.currentTime || 0),
        move_seek_interval_ms: MOVE_SEEK_INTERVAL_MS,
      };
    },
    reset() {
      const previewVideo = globalThis.document?.getElementById?.('modalTimelinePreviewVideo');
      resetHover(previewVideo, { clearSource: true });
    },
  };
})();
