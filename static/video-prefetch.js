/**
 * Archivebate Video Browser - Video Prefetch / Cache Module
 * Odpowiada za buforowanie szczegółów wideo (LRU cache), prefetch metadanych,
 * leniwe ładowanie miniatur (IntersectionObserver) oraz planowanie rozgrzewania miniatur (warmup).
 */

(function (global) {
  'use strict';

  const perf = global.ArchivebatePerf || {
    LRUCache: class {
      constructor(max = 180) {
        this.max = max;
        this.m = new Map();
      }
      get(k) {
        if (!this.m.has(k)) return undefined;
        const v = this.m.get(k);
        this.m.delete(k);
        this.m.set(k, v);
        return v;
      }
      set(k, v) {
        if (this.m.has(k)) this.m.delete(k);
        else if (this.m.size >= this.max) {
          const firstKey = this.m.keys().next().value;
          this.m.delete(firstKey);
        }
        this.m.set(k, v);
      }
      has(k) {
        return this.m.has(k);
      }
    },
    prefetchUrls: () => Promise.resolve(),
    idle: (fn) => setTimeout(fn, 16)
  };

  const api = global.ArchivebateAPI || {
    getJSON: (url, opts) => fetch(url, opts).then(r => r.json())
  };

  // Pamięć podręczna detali wideo dla natychmiastowego startu po kliknięciu
  const videoDetailsCache = new perf.LRUCache(180);

  // Znane niedostępne nagrania przechowujemy lokalnie i czasowo. Nie oznaczamy
  // filmu jako martwego po pojedynczym błędzie sieciowym: wymagamy dwóch kolejnych
  // odpowiedzi `availability=unavailable`, z czego druga omija cache serwera.
  // TTL chroni przed trwałym ukryciem filmu po chwilowej awarii hostingu.
  const UNAVAILABLE_STORAGE_KEY = 'archivebate_unavailable_videos_v1';
  const UNAVAILABLE_TTL_MS = 12 * 60 * 60 * 1000;
  const unavailableConfirmInflight = new Map();

  function unavailableKey(videoId) {
    const raw = String(videoId || '').trim();
    if (!raw || raw.toLowerCase().startsWith('cw_')) return null;
    return `archivebate:id:${raw}`;
  }

  function loadUnavailableRegistry() {
    const registry = new Map();
    if (typeof localStorage === 'undefined') return registry;
    try {
      const parsed = JSON.parse(localStorage.getItem(UNAVAILABLE_STORAGE_KEY) || '{}');
      const now = Date.now();
      for (const [key, expiresAt] of Object.entries(parsed || {})) {
        const expiry = Number(expiresAt);
        if (Number.isFinite(expiry) && expiry > now) registry.set(key, expiry);
      }
    } catch (_) {}
    return registry;
  }

  const unavailableRegistry = loadUnavailableRegistry();

  function persistUnavailableRegistry() {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(UNAVAILABLE_STORAGE_KEY, JSON.stringify(Object.fromEntries(unavailableRegistry)));
    } catch (_) {}
  }

  function isKnownUnavailableVideo(videoId) {
    const key = unavailableKey(videoId);
    if (!key) return false;
    const expiresAt = Number(unavailableRegistry.get(key));
    if (!Number.isFinite(expiresAt)) return false;
    if (expiresAt <= Date.now()) {
      unavailableRegistry.delete(key);
      persistUnavailableRegistry();
      return false;
    }
    return true;
  }

  function forgetUnavailableVideo(videoId) {
    const key = unavailableKey(videoId);
    if (!key || !unavailableRegistry.has(key)) return;
    unavailableRegistry.delete(key);
    persistUnavailableRegistry();
  }

  function groupMemberArrays(video) {
    if (!video || typeof video !== 'object') return [];
    const arrays = [];
    if (Array.isArray(video.grouped_videos)) arrays.push(video.grouped_videos);
    if (Array.isArray(video._groupedVideos) && video._groupedVideos !== video.grouped_videos) arrays.push(video._groupedVideos);
    return arrays;
  }

  function isGroupedAggregate(video) {
    if (!video || typeof video !== 'object') return false;
    const arrays = groupMemberArrays(video);
    return Boolean(
      video.is_grouped || video._isGrouped || video.group_members_lazy ||
      Number(video.group_count || 0) > 1 || Number(video._groupCount || 0) > 1 ||
      arrays.some(items => items.length > 1)
    );
  }

  function pruneUnavailableGroupedMember(video, raw) {
    if (!isGroupedAggregate(video)) return { grouped: false, changed: false, promoted: null };

    const arrays = groupMemberArrays(video);
    const knownIds = Array.isArray(video._unavailableMemberIds)
      ? video._unavailableMemberIds.map(value => String(value || '').trim()).filter(Boolean)
      : [];
    const alreadyMarked = knownIds.includes(raw);
    if (!alreadyMarked) knownIds.push(raw);

    const uniqueMembers = [];
    const seen = new Set();
    for (const items of arrays) {
      for (const member of items) {
        const id = String(member?.id || '').trim();
        if (!id || seen.has(id)) continue;
        seen.add(id);
        uniqueMembers.push(member);
      }
    }

    const memberWasPresent = uniqueMembers.some(member => String(member?.id || '').trim() === raw);
    const leaderWasUnavailable = String(video.id || '').trim() === raw || isKnownUnavailableVideo(video.id);
    if (!memberWasPresent && !leaderWasUnavailable && alreadyMarked) {
      return { grouped: true, changed: false, promoted: null };
    }

    const keepMember = member => {
      const id = String(member?.id || '').trim();
      return Boolean(id) && id !== raw && !isKnownUnavailableVideo(id);
    };
    const remaining = uniqueMembers.filter(keepMember);

    // Mutujemy istniejące tablice in-place. video-card.js trzyma do nich referencje
    // w rozwijanej szufladzie, więc podmiana całej tablicy pozostawiałaby stare wpisy.
    for (const items of arrays) {
      const kept = items.filter(keepMember);
      items.splice(0, items.length, ...kept);
    }

    const oldCount = Math.max(
      1,
      Number(video.group_count || 0),
      Number(video._groupCount || 0),
      uniqueMembers.length
    );
    const newCount = alreadyMarked ? oldCount : Math.max(1, oldCount - 1);

    let promoted = null;
    if (leaderWasUnavailable && remaining.length > 0) {
      promoted = remaining[0];
      const groupedVideosRef = Array.isArray(video.grouped_videos) ? video.grouped_videos : null;
      const groupedVideosCompatRef = Array.isArray(video._groupedVideos) ? video._groupedVideos : null;
      const groupMembersLazy = video.group_members_lazy;
      const groupMembersUrl = video.group_members_url;
      const revision = video.revision;
      Object.assign(video, promoted);
      if (groupedVideosRef) video.grouped_videos = groupedVideosRef;
      if (groupedVideosCompatRef) video._groupedVideos = groupedVideosCompatRef;
      if (groupMembersLazy !== undefined) video.group_members_lazy = groupMembersLazy;
      if (groupMembersUrl !== undefined) video.group_members_url = groupMembersUrl;
      if (revision !== undefined) video.revision = revision;
    }

    video.group_count = newCount;
    video._groupCount = newCount;
    video.is_grouped = newCount > 1;
    video._isGrouped = newCount > 1;
    video._unavailableMemberIds = knownIds;
    video._representativeUnavailable = Boolean(leaderWasUnavailable && !promoted);

    return {
      grouped: true,
      changed: !alreadyMarked || memberWasPresent || leaderWasUnavailable,
      promoted
    };
  }

  function removeUnavailableCardInstances(videoId) {
    if (typeof document === 'undefined') return;
    const raw = String(videoId || '').trim();
    if (!raw) return;

    const state = global.ArchivebateAppContext?.state || global.state;

    document.querySelectorAll('.video-card').forEach(card => {
      if (String(card?.dataset?.source || '').toLowerCase() !== 'archivebate') return;

      const cardVideo = card?._videoData;
      const groupedResult = pruneUnavailableGroupedMember(cardVideo, raw);
      if (groupedResult.grouped && groupedResult.changed) {
        // Najważniejsza zasada: awaria reprezentanta NIE usuwa całej grupy.
        // Usuwamy tylko konkretny niedostępny element i, gdy mamy lokalnie
        // kolejnego członka, promujemy go na miniaturkę/reprezentanta grupy.
        if (groupedResult.promoted) {
          card.dataset.videoId = String(cardVideo?.id || '');
          card.dataset.source = String(cardVideo?.source || 'archivebate').toLowerCase();
        }
        if (typeof card._updateCard === 'function') {
          try { card._updateCard(cardVideo, card._cardIndex); } catch (_) {}
        }
        return;
      }

      if (String(card?.dataset?.videoId || '').trim() !== raw) return;
      const key = card._cardKey;
      try { card.remove(); } catch (_) {}
      if (key && state?.gridCardMap?.delete) state.gridCardMap.delete(key);
    });

    if (state && Array.isArray(state.videos)) {
      const nextVideos = [];
      for (const video of state.videos) {
        if (!video || typeof video !== 'object') continue;
        const groupedResult = pruneUnavailableGroupedMember(video, raw);
        if (groupedResult.grouped) {
          // Zachowaj agregat: pozostałe 99 działających filmów nie mogą zniknąć
          // tylko dlatego, że jeden reprezentant/element hostingu wygasł.
          nextVideos.push(video);
          continue;
        }
        if (String(video.id || '').trim() !== raw) nextVideos.push(video);
      }
      state.videos = nextVideos;
      const dom = global.ArchivebateAppContext?.dom || global.dom || {};
      if (dom.statPageVideos) dom.statPageVideos.textContent = String(state.videos.length);
    }
  }

  function markUnavailableVideo(videoId) {
    const key = unavailableKey(videoId);
    if (!key) return false;
    unavailableRegistry.set(key, Date.now() + UNAVAILABLE_TTL_MS);
    persistUnavailableRegistry();
    removeUnavailableCardInstances(videoId);
    try {
      global.dispatchEvent?.(new CustomEvent('archivebate:video-unavailable', {
        detail: { videoId: String(videoId), source: 'archivebate' }
      }));
    } catch (_) {}
    return true;
  }

  function detailsAreUnavailable(details) {
    return Boolean(
      details &&
      details.availability === 'unavailable' &&
      !details.is_private &&
      !details.direct_url &&
      !details.proxy_stream_url
    );
  }

  async function confirmUnavailableVideo(videoId, initialDetails, signal = null) {
    const key = unavailableKey(videoId);
    if (!key || !detailsAreUnavailable(initialDetails) || signal?.aborted) return initialDetails;
    if (unavailableConfirmInflight.has(key)) return unavailableConfirmInflight.get(key);

    const request = api.getJSON(
      `/api/video/details?id=${encodeURIComponent(videoId)}&force_refresh=true`,
      { timeoutMs: 12000, signal }
    ).then(fresh => {
      if (fresh && !signal?.aborted) videoDetailsCache.set(videoId, fresh);
      if (detailsAreUnavailable(fresh)) {
        markUnavailableVideo(videoId);
      } else if (fresh && (fresh.availability === 'available' || fresh.direct_url || fresh.proxy_stream_url)) {
        forgetUnavailableVideo(videoId);
      }
      return fresh || initialDetails;
    }).catch(() => initialDetails).finally(() => {
      unavailableConfirmInflight.delete(key);
    });

    unavailableConfirmInflight.set(key, request);
    return request;
  }

  // Ładuj obrazy dopiero, gdy karta zbliża się do viewportu. Wcześniej
  // armLazyThumbnail() ustawiał src od razu wszystkim ~280 kartom, co tworzyło
  // duży burst requestów do /api/thumb i opóźniało pierwszy użyteczny ekran.
  const lazyThumbObserver = (typeof window !== 'undefined' && 'IntersectionObserver' in window)
    ? new IntersectionObserver((entries, observer) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const img = entry.target;
          if (img.dataset.src && !img.hasAttribute('src')) {
            img.src = img.dataset.src;
            delete img.dataset.src;
          }
          observer.unobserve(img);
        }
      }, { rootMargin: '700px 0px', threshold: 0.01 })
    : null;

  // video-views.js rozłącza obserwator przy zmianie widoku, aby stare elementy
  // nie utrzymywały requestów po przejściu na inną stronę.
  global.lazyThumbObserver = lazyThumbObserver;

  function armLazyThumbnail(img) {
    if (!img || !img.dataset.src) return;
    const src = img.dataset.src;
    img.loading = 'lazy';

    if (lazyThumbObserver) {
      lazyThumbObserver.observe(img);
      return;
    }

    // Fallback dla WebView/przeglądarki bez IntersectionObserver.
    img.src = src;
    delete img.dataset.src;
  }

  const videoDetailsInflight = new Map();
  const MAX_CONCURRENT_PREFETCH = 2;

  function prefetchVideoDetails(videoId, options = {}) {
    if (!videoId) return Promise.resolve(null);
    if (isKnownUnavailableVideo(videoId)) {
      removeUnavailableCardInstances(videoId);
      return Promise.resolve({ id: videoId, availability: 'unavailable', locally_quarantined: true });
    }

    const cached = videoDetailsCache.get(videoId);
    if (cached) {
      if (detailsAreUnavailable(cached)) void confirmUnavailableVideo(videoId, cached);
      else if (cached.availability === 'available' || cached.direct_url || cached.proxy_stream_url) forgetUnavailableVideo(videoId);
      return Promise.resolve(cached);
    }

    const signal = (typeof options === 'object' && options?.signal) ? options.signal : null;
    if (signal?.aborted) return Promise.resolve(null);

    if (videoDetailsInflight.has(videoId)) return videoDetailsInflight.get(videoId);
    if (videoDetailsInflight.size >= MAX_CONCURRENT_PREFETCH) return Promise.resolve(null);

    const promise = api.getJSON(`/api/video/details?id=${encodeURIComponent(videoId)}`, { timeoutMs: 9000, signal })
      .then(details => {
        if (details && !signal?.aborted) {
          videoDetailsCache.set(videoId, details);
          if (detailsAreUnavailable(details)) {
            void confirmUnavailableVideo(videoId, details, signal);
          } else if (details.availability === 'available' || details.direct_url || details.proxy_stream_url) {
            forgetUnavailableVideo(videoId);
          }
        }
        return details;
      })
      .catch(() => null)
      .finally(() => videoDetailsInflight.delete(videoId));

    videoDetailsInflight.set(videoId, promise);
    return promise;
  }

  /**
   * Pobranie początkowego zakresu bajtów (np. 256 KiB) wyłącznie dla aktywnego kandydata (Pakiet C, punkt 7).
   * Jeśli serwer ignoruje Range i zwraca 200, połączenie jest natychmiast przerywane!
   */
  async function prefetchCandidateStreamChunk(streamUrl, { maxBytes = 262144, signal } = {}) {
    if (!streamUrl || signal?.aborted) return false;
    const ac = new AbortController();
    const forwardAbort = () => ac.abort();
    if (signal) signal.addEventListener('abort', forwardAbort, { once: true });
    try {
      const resp = await fetch(streamUrl, {
        headers: { Range: `bytes=0-${maxBytes - 1}` },
        signal: ac.signal
      });
      // Jeśli serwer zignorował nagłówek Range (zwrócił 200 zamiast 206), natychmiast przerywamy!
      if (resp.status === 200 || resp.status !== 206) {
        ac.abort();
        return false;
      }
      const reader = resp.body?.getReader();
      if (!reader) return false;
      let total = 0;
      while (total < maxBytes && !ac.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value ? value.byteLength : 0;
      }
      return true;
    } catch (_) {
      return false;
    } finally {
      ac.abort();
      if (signal) signal.removeEventListener('abort', forwardAbort);
    }
  }

  function thumbnailUrlForVideo(v) {
    if (!v) return '';
    return v.poster_proxy || v.thumbnail_proxy || (v.poster ? `/api/thumb?url=${encodeURIComponent(v.poster)}` : '');
  }

  let thumbnailWarmupController = null;
  function scheduleThumbnailWarmup(videos, start = 12, count = 60) {
    if (!Array.isArray(videos) || videos.length <= start) return;
    if (thumbnailWarmupController) thumbnailWarmupController.abort();
    thumbnailWarmupController = new AbortController();

    // Warmup ma pomagać pierwszemu ekranowi, a nie konkurować z nim. Ograniczamy
    // go do 12 miniatur; resztę przejmuje IntersectionObserver podczas scrolla.
    const warmCount = Math.min(Math.max(0, count), 12);
    if (warmCount === 0) return;
    const urls = videos.slice(start, start + warmCount).map(thumbnailUrlForVideo).filter(Boolean);
    perf.idle(() => {
      perf.prefetchUrls(urls, { concurrency: 3, signal: thumbnailWarmupController.signal }).catch(() => {});
    }, 900);
  }

  function hasVideoDetails(videoId) {
    return videoDetailsCache.has(videoId);
  }

  function getVideoDetails(videoId) {
    return videoDetailsCache.get(videoId);
  }

  function setVideoDetails(videoId, details) {
    videoDetailsCache.set(videoId, details);
    if (details && (details.availability === 'available' || details.direct_url || details.proxy_stream_url)) {
      forgetUnavailableVideo(videoId);
    }
  }

  function isDetailsInflight(videoId) {
    return videoDetailsInflight.has(videoId);
  }

  const ArchivebateVideoPrefetch = {
    detailsCache: videoDetailsCache,
    videoDetailsCache,
    videoDetailsInflight,
    prefetchVideoDetails,
    prefetchDetails: prefetchVideoDetails,
    armLazyThumbnail,
    thumbnailUrlForVideo,
    thumbnailUrl: thumbnailUrlForVideo,
    scheduleThumbnailWarmup,
    scheduleWarmup: scheduleThumbnailWarmup,
    hasVideoDetails,
    getVideoDetails,
    setVideoDetails,
    isDetailsInflight,
    prefetchCandidateStreamChunk,
    isKnownUnavailableVideo,
    markUnavailableVideo,
    forgetUnavailableVideo,
    confirmUnavailableVideo
  };

  global.ArchivebateVideoPrefetch = ArchivebateVideoPrefetch;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoPrefetch;
  }
})(typeof window !== 'undefined' ? window : globalThis);
