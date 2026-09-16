(() => {
  'use strict';

  function percentiles(perf, key) {
    if (!perf || typeof perf.calculatePercentiles !== 'function') {
      return { count: 0, p50: 0, p95: 0 };
    }
    return perf.calculatePercentiles(key);
  }

  function activePlayerSnapshot(perf) {
    const modal = globalThis.document?.getElementById?.('modalVideo');
    const watch = globalThis.document?.getElementById?.('mainPlayer');
    const video = watch || modal || null;
    if (!video) return null;
    let bufferedAhead = 0;
    try {
      bufferedAhead = Number(perf?.getBufferedAhead?.(video) || 0);
      if (!bufferedAhead && video.buffered?.length) {
        const current = Number(video.currentTime) || 0;
        for (let i = 0; i < video.buffered.length; i += 1) {
          if (video.buffered.start(i) <= current + 0.1 && video.buffered.end(i) >= current) {
            bufferedAhead = Math.max(0, video.buffered.end(i) - current);
            break;
          }
        }
      }
    } catch (_) {}

    let media = {};
    try {
      media = globalThis.ArchivebatePlayerQoS?.snapshot?.(video) ||
        globalThis.ArchivebatePlayerQoS?.getPlaybackMediaStats?.(video) || {};
    } catch (_) {}

    return {
      id: String(video.id || ''),
      dataset_video_id: String(video.dataset?.videoId || ''),
      paused: !!video.paused,
      ended: !!video.ended,
      seeking: !!video.seeking,
      ready_state: Number(video.readyState || 0),
      current_time_s: Number((Number(video.currentTime) || 0).toFixed(3)),
      duration_s: Number.isFinite(Number(video.duration)) ? Number(Number(video.duration).toFixed(3)) : null,
      buffered_ahead_s: Number(bufferedAhead.toFixed(3)),
      video_width: Number(media.video_width || video.videoWidth || 0),
      video_height: Number(media.video_height || video.videoHeight || 0),
      display_width: Number(media.display_width || video.clientWidth || 0),
      display_height: Number(media.display_height || video.clientHeight || 0),
      total_video_frames: Number(media.total_video_frames || 0),
      dropped_video_frames: Number(media.dropped_video_frames || 0),
      corrupted_video_frames: Number(media.corrupted_video_frames || 0),
      dropped_frame_ratio: Number(media.dropped_frame_ratio || 0),
      stall_count: Number(media.stall_count || 0),
      stall_duration_ms: Number(media.stall_duration_ms || 0),
      stall_active: !!media.stall_active,
      qos_reason: String(media.reason || media.last_reason || ''),
    };
  }

  function activeContextSnapshot() {
    const state = globalThis.ArchivebateAppContext?.state || globalThis.state || null;
    const details = state?.currentVideoDetails || null;
    const id = String(details?.id || state?.currentVideoId || '').trim();
    const source = String(details?.source || '').trim() || (id.startsWith('cw_') ? 'camwhores' : 'archivebate');
    return {
      current_video_id: id,
      current_details_id: String(details?.id || ''),
      source,
      has_camwhores_timeline_prefix: !!state?.currentTimelinePrefix,
      timeline_prefix: state?.currentTimelinePrefix ? String(state.currentTimelinePrefix) : '',
      timeline_count: Number(state?.currentTimelineCount || 0),
      coarse_board_ready: !!state?.timelineSpriteBoard?.sprite_url,
      coarse_board_frames: Number(state?.timelineSpriteBoard?.frame_count || 0),
      hover_controller_active: !!state?.timelineHoverController && !state.timelineHoverController.signal?.aborted,
      storyboard_api_loaded: !!globalThis.ArchivebateYouTubeStoryboard,
      storyboard_request_segment: typeof globalThis.ArchivebateYouTubeStoryboard?.requestSegment === 'function',
      player_qos_loaded: !!globalThis.ArchivebatePlayerQoS,
    };
  }

  function recentPlaybackSessions(perf, limit = 12) {
    const rows = Array.isArray(perf?.sessionHistory) ? perf.sessionHistory.slice(-limit) : [];
    return rows.map(item => ({
      owner: item?.owner || '',
      reason: item?.reason || '',
      host: item?.host || '',
      stages: { ...(item?.stages || {}) },
    }));
  }

  async function fetchJSON(url) {
    try {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) return { error: `HTTP ${response.status}` };
      return await response.json();
    } catch (error) {
      return { error: String(error?.message || error || 'request_failed') };
    }
  }

  async function serverStoryboardStats() {
    const data = await fetchJSON('/api/diagnostics');
    if (data?.error) return data;
    return data?.jobs?.storyboard || null;
  }

  async function collect() {
    const perf = globalThis.ArchivebatePerf || null;
    const storyboard = globalThis.ArchivebateYouTubeStoryboard || null;
    const fallback = globalThis.ArchivebateV43TimelineFallback || null;
    const clientStoryboard = typeof storyboard?.stats === 'function' ? storyboard.stats() : null;
    const fallbackStats = typeof fallback?.stats === 'function' ? fallback.stats() : null;
    const [serverStoryboard, coarseServer, qosServer, runtime] = await Promise.all([
      serverStoryboardStats(),
      fetchJSON('/api/runtime/v43/storyboard/stats'),
      fetchJSON('/api/runtime/v452/qos'),
      fetchJSON('/api/runtime/v43'),
    ]);

    return {
      schema: 'archivebate-v452-live-report/1',
      generated_at: new Date().toISOString(),
      runtime,
      playback: {
        click_to_first_frame: percentiles(perf, 'total_click_to_first_frame_ms'),
        click_to_resolve: percentiles(perf, 'click_to_resolve_ms'),
        resolve_to_connect: percentiles(perf, 'resolve_to_connect_ms'),
        connect_to_first_byte: percentiles(perf, 'connect_to_first_byte_ms'),
        first_byte_to_metadata: percentiles(perf, 'first_byte_to_metadata_ms'),
        metadata_to_first_frame: percentiles(perf, 'metadata_to_first_frame_ms'),
        recent_sessions: recentPlaybackSessions(perf),
      },
      timeline: {
        exact_client: clientStoryboard,
        fallback: fallbackStats,
        exact_server: serverStoryboard,
        coarse_server: coarseServer,
      },
      qos: {
        client: globalThis.ArchivebatePlayerQoS ? { installed: true } : { installed: false },
        server: qosServer,
      },
      player: activePlayerSnapshot(perf),
      context: activeContextSnapshot(),
    };
  }

  async function print() {
    const report = await collect();
    try {
      console.group('Archivebate V4.5.2 live gate');
      console.log('click -> first frame', report.playback.click_to_first_frame);
      console.log('timeline exact client', report.timeline.exact_client);
      console.log('timeline fallback', report.timeline.fallback);
      console.log('timeline exact server', report.timeline.exact_server);
      console.log('timeline coarse server', report.timeline.coarse_server);
      console.log('player QoS', report.qos);
      console.log('player', report.player);
      console.log('context', report.context);
      console.log(JSON.stringify(report, null, 2));
      console.groupEnd();
    } catch (_) {}
    return report;
  }

  const api = { collect, print };
  globalThis.ArchivebateV43Diagnostics = api;
  globalThis.ArchivebateV452Diagnostics = api;
})();
