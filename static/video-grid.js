/**
 * Archivebate Video Browser - Video Grid Core Module
 * Odpowiada za renderowanie siatki wideo, zero-flash reconcilePage (Pakiet A),
 * replaceView, deduplikację kluczy, stabilne klucze getVideoKey oraz dynamiczny append.
 */

(function (global) {
  'use strict';

  const state = new Proxy({}, {
    get(target, prop) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.state && prop in ctx.state) return ctx.state[prop];
      if (global.state && prop in global.state) return global.state[prop];
      return target[prop];
    },
    set(target, prop, value) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.state) { ctx.state[prop] = value; return true; }
      if (global.state) { global.state[prop] = value; return true; }
      target[prop] = value;
      return true;
    }
  });

  const dom = new Proxy({}, {
    get(target, prop) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.dom && prop in ctx.dom) return ctx.dom[prop];
      if (global.dom && prop in global.dom) return global.dom[prop];
      return target[prop];
    },
    set(target, prop, value) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.dom) { ctx.dom[prop] = value; return true; }
      if (global.dom) { global.dom[prop] = value; return true; }
      target[prop] = value;
      return true;
    }
  });

  let createVideoCardFn = (v, idx) => {
    if (typeof global.createVideoCard === 'function') {
      return global.createVideoCard(v, idx);
    }
    if (global.ArchivebateVideoCard && typeof global.ArchivebateVideoCard.createVideoCard === 'function') {
      return global.ArchivebateVideoCard.createVideoCard(v, idx);
    }
    return null;
  };

  let updateCheckpointUIFn = () => {
    if (typeof global.updateCheckpointUI === 'function') global.updateCheckpointUI();
    else if (global.ArchivebateCheckpoints && typeof global.ArchivebateCheckpoints.updateUI === 'function') {
      global.ArchivebateCheckpoints.updateUI();
    }
  };

  let checkAndHighlightCheckpointFn = () => {
    if (typeof global.checkAndHighlightCheckpoint === 'function') global.checkAndHighlightCheckpoint();
    else if (global.ArchivebateCheckpoints && typeof global.ArchivebateCheckpoints.checkAndHighlight === 'function') {
      global.ArchivebateCheckpoints.checkAndHighlight();
    }
  };

  function getGroupVideosByAuthor() {
    if (global.ArchivebateFilters && typeof global.ArchivebateFilters.groupVideosByAuthor === 'function') {
      return global.ArchivebateFilters.groupVideosByAuthor;
    }
    if (typeof global.groupVideosByAuthor === 'function') {
      return global.groupVideosByAuthor;
    }
    return x => x;
  }

  function init(deps = {}) {
    if (typeof deps.createVideoCard === 'function') {
      createVideoCardFn = deps.createVideoCard;
    }
    if (typeof deps.updateCheckpointUI === 'function') {
      updateCheckpointUIFn = deps.updateCheckpointUI;
    }
    if (typeof deps.checkAndHighlightCheckpoint === 'function') {
      checkAndHighlightCheckpointFn = deps.checkAndHighlightCheckpoint;
    }
  }

  // ELIMINACJA DUPLIKATÓW
  function deduplicateVideos(videos) {
    const seen = new Map();
    const result = [];
    for (const video of videos || []) {
      if (!video || typeof video !== 'object') continue;
      let id = String(video.id || '').trim();
      const source = id.startsWith('cw_') || video.source === 'camwhores' ? 'camwhores' : (video.source || 'archivebate');
      if (source === 'camwhores') id = id.replace(/^cw_/, '');
      const url = String(video.url || '').trim().split('#')[0].replace(/\/$/, '');
      const key = id ? `${source}:id:${id}` : url ? `${source}:url:${url}` : null;
      if (key && seen.has(key)) {
        const old = seen.get(key);
        for (const [field, value] of Object.entries(video)) if (!old[field] && value) old[field] = value;
        continue;
      }
      const item = {...video};
      result.push(item);
      if (key) seen.set(key, item);
    }
    return result;
  }

  // KLUCZ IDENTYFIKACYJNY DLA KAFELKA LUB GRUPY (STABILNY MIĘDZY REWIZJAMI)
  function getVideoKey(v) {
    if (!v || typeof v !== 'object') return null;
    const isGrouped = Boolean(
      (typeof state !== 'undefined' && state?.groupByAuthor) ||
      v.is_grouped || v._isGrouped ||
      (v.group_count && v.group_count > 1) ||
      (v._groupCount && v._groupCount > 1) ||
      (Array.isArray(v.grouped_videos) && v.grouped_videos.length > 1) ||
      (Array.isArray(v._groupedVideos) && v._groupedVideos.length > 1)
    );
    if (isGrouped) {
      const rawU = String(v.username || '').trim();
      const normU = rawU.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (normU && normU !== 'model') {
        return `group:author:${normU}`;
      }
    }
    let id = String(v.id !== undefined && v.id !== null ? v.id : '').trim();
    const source = (id.startsWith('cw_') || v.source === 'camwhores' || (v.platform && String(v.platform).toLowerCase().includes('camwhores')))
      ? 'camwhores'
      : (v.source || 'archivebate');
    if (source === 'camwhores') id = id.replace(/^cw_/, '');
    if (id) return `${source}:id:${id}`;
    const url = String(v.url || '').trim().split('#')[0].replace(/\/$/, '');
    if (url) return `${source}:url:${url}`;
    return null;
  }

  // CAŁKOWITA WYMIANA WIDOKU (ZMIANA STRONY, ZAKŁADKI, FILTRÓW)
  function replaceView(videos, options = {}) {
    if (!dom.videoGrid) return;

    state.gridController?.abort();
    state.gridController = typeof AbortController !== 'undefined' ? new AbortController() : null;

    dom.videoGrid.querySelectorAll?.('video')?.forEach?.(video => {
      try {
        video.pause?.();
        video.removeAttribute?.('src');
        video.load?.();
      } catch (_) {}
    });

    const generation = state.gridGeneration = (state.gridGeneration || 0) + 1;
    if (typeof global.lazyThumbObserver !== 'undefined') global.lazyThumbObserver?.disconnect?.();

    dom.videoGrid.innerHTML = '';
    state.gridCardMap = new Map();
    state.lastAppliedFeedRevision = -1;
    state.lastAppliedFeedUpdatedAt = 0;
    state.lastAppliedFeedVideoCount = -1;
    state.lastAppliedVideosCount = 0;

    if (!videos || videos.length === 0) return;

    videos = deduplicateVideos(videos);
    const shouldGroup = state.groupByAuthor && state.mode === 'search';
    const groupFn = getGroupVideosByAuthor();
    const displayVideos = shouldGroup ? groupFn(videos) : videos;

    const INITIAL_BATCH = 32;
    const CHUNK_SIZE = 32;
    const initial = displayVideos.slice(0, INITIAL_BATCH);
    const firstFragment = document.createDocumentFragment();

    initial.forEach((v, idx) => {
      const card = createVideoCardFn(v, idx);
      const key = getVideoKey(v);
      if (key && state.gridCardMap) state.gridCardMap.set(key, card);
      firstFragment.appendChild(card);
    });
    dom.videoGrid.appendChild(firstFragment);
    updateCheckpointUIFn();
    checkAndHighlightCheckpointFn();

    let cursor = INITIAL_BATCH;
    const appendNextChunk = () => {
      if (generation !== state.gridGeneration || cursor >= displayVideos.length) return;
      const end = Math.min(cursor + CHUNK_SIZE, displayVideos.length);
      const fragment = document.createDocumentFragment();
      for (let i = cursor; i < end; i += 1) {
        const v = displayVideos[i];
        const card = createVideoCardFn(v, i);
        const key = getVideoKey(v);
        if (key && state.gridCardMap) state.gridCardMap.set(key, card);
        fragment.appendChild(card);
      }
      dom.videoGrid.appendChild(fragment);
      cursor = end;

      if (cursor < displayVideos.length) {
        if ('requestIdleCallback' in global) {
          global.requestIdleCallback(appendNextChunk, { timeout: 250 });
        } else {
          setTimeout(appendNextChunk, 16);
        }
      } else {
        updateCheckpointUIFn();
        checkAndHighlightCheckpointFn();
      }
    };

    if (cursor < displayVideos.length) {
      if ('requestIdleCallback' in global) {
        global.requestIdleCallback(appendNextChunk, { timeout: 200 });
      } else {
        setTimeout(appendNextChunk, 16);
      }
    }
  }

  // RECONCILIACJA PORCJI BEZ MIGOTANIA (ZERO INNERHTML='')
  function reconcilePage(videos, options = {}) {
    if (!dom.videoGrid) return;
    if (!videos || !Array.isArray(videos)) return;

    if (videos.length === 0 && state.gridCardMap && state.gridCardMap.size > 0) {
      return;
    }

    videos = deduplicateVideos(videos);
    const shouldGroup = state.groupByAuthor && state.mode === 'search';
    const groupFn = getGroupVideosByAuthor();
    const displayVideos = shouldGroup ? groupFn(videos) : videos;

    if (displayVideos.length === 0 && (!state.gridCardMap || state.gridCardMap.size === 0)) {
      if (options && options.complete) {
        dom.videoGrid.innerHTML = '';
      }
      return;
    }

    if (!state.gridCardMap) {
      state.gridCardMap = new Map();
    }

    if (displayVideos.length > 0) {
      const skeletons = dom.videoGrid.querySelectorAll?.('.skeleton-card');
      if (skeletons && skeletons.length > 0) {
        skeletons.forEach(sk => sk.remove?.());
      }
    }

    const incomingKeys = new Set();
    const fragment = document.createDocumentFragment();
    let appendedCount = 0;

    displayVideos.forEach((v, idx) => {
      const key = getVideoKey(v);
      if (!key) return;
      incomingKeys.add(key);

      if (state.gridCardMap.has(key)) {
        const existingCard = state.gridCardMap.get(key);
        if (existingCard && typeof existingCard._updateCard === 'function') {
          existingCard._updateCard(v, idx);
        } else if (existingCard && typeof existingCard === 'object') {
          existingCard._videoData = Object.assign(existingCard._videoData || {}, v);
        }
      } else {
        const card = createVideoCardFn(v, idx);
        if (card && typeof card === 'object') {
          card._cardKey = key;
        }
        state.gridCardMap.set(key, card);
        fragment.appendChild(card);
        appendedCount++;
      }
    });

    if (options && (options.complete || options.allowRemoval)) {
      for (const [oldKey, oldCard] of state.gridCardMap.entries()) {
        if (!incomingKeys.has(oldKey)) {
          if (oldCard && typeof oldCard.remove === 'function') {
            oldCard.remove();
          }
          state.gridCardMap.delete(oldKey);
        }
      }
    }

    if (appendedCount > 0 && fragment.childNodes && fragment.childNodes.length > 0) {
      dom.videoGrid.appendChild(fragment);
    } else if (appendedCount > 0 && fragment.children && fragment.children.length > 0) {
      dom.videoGrid.appendChild(fragment);
    }

    updateCheckpointUIFn();
    checkAndHighlightCheckpointFn();
  }

  // RENDEROWANIE KAFELKÓW: ZGODNOŚĆ Z WYWOŁANIAMI
  function renderVideoGrid(videos, options = {}) {
    if (options && options.reconcile) {
      return reconcilePage(videos, options);
    }
    return replaceView(videos, options);
  }

  // STOPNIOWE DOKŁADANIE KAFELKÓW W CZASIE RZECZYWISTYM
  function appendVideoBatch(videos) {
    if (!videos || videos.length === 0 || !dom.videoGrid) return;
    const existingDomIds = new Set(
      Array.from(dom.videoGrid.querySelectorAll('.video-card, .skeleton-card'))
        .map(el => el.dataset?.videoId || el.getAttribute?.('data-video-id'))
        .filter(Boolean)
    );

    const fragment = document.createDocumentFragment();
    let currentCount = dom.videoGrid.children ? dom.videoGrid.children.length : 0;

    videos.forEach((v) => {
      if (!v || !v.id) return;
      const vid = String(v.id);
      if (existingDomIds.has(vid)) return;
      existingDomIds.add(vid);

      const card = createVideoCardFn(v, currentCount++);
      if (card && card.classList) card.classList.add('stream-appear');
      const key = getVideoKey(v);
      if (key && state.gridCardMap) state.gridCardMap.set(key, card);
      fragment.appendChild(card);
    });

    if (fragment.childNodes && fragment.childNodes.length > 0) {
      dom.videoGrid.appendChild(fragment);
    } else if (fragment.children && fragment.children.length > 0) {
      dom.videoGrid.appendChild(fragment);
    }

    updateCheckpointUIFn();
    checkAndHighlightCheckpointFn();
  }

  const ArchivebateVideoGrid = {
    init,
    deduplicate: deduplicateVideos,
    deduplicateVideos,
    render: renderVideoGrid,
    renderVideoGrid,
    replaceView,
    reconcilePage,
    getVideoKey,
    append: appendVideoBatch,
    appendVideoBatch
  };

  global.ArchivebateVideoGrid = ArchivebateVideoGrid;
  if (!global.renderVideoGrid) global.renderVideoGrid = renderVideoGrid;
  if (!global.reconcilePage) global.reconcilePage = reconcilePage;
  if (!global.replaceView) global.replaceView = replaceView;
  if (!global.getVideoKey) global.getVideoKey = getVideoKey;
  if (!global.deduplicateVideos) global.deduplicateVideos = deduplicateVideos;
  if (!global.appendVideoBatch) global.appendVideoBatch = appendVideoBatch;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoGrid;
  }
})(typeof window !== 'undefined' ? window : globalThis);
