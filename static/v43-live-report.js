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
    };
  }

  function activeContextSnapshot() {
    const state = globalThis.ArchivebateAppContext?.state || globalThis.state || null;
    const details = state?.currentVideoDetails || null;
    const id = String(state?.currentVideoId || details?.id || '').trim();
    const source = String(details?.source || '').trim() || (id.startsWith('cw_') ? 'camwhores' : 'archivebate');
    return {
      current_video_id: id,
      current_details_id: String(details?.id || ''),
      source,
      has_camwhores_timeline_prefix: !!state?.currentTimelinePrefix,
      timeline_prefix: state?.currentTimelinePrefix ? String(state.currentTimelinePrefix) : '',
      timeline_count: Number(state?.currentTimelineCount || 0),
      hover_controller_active: !!state?.timelineHoverController && !state.timelineHoverController.signal?.aborted,
      storyboard_api_loaded: !!globalThis.ArchivebateYouTubeStoryboard,
      storyboard_request_segment: typeof globalThis.ArchivebateYouTubeStoryboard?.requestSegment === 'function',
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

  async function serverStoryboardStats() {
    try {
      const response = await fetch('/api/diagnostics', { cache: 'no-store' });
      if (!response.ok) return { error: `HTTP ${response.status}` };
      const data = await response.json();
      return data?.jobs?.storyboard || null;
    } catch (error) {
      return { error: String(error?.message || error || 'diagnostics_failed') };
    }
  }

  async function collect() {
    const perf = globalThis.ArchivebatePerf || null;
    const storyboard = globalThis.ArchivebateYouTubeStoryboard || null;
    const clientStoryboard = typeof storyboard?.stats === 'function' ? storyboard.stats() : null;
    const serverStoryboard = await serverStoryboardStats();

    return {
      schema: 'archivebate-v43-live-report/2',
      generated_at: new Date().toISOString(),
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
        client: clientStoryboard,
        server: serverStoryboard,
      },
      player: activePlayerSnapshot(perf),
      context: activeContextSnapshot(),
    };
  }

  async function print() {
    const report = await collect();
    try {
      console.group('Archivebate V4.3 live gate');
      console.log('click -> first frame', report.playback.click_to_first_frame);
      console.log('timeline client', report.timeline.client);
      console.log('timeline server', report.timeline.server);
      console.log('player', report.player);
      console.log('context', report.context);
      console.log(JSON.stringify(report, null, 2));
      console.groupEnd();
    } catch (_) {}
    return report;
  }

  globalThis.ArchivebateV43Diagnostics = { collect, print };
})();