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

  // Miniatury poza viewportem nie powinny konkurować o łącze z tymi, które użytkownik widzi.
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
      }, { rootMargin: '1000px 0px', threshold: 0.01 })
    : null;

  function armLazyThumbnail(img) {
    if (!img || !img.dataset.src) return;
    if (lazyThumbObserver) {
      lazyThumbObserver.observe(img);
    } else {
      img.src = img.dataset.src;
      delete img.dataset.src;
    }
  }

  const videoDetailsInflight = new Map();
  const MAX_CONCURRENT_PREFETCH = 2;

  function prefetchVideoDetails(videoId, options = {}) {
    if (!videoId) return Promise.resolve(null);
    const cached = videoDetailsCache.get(videoId);
    if (cached) return Promise.resolve(cached);

    const signal = (typeof options === 'object' && options?.signal) ? options.signal : null;
    if (signal?.aborted) return Promise.resolve(null);

    if (videoDetailsInflight.has(videoId)) return videoDetailsInflight.get(videoId);
    if (videoDetailsInflight.size >= MAX_CONCURRENT_PREFETCH) return Promise.resolve(null);

    const promise = api.getJSON(`/api/video/details?id=${encodeURIComponent(videoId)}`, { timeoutMs: 9000, signal })
      .then(details => {
        if (details && !signal?.aborted) {
          videoDetailsCache.set(videoId, details);
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
    const urls = videos.slice(start, start + count).map(thumbnailUrlForVideo).filter(Boolean);
    perf.idle(() => {
      perf.prefetchUrls(urls, { concurrency: 4, signal: thumbnailWarmupController.signal }).catch(() => {});
    }, 700);
  }

  function hasVideoDetails(videoId) {
    return videoDetailsCache.has(videoId);
  }

  function getVideoDetails(videoId) {
    return videoDetailsCache.get(videoId);
  }

  function setVideoDetails(videoId, details) {
    videoDetailsCache.set(videoId, details);
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
    prefetchCandidateStreamChunk
  };

  global.ArchivebateVideoPrefetch = ArchivebateVideoPrefetch;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoPrefetch;
  }
})(typeof window !== 'undefined' ? window : globalThis);
