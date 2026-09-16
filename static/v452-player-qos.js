(() => {
  'use strict';

  const REPORT_INTERVAL_MS = 700;
  const statsByVideo = new WeakMap();
  const lastVideoIds = new WeakMap();
  const lastReports = new WeakMap();

  function mutationHeaders() {
    const token = document.querySelector?.('meta[name="archivebate-mutation-token"]')?.content || '';
    return {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Archivebate-Mutation-Token': token } : {}),
    };
  }

  function postJSON(url, body = {}) {
    if (globalThis.ArchivebateAPI?.postJSON) {
      return globalThis.ArchivebateAPI.postJSON(url, body, { timeoutMs: 2500, keepalive: true }).catch(() => null);
    }
    return fetch(url, {
      method: 'POST',
      headers: mutationHeaders(),
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => null);
  }

  function resolveVideoId(video) {
    const datasetId = String(video?.dataset?.videoId || '').trim();
    if (datasetId) return datasetId;
    if (video?.id === 'modalVideo') {
      const state = globalThis.ArchivebateAppContext?.state || globalThis.state || null;
      const modalId = String(state?.currentVideoDetails?.id || state?.currentVideoId || '').trim();
      if (modalId) return modalId;
    }
    if (video?.id === 'mainPlayer') {
      try {
        const parts = String(location.pathname || '').split('/').filter(Boolean);
        if (parts[0]?.toLowerCase() === 'watch' && parts[1]) return decodeURIComponent(parts[parts.length - 1]);
      } catch (_) {}
    }
    return lastVideoIds.get(video) || '';
  }

  function bufferedAhead(video) {
    try {
      if (globalThis.ArchivebatePerf?.getBufferedAhead) {
        return Math.max(0, Number(globalThis.ArchivebatePerf.getBufferedAhead(video) || 0));
      }
      const current = Number(video?.currentTime) || 0;
      for (let i = 0; i < (video?.buffered?.length || 0); i += 1) {
        if (video.buffered.start(i) <= current + 0.1 && video.buffered.end(i) >= current) {
          return Math.max(0, video.buffered.end(i) - current);
        }
      }
    } catch (_) {}
    return 0;
  }

  function stateFor(video) {
    let stats = statsByVideo.get(video);
    if (!stats) {
      stats = {
        stall_count: 0,
        stall_started_at: 0,
        stall_duration_ms: 0,
        last_reason: 'startup',
      };
      statsByVideo.set(video, stats);
    }
    return stats;
  }

  function beginStall(video, reason) {
    const stats = stateFor(video);
    stats.last_reason = reason;
    if (!stats.stall_started_at) {
      stats.stall_started_at = performance.now();
      stats.stall_count += 1;
    }
  }

  function endStall(video) {
    const stats = stateFor(video);
    if (stats.stall_started_at) {
      stats.stall_duration_ms += Math.max(0, performance.now() - stats.stall_started_at);
      stats.stall_started_at = 0;
    }
  }

  function mediaStats(video) {
    const stats = stateFor(video);
    let total = 0;
    let dropped = 0;
    let corrupted = 0;
    try {
      const quality = video?.getVideoPlaybackQuality?.();
      total = Number(quality?.totalVideoFrames || 0);
      dropped = Number(quality?.droppedVideoFrames || 0);
      corrupted = Number(quality?.corruptedVideoFrames || 0);
    } catch (_) {}
    const runningStall = stats.stall_started_at ? Math.max(0, performance.now() - stats.stall_started_at) : 0;
    const rect = video?.getBoundingClientRect?.();
    return {
      video_width: Number(video?.videoWidth || 0),
      video_height: Number(video?.videoHeight || 0),
      display_width: Number(rect?.width || video?.clientWidth || 0),
      display_height: Number(rect?.height || video?.clientHeight || 0),
      total_video_frames: total,
      dropped_video_frames: dropped,
      corrupted_video_frames: corrupted,
      dropped_frame_ratio: total > 0 ? Number((dropped / total).toFixed(6)) : 0,
      stall_count: Number(stats.stall_count || 0),
      stall_duration_ms: Number((stats.stall_duration_ms + runningStall).toFixed(1)),
      stall_active: !!stats.stall_started_at,
      last_reason: stats.last_reason,
    };
  }

  function statusBody(video, reason) {
    const videoId = resolveVideoId(video);
    if (videoId) lastVideoIds.set(video, videoId);
    const buffered = bufferedAhead(video);
    const paused = !!video?.paused;
    const seeking = !!video?.seeking;
    const readyState = Number(video?.readyState || 0);
    return {
      video_id: videoId,
      active: !paused && !video?.ended,
      paused,
      seeking,
      ready_state: readyState,
      buffered_seconds: Number(buffered.toFixed(3)),
      is_busy: paused || seeking || readyState < 2 || buffered < 5,
      reason,
    };
  }

  function report(video, reason = 'status', force = false) {
    if (!video || (video.id !== 'modalVideo' && video.id !== 'mainPlayer')) return;
    const now = performance.now();
    const last = Number(lastReports.get(video) || 0);
    if (!force && now - last < REPORT_INTERVAL_MS) return;
    lastReports.set(video, now);
    postJSON('/api/runtime/v452/playback/status', statusBody(video, reason));
  }

  function hardCancel(video, reason) {
    const videoId = lastVideoIds.get(video) || resolveVideoId(video);
    if (!videoId) return;
    postJSON(`/api/runtime/v452/storyboard/cancel?id=${encodeURIComponent(videoId)}&reason=${encodeURIComponent(reason)}`, {});
  }

  function onEvent(event) {
    const video = event?.target;
    if (!video || (video.id !== 'modalVideo' && video.id !== 'mainPlayer')) return;
    const reason = String(event.type || 'event');
    const videoId = resolveVideoId(video);
    if (videoId) lastVideoIds.set(video, videoId);

    if (reason === 'waiting' || reason === 'stalled') beginStall(video, reason);
    if (reason === 'playing' || reason === 'canplay') endStall(video);
    if (reason === 'emptied') {
      endStall(video);
      hardCancel(video, 'video_emptied');
      lastReports.delete(video);
      return;
    }
    if (reason === 'seeking') beginStall(video, reason);
    if (reason === 'seeked') endStall(video);
    report(video, reason, ['waiting', 'stalled', 'seeking', 'seeked', 'playing', 'pause'].includes(reason));
  }

  const EVENTS = ['loadedmetadata', 'playing', 'pause', 'waiting', 'stalled', 'seeking', 'seeked', 'canplay', 'progress', 'timeupdate', 'emptied'];
  for (const name of EVENTS) document.addEventListener(name, onEvent, true);

  addEventListener('pagehide', () => {
    for (const id of ['modalVideo', 'mainPlayer']) {
      const video = document.getElementById?.(id);
      if (video) hardCancel(video, 'pagehide');
    }
  }, { capture: true });

  const api = {
    getPlaybackMediaStats: mediaStats,
    snapshot(video) {
      return { ...statusBody(video, 'snapshot'), ...mediaStats(video) };
    },
    report,
    hardCancel,
  };
  globalThis.ArchivebatePlayerQoS = api;
  if (globalThis.ArchivebatePerf) {
    globalThis.ArchivebatePerf.getPlaybackMediaStats = mediaStats;
  }
})();
