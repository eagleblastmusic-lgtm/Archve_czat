(() => {
  'use strict';

  // V4.5.2 startup optimization: once the current modal video has actually
  // started playing, resolve details/direct_url for exactly one likely next
  // video. The request intentionally has no player-generation AbortSignal, so
  // switching A -> B does not cancel the warm-up for B just before it is needed.
  const stats = {
    started: 0,
    completed: 0,
    cacheReady: 0,
    failed: 0,
    skipped: 0,
    watchdogTriggers: 0,
    eventTriggers: 0,
    lastCurrentId: '',
    lastCandidateId: '',
    lastDurationMs: 0,
  };

  let activeWarm = null;
  let rerunRequested = false;
  let lastObservedPlayingId = '';

  function state() {
    return globalThis.ArchivebateAppContext?.state || globalThis.state || {};
  }

  function currentVideo() {
    return state().currentVideoDetails || null;
  }

  function currentVideoId() {
    return String(currentVideo()?.id || state().currentVideoId || '').trim();
  }

  function candidateFromAuthorPlaylist(modal, current) {
    const username = String(modal?.getEffectiveVideoUsername?.(current) || current?.username || '').trim().toLowerCase();
    if (!username) return null;
    const playlist = modal?.authorPlaylists?.get?.(username);
    if (!playlist || !Array.isArray(playlist.videos) || playlist.videos.length < 2) return null;
    const index = modal.getAuthorVideoIndex?.(playlist, current) ?? -1;
    if (index < 0 || index >= playlist.videos.length - 1) return null;
    return playlist.videos[index + 1] || null;
  }

  function candidateFromActiveList(modal, current) {
    const list = modal?.getActiveFilteredVideos?.();
    if (!Array.isArray(list) || list.length < 2) return null;
    const currentId = String(current?.id || '');
    const currentUrl = String(current?.url || '');
    const index = list.findIndex(video =>
      (currentId && String(video?.id || '') === currentId) ||
      (currentUrl && String(video?.url || '') === currentUrl)
    );
    if (index < 0 || index >= list.length - 1) return null;
    return list[index + 1] || null;
  }

  function resolveCandidate() {
    const modal = globalThis.ArchivebateVideoModal;
    const current = currentVideo();
    if (!modal || !current) return null;
    return candidateFromAuthorPlaylist(modal, current) || candidateFromActiveList(modal, current);
  }

  async function warmNextCandidate() {
    if (activeWarm) {
      rerunRequested = true;
      return activeWarm.promise;
    }

    const prefetch = globalThis.ArchivebateVideoPrefetch;
    const currentId = currentVideoId();
    const candidate = resolveCandidate();
    const candidateId = String(candidate?.id || '').trim();

    if (!prefetch?.prefetchVideoDetails || !currentId || !candidateId || candidateId === currentId) {
      stats.skipped += 1;
      return null;
    }

    stats.lastCurrentId = currentId;
    stats.lastCandidateId = candidateId;

    if (prefetch.hasVideoDetails?.(candidateId)) {
      stats.cacheReady += 1;
      return prefetch.getVideoDetails?.(candidateId) || null;
    }

    const startedAt = performance.now();
    stats.started += 1;

    const holder = { id: candidateId, promise: null };
    holder.promise = Promise.resolve(prefetch.prefetchVideoDetails(candidateId))
      .then(details => {
        stats.lastDurationMs = Math.round(performance.now() - startedAt);
        if (details) stats.completed += 1;
        else stats.failed += 1;
        return details;
      })
      .catch(() => {
        stats.lastDurationMs = Math.round(performance.now() - startedAt);
        stats.failed += 1;
        return null;
      })
      .finally(() => {
        if (activeWarm === holder) activeWarm = null;
        if (rerunRequested) {
          rerunRequested = false;
          setTimeout(() => warmNextCandidate().catch(() => {}), 0);
        }
      });

    activeWarm = holder;
    return holder.promise;
  }

  function modalIsActive() {
    return Boolean(globalThis.document?.getElementById?.('videoModal')?.classList?.contains?.('active'));
  }

  function maybeWarmFromPlayer(source = 'watchdog') {
    const video = globalThis.document?.getElementById?.('modalVideo');
    const currentId = currentVideoId();
    if (!modalIsActive() || !video || !currentId || video.paused || video.ended || Number(video.readyState || 0) < 2) {
      return false;
    }

    // If playlist/list state is still being populated, leave the ID unclaimed so
    // the watchdog can retry on the next tick instead of permanently skipping it.
    const candidate = resolveCandidate();
    const candidateId = String(candidate?.id || '').trim();
    if (!candidateId || candidateId === currentId) return false;

    if (lastObservedPlayingId === currentId) return false;
    lastObservedPlayingId = currentId;
    if (source === 'event') stats.eventTriggers += 1;
    else stats.watchdogTriggers += 1;
    warmNextCandidate().catch(() => {});
    return true;
  }

  // Keep the event path because it is the cheapest fast path when Chromium
  // delivers the non-bubbling media event through capture as expected.
  globalThis.document?.addEventListener?.('playing', event => {
    if (event?.target?.id !== 'modalVideo') return;
    maybeWarmFromPlayer('event');
  }, true);

  // Some real browser sessions did not deliver that document-level media event.
  // A tiny state watchdog makes prefetch deterministic without touching player
  // lifecycle code. It fires at most once per actually-playing current video ID.
  const watchdog = globalThis.setInterval?.(() => {
    maybeWarmFromPlayer('watchdog');
  }, 250);

  // Also cover the case where this runtime script is injected after playback has
  // already begun (for example after a hard reload during an open modal).
  globalThis.setTimeout?.(() => maybeWarmFromPlayer('watchdog'), 0);

  globalThis.ArchivebateNextVideoPrefetch = {
    warmNextCandidate,
    maybeWarmFromPlayer,
    stats: () => ({
      ...stats,
      active_candidate_id: activeWarm?.id || '',
      active: Boolean(activeWarm),
      rerun_requested: rerunRequested,
      last_observed_playing_id: lastObservedPlayingId,
      watchdog_active: Boolean(watchdog),
    }),
  };
})();
