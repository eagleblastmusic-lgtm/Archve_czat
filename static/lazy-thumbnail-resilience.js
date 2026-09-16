(() => {
  'use strict';

  // Safety net for the card thumbnail IntersectionObserver. A same-page refresh
  // can temporarily disconnect the shared observer while keeping existing cards
  // in the DOM. Those cards still carry data-src, so re-promote only thumbnails
  // that are near the viewport instead of eagerly loading the whole page.
  const PRELOAD_MARGIN_PX = 900;
  const stats = {
    scans: 0,
    promoted: 0,
    mutationScans: 0,
    scrollScans: 0,
  };

  let rafId = 0;
  let mutationObserver = null;

  function isNearViewport(img) {
    if (!img?.getBoundingClientRect) return false;
    const rect = img.getBoundingClientRect();
    const viewportHeight = globalThis.innerHeight || document.documentElement?.clientHeight || 0;
    const viewportWidth = globalThis.innerWidth || document.documentElement?.clientWidth || 0;
    return (
      rect.bottom >= -PRELOAD_MARGIN_PX &&
      rect.top <= viewportHeight + PRELOAD_MARGIN_PX &&
      rect.right >= -PRELOAD_MARGIN_PX &&
      rect.left <= viewportWidth + PRELOAD_MARGIN_PX
    );
  }

  function promote(img) {
    const src = String(img?.dataset?.src || '').trim();
    if (!src) return false;

    // A blank src attribute is equivalent to not having a usable source.
    const activeSrc = String(img.getAttribute?.('src') || '').trim();
    if (activeSrc) return false;

    img.src = src;
    delete img.dataset.src;
    stats.promoted += 1;
    return true;
  }

  function scanPendingThumbnails(root = document) {
    stats.scans += 1;
    const pending = root?.querySelectorAll?.('.thumbnail-img[data-src]') || [];
    const sharedObserver = globalThis.lazyThumbObserver;

    for (const img of pending) {
      // Re-arm the canonical observer in case a view refresh disconnected it.
      try { sharedObserver?.observe?.(img); } catch (_) {}

      // Do not wait for a future IntersectionObserver callback when the image is
      // already close to the viewport; promote it now so scrolling feels instant.
      if (isNearViewport(img)) promote(img);
    }
  }

  function scheduleScan(reason = 'generic') {
    if (reason === 'scroll') stats.scrollScans += 1;
    if (reason === 'mutation') stats.mutationScans += 1;
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = 0;
      scanPendingThumbnails(document);
    });
  }

  function mutationContainsPendingThumbnail(mutation) {
    for (const node of mutation.addedNodes || []) {
      if (!(node instanceof Element)) continue;
      if (node.matches?.('.thumbnail-img[data-src]')) return true;
      if (node.querySelector?.('.thumbnail-img[data-src]')) return true;
    }
    return false;
  }

  function init() {
    scanPendingThumbnails(document);

    globalThis.addEventListener?.('scroll', () => scheduleScan('scroll'), {
      passive: true,
      capture: true,
    });
    globalThis.addEventListener?.('resize', () => scheduleScan('resize'), {
      passive: true,
    });

    if (typeof MutationObserver !== 'undefined' && document.documentElement) {
      mutationObserver = new MutationObserver((mutations) => {
        if (mutations.some(mutationContainsPendingThumbnail)) {
          scheduleScan('mutation');
        }
      });
      mutationObserver.observe(document.documentElement, {
        childList: true,
        subtree: true,
      });
    }
  }

  globalThis.ArchivebateLazyThumbnailResilience = {
    scan: () => scanPendingThumbnails(document),
    stats: () => ({
      ...stats,
      pending: document.querySelectorAll?.('.thumbnail-img[data-src]')?.length || 0,
      observer_available: Boolean(globalThis.lazyThumbObserver),
      mutation_observer_active: Boolean(mutationObserver),
    }),
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
