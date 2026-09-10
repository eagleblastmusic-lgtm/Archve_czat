/**
 * Archivebate Video Browser - Frontend Bootstrap & Modular Orchestrator
 */

// Kontekst aplikacji (stan oraz elementy DOM)
const { state, dom } = (typeof window !== 'undefined' && window.ArchivebateAppContext)
  ? window.ArchivebateAppContext
  : (typeof ArchivebateAppContext !== 'undefined' ? ArchivebateAppContext : { state: {}, dom: {} });

function isFavoriteAuthor(username) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const fav = g.ArchivebateFavorites || (typeof require === 'function' ? (require('./static/favorites.js') || g.ArchivebateFavorites) : null);
  return fav && typeof fav.isFavoriteAuthor === 'function' ? fav.isFavoriteAuthor(username) : false;
}

// Inicjalizacja
document.addEventListener('DOMContentLoaded', () => {
  if (window.ArchivebateFavorites && typeof window.ArchivebateFavorites.init === 'function') {
    window.ArchivebateFavorites.init({
      showToast
    });
  }
  if (window.ArchivebateBlockedModels && typeof window.ArchivebateBlockedModels.init === 'function') {
    window.ArchivebateBlockedModels.init({
      showToast,
      closeModal,
      updateHomeStats,
      deduplicateVideos,
      renderVideoGrid
    });
  }
  if (window.ArchivebateAccount && typeof window.ArchivebateAccount.init === 'function') {
    window.ArchivebateAccount.init({
      showToast,
      setActiveNavTab,
      loadFavorites,
      loadHistory,
      loadFollowing,
      updateAllAuthorNameColors,
      updateBlockedModelsCount
    });
    window.ArchivebateAccount.initUserStatus();
  }
  initTags();
  const filtersModule = (typeof window !== 'undefined' && window.ArchivebateFilters)
    ? window.ArchivebateFilters
    : (typeof ArchivebateFilters !== 'undefined' ? ArchivebateFilters : null);
  if (filtersModule && typeof filtersModule.init === 'function') {
    filtersModule.init({
      showToast,
      loadHomeVideos,
      performSearch
    });
  }
  initProfileScanner();
  updateHomeStats();
  if (window.ArchivebateCheckpoints && typeof window.ArchivebateCheckpoints.init === 'function') {
    window.ArchivebateCheckpoints.init({
      showToast,
      setActiveNavTab,
      performSearch,
      loadModelVideos,
      loadFavorites,
      loadHistory,
      loadFollowing,
      loadHomeVideos
    });
  }
  updateCheckpointUI();
  if (window.ArchivebatePagination && typeof window.ArchivebatePagination.init === 'function') {
    window.ArchivebatePagination.init({
      loadHomeVideos,
      performSearch,
      loadModelVideos,
      loadFavorites,
      loadHistory,
      loadFollowing
    });
  }
  if (window.ArchivebateVideoGrid && typeof window.ArchivebateVideoGrid.init === 'function') {
    window.ArchivebateVideoGrid.init({
      createVideoCard,
      updateCheckpointUI,
      checkAndHighlightCheckpoint
    });
  }
  if (window.ArchivebateVideoViews && typeof window.ArchivebateVideoViews.init === 'function') {
    window.ArchivebateVideoViews.init({
      showToast,
      setActiveNavTab,
      updateHomeStats,
      renderPagination,
      renderVideoGrid,
      deduplicateVideos,
      scheduleThumbnailWarmup,
      thumbnailUrlForVideo
    });
  }
  if (window.ArchivebateSearchResults && typeof window.ArchivebateSearchResults.init === 'function') {
    window.ArchivebateSearchResults.init({
      showToast,
      showSkeletons,
      renderVideoGrid,
      appendVideoBatch,
      scheduleThumbnailWarmup,
      renderPagination,
      loadModelVideos
    });
  }
  if (window.ArchivebateVideoModal && typeof window.ArchivebateVideoModal.init === 'function') {
    window.ArchivebateVideoModal.init({
      showToast,
      isFavoriteAuthor,
      blockModel,
      loadModelVideos,
      performSearch,
      toggleFavoriteVideo,
      updateModalFavButton,
      changePage,
      loadVideos: loadHomeVideos
    });
  }
  if (window.ArchivebateModalPlayerControls && typeof window.ArchivebateModalPlayerControls.init === 'function') {
    window.ArchivebateModalPlayerControls.init({
      playNextVideo,
      playPrevVideo
    });
  }
  if (window.ArchivebateAppEvents && typeof window.ArchivebateAppEvents.init === 'function') {
    window.ArchivebateAppEvents.init({
      toggleFavoriteVideo,
      setCheckpoint,
      navigateToCheckpoint,
      performSearch,
      loadModelVideos,
      blockModel,
      openVideoModal,
      closeModal,
      showBlockedModelsManager,
      loadHomeVideos,
      loadFavorites,
      loadHistory,
      loadFollowing,
      resetToHome,
      changePage,
      updateModalFavButton,
      showToast
    });
  }
  
  const urlParams = new URLSearchParams(window.location.search);
  const searchParam = urlParams.get('search');
  if (searchParam) {
    if (dom.searchInput) dom.searchInput.value = searchParam;
    if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = 'flex';
    performSearch(searchParam, 1);
  } else {
    loadHomeVideos(1);
  }

  setupEvents();
  initModalPlayerControls();
});

function initProfileScanner() {
  const scannerModule = (typeof window !== 'undefined' && window.ArchivebateProfileScanner)
    ? window.ArchivebateProfileScanner
    : (typeof ArchivebateProfileScanner !== 'undefined' ? ArchivebateProfileScanner : null);
  if (scannerModule && typeof scannerModule.init === 'function') {
    scannerModule.init({
      showToast,
      updateHomeStats
    });
  }
}

// ============================================================
// STATYSTYKI STRONY GŁÓWNEJ (WIDEO I PROFILE)
// ============================================================
async function updateHomeStats() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const hs = g.ArchivebateHomeStats || (typeof require === 'function' ? (require('./static/home-stats.js') || g.ArchivebateHomeStats) : null);
  return hs && typeof hs.update === 'function' ? hs.update() : Promise.resolve();
}

function setActiveNavTab(tabBtn) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const ae = g.ArchivebateAppEvents || (typeof require === 'function' ? (require('./static/app-events.js') || g.ArchivebateAppEvents) : null);
  if (ae && typeof ae.setActiveNavTab === 'function') return ae.setActiveNavTab(tabBtn);
  if (typeof document !== 'undefined') {
    document.querySelectorAll('.nav-link').forEach(btn => btn.classList.remove('active'));
    if (tabBtn) tabBtn.classList.add('active');
  }
}

// ============================================================
// SYSTEM PUNKTÓW KONTROLNYCH (CHECKPOINTS)
// ============================================================
function getSavedCheckpoint() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const cp = g.ArchivebateCheckpoints || (typeof require === 'function' ? (require('./static/checkpoints.js') || g.ArchivebateCheckpoints) : null);
  return cp && typeof cp.getSavedCheckpoint === 'function' ? cp.getSavedCheckpoint() : null;
}

function setCheckpoint(v) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const cp = g.ArchivebateCheckpoints || (typeof require === 'function' ? (require('./static/checkpoints.js') || g.ArchivebateCheckpoints) : null);
  return cp && typeof cp.setCheckpoint === 'function' ? cp.setCheckpoint(v) : undefined;
}

function updateCheckpointUI() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const cp = g.ArchivebateCheckpoints || (typeof require === 'function' ? (require('./static/checkpoints.js') || g.ArchivebateCheckpoints) : null);
  return cp && typeof cp.updateUI === 'function' ? cp.updateUI() : undefined;
}

function navigateToCheckpoint() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const cp = g.ArchivebateCheckpoints || (typeof require === 'function' ? (require('./static/checkpoints.js') || g.ArchivebateCheckpoints) : null);
  return cp && typeof cp.navigate === 'function' ? cp.navigate() : undefined;
}

function checkAndHighlightCheckpoint() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const cp = g.ArchivebateCheckpoints || (typeof require === 'function' ? (require('./static/checkpoints.js') || g.ArchivebateCheckpoints) : null);
  return cp && typeof cp.checkAndHighlight === 'function' ? cp.checkAndHighlight() : undefined;
}

// ============================================================
// DELEGOWANA OBSŁUGA ZDARZEŃ SIATKI WIDEO
// ============================================================
function handleGridClick(e) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const ae = g.ArchivebateAppEvents || (typeof require === 'function' ? (require('./static/app-events.js') || g.ArchivebateAppEvents) : null);
  return ae && ae.handleGridClick ? ae.handleGridClick(e) : undefined;
}

function handleGridAuxClick(e) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const ae = g.ArchivebateAppEvents || (typeof require === 'function' ? (require('./static/app-events.js') || g.ArchivebateAppEvents) : null);
  return ae && ae.handleGridAuxClick ? ae.handleGridAuxClick(e) : undefined;
}

function setupEvents() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const ae = g.ArchivebateAppEvents || (typeof require === 'function' ? (require('./static/app-events.js') || g.ArchivebateAppEvents) : null);
  return ae && ae.setupEvents ? ae.setupEvents() : undefined;
}

function changePage(newPage) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const pg = g.ArchivebatePagination || (typeof require === 'function' ? (require('./static/pagination.js') || g.ArchivebatePagination) : null);
  return pg && typeof pg.changePage === 'function' ? pg.changePage(newPage) : undefined;
}

function updateAllAuthorNameColors() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const fav = g.ArchivebateFavorites || (typeof require === 'function' ? (require('./static/favorites.js') || g.ArchivebateFavorites) : null);
  return fav && typeof fav.updateAllAuthorNameColors === 'function' ? fav.updateAllAuthorNameColors() : undefined;
}

function toggleFavoriteVideo(video, buttonEl = null) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const fav = g.ArchivebateFavorites || (typeof require === 'function' ? (require('./static/favorites.js') || g.ArchivebateFavorites) : null);
  return fav && typeof fav.toggleVideo === 'function' ? fav.toggleVideo(video, buttonEl) : undefined;
}

// ============================================================
// BLOKOWANIE I USUNIĘCIE PROFILU Z PROGRAMU
// ============================================================
function blockModel(username) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const bm = g.ArchivebateBlockedModels || (typeof require === 'function' ? (require('./static/blocked-models.js') || g.ArchivebateBlockedModels) : null);
  return bm && typeof bm.block === 'function' ? bm.block(username) : undefined;
}

function unblockModel(username) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const bm = g.ArchivebateBlockedModels || (typeof require === 'function' ? (require('./static/blocked-models.js') || g.ArchivebateBlockedModels) : null);
  return bm && typeof bm.unblock === 'function' ? bm.unblock(username) : undefined;
}

function updateBlockedModelsCount() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const bm = g.ArchivebateBlockedModels || (typeof require === 'function' ? (require('./static/blocked-models.js') || g.ArchivebateBlockedModels) : null);
  return bm && typeof bm.updateCount === 'function' ? bm.updateCount() : undefined;
}

function showBlockedModelsManager() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const bm = g.ArchivebateBlockedModels || (typeof require === 'function' ? (require('./static/blocked-models.js') || g.ArchivebateBlockedModels) : null);
  return bm && typeof bm.showManager === 'function' ? bm.showManager() : undefined;
}

function updateModalFavButton(isFav) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const fav = g.ArchivebateFavorites || (typeof require === 'function' ? (require('./static/favorites.js') || g.ArchivebateFavorites) : null);
  return fav && typeof fav.updateModalButton === 'function' ? fav.updateModalButton(isFav) : undefined;
}

// TAGI
function initTags() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const tagsMod = g.ArchivebateTags || (typeof require === 'function' ? (require('./static/tags.js') || g.ArchivebateTags) : null);
  if (tagsMod && typeof tagsMod.init === 'function') {
    return tagsMod.init({
      setActiveNavTab,
      performSearch
    });
  }
}

// Video Views Adapters
function prefetchNextPage() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  if (vv && typeof vv.prefetchNextPage === 'function') return vv.prefetchNextPage();
}

async function loadHomeVideos(page = 1, force = false) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.loadHomeVideos ? vv.loadHomeVideos(page, force) : Promise.resolve();
}

async function loadFavorites(page = 1) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.loadFavorites ? vv.loadFavorites(page) : Promise.resolve();
}

async function loadHistory(page = 1) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.loadHistory ? vv.loadHistory(page) : Promise.resolve();
}

async function loadFollowing(page = 1) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.loadFollowing ? vv.loadFollowing(page) : Promise.resolve();
}

// Search Results Adapter
async function performSearch(query, page = 1) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const sr = g.ArchivebateSearchResults || (typeof require === 'function' ? (require('./static/search-results.js') || g.ArchivebateSearchResults) : null);
  return sr && sr.performSearch ? sr.performSearch(query, page) : Promise.resolve();
}

// Video Views Adapters (Model & Home reset & Back)
async function loadModelVideos(username, page = 1) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.loadModelVideos ? vv.loadModelVideos(username, page) : Promise.resolve();
}

function resetToHome() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.resetToHome ? vv.resetToHome() : undefined;
}

function pushNavigationHistory() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.pushNavigationHistory ? vv.pushNavigationHistory() : undefined;
}

function updateBackButtonUI() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.updateBackButtonUI ? vv.updateBackButtonUI() : undefined;
}

function goBack() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  return vv && vv.goBack ? vv.goBack() : undefined;
}

// RENDEROWANIE PAGINACJI
function renderPagination() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const pg = g.ArchivebatePagination || (typeof require === 'function' ? (require('./static/pagination.js') || g.ArchivebatePagination) : null);
  return pg && typeof pg.render === 'function' ? pg.render() : undefined;
}

// Video Prefetch & Cache Adapters
const videoDetailsCache = (typeof window !== 'undefined' && window.ArchivebateVideoPrefetch)
  ? window.ArchivebateVideoPrefetch.detailsCache
  : (typeof ArchivebateVideoPrefetch !== 'undefined' ? ArchivebateVideoPrefetch.detailsCache : new (window.ArchivebatePerf?.LRUCache || Map)(180));

const videoDetailsInflight = (typeof window !== 'undefined' && window.ArchivebateVideoPrefetch)
  ? window.ArchivebateVideoPrefetch.videoDetailsInflight
  : (typeof ArchivebateVideoPrefetch !== 'undefined' ? ArchivebateVideoPrefetch.videoDetailsInflight : new Map());

function armLazyThumbnail(img) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vp = g.ArchivebateVideoPrefetch || (typeof require === 'function' ? (require('./static/video-prefetch.js') || g.ArchivebateVideoPrefetch) : null);
  if (vp && typeof vp.armLazyThumbnail === 'function') {
    return vp.armLazyThumbnail(img);
  }
}

function prefetchVideoDetails(videoId, active = false) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vp = g.ArchivebateVideoPrefetch || (typeof require === 'function' ? (require('./static/video-prefetch.js') || g.ArchivebateVideoPrefetch) : null);
  if (vp && typeof vp.prefetchVideoDetails === 'function') {
    return vp.prefetchVideoDetails(videoId, active);
  }
  return Promise.resolve(null);
}

function thumbnailUrlForVideo(v) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vp = g.ArchivebateVideoPrefetch || (typeof require === 'function' ? (require('./static/video-prefetch.js') || g.ArchivebateVideoPrefetch) : null);
  if (vp && typeof vp.thumbnailUrlForVideo === 'function') {
    return vp.thumbnailUrlForVideo(v);
  }
  if (!v) return '';
  return v.poster_proxy || v.thumbnail_proxy || (v.poster ? `/api/thumb?url=${encodeURIComponent(v.poster)}` : '');
}

function scheduleThumbnailWarmup(videos, start = 12, count = 24) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vp = g.ArchivebateVideoPrefetch || (typeof require === 'function' ? (require('./static/video-prefetch.js') || g.ArchivebateVideoPrefetch) : null);
  if (vp && typeof vp.scheduleThumbnailWarmup === 'function') {
    return vp.scheduleThumbnailWarmup(videos, start, count);
  }
}

// Video Card Adapter
function createVideoCard(v, idx) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vc = g.ArchivebateVideoCard || (typeof require === 'function' ? (require('./static/video-card.js') || g.ArchivebateVideoCard) : null);
  return vc && vc.createVideoCard ? vc.createVideoCard(v, idx) : null;
}

// Video Grid Core Adapters & Fallback Slice for regression_frontend.cjs
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

function renderVideoGrid(videos, options = {}) {
  if (options && options.reconcile) {
    return reconcilePage(videos, options);
  }
  return replaceView(videos, options);
}

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
  if (typeof lazyThumbObserver !== 'undefined') lazyThumbObserver?.disconnect?.();

  dom.videoGrid.innerHTML = '';
  state.gridCardMap = new Map();
  state.lastAppliedFeedRevision = -1;
  state.lastAppliedFeedUpdatedAt = 0;
  state.lastAppliedFeedVideoCount = -1;
  state.lastAppliedVideosCount = 0;

  if (!videos || videos.length === 0) return;

  videos = deduplicateVideos(videos);
  const shouldGroup = state.groupByAuthor && state.mode === 'search';
  const displayVideos = shouldGroup && typeof groupVideosByAuthor === 'function' ? groupVideosByAuthor(videos) : videos;

  const INITIAL_BATCH = 32;
  const CHUNK_SIZE = 32;
  const initial = displayVideos.slice(0, INITIAL_BATCH);
  const firstFragment = document.createDocumentFragment();

  initial.forEach((v, idx) => {
    const card = createVideoCard(v, idx);
    const key = getVideoKey(v);
    if (key && state.gridCardMap) state.gridCardMap.set(key, card);
    firstFragment.appendChild(card);
  });
  dom.videoGrid.appendChild(firstFragment);
  updateCheckpointUI?.();
  checkAndHighlightCheckpoint?.();

  let cursor = INITIAL_BATCH;
  const appendNextChunk = () => {
    if (generation !== state.gridGeneration || cursor >= displayVideos.length) return;
    const end = Math.min(cursor + CHUNK_SIZE, displayVideos.length);
    const fragment = document.createDocumentFragment();
    for (let i = cursor; i < end; i += 1) {
      const v = displayVideos[i];
      const card = createVideoCard(v, i);
      const key = getVideoKey(v);
      if (key && state.gridCardMap) state.gridCardMap.set(key, card);
      fragment.appendChild(card);
    }
    dom.videoGrid.appendChild(fragment);
    cursor = end;

    if (cursor < displayVideos.length) {
      if ('requestIdleCallback' in window) {
        requestIdleCallback(appendNextChunk, { timeout: 250 });
      } else {
        setTimeout(appendNextChunk, 16);
      }
    } else {
      updateCheckpointUI?.();
      checkAndHighlightCheckpoint?.();
    }
  };

  if (cursor < displayVideos.length) {
    if ('requestIdleCallback' in window) {
      requestIdleCallback(appendNextChunk, { timeout: 200 });
    } else {
      setTimeout(appendNextChunk, 16);
    }
  }
}

function reconcilePage(videos, options = {}) {
  if (!dom.videoGrid) return;
  if (!videos || !Array.isArray(videos)) return;

  if (videos.length === 0 && state.gridCardMap && state.gridCardMap.size > 0) {
    return;
  }

  videos = deduplicateVideos(videos);
  const shouldGroup = state.groupByAuthor && state.mode === 'search';
  const displayVideos = shouldGroup && typeof groupVideosByAuthor === 'function' ? groupVideosByAuthor(videos) : videos;

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
      const card = createVideoCard(v, idx);
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

  updateCheckpointUI?.();
  checkAndHighlightCheckpoint?.();
}

// STOPNIOWE DOKŁADANIE KAFELKÓW W CZASIE RZECZYWISTYM ("PO KOLEI") Z ZERO-FLASH GATE
function appendVideoBatch(videos) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vg = g.ArchivebateVideoGrid || (typeof require === 'function' ? (require('./static/video-grid.js') || g.ArchivebateVideoGrid) : null);
  return vg && vg.appendVideoBatch ? vg.appendVideoBatch(videos) : undefined;
}

// Video Modal Adapters
async function openVideoModal(video) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.openVideoModal ? vm.openVideoModal(video) : undefined;
}

function togglePlayerMode() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.togglePlayerMode ? vm.togglePlayerMode() : undefined;
}

function enableIframeMode() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.enableIframeMode ? vm.enableIframeMode() : undefined;
}

function getActiveFilteredVideos() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.getActiveFilteredVideos ? vm.getActiveFilteredVideos() : [];
}

function getCurrentVideoIndex() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.getCurrentVideoIndex ? vm.getCurrentVideoIndex() : -1;
}

async function playNextVideo() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.playNextVideo ? vm.playNextVideo() : undefined;
}

async function playPrevVideo() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.playPrevVideo ? vm.playPrevVideo() : undefined;
}

function closeModal() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vm = g.ArchivebateVideoModal || (typeof require === 'function' ? (require('./static/video-modal.js') || g.ArchivebateVideoModal) : null);
  return vm && vm.closeModal ? vm.closeModal() : undefined;
}

// SKELETONY
function showSkeletons() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const vv = g.ArchivebateVideoViews || (typeof require === 'function' ? (require('./static/video-views.js') || g.ArchivebateVideoViews) : null);
  if (vv && typeof vv.showSkeletons === 'function') return vv.showSkeletons();
}

// TOAST
function showToast(message, type = 'info', existingToast = null) {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const t = g.ArchivebateToast || (typeof require === 'function' ? (require('./static/toast.js') || g.ArchivebateToast) : null);
  return t && typeof t.show === 'function' ? t.show(message, type, existingToast) : undefined;
}

// Modal Player Controls Adapter
function initModalPlayerControls() {
  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);
  const mpc = g.ArchivebateModalPlayerControls || (typeof require === 'function' ? (require('./static/modal-player-controls.js') || g.ArchivebateModalPlayerControls) : null);
  return mpc && mpc.initModalPlayerControls ? mpc.initModalPlayerControls() : undefined;
}
