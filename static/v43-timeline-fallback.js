(() => {
  'use strict';

  // V4.3 dynamic fallback for Archivebate timeline hover.
  // Exact 1-fps storyboard segments remain the preferred source. When the
  // hovered 30-second segment is still cold, use one muted low-priority video
  // element with coalesced seeking instead of showing the same poster for every
  // timeline position. This does NOT re-enable cold QUICK FFmpeg work.
  const MIN_INTERVAL_MS = 90;
  const WATCHDOG_MS = 1800;

  let installed = false;
  let hoverActive = false;
  let seeker = null;
  let sourceVideoId = '';
  let latestTargetTime = 0;
  let latestDuration = 0;
  let latestVideoId = '';
  let hasDynamicFrame = false;
  let lastFrameTargetTime = null;

  const metrics = {
    requests: 0,
    frames: 0,
    sourceLoads: 0,
    exactSkips: 0,
    coarseSkips: 0,
    notReadySkips: 0,
    metadataEvents: 0,
    seekedEvents: 0,
    videoErrors: 0,
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

  // requestVideoFrameCallback may never fire for display:none media because no
  // frame reaches the compositor. Keep the element composited at tiny opacity
  // until the first decoded frame arrives; the ordinary poster remains visible
  // above it. Once a frame exists, keep that last decoded frame visible while
  // the next seek is in flight so pointer movement never falls back to one
  // static poster between seeks.
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

  function resetSeeker(previewVideo, { clearSource = false } = {}) {
    seeker?.reset?.();
    hideDynamicVideo(previewVideo);
    hoverActive = false;
    latestTargetTime = 0;
    latestDuration = 0;
    latestVideoId = '';
    hasDynamicFrame = false;
    lastFrameTargetTime = null;
    if (clearSource && previewVideo) {
      try { previewVideo.pause?.(); } catch (_) {}
      previewVideo.removeAttribute?.('src');
      try { previewVideo.load?.(); } catch (_) {}
      if (previewVideo.dataset) delete previewVideo.dataset.v43PreviewVideoId;
      sourceVideoId = '';
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
    if (!timeline || !mainVideo || !previewVideo || !globalThis.ArchivebatePlayerCore?.createPreviewSeeker) return;

    installed = true;
    previewVideo.muted = true;
    previewVideo.playsInline = true;
    previewVideo.preload = 'metadata';
    previewVideo.addEventListener('loadedmetadata', () => { metrics.metadataEvents += 1; });
    previewVideo.addEventListener('seeked', () => { metrics.seekedEvents += 1; });
    previewVideo.addEventListener('error', () => { metrics.videoErrors += 1; });

    seeker = globalThis.ArchivebatePlayerCore.createPreviewSeeker(previewVideo, {
      minInterval: MIN_INTERVAL_MS,
      watchdogMs: WATCHDOG_MS,
      onFrame: (_actualTime, requestedTime) => {
        if (!hoverActive) return;
        if (!modal?.classList?.contains?.('active')) return;
        const state = appState();
        const { videoId, duration } = currentIdentity(mainVideo, state);
        if (!videoId || videoId !== latestVideoId || Math.abs(duration - latestDuration) > 1.0) return;

        // Exact sprite may have completed while the fallback seek was in flight.
        // Never cover a ready exact frame with the lower-priority video fallback.
        if (currentSegmentCached(videoId, duration, latestTargetTime)) {
          metrics.exactSkips += 1;
          hideDynamicVideo(previewVideo);
          return;
        }
        if (state?.currentTimelinePrefix || state?.timelineSpriteBoard) {
          metrics.coarseSkips += 1;
          hideDynamicVideo(previewVideo);
          return;
        }

        // The seeker serializes decode work, so every callback is the newest
        // actually decoded frame even when a newer pointer target is already
        // queued. Showing it is preferable to rejecting every in-flight frame
        // and leaving the static poster visible until the cursor stops.
        hasDynamicFrame = true;
        lastFrameTargetTime = Number(requestedTime) || 0;
        if (previewImg) previewImg.style.display = 'none';
        if (sprite && globalThis.ArchivebateYouTubeStoryboard?.clearFrame) {
          globalThis.ArchivebateYouTubeStoryboard.clearFrame(sprite);
        }
        if (status) status.style.display = 'none';
        keepPreviewComposited(previewVideo, true);
        metrics.frames += 1;
      },
      onError: () => {
        metrics.videoErrors += 1;
        if (!hasDynamicFrame) hideDynamicVideo(previewVideo);
      },
    });

    function ensurePreviewSource(videoId) {
      if (!videoId) return false;
      if (sourceVideoId === videoId && previewVideo.getAttribute?.('src')) {
        keepPreviewComposited(previewVideo, hasDynamicFrame);
        return true;
      }
      seeker?.reset?.();
      hasDynamicFrame = false;
      lastFrameTargetTime = null;
      sourceVideoId = videoId;
      const src = `/api/video/stream?id=${encodeURIComponent(videoId)}&owner=preview&priority=low&reason=timeline_preview`;
      if (previewVideo.dataset) previewVideo.dataset.v43PreviewVideoId = videoId;
      previewVideo.src = src;
      previewVideo.preload = 'metadata';
      // Critical: compositor-backed frame callbacks require the media element to
      // participate in rendering. It is almost transparent until decoding wins.
      keepPreviewComposited(previewVideo, false);
      try { previewVideo.load(); } catch (_) {}
      metrics.sourceLoads += 1;
      return true;
    }

    function requestFromPointer(event) {
      if (!event || !modal?.classList?.contains?.('active')) return;
      const state = appState();
      if (!state || state.currentTimelinePrefix) return;
      const { videoId, duration } = currentIdentity(mainVideo, state);
      if (!videoId || !Number.isFinite(duration) || duration <= 0) return;

      const rect = timeline.getBoundingClientRect?.();
      if (!rect?.width) return;
      const pos = Math.max(0, Math.min(1, (Number(event.clientX) - rect.left) / rect.width));
      const targetTime = pos * duration;

      // The exact sprite and any already-prepared coarse board stay above this
      // fallback. We only open the extra media source when the UI would
      // otherwise show the same static poster at every position.
      if (currentSegmentCached(videoId, duration, targetTime)) {
        metrics.exactSkips += 1;
        seeker?.reset?.();
        hideDynamicVideo(previewVideo);
        return;
      }
      if (state.timelineSpriteBoard) {
        metrics.coarseSkips += 1;
        seeker?.reset?.();
        hideDynamicVideo(previewVideo);
        return;
      }

      // Do not compete with click-to-first-frame. The main player must already
      // have decoded useful media before the fallback opens a second range stream.
      if (Number(mainVideo.readyState || 0) < 2) {
        metrics.notReadySkips += 1;
        return;
      }

      if (!ensurePreviewSource(videoId)) return;
      hoverActive = true;
      latestTargetTime = targetTime;
      latestDuration = duration;
      latestVideoId = videoId;

      // modal-player-controls shows the poster on every pointermove. Our handler
      // runs afterwards, restoring the last decoded frame immediately while the
      // next seek is pending. Before the first frame we stay composited at tiny
      // opacity so requestVideoFrameCallback can fire.
      keepPreviewComposited(previewVideo, hasDynamicFrame);
      if (hasDynamicFrame) {
        if (previewImg) previewImg.style.display = 'none';
        if (status) status.style.display = 'none';
      } else if (status) {
        status.textContent = 'Pobieranie klatki…';
        status.style.display = 'block';
      }

      metrics.requests += 1;
      seeker.request(targetTime);
    }

    timeline.addEventListener('pointerenter', requestFromPointer, { passive: true });
    timeline.addEventListener('pointermove', requestFromPointer, { passive: true });
    timeline.addEventListener('pointerleave', () => resetSeeker(previewVideo), { passive: true });

    // Switching/closing the primary player is a hard lifecycle boundary for the
    // low-priority preview source as well.
    mainVideo.addEventListener('emptied', () => resetSeeker(previewVideo, { clearSource: true }));
  }

  function scheduleInstall() {
    // modal-player-controls.js registers its handlers earlier in index.html. Run
    // after DOMContentLoaded listeners so its normal exact-sprite path executes
    // first and this module remains only a fallback layer.
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
        frames: metrics.frames,
        source_loads: metrics.sourceLoads,
        exact_skips: metrics.exactSkips,
        coarse_skips: metrics.coarseSkips,
        not_ready_skips: metrics.notReadySkips,
        metadata_events: metrics.metadataEvents,
        seeked_events: metrics.seekedEvents,
        video_errors: metrics.videoErrors,
        has_dynamic_frame: hasDynamicFrame,
        last_target_time: Number(latestTargetTime.toFixed?.(3) ?? latestTargetTime),
        last_frame_target_time: lastFrameTargetTime,
        preview_ready_state: Number(previewVideo?.readyState || 0),
        preview_duration: Number.isFinite(Number(previewVideo?.duration)) ? Number(previewVideo.duration) : 0,
        preview_current_time: Number(previewVideo?.currentTime || 0),
        min_interval_ms: MIN_INTERVAL_MS,
      };
    },
    reset() {
      const previewVideo = globalThis.document?.getElementById?.('modalTimelinePreviewVideo');
      resetSeeker(previewVideo, { clearSource: true });
    },
  };
})();
