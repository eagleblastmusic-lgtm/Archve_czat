/**
 * Archivebate Video Browser - Search Results & SSE Streaming Module
 * Odpowiada za wyszukiwanie po tagu/frazie:
 * - dla strony 1: strumieniowanie w czasie rzeczywistym przez EventSource (SSE)
 * - dla strony > 1: paginowane pobieranie JSON z pamięci podręcznej API
 * - dopasowane profile modelek (chips) z obsługą zakładek (tabManager)
 * - obsługa grupowania wg autora w wynikach wyszukiwania (reconcilePage)
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

  const api = () => global.ArchivebateAPI || {
    getJSON: (url, opts) => fetch(url, opts).then(r => r.json())
  };

  function triggerToast(msg, type) {
    if (global.ArchivebateToast && typeof global.ArchivebateToast.show === 'function') {
      return global.ArchivebateToast.show(msg, type);
    }
    if (typeof global.showToast === 'function') return global.showToast(msg, type);
  }

  function showSkeletons() {
    if (global.ArchivebateVideoViews && typeof global.ArchivebateVideoViews.showSkeletons === 'function') {
      return global.ArchivebateVideoViews.showSkeletons();
    }
    if (typeof global.showSkeletons === 'function') return global.showSkeletons();
  }

  function beginViewRequest() {
    if (global.ArchivebateVideoViews && typeof global.ArchivebateVideoViews.beginViewRequest === 'function') {
      return global.ArchivebateVideoViews.beginViewRequest();
    }
    if (typeof global.beginViewRequest === 'function') return global.beginViewRequest();
    state.viewGeneration = (state.viewGeneration || 0) + 1;
    return state.viewGeneration;
  }

  function updateBackButtonUI() {
    if (global.ArchivebateVideoViews && typeof global.ArchivebateVideoViews.updateBackButtonUI === 'function') {
      return global.ArchivebateVideoViews.updateBackButtonUI();
    }
    if (typeof global.updateBackButtonUI === 'function') return global.updateBackButtonUI();
  }

  function renderVideoGrid(videos, options) {
    if (global.ArchivebateVideoGrid && typeof global.ArchivebateVideoGrid.renderVideoGrid === 'function') {
      return global.ArchivebateVideoGrid.renderVideoGrid(videos, options);
    }
    if (typeof global.renderVideoGrid === 'function') return global.renderVideoGrid(videos, options);
  }

  function reconcilePage(videos, options) {
    if (global.ArchivebateVideoGrid && typeof global.ArchivebateVideoGrid.reconcilePage === 'function') {
      return global.ArchivebateVideoGrid.reconcilePage(videos, options);
    }
    if (typeof global.reconcilePage === 'function') return global.reconcilePage(videos, options);
  }

  function appendVideoBatch(videos) {
    if (global.ArchivebateVideoGrid && typeof global.ArchivebateVideoGrid.appendVideoBatch === 'function') {
      return global.ArchivebateVideoGrid.appendVideoBatch(videos);
    }
    if (typeof global.appendVideoBatch === 'function') return global.appendVideoBatch(videos);
  }

  function scheduleThumbnailWarmup(videos, start, count) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.scheduleThumbnailWarmup === 'function') {
      return global.ArchivebateVideoPrefetch.scheduleThumbnailWarmup(videos, start, count);
    }
    if (typeof global.scheduleThumbnailWarmup === 'function') return global.scheduleThumbnailWarmup(videos, start, count);
  }

  function renderPagination() {
    if (global.ArchivebatePagination && typeof global.ArchivebatePagination.render === 'function') {
      return global.ArchivebatePagination.render();
    }
    if (typeof global.renderPagination === 'function') return global.renderPagination();
  }

  function loadModelVideos(username, page) {
    if (global.ArchivebateVideoViews && typeof global.ArchivebateVideoViews.loadModelVideos === 'function') {
      return global.ArchivebateVideoViews.loadModelVideos(username, page);
    }
    if (typeof global.loadModelVideos === 'function') return global.loadModelVideos(username, page);
  }

  async function performSearch(query, page = 1) {
    const generation = beginViewRequest();
    const controller = state.viewController;
    if (state.activeSearchSource) {
      state.activeSearchSource.close();
      state.activeSearchSource = null;
    }

    state.mode = 'search';
    state.currentQuery = query;
    state.currentPage = page;

    const isTag = query.startsWith('#') || (typeof document !== 'undefined' && document.querySelector(`.tag-pill[data-tag="${query.replace('#','').toLowerCase()}"]`) !== null);
    const cleanTagName = query.replace('#', '').trim();
    if (global.tabManager && typeof global.tabManager.updateActiveTabInfo === 'function') {
      global.tabManager.updateActiveTabInfo(isTag ? `#${cleanTagName}` : `Szukaj: ${query}`, isTag ? 'fa-solid fa-tag' : 'fa-solid fa-magnifying-glass');
    }

    if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
    if (dom.tagsSection) dom.tagsSection.style.display = 'block';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
    if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
    let filterSuffix = '';
    if (state.sourceFilter === 'only-camwhores') filterSuffix += ' • Tylko Camwhores';
    else if (state.sourceFilter === 'only-archivebate') filterSuffix += ' • Tylko Archivebate';
    if (state.authorFilter === 'only_fav') filterSuffix += ' • Tylko polubieni';
    else if (state.authorFilter === 'exclude_fav') filterSuffix += ' • Bez polubionych';
    if (state.groupByAuthor) filterSuffix += ' • Zgrupowane (1/autora)';

    if (dom.viewTitle) dom.viewTitle.innerText = isTag ? `🏷️ Tag: #${cleanTagName}${filterSuffix} (Strona ${page})` : `Wyniki dla: "${query}"${filterSuffix} (Strona ${page})`;
    if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'flex';
    updateBackButtonUI();
    if (dom.pageJumpInput) dom.pageJumpInput.value = page;

    const src = encodeURIComponent(state.sourceFilter || 'all');
    const af = encodeURIComponent(state.authorFilter || 'all');
    const grp = state.groupByAuthor ? '1' : '0';

    // Dla kolejnych stron (page > 1) pobieramy z pamięci RAM (cache)
    if (page > 1) {
      showSkeletons();
      state.isLoading = true;
      if (dom.paginationSection) dom.paginationSection.style.display = 'flex';
      try {
        const data = await api().getJSON(`/api/search?q=${encodeURIComponent(query)}&page=${page}&source=${src}&author_filter=${af}&group_authors=${grp}`, { timeoutMs: 15000, signal: controller.signal });
        if (generation !== state.viewGeneration) return;
        state.lastPage = data.last_page || 1;
        state.videos = data.videos || [];
        renderVideoGrid(state.videos);
        scheduleThumbnailWarmup(state.videos);
        renderPagination();
        if (dom.videoCount) dom.videoCount.innerText = `${state.videos.length} na stronie • Strona ${page} z ${state.lastPage} • Łącznie: ${Number(data.total_videos || 0).toLocaleString('pl-PL')} filmów • 5.5M+ w serwisach`;
      } catch (e) {
        if (generation !== state.viewGeneration || e?.code === 'cancelled') return;
        triggerToast(e?.message || 'Błąd ładowania strony wyników', 'error');
      } finally {
        if (generation === state.viewGeneration) state.isLoading = false;
      }
      return;
    }

    // DLA STRONY 1: STRUMIENIOWANIE W CZASIE RZECZYWISTYM
    if (dom.videoGrid) dom.videoGrid.innerHTML = '';
    showSkeletons();
    if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
    if (dom.profilesList) dom.profilesList.innerHTML = '';
    if (dom.videoCount) dom.videoCount.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Pobieranie najnowszych wideo...`;

    let accumulatedVideos = [];
    let isFirstBatch = true;

    const evtSource = new EventSource(`/api/search/stream?q=${encodeURIComponent(query)}&source=${src}&author_filter=${af}&group_authors=${grp}`);
    state.activeSearchSource = evtSource;

    evtSource.onmessage = (event) => {
      if (generation !== state.viewGeneration) return;
      try {
        const payload = JSON.parse(event.data);

        if (payload.type === 'profiles') {
          if (payload.last_page) {
            state.lastPage = payload.last_page;
            renderPagination();
          }
          const profiles = payload.profiles || [];
          if (profiles.length > 0 && !isTag) {
            if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'block';
            if (dom.profilesList) dom.profilesList.innerHTML = '';
            profiles.forEach(p => {
              const chip = document.createElement('div');
              chip.className = 'profile-chip';
              chip.innerHTML = `
                <div class="profile-chip-avatar">${(p.username || 'M').substring(0, 2).toUpperCase()}</div>
                <div>
                  <div class="profile-chip-name">${p.username}</div>
                  <div class="profile-chip-meta">${p.platform || 'Cam'} ${p.gender ? '• ' + p.gender : ''}</div>
                </div>
              `;
              chip.title = `Zobacz profil ${p.username} (LPM) lub otwórz w nowej karcie (Kółko myszy)`;
              chip.addEventListener('click', () => loadModelVideos(p.username, 1));
              chip.addEventListener('auxclick', (e) => {
                if (e.button === 1) {
                  e.preventDefault();
                  e.stopPropagation();
                  if (global.tabManager && typeof global.tabManager.openTab === 'function') {
                    global.tabManager.openTab({
                      title: p.username,
                      icon: 'fa-solid fa-circle-user',
                      type: 'model',
                      username: p.username,
                      inBackground: true
                    });
                  }
                }
              });
              chip.addEventListener('mousedown', (e) => {
                if (e.button === 1) e.preventDefault();
              });
              if (dom.profilesList) dom.profilesList.appendChild(chip);
            });
          }
          const totalP = payload.total_profiles || profiles.length;
          const estTotal = payload.estimated_total_videos ? ` • szacunkowo ~${Number(payload.estimated_total_videos).toLocaleString('pl-PL')} filmów` : '';
          if (dom.videoCount) dom.videoCount.innerText = `Znaleziono ${totalP} profili${estTotal}. Pobieranie nagrań...`;
        } else if (payload.type === 'videos') {
          const newVids = payload.videos || [];
          if (newVids.length > 0) {
            if (isFirstBatch) {
              if (dom.videoGrid) dom.videoGrid.innerHTML = '';
              isFirstBatch = false;
            }
            accumulatedVideos = accumulatedVideos.concat(newVids);
            state.videos = accumulatedVideos;

            if (state.groupByAuthor) {
              reconcilePage(state.videos);
            } else {
              appendVideoBatch(newVids);
            }
            if (!state.lastPage || state.lastPage < 2) {
              state.lastPage = Math.max(state.lastPage || 1, Math.ceil(accumulatedVideos.length / 280));
            }
            renderPagination();
            if (dom.videoCount) dom.videoCount.innerText = `Załadowano ${accumulatedVideos.length} filmów • Strona 1 z ${state.lastPage} (wyszukiwanie trwa...)`;
          }
        } else if (payload.type === 'done') {
          evtSource.close();
          state.activeSearchSource = null;

          if (accumulatedVideos.length === 0) {
            if (dom.videoGrid) {
              dom.videoGrid.innerHTML = `
                <div class="empty-state" style="grid-column: 1 / -1;">
                  <div class="empty-state-icon"><i class="fa-solid fa-film"></i></div>
                  <h3>Nie znaleziono filmów dla "${query}"</h3>
                  <p>Spróbuj użyć innego tagu, nazwy modelki lub platformy.</p>
                </div>
              `;
            }
            if (dom.videoCount) dom.videoCount.innerText = '0 filmów';
          } else {
            if (payload.all_sorted_videos && payload.all_sorted_videos.length > 0) {
              accumulatedVideos = payload.all_sorted_videos;
              state.videos = accumulatedVideos;
              renderVideoGrid(state.videos);
              scheduleThumbnailWarmup(state.videos);
            }
            const totalCount = payload.total_videos || accumulatedVideos.length;
            state.lastPage = payload.last_page || Math.ceil(totalCount / 280) || 1;
            if (dom.videoCount) {
              dom.videoCount.innerText = `${accumulatedVideos.length} na stronie • Strona 1 z ${state.lastPage} • Łącznie: ${Number(totalCount).toLocaleString('pl-PL')} filmów • 5.5M+ w serwisach`;
            }
            renderPagination();
          }
        }
      } catch (err) {
        console.error('Błąd SSE:', err);
      }
    };

    evtSource.onerror = () => {
      if (generation !== state.viewGeneration) return;
      evtSource.close();
      state.activeSearchSource = null;
      renderPagination();
    };
  }

  const ArchivebateSearchResults = {
    performSearch,
    init: () => {}
  };

  global.ArchivebateSearchResults = ArchivebateSearchResults;
  if (!global.performSearch) global.performSearch = performSearch;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateSearchResults;
  }
})(typeof window !== 'undefined' ? window : globalThis);
