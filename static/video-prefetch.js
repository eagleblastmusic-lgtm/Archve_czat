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
      delete(k) {
        return this.m.delete(k);
      }
    },
    prefetchUrls: () => Promise.resolve(),
    idle: (fn) => setTimeout(fn, 16)
  };

  const api = global.ArchivebateAPI || {
    getJSON: (url, opts) => fetch(url, opts).then(r => r.json()),
    postJSON: (url, body, opts = {}) => fetch(url, {
      ...opts,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      body: JSON.stringify(body ?? {})
    }).then(r => r.json())
  };

  // Pamięć podręczna detali wideo dla natychmiastowego startu po kliknięciu
  const videoDetailsCache = new perf.LRUCache(180);

  // Only provider-confirmed source-page removals are quarantined. Older v1
  // entries had no evidence and are exported locally for diagnosis, then ignored.
  const UNAVAILABLE_STORAGE_KEY = 'archivebate_unavailable_videos_v2';
  const LEGACY_UNAVAILABLE_STORAGE_KEY = 'archivebate_unavailable_videos_v1';
  const LEGACY_UNAVAILABLE_EXPORT_KEY = 'archivebate_unavailable_videos_v1_diagnostic';
  const UNAVAILABLE_SCHEMA_VERSION = 2;
  const UNAVAILABLE_TTL_MS = 12 * 60 * 60 * 1000;
  const UNAVAILABLE_CONFIRM_GAP_MS = 1500;
  const unavailableConfirmInflight = new Map();
  const availabilityGenerations = new Map();
  const quarantinedVideoSnapshots = new Map();

  function videoIdentity(videoOrId, sourceHint = '') {
    const video = videoOrId && typeof videoOrId === 'object' ? videoOrId : null;
    if (video?.key && video?.source && (video.providerId || video.provider_id)) {
      const source = String(video.source).trim().toLowerCase();
      const providerId = String(video.providerId || video.provider_id).trim();
      if (['archivebate', 'camwhores'].includes(source) && /^[a-z0-9_-]{1,200}$/i.test(providerId)) {
        return { source, providerId, key: `${source}:id:${providerId}` };
      }
    }
    let id = String(video ? (video.provider_id || video.providerId || video.id || video.video_id || video.url || '') : (videoOrId || '')).trim();
    let source = String(sourceHint || video?.source || '').trim().toLowerCase();
    const providerUrl = String(video?.url || (typeof videoOrId === 'string' ? videoOrId : ''));
    let providerHost = '';
    try { providerHost = new URL(providerUrl).hostname.toLowerCase(); } catch (_) {}
    if (!source && (id.toLowerCase().startsWith('cw_') || String(video?.platform || '').toLowerCase().includes('camwhores') ||
        ['camwhores.tv', 'camwhores.co'].some(host => providerHost === host || providerHost.endsWith(`.${host}`)))) source = 'camwhores';
    if (!source && (String(video?.platform || '').toLowerCase().includes('archivebate') ||
        providerHost === 'archivebate.com' || providerHost.endsWith('.archivebate.com'))) source = 'archivebate';
    // Legacy callers still pass a bare Archivebate provider ID.  Numeric/raw
    // IDs have no host from which to infer scope, and Camwhores IDs are
    // normalized with the explicit `cw_` prefix, so the safe compatibility
    // default is the Archivebate namespace.
    if (!source && /^[a-z0-9_-]{1,200}$/i.test(id) && !/^cw_/i.test(id)) source = 'archivebate';
    if (!['archivebate', 'camwhores'].includes(source)) return null;
    const fromCamwhoresUrl = source === 'camwhores' && id.match(/\/videos\/(\d+)(?:\/|$)/i);
    const fromArchivebateUrl = source === 'archivebate' && id.match(/\/watch\/([^/?#]+)/i);
    if (fromCamwhoresUrl) id = fromCamwhoresUrl[1];
    else if (fromArchivebateUrl) id = fromArchivebateUrl[1];
    else if (id.includes('/')) id = id.split(/[/?#]/).filter(Boolean).pop() || '';
    if (source === 'camwhores') id = id.replace(/^cw_/i, '');
    if (!/^[a-z0-9_-]{1,200}$/i.test(id) || ['none', 'null', 'undefined'].includes(id.toLowerCase())) return null;
    return { source, providerId: id, key: `${source}:id:${id}` };
  }

  function requestVideoId(videoOrId, identity = videoIdentity(videoOrId)) {
    let id = String(videoOrId && typeof videoOrId === 'object'
      ? (videoOrId.id || videoOrId.video_id || videoOrId.provider_id || videoOrId.providerId || videoOrId.url || '')
      : (videoOrId || '')).trim();
    if (identity?.source === 'camwhores' && !/^cw_/i.test(id)) id = `cw_${identity.providerId}`;
    else if (identity?.source === 'archivebate' && (id.includes('/') || id.includes('://'))) id = identity.providerId;
    return id;
  }

  function detailsCacheKey(videoOrId, sourceHint = '') {
    return videoIdentity(videoOrId, sourceHint)?.key || null;
  }

  function unavailableKey(videoOrId, sourceHint = '') {
    return videoIdentity(videoOrId, sourceHint)?.key || null;
  }

  function loadUnavailableRegistry() {
    const registry = new Map();
    if (typeof localStorage === 'undefined') return registry;
    try {
      const legacyRaw = localStorage.getItem(LEGACY_UNAVAILABLE_STORAGE_KEY);
      if (legacyRaw) {
        if (!localStorage.getItem(LEGACY_UNAVAILABLE_EXPORT_KEY)) {
          try { localStorage.setItem(LEGACY_UNAVAILABLE_EXPORT_KEY, legacyRaw); } catch (_) {}
        }
        try { localStorage.removeItem(LEGACY_UNAVAILABLE_STORAGE_KEY); } catch (_) {}
      }
      const decoded = JSON.parse(localStorage.getItem(UNAVAILABLE_STORAGE_KEY) || '{}');
      const parsed = decoded?.version === UNAVAILABLE_SCHEMA_VERSION ? decoded.entries : {};
      const now = Date.now();
      for (const [key, value] of Object.entries(parsed || {})) {
        const entry = value && typeof value === 'object' ? value : {};
        const expiry = Number(entry.expires_at);
        const checkedAt = Number(entry.checked_at);
        const attemptedAt = Number(entry.attempted_at);
        const separator = key.indexOf(':id:');
        const source = separator > 0 ? key.slice(0, separator) : '';
        const providerId = separator > 0 ? key.slice(separator + 4) : '';
        if (['archivebate', 'camwhores'].includes(source) && /^[a-z0-9_-]{1,200}$/i.test(providerId) &&
            Number.isFinite(checkedAt) && checkedAt > 0 &&
            Number.isFinite(attemptedAt) && attemptedAt > 0 &&
            Number.isFinite(expiry) && expiry > now && expiry > attemptedAt &&
            expiry - attemptedAt <= UNAVAILABLE_TTL_MS && ['source_page_not_found', 'embed_file_not_found', 'stream_file_not_found'].includes(entry.reason) &&
            (entry.reason !== 'stream_file_not_found' || entry.stream_missing_confirmations === 2)) {
          registry.set(key, {
            reason: entry.reason,
            checked_at: checkedAt,
            attempted_at: attemptedAt,
            expires_at: expiry,
            stream_missing_confirmations: entry.stream_missing_confirmations,
          });
        }
      }
    } catch (_) {}
    return registry;
  }

  const unavailableRegistry = loadUnavailableRegistry();

  function persistUnavailableRegistry() {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(UNAVAILABLE_STORAGE_KEY, JSON.stringify({
        version: UNAVAILABLE_SCHEMA_VERSION,
        entries: Object.fromEntries(unavailableRegistry),
      }));
    } catch (_) {}
  }

  function isKnownUnavailableVideo(videoOrId, sourceHint = '') {
    const key = unavailableKey(videoOrId, sourceHint);
    if (!key) return false;
    const expiresAt = Number(unavailableRegistry.get(key)?.expires_at);
    if (!Number.isFinite(expiresAt)) return false;
    if (expiresAt <= Date.now()) {
      unavailableRegistry.delete(key);
      persistUnavailableRegistry();
      return false;
    }
    return true;
  }

  function bumpAvailabilityGeneration(key) {
    if (!key) return 0;
    const next = (availabilityGenerations.get(key) || 0) + 1;
    availabilityGenerations.set(key, next);
    return next;
  }

  function forgetUnavailableVideo(videoOrId, sourceHint = '') {
    const key = unavailableKey(videoOrId, sourceHint);
    if (!key) return;
    bumpAvailabilityGeneration(key);
    if (unavailableRegistry.delete(key)) persistUnavailableRegistry();
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
    const target = raw && typeof raw === 'object' ? raw : videoIdentity(raw);
    if (!target?.key) return { grouped: true, changed: false, related: false, promoted: null };
    const arrays = groupMemberArrays(video);
    const originalArrayLengths = arrays.map(items => items.length);
    const appliedKeys = Array.isArray(video._unavailableMemberKeys)
      ? video._unavailableMemberKeys.map(value => String(value || '').trim()).filter(Boolean)
      : [];
    const eventKey = `${Number(video.revision || 0)}|${target.key}`;

    const uniqueMembers = [];
    const seen = new Set();
    for (const items of arrays) {
      for (const member of items) {
        const key = videoIdentity(member)?.key;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        uniqueMembers.push(member);
      }
    }

    const leaderKey = videoIdentity(video)?.key;
    const memberWasPresent = uniqueMembers.some(member => videoIdentity(member)?.key === target.key);
    const leaderWasUnavailable = leaderKey === target.key;
    // An unrelated ID must never mutate this group, even on its first event.
    if (!memberWasPresent && !leaderWasUnavailable) return { grouped: true, changed: false, related: false, promoted: null };
    const alreadyApplied = appliedKeys.includes(eventKey);
    if (!alreadyApplied) appliedKeys.push(eventKey);

    const keepMember = member => {
      const key = videoIdentity(member)?.key;
      return Boolean(key) && key !== target.key && !isKnownUnavailableVideo(member);
    };
    const remaining = uniqueMembers.filter(keepMember);

    // Mutujemy istniejące tablice in-place. video-card.js trzyma do nich referencje
    // w rozwijanej szufladzie, więc podmiana całej tablicy pozostawiałaby stare wpisy.
    for (const items of arrays) {
      const kept = items.filter(keepMember);
      items.splice(0, items.length, ...kept);
    }

    const oldCount = Math.max(0, Number(video.group_count || video._groupCount || uniqueMembers.length || 0));
    const newCount = alreadyApplied ? oldCount : Math.max(0, oldCount - 1);

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
    // Preserve aggregate identity through the 2 -> 1 transition so the card
    // remains in grouped rendering and can still load its final member lazily.
    video.is_grouped = true;
    video._isGrouped = true;
    video._unavailableMemberKeys = appliedKeys;
    video._representativeUnavailable = Boolean(leaderWasUnavailable && !promoted);

    return {
      grouped: true,
      changed: !alreadyApplied || Boolean(promoted) || arrays.some((items, index) => items.length !== originalArrayLengths[index]),
      related: true,
      empty: newCount === 0,
      promoted
    };
  }

  function reconcileKnownUnavailableGroup(video) {
    if (!isGroupedAggregate(video)) return true;
    const candidates = [];
    const leader = videoIdentity(video);
    if (leader) candidates.push(leader);
    for (const items of groupMemberArrays(video)) {
      for (const member of items) {
        const identity = videoIdentity(member);
        if (identity && !candidates.some(value => value.key === identity.key)) candidates.push(identity);
      }
    }
    for (const identity of candidates) {
      if (isKnownUnavailableVideo(identity, identity.source)) pruneUnavailableGroupedMember(video, identity);
    }
    return Number(video.group_count || video._groupCount || 0) > 0;
  }

  function removeUnavailableCardInstances(videoOrId, sourceHint = '') {
    if (typeof document === 'undefined') return;
    const target = videoIdentity(videoOrId, sourceHint);
    if (!target) return;

    const state = global.ArchivebateAppContext?.state || global.state;

    document.querySelectorAll('.video-card').forEach(card => {
      const cardVideo = card?._videoData;
      const cardIdentity = videoIdentity(cardVideo || { id: card?.dataset?.videoId, source: card?.dataset?.source });
      const groupedResult = pruneUnavailableGroupedMember(cardVideo, target);
      if (groupedResult.grouped && groupedResult.changed) {
        // Najważniejsza zasada: awaria reprezentanta NIE usuwa całej grupy.
        // Usuwamy tylko konkretny niedostępny element i, gdy mamy lokalnie
        // kolejnego członka, promujemy go na miniaturkę/reprezentanta grupy.
        if (groupedResult.promoted) {
          card.dataset.videoId = String(cardVideo?.id || '');
          card.dataset.source = String(cardVideo?.source || 'archivebate').toLowerCase();
        }
        if (groupedResult.empty) {
          try { card.remove(); } catch (_) {}
          if (card._cardKey && state?.gridCardMap?.delete) state.gridCardMap.delete(card._cardKey);
          return;
        }
        if (typeof card._updateCard === 'function') {
          try { card._updateCard(cardVideo, card._cardIndex); } catch (_) {}
        }
        return;
      }

      if (groupedResult.grouped || cardIdentity?.key !== target.key) return;
      if (cardVideo) quarantinedVideoSnapshots.set(target.key, cardVideo);
      const key = card._cardKey;
      try { card.remove(); } catch (_) {}
      if (key && state?.gridCardMap?.delete) state.gridCardMap.delete(key);
    });

    if (state && Array.isArray(state.videos)) {
      const nextVideos = [];
      for (const video of state.videos) {
        if (!video || typeof video !== 'object') continue;
        const identity = videoIdentity(video);
        const groupedResult = pruneUnavailableGroupedMember(video, target);
        if (groupedResult.grouped) {
          if (groupedResult.empty) continue;
          nextVideos.push(video);
          continue;
        }
        if (identity?.key === target.key) {
          quarantinedVideoSnapshots.set(target.key, video);
          continue;
        }
        nextVideos.push(video);
      }
      state.videos = nextVideos;
      const dom = global.ArchivebateAppContext?.dom || global.dom || {};
      if (dom.statPageVideos) dom.statPageVideos.textContent = String(state.videos.length);
    }
  }

  function markUnavailableVideo(videoId, evidence) {
    const identity = videoIdentity(videoId, evidence?.source);
    if (!identity || !detailsAreUnavailable(evidence)) return false;
    const attemptedAt = Date.now();
    unavailableRegistry.set(identity.key, {
      reason: evidence.availability_reason,
      checked_at: Number(evidence.checked_at) || null,
      attempted_at: attemptedAt,
      expires_at: attemptedAt + UNAVAILABLE_TTL_MS,
      stream_missing_confirmations: evidence.stream_missing_confirmations,
    });
    persistUnavailableRegistry();
    bumpAvailabilityGeneration(identity.key);
    removeUnavailableCardInstances(identity, identity.source);
    try {
      global.dispatchEvent?.(new CustomEvent('archivebate:video-unavailable', {
        detail: { videoId: identity.providerId, source: identity.source, reason: evidence.availability_reason, checkedAt: evidence.checked_at, expiresAt: attemptedAt + UNAVAILABLE_TTL_MS }
      }));
    } catch (_) {}

    // The just-hidden card can be restored or checked again without clearing
    // browser data. The retained snapshot is in memory only; persistent state
    // contains only source identity and the provider evidence timestamps.
    try {
      global.ArchivebateToast?.show?.('Nagranie potwierdzono jako usunięte ze źródła.', 'info', null, [
        { label: 'Pokaż ponownie', onClick: () => restoreUnavailableVideo(identity, false) },
        { label: 'Sprawdź ponownie', onClick: () => restoreUnavailableVideo(identity, true) },
      ]);
    } catch (_) {}
    return true;
  }

  function detailsAreUnavailable(details) {
    return Boolean(
      details &&
      details.availability === 'unavailable' &&
      ['source_page_not_found', 'embed_file_not_found', 'stream_file_not_found'].includes(details.availability_reason) &&
      (details.availability_reason !== 'stream_file_not_found' || details.stream_missing_confirmations === 2) &&
      details.retryable === false &&
      Number(details.checked_at) > 0 &&
      !details.is_private &&
      !details.direct_url &&
      !details.proxy_stream_url
    );
  }

  function restoreUnavailableVideo(videoOrIdentity, checkAgain = false) {
    const identity = videoOrIdentity?.key && videoOrIdentity?.source
      ? videoOrIdentity
      : videoIdentity(videoOrIdentity);
    if (!identity?.key) return false;
    forgetUnavailableVideo(identity, identity.source);
    const video = quarantinedVideoSnapshots.get(identity.key);
    quarantinedVideoSnapshots.delete(identity.key);
    const state = global.ArchivebateAppContext?.state || global.state;
    if (video && state && Array.isArray(state.videos)) {
      if (!state.videos.some(item => videoIdentity(item)?.key === identity.key)) state.videos.push(video);
      const grid = global.ArchivebateVideoGrid;
      if (typeof grid?.replaceView === 'function') grid.replaceView(state.videos);
      else if (typeof global.renderVideoGrid === 'function') global.renderVideoGrid(state.videos);
    } else if (typeof global.ArchivebateVideoViews?.loadHomeVideos === 'function') {
      void global.ArchivebateVideoViews.loadHomeVideos(state?.currentPage || 1, false);
    }
    if (checkAgain) {
      const videoId = requestVideoId(video || identity.providerId, identity);
      videoDetailsCache.delete?.(detailsCacheKey(video || videoId, identity.source));
      void prefetchVideoDetails(videoId, { source: identity.source });
    }
    return true;
  }

  function listUnavailableVideos() {
    return Array.from(unavailableRegistry.entries()).map(([key, entry]) => {
      const separator = key.indexOf(':id:');
      return {
        key,
        source: key.slice(0, separator),
        providerId: key.slice(separator + 4),
        reason: entry.reason,
        checkedAt: entry.checked_at,
        attemptedAt: entry.attempted_at,
        expiresAt: entry.expires_at,
      };
    });
  }

  function waitForRetryGap(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(new Error('aborted'));
      const timer = setTimeout(() => {
        signal?.removeEventListener?.('abort', abort);
        resolve();
      }, ms);
      const abort = () => {
        clearTimeout(timer);
        signal?.removeEventListener?.('abort', abort);
        reject(new Error('aborted'));
      };
      signal?.addEventListener?.('abort', abort, { once: true });
    });
  }

  async function confirmUnavailableVideo(videoId, initialDetails, signal = null) {
    const identity = videoIdentity(videoId, initialDetails?.source);
    const key = identity?.key;
    if (!key || !detailsAreUnavailable(initialDetails) || signal?.aborted) return initialDetails;
    // The backend has already refreshed the URL and confirmed two missing
    // stream responses. Re-resolving metadata can return the same dead URL as
    // "available" and undo that stronger evidence.
    if (initialDetails.availability_reason === 'stream_file_not_found') {
      videoDetailsCache.set(detailsCacheKey(videoId, identity.source), initialDetails);
      markUnavailableVideo(videoId, initialDetails);
      return initialDetails;
    }
    if (unavailableConfirmInflight.has(key)) return unavailableConfirmInflight.get(key);
    const generation = bumpAvailabilityGeneration(key);

    const refresh = () => initialDetails.availability_reason === 'stream_file_not_found'
      ? api.postJSON(`/api/video/availability?id=${encodeURIComponent(requestVideoId(videoId, identity))}&force=true`, {}, { timeoutMs: 20000, signal })
      : typeof api.refreshVideoDetails === 'function'
      ? api.refreshVideoDetails(requestVideoId(videoId, identity), { timeoutMs: 12000, signal })
      : api.postJSON(
          `/api/video/details/refresh?id=${encodeURIComponent(requestVideoId(videoId, identity))}`,
          {},
          { timeoutMs: 12000, signal }
        );
    const request = (async () => {
      try {
        const first = await refresh();
        if (signal?.aborted || availabilityGenerations.get(key) !== generation) return initialDetails;
        if (first && (first.availability === 'available' || first.direct_url || first.proxy_stream_url)) {
          videoDetailsCache.set(detailsCacheKey(videoId, identity.source), first);
          forgetUnavailableVideo(identity, identity.source);
          return first;
        }
        if (!detailsAreUnavailable(first)) return first || initialDetails;

        // Two provider reads must be independent in time. A network failure or
        // an unverified response on either attempt never confirms removal.
        await waitForRetryGap(UNAVAILABLE_CONFIRM_GAP_MS, signal);
        if (availabilityGenerations.get(key) !== generation) return initialDetails;
        const second = await refresh();
        if (signal?.aborted || availabilityGenerations.get(key) !== generation) return initialDetails;
        const firstChecked = Number(first.checked_at) || 0;
        const secondChecked = Number(second?.checked_at) || 0;
        if (detailsAreUnavailable(second) && secondChecked > firstChecked) {
          videoDetailsCache.set(detailsCacheKey(videoId, identity.source), second);
          markUnavailableVideo(videoId, second);
          return second;
        }
        if (second && (second.availability === 'available' || second.direct_url || second.proxy_stream_url)) {
          videoDetailsCache.set(detailsCacheKey(videoId, identity.source), second);
          forgetUnavailableVideo(identity, identity.source);
        }
        return second || first || initialDetails;
      } catch (_) {
        return initialDetails;
      }
    })().finally(() => {
      unavailableConfirmInflight.delete(key);
    });

    unavailableConfirmInflight.set(key, request);
    return request;
  }

  async function checkPlaybackFailure(video, signal = null) {
    const identity = videoIdentity(video);
    if (!identity || signal?.aborted) return null;
    try {
      // Media errors do not expose HTTP status. Probe the actual stream via
      // the backend, rather than trusting cached metadata with a dead URL.
      const details = await api.postJSON(
        `/api/video/availability?id=${encodeURIComponent(requestVideoId(video, identity))}&force=true`,
        {}, { timeoutMs: 30000, signal }
      );
      if (signal?.aborted) return null;
      if (detailsAreUnavailable(details)) return confirmUnavailableVideo(video, details, signal);
      return details;
    } catch (_) {
      return null;
    }
  }

  // Ładuj obrazy dopiero, gdy karta zbliża się do viewportu. Wcześniej
  // armLazyThumbnail() ustawiał src od razu wszystkim ~280 kartom, co tworzyło
  // duży burst requestów do /api/thumb i opóźniało pierwszy użyteczny ekran.
  const lazyThumbObserver = (typeof window !== 'undefined' && 'IntersectionObserver' in window)
    ? new IntersectionObserver((entries, observer) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const img = entry.target;
          if (img.dataset.src && !String(img.getAttribute('src') || '').trim()) {
            // The observer already bounds preloading. Native lazy loading can
            // otherwise defer an image a second time after it was promoted.
            img.loading = 'eager';
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

  function consumeVideoDetails(entry, signal) {
    if (signal?.aborted) return Promise.resolve(null);
    entry.consumers += 1;
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener('abort', onAbort);
        entry.consumers -= 1;
        resolve(value);
      };
      const onAbort = () => {
        finish(null);
        if (entry.consumers === 0) {
          entry.controller.abort();
          if (videoDetailsInflight.get(entry.key) === entry) videoDetailsInflight.delete(entry.key);
        }
      };
      signal?.addEventListener('abort', onAbort, { once: true });
      entry.promise.then(finish, () => finish(null));
    });
  }

  function prefetchVideoDetails(videoId, options = {}) {
    if (videoId?._mediaScope === 'local_catalog' && !options.userInitiated) return Promise.resolve(null);
    if (!videoId) return Promise.resolve(null);
    const sourceHint = options?.source || '';
    const identity = videoIdentity(videoId, sourceHint);
    const cacheKey = detailsCacheKey(videoId, sourceHint) || String(videoId);
    const requestId = requestVideoId(videoId, identity);
    if (isKnownUnavailableVideo(identity, identity?.source)) {
      removeUnavailableCardInstances(identity, identity?.source);
      return Promise.resolve({ id: requestId, availability: 'unavailable', locally_quarantined: true });
    }

    const cached = videoDetailsCache.get(cacheKey);
    if (cached) {
      if (detailsAreUnavailable(cached)) void confirmUnavailableVideo(requestId, cached, null);
      else if (cached.availability === 'available' || cached.direct_url || cached.proxy_stream_url) forgetUnavailableVideo(identity, identity?.source);
      return Promise.resolve(cached);
    }

    const signal = (typeof options === 'object' && options?.signal) ? options.signal : null;
    if (signal?.aborted) return Promise.resolve(null);

    const existing = videoDetailsInflight.get(cacheKey);
    if (existing) return consumeVideoDetails(existing, signal);
    if (videoDetailsInflight.size >= MAX_CONCURRENT_PREFETCH) return Promise.resolve(null);

    const controller = new AbortController();
    const entry = { key: cacheKey, controller, consumers: 0, promise: null };
    entry.promise = api.getJSON(`/api/video/details?id=${encodeURIComponent(requestId)}`, { timeoutMs: 9000, signal: controller.signal })
      .then(details => {
        if (isKnownUnavailableVideo(identity, identity?.source)) {
          return videoDetailsCache.get(cacheKey) || details;
        }
        if (details && !controller.signal.aborted) {
          videoDetailsCache.set(cacheKey, details);
          if (detailsAreUnavailable(details)) {
            void confirmUnavailableVideo(requestId, details, controller.signal);
          } else if (details.availability === 'available' || details.direct_url || details.proxy_stream_url) {
            forgetUnavailableVideo(identity, identity?.source);
          }
        }
        return details;
      })
      .catch(() => null)
      .finally(() => {
        if (videoDetailsInflight.get(cacheKey) === entry) videoDetailsInflight.delete(cacheKey);
      });

    videoDetailsInflight.set(cacheKey, entry);
    return consumeVideoDetails(entry, signal);
  }

  function waitForPrefetchSlot(signal = null) {
    if (signal?.aborted) return Promise.resolve(false);
    if (videoDetailsInflight.size < MAX_CONCURRENT_PREFETCH) return Promise.resolve(true);
    return Promise.race(Array.from(videoDetailsInflight.values(), entry => entry.promise.then(() => true, () => true)))
      .then(() => !signal?.aborted && videoDetailsInflight.size < MAX_CONCURRENT_PREFETCH);
  }

  function hasPrefetchCapacity() {
    return videoDetailsInflight.size < MAX_CONCURRENT_PREFETCH;
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
    if (v._mediaScope === 'local_catalog') {
      let poster = v.poster || v.thumbnail || '';
      if (!poster) {
        try { poster = new URL(v.poster_proxy || v.thumbnail_proxy, global.location?.href).searchParams.get('url') || ''; } catch (_) {}
      }
      return poster ? `/api/thumb?url=${encodeURIComponent(poster)}&cache_only=true` : '';
    }
    return v.poster_proxy || v.thumbnail_proxy || (v.poster ? `/api/thumb?url=${encodeURIComponent(v.poster)}` : '');
  }

  // Checking every catalog entry before first paint would serialize browsing.
  // Check only visible cards, two at a time, and suspend during primary playback.
  const availabilityQueue = new Map();
  const availabilityChecked = new Map();
  let availabilityActive = 0;
  let availabilityTimer = null;
  function drainAvailability() {
    availabilityTimer = null;
    const players = ['modalVideo', 'mainPlayer'].map(id => global.document?.getElementById?.(id)).filter(Boolean);
    if (global.document?.hidden || players.some(video => !video.paused && !video.ended)) {
      if (availabilityQueue.size) availabilityTimer = setTimeout(drainAvailability, 1500);
      return;
    }
    while (availabilityActive < 2 && availabilityQueue.size) {
      const [key, work] = availabilityQueue.entries().next().value;
      availabilityQueue.delete(key);
      if (work.signal?.aborted || work.card._videoData?._mediaScope === 'local_catalog' || work.card.isConnected === false || !work.visible()) continue;
      availabilityActive += 1;
      const id = requestVideoId(work.card._videoData, work.identity);
      api.postJSON(`/api/video/availability?id=${encodeURIComponent(id)}`, {}, { timeoutMs: 20000, signal: work.signal })
        .then(async details => {
          if (work.signal?.aborted || videoIdentity(work.card._videoData)?.key !== key) return;
          availabilityChecked.set(key, Date.now());
          while (availabilityChecked.size > 600) availabilityChecked.delete(availabilityChecked.keys().next().value);
          if (detailsAreUnavailable(details)) await confirmUnavailableVideo(id, details, work.signal);
          else if (details?.availability === 'available') setVideoDetails(id, details, { source: work.identity.source });
          const img = work.card.querySelector?.('.thumbnail-img');
          const recovered = details?.thumbnail || details?.poster;
          if (img && recovered && img.dataset.retried && recovered !== work.card._videoData.poster) {
            work.card._videoData.poster = recovered;
            work.card._videoData.poster_proxy = `/api/thumb?url=${encodeURIComponent(recovered)}`;
            work.card._updateCard?.(work.card._videoData, work.card._cardIndex);
          }
        }).catch(() => {}).finally(() => { availabilityActive -= 1; drainAvailability(); });
    }
  }
  const availabilityObserver = typeof global.IntersectionObserver === 'function'
    ? new global.IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const card = entry.target;
        const video = card._videoData;
        const identity = videoIdentity(video);
        if (!identity || video?._mediaScope === 'local_catalog' || isKnownUnavailableVideo(video)) continue;
        if (Date.now() - (availabilityChecked.get(identity.key) || 0) < 5 * 60 * 1000) continue;
        const state = global.ArchivebateAppContext?.state || global.state;
        const visible = () => {
          const rect = card.getBoundingClientRect?.();
          return !rect || (rect.bottom > 0 && rect.top < global.innerHeight);
        };
        availabilityQueue.set(identity.key, { card, identity, signal: state?.viewController?.signal, visible });
        if (!availabilityTimer) availabilityTimer = setTimeout(drainAvailability, 600);
      }
    }, { threshold: 0.01 }) : null;

  function observeAvailability(card) { if (card?._videoData?._mediaScope !== 'local_catalog') availabilityObserver?.observe(card); }
  function cancelAvailabilityChecks() {
    availabilityQueue.clear();
    clearTimeout(availabilityTimer);
    availabilityTimer = null;
    availabilityObserver?.disconnect();
  }

  let thumbnailWarmupController = null;
  let thumbnailWarmupTimer = null;
  function cancelThumbnailWarmup() {
    if (thumbnailWarmupTimer !== null) clearTimeout(thumbnailWarmupTimer);
    thumbnailWarmupTimer = null;
    thumbnailWarmupController?.abort?.();
    thumbnailWarmupController = null;
  }

  function scheduleThumbnailWarmup(videos, start = 12, count = 60) {
    cancelThumbnailWarmup();
    if (videos?.some?.(video => video?._mediaScope === 'local_catalog')) return;
    if (!Array.isArray(videos) || videos.length <= start) return;
    const controller = thumbnailWarmupController = new AbortController();

    // Warmup ma pomagać pierwszemu ekranowi, a nie konkurować z nim. Ograniczamy
    // go do 12 miniatur; resztę przejmuje IntersectionObserver podczas scrolla.
    const warmCount = Math.min(Math.max(0, count), 12);
    if (warmCount === 0) {
      cancelThumbnailWarmup();
      return;
    }
    const urls = videos.slice(start, start + warmCount).map(thumbnailUrlForVideo).filter(Boolean);
    const generationController = controller;
    const startWarmup = () => {
      thumbnailWarmupTimer = null;
      if (generationController.signal.aborted || thumbnailWarmupController !== generationController) return;
      perf.prefetchUrls(urls, { concurrency: 3, signal: generationController.signal }).catch(() => {});
    };
    if (typeof perf.idle === 'function') {
      const scheduled = perf.idle(startWarmup, 900);
      if (typeof scheduled === 'number') thumbnailWarmupTimer = scheduled;
    } else {
      thumbnailWarmupTimer = setTimeout(startWarmup, 900);
    }
  }

  function hasVideoDetails(videoId, options = {}) {
    return videoDetailsCache.has(detailsCacheKey(videoId, options?.source || '') || String(videoId));
  }

  function getVideoDetails(videoId, options = {}) {
    return videoDetailsCache.get(detailsCacheKey(videoId, options?.source || '') || String(videoId));
  }

  function setVideoDetails(videoId, details, options = {}) {
    const source = options?.source || details?.source || '';
    // A resolved URL is metadata, not proof that the file still exists.
    // Late player/cache writes must not restore a confirmed removed card.
    if (isKnownUnavailableVideo(videoId, source) && !detailsAreUnavailable(details)) return;
    videoDetailsCache.set(detailsCacheKey(videoId, source) || String(videoId), details);
  }

  function isDetailsInflight(videoId, options = {}) {
    return videoDetailsInflight.has(detailsCacheKey(videoId, options?.source || '') || String(videoId));
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
    cancelThumbnailWarmup,
    observeAvailability,
    cancelAvailabilityChecks,
    hasVideoDetails,
    getVideoDetails,
    setVideoDetails,
    isDetailsInflight,
    waitForPrefetchSlot,
    hasPrefetchCapacity,
    prefetchCandidateStreamChunk,
    isKnownUnavailableVideo,
    listUnavailableVideos,
    restoreUnavailableVideo,
    markUnavailableVideo,
    reconcileKnownUnavailableGroup,
    forgetUnavailableVideo,
    videoIdentity,
    confirmUnavailableVideo,
    checkPlaybackFailure,
    detailsAreUnavailable
  };

  global.addEventListener?.('storage', event => {
    if (event.key !== UNAVAILABLE_STORAGE_KEY) return;
    const fresh = loadUnavailableRegistry();
    for (const key of new Set([...unavailableRegistry.keys(), ...fresh.keys()])) bumpAvailabilityGeneration(key);
    unavailableRegistry.clear();
    for (const [key, entry] of fresh) {
      unavailableRegistry.set(key, entry);
      const [source, providerId] = key.split(':id:');
      removeUnavailableCardInstances({ source, providerId, key }, source);
    }
  });

  global.ArchivebateVideoPrefetch = ArchivebateVideoPrefetch;

  if (unavailableRegistry.size && global.ArchivebateToast?.show) {
    const first = listUnavailableVideos()[0];
    const identity = first && { source: first.source, providerId: first.providerId, key: first.key };
    global.setTimeout?.(() => global.ArchivebateToast.show(
      `Ukryto ${unavailableRegistry.size} potwierdzonych nagrań.`,
      'info',
      null,
      [
        { label: 'Pokaż ponownie', onClick: () => restoreUnavailableVideo(identity, false) },
        { label: 'Sprawdź ponownie', onClick: () => restoreUnavailableVideo(identity, true) },
      ]
    ), 0);
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoPrefetch;
  }
})(typeof window !== 'undefined' ? window : globalThis);
