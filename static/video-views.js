/**
 * Archivebate Video Browser - Video Views & Loaders Module
 * Odpowiada za ładowanie widoków: Strona Główna (Feed & SSE snapshot stream),
 * Ulubione (Favorites), Historia (History), Obserwowane (Following),
 * Filmy Modelki (Model Videos), historię nawigacji (Wstecz) oraz wskaźniki stanu.
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
    getJSON: (url, opts) => fetch(url, opts).then(r => r.json()),
    postJSON: (url, body, opts) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), ...opts }).then(r => r.json())
  };

  const perf = () => global.ArchivebatePerf || {
    prefetchUrls: () => Promise.resolve(),
    measure: () => {}
  };

  function triggerToast(msg, type) {
    if (global.ArchivebateToast && typeof global.ArchivebateToast.show === 'function') {
      return global.ArchivebateToast.show(msg, type);
    }
    if (typeof global.showToast === 'function') {
      return global.showToast(msg, type);
    }
  }

  function setActiveNavTab(tabBtn) {
    if (global.ArchivebateAppEvents && typeof global.ArchivebateAppEvents.setActiveNavTab === 'function') {
      return global.ArchivebateAppEvents.setActiveNavTab(tabBtn);
    }
    if (typeof global.setActiveNavTab === 'function') {
      return global.setActiveNavTab(tabBtn);
    }
    document.querySelectorAll('.nav-link').forEach(btn => btn.classList.remove('active'));
    if (tabBtn) tabBtn.classList.add('active');
  }

  function updateHomeStats() {
    if (global.ArchivebateHomeStats && typeof global.ArchivebateHomeStats.update === 'function') {
      return global.ArchivebateHomeStats.update();
    }
    if (typeof global.updateHomeStats === 'function') {
      return global.updateHomeStats();
    }
  }

  function renderPagination() {
    if (global.ArchivebatePagination && typeof global.ArchivebatePagination.render === 'function') {
      return global.ArchivebatePagination.render();
    }
    if (typeof global.renderPagination === 'function') {
      return global.renderPagination();
    }
  }

  function renderVideoGrid(videos, options) {
    if (global.ArchivebateVideoGrid && typeof global.ArchivebateVideoGrid.renderVideoGrid === 'function') {
      return global.ArchivebateVideoGrid.renderVideoGrid(videos, options);
    }
    if (typeof global.renderVideoGrid === 'function') {
      return global.renderVideoGrid(videos, options);
    }
  }

  function reconcilePage(videos, options) {
    if (global.ArchivebateVideoGrid && typeof global.ArchivebateVideoGrid.reconcilePage === 'function') {
      return global.ArchivebateVideoGrid.reconcilePage(videos, options);
    }
    if (typeof global.reconcilePage === 'function') {
      return global.reconcilePage(videos, options);
    }
  }

  function deduplicateVideos(videos) {
    if (global.ArchivebateVideoGrid && typeof global.ArchivebateVideoGrid.deduplicateVideos === 'function') {
      return global.ArchivebateVideoGrid.deduplicateVideos(videos);
    }
    if (typeof global.deduplicateVideos === 'function') {
      return global.deduplicateVideos(videos);
    }
    return videos || [];
  }

  function scheduleThumbnailWarmup(videos, start = 12, count = 24) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.scheduleThumbnailWarmup === 'function') {
      return global.ArchivebateVideoPrefetch.scheduleThumbnailWarmup(videos, start, count);
    }
    if (typeof global.scheduleThumbnailWarmup === 'function') {
      return global.scheduleThumbnailWarmup(videos, start, count);
    }
  }

  function thumbnailUrlForVideo(v) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.thumbnailUrlForVideo === 'function') {
      return global.ArchivebateVideoPrefetch.thumbnailUrlForVideo(v);
    }
    if (typeof global.thumbnailUrlForVideo === 'function') {
      return global.thumbnailUrlForVideo(v);
    }
    if (!v) return '';
    return v.poster_proxy || v.thumbnail_proxy || (v.poster ? `/api/thumb?url=${encodeURIComponent(v.poster)}` : '');
  }

  function showSkeletons() {
    state.gridGeneration = (state.gridGeneration || 0) + 1;
    if (dom.videoGrid) {
      dom.videoGrid.innerHTML = '';
      for (let i = 0; i < 12; i++) {
        const sk = document.createElement('div');
        sk.className = 'skeleton-card';
        dom.videoGrid.appendChild(sk);
      }
    }
  }

  function setFeedRefreshingIndicator(show) {
    let indicator = document.getElementById('feedRefreshIndicator');
    if (show) {
      if (!indicator && dom.contentHeader) {
        indicator = document.createElement('span');
        indicator.id = 'feedRefreshIndicator';
        indicator.className = 'feed-refresh-indicator';
        indicator.innerHTML = '<i class="fa-solid fa-rotate fa-spin" style="font-size:12px;margin-left:8px;color:var(--accent-color, #ffa31a);"></i>';
        indicator.title = 'Trwa odświeżanie danych w tle...';
        const targetHeader = dom.viewTitle || dom.contentHeader;
        if (targetHeader) targetHeader.appendChild(indicator);
      }
      if (indicator) indicator.style.display = 'inline-flex';
    } else {
      if (indicator) indicator.style.display = 'none';
    }
  }

  function prefetchNextPage() {
    // Home is filled via its SSE subscriber.
  }

  function beginViewRequest() {
    state.gridController?.abort();
    if (typeof global.lazyThumbObserver !== 'undefined') global.lazyThumbObserver?.disconnect?.();
    if (global.activeHoverVideo) {
      try {
        global.activeHoverVideo.pause();
        global.activeHoverVideo.removeAttribute('src');
        global.activeHoverVideo.load();
      } catch (_) {}
      global.activeHoverVideo = null;
    }
    state.viewController?.abort();
    if (global.thumbnailWarmupController) global.thumbnailWarmupController?.abort?.();
    state.activeSearchSource?.close?.();
    state.activeSearchSource = null;
    state.viewController = new AbortController();
    state.viewGeneration = (state.viewGeneration || 0) + 1;
    state.gridGeneration = (state.gridGeneration || 0) + 1;
    state.isLoading = false;
    return state.viewGeneration;
  }

  async function loadHomeVideos(page = 1, force = false) {
    const started = performance.now();
    const previousPage = state.currentPage;
    const generation = beginViewRequest();
    const controller = state.viewController;
    state.isLoading = true;
    state.mode = 'home';

    state.currentPage = page;
    if (global.tabManager && typeof global.tabManager.updateActiveTabInfo === 'function') {
      global.tabManager.updateActiveTabInfo('Odkrywaj', 'fa-solid fa-house');
    }

    const src = encodeURIComponent(state.sourceFilter || 'all');
    const af = encodeURIComponent(state.authorFilter || 'all');
    const grp = state.groupByAuthor ? '1' : '0';
    const specKey = `${src}|${af}|${grp}`;

    const hasExistingCards = Boolean(
      dom.videoGrid &&
      dom.videoGrid.children &&
      dom.videoGrid.children.length > 0 &&
      !dom.videoGrid.querySelector('.skeleton-card') &&
      state.gridCardMap &&
      state.gridCardMap.size > 0
    );
    const isSamePageRefresh = hasExistingCards && !force && previousPage === page && state.feedSpecKey === specKey;

    if (!isSamePageRefresh) {
      state.videos = [];
      showSkeletons();
      state.gridCardMap = new Map();
      state.lastAppliedFeedRevision = -1;
      state.lastAppliedFeedUpdatedAt = 0;
      state.lastAppliedFeedVideoCount = -1;
      state.lastAppliedVideosCount = 0;
    } else {
      setFeedRefreshingIndicator(true);
    }

    if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
    if (dom.tagsSection) dom.tagsSection.style.display = 'block';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'grid';
    if (dom.contentHeader) dom.contentHeader.style.display = 'flex';

    let filterTitle = 'Najnowsze wideo';
    if (state.sourceFilter === 'only-camwhores') filterTitle += ' • Tylko Camwhores';
    else if (state.sourceFilter === 'only-archivebate') filterTitle += ' • Tylko Archivebate';
    if (state.authorFilter === 'only_fav') filterTitle += ' • Tylko polubieni';
    else if (state.authorFilter === 'exclude_fav') filterTitle += ' • Bez polubionych';
    if (state.groupByAuthor) filterTitle += ' • Zgrupowane (1/autora)';

    if (dom.viewTitle) dom.viewTitle.innerText = filterTitle;
    if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'none';
    updateBackButtonUI();
    if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
    if (dom.pageJumpInput) dom.pageJumpInput.value = page;
    updateHomeStats();

    const showFeedError = () => {
      if (generation !== state.viewGeneration) return;
      if (dom.videoCount) dom.videoCount.innerText = 'Nie udało się załadować filmów. Ponów próbę.';
      dom.videoGrid?.querySelectorAll('.skeleton-card').forEach(card => card.remove());
      if (dom.videoGrid && !dom.videoGrid.querySelector('.feed-retry')) {
        const retry = document.createElement('button');
        retry.className = 'btn-card feed-retry';
        retry.textContent = 'Ponów ładowanie filmów';
        retry.onclick = () => loadHomeVideos(page, true);
        dom.videoGrid.appendChild(retry);
      }
      setFeedRefreshingIndicator(false);
    };

    try {
      const snapshot = !force && state.feedSpecKey === specKey ? state.feedSnapshotId : null;
      const revParam = !force && state.catalogRevision ? `&revision=${encodeURIComponent(state.catalogRevision)}` : '';
      const params = `page=${page}&source=${src}&author_filter=${af}&group_authors=${grp}${force ? '&force_refresh=true' : ''}${revParam}`;
      const data = await api().getJSON(`/api/feed?${params}${snapshot ? `&snapshot_id=${encodeURIComponent(snapshot)}` : ''}`, { timeoutMs: 12000, signal: controller.signal });
      if (generation !== state.viewGeneration) return;
      state.feedSpecKey = specKey;
      state.feedSnapshotId = data.snapshot_id;
      state.catalogRevision = data.catalog_revision !== undefined ? data.catalog_revision : data.revision;
      const streamRevision = data.refresh_revision || state.catalogRevision;
      const streamSnapshotId = data.refresh_revision ? String(data.refresh_revision) : data.snapshot_id;
      if (data.refresh_pending) setFeedRefreshingIndicator(true);
      let firstBatch = true;
      let pendingBatchData = null;
      let batchRafId = null;

      const updateFeedCounters = d => {
        const isComplete = d.catalog_complete !== undefined ? d.catalog_complete : d.complete;
        const totalVids = d.video_count !== undefined ? d.video_count : (d.total_videos || d.known_count || 0);
        const totalGroups = d.group_count !== undefined ? d.group_count : totalVids;
        const pageCount = d.page_count || d.last_page || 1;
        state.lastPage = pageCount;
        state.totalCatalogVideos = totalVids;
        if (dom.pageJumpInput) dom.pageJumpInput.max = pageCount;
        if (dom.pageJumpInputTop) dom.pageJumpInputTop.max = pageCount;

        if (dom.videoCount) {
          if (isComplete) {
            if (state.groupByAuthor) {
              dom.videoCount.innerText = `${state.videos.length} na stronie • ${totalGroups.toLocaleString('pl-PL')} grup (${totalVids.toLocaleString('pl-PL')} nagrań) • strona ${page} z ${pageCount.toLocaleString('pl-PL')}`;
            } else {
              dom.videoCount.innerText = `${state.videos.length} na stronie • ${totalVids.toLocaleString('pl-PL')} nagrań • strona ${page} z ${pageCount.toLocaleString('pl-PL')}`;
            }
          } else {
            dom.videoCount.innerText = `${state.videos.length} na stronie • Przygotowanie katalogu… (${totalVids.toLocaleString('pl-PL')} pozycji) • strona ${page}`;
          }
        }
        if (dom.statPageVideos) dom.statPageVideos.innerText = state.videos.length;
        renderPagination();
      };

      const commitBatch = (batchData, isInitial = false) => {
        if (generation !== state.viewGeneration) return;
        const incomingRevision = Number(batchData.catalog_revision !== undefined ? batchData.catalog_revision : batchData.revision);
        const currentRevision = Number(state.catalogRevision);
        const isNewerCatalogRevision = Number.isFinite(incomingRevision) && Number.isFinite(currentRevision) && incomingRevision > currentRevision;
        if (batchData.snapshot_id && state.feedSnapshotId && batchData.snapshot_id !== state.feedSnapshotId && batchData.catalog_revision !== state.catalogRevision && !isNewerCatalogRevision) return;

        const currentRev = batchData.catalog_revision !== undefined ? batchData.catalog_revision : batchData.revision;
        const incomingUpdatedAt = Number(batchData.updated_at || 0);
        const incomingVideoCount = Number(batchData.video_count !== undefined ? batchData.video_count : (batchData.known_count || 0));
        const sameRevisionProgress = currentRev !== undefined && currentRev === state.lastAppliedFeedRevision && (
          incomingUpdatedAt > Number(state.lastAppliedFeedUpdatedAt || 0) ||
          incomingVideoCount > Number(state.lastAppliedFeedVideoCount ?? -1) ||
          (!!batchData.catalog_complete && !state.catalogComplete)
        );
        if (!isInitial && currentRev !== undefined && state.lastAppliedFeedRevision !== undefined && (
          currentRev < state.lastAppliedFeedRevision ||
          (currentRev === state.lastAppliedFeedRevision && !sameRevisionProgress)
        )) {
          if (batchData.complete || batchData.stopped || batchData.type === 'source_error') {
            updateFeedCounters(batchData);
            setFeedRefreshingIndicator(false);
            if (batchData.retryable || batchData.type === 'source_error') showFeedError();
          }
          return;
        }

        const currentVideosCount = state.videos ? state.videos.length : 0;
        const incomingVideos = batchData.videos || batchData.items || [];
        if (!isInitial && currentVideosCount >= 280 && incomingVideos.length === currentVideosCount && state.lastAppliedVideosCount === currentVideosCount) {
          state.lastAppliedFeedRevision = currentRev;
          state.lastAppliedFeedUpdatedAt = incomingUpdatedAt;
          state.lastAppliedFeedVideoCount = incomingVideoCount;
          state.catalogRevision = currentRev;
          if (batchData.snapshot_id) state.feedSnapshotId = batchData.snapshot_id;
          state.catalogComplete = !!batchData.catalog_complete;
          state.totalCatalogVideos = batchData.video_count !== undefined ? batchData.video_count : batchData.known_count;
          state.feedHasMore = batchData.has_more;
          state.lastPage = batchData.page_count || batchData.last_page;
          updateFeedCounters(batchData);
          if (batchData.complete || batchData.stopped) setFeedRefreshingIndicator(false);
          return;
        }

        state.lastAppliedFeedRevision = currentRev;
        state.lastAppliedFeedUpdatedAt = incomingUpdatedAt;
        state.lastAppliedFeedVideoCount = incomingVideoCount;
        state.catalogRevision = currentRev;
        state.lastAppliedVideosCount = incomingVideos.length;
        state.videos = incomingVideos;
        if (firstBatch && state.videos.length) {
          perf().measure('feed_first_batch', started);
          firstBatch = false;
        }
        state.feedHasMore = batchData.has_more;
        state.lastPage = batchData.page_count || batchData.last_page;
        state.totalCatalogVideos = batchData.video_count !== undefined ? batchData.video_count : batchData.known_count;
        state.catalogComplete = !!batchData.catalog_complete;

        reconcilePage(state.videos, { complete: batchData.complete || batchData.stopped || batchData.catalog_complete });
        updateFeedCounters(batchData);
        if (batchData.complete || batchData.stopped || batchData.catalog_complete) {
          setFeedRefreshingIndicator(false);
        }
        if (batchData.retryable || batchData.type === 'source_error') showFeedError();
      };

      const apply = (batchData, isInitial = false) => {
        if (generation !== state.viewGeneration) return;
        if (isInitial) {
          commitBatch(batchData, true);
          return;
        }
        pendingBatchData = batchData;
        if (batchRafId === null) {
          const scheduleFrame = (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function')
            ? window.requestAnimationFrame
            : (cb => setTimeout(cb, 16));
          batchRafId = scheduleFrame(() => {
            batchRafId = null;
            if (!pendingBatchData) return;
            const next = pendingBatchData;
            pendingBatchData = null;
            commitBatch(next, false);
          });
        }
      };

      apply(data, true);
      const catalogRevisionStream = data.catalog_complete === false && /^\d+$/.test(String(streamSnapshotId || ''));
      if (data.refresh_pending || catalogRevisionStream || !data.complete) {
        const streamRevisionParam = streamRevision ? `&revision=${encodeURIComponent(streamRevision)}` : '';
        const stream = new EventSource(`/api/feed/stream?${params}${streamRevisionParam}&snapshot_id=${encodeURIComponent(streamSnapshotId)}`);
        state.activeSearchSource = stream;
        // EventSource nie ma własnego limitu czasu. Gdy dostawca milczy,
        // skeleton nie może wisieć bez końca — pokaż kontrolowany retry.
        let streamWatchdog = setTimeout(() => {
          if (generation !== state.viewGeneration) return;
          stream.close();
          if (state.activeSearchSource === stream) state.activeSearchSource = null;
          showFeedError();
        }, 25000);
        stream.onmessage = event => {
          if (generation !== state.viewGeneration) { stream.close(); return; }
          let batch;
          try { batch = JSON.parse(event.data); }
          catch (_) { clearTimeout(streamWatchdog); stream.close(); showFeedError(); return; }
          if ((batch.videos || batch.items || []).length || batch.catalog_complete || batch.stopped) {
            clearTimeout(streamWatchdog);
          }
          apply(batch, false);
          const streamComplete = batch.catalog_complete !== undefined ? batch.catalog_complete : batch.complete;
          if (streamComplete || batch.stopped) {
            stream.close();
            if (state.activeSearchSource === stream) state.activeSearchSource = null;
            if (batch.type === 'source_error' && dom.videoCount) dom.videoCount.innerText += ' • źródło chwilowo niedostępne; odśwież widok';
          }
        };
        stream.onerror = () => { clearTimeout(streamWatchdog); stream.close(); showFeedError(); };
      }
    } catch (e) {
      if (generation !== state.viewGeneration || e?.code === 'cancelled') return;
      if (e?.status === 409 && !force) {
        state.feedSnapshotId = null;
        state.catalogRevision = null;
        return loadHomeVideos(page, true);
      }
      showFeedError();
      triggerToast(e?.message || 'Błąd podczas pobierania filmów', 'error');
    } finally {
      if (generation === state.viewGeneration) {
        state.isLoading = false;
        setFeedRefreshingIndicator(false);
      }
    }
  }

  async function loadFavorites(page = 1) {
    const generation = beginViewRequest();
    const controller = state.viewController;
    state.mode = 'favorites';
    state.currentPage = page;
    if (global.tabManager && typeof global.tabManager.updateActiveTabInfo === 'function') {
      global.tabManager.updateActiveTabInfo('Ulubione', 'fa-solid fa-heart');
    }

    showSkeletons();
    if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
    if (dom.tagsSection) dom.tagsSection.style.display = 'none';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
    if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
    if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'flex';
    if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
    if (dom.pageJumpInput) dom.pageJumpInput.value = page;

    try {
      const data = await api().getJSON(`/api/account/favorites?page=${page}&per_page=280`, { timeoutMs: 12000, signal: controller.signal });
      if (generation !== state.viewGeneration) return;
      state.videos = data.videos || [];
      state.lastPage = data.last_page || 1;

      if (dom.viewTitle) dom.viewTitle.innerText = `❤️ Moje Ulubione Filmy (Strona ${page} z ${state.lastPage})`;
      renderVideoGrid(state.videos);
      scheduleThumbnailWarmup(state.videos);
      if (dom.videoCount) dom.videoCount.innerText = `${state.videos.length} na stronie • Łącznie: ${data.total || state.videos.length} w ulubionych`;

      if (dom.paginationSection) {
        if (state.lastPage > 1 || state.videos.length > 0) {
          dom.paginationSection.style.display = 'flex';
          renderPagination();
          prefetchNextPage();
        } else {
          dom.paginationSection.style.display = 'none';
        }
      }

      if (state.videos.length === 0 && dom.videoGrid) {
        dom.videoGrid.innerHTML = `
          <div class="empty-state" style="grid-column: 1 / -1;">
            <div class="empty-state-icon"><i class="fa-regular fa-heart"></i></div>
            <h3>Brak ulubionych filmów</h3>
            <p>Kliknij serduszko na dowolnym wideo lub kliknij "Synchronizuj z Archivebate" w Panelu Konta.</p>
          </div>
        `;
      }
    } catch (e) {
      if (generation !== state.viewGeneration || e?.code === 'cancelled') return;
      triggerToast(e?.message || 'Błąd ładowania ulubionych', 'error');
    }
  }

  async function loadHistory(page = 1) {
    const generation = beginViewRequest();
    const controller = state.viewController;
    state.mode = 'history';
    state.currentPage = page;
    if (global.tabManager && typeof global.tabManager.updateActiveTabInfo === 'function') {
      global.tabManager.updateActiveTabInfo('Historia', 'fa-solid fa-clock-rotate-left');
    }

    showSkeletons();
    if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
    if (dom.tagsSection) dom.tagsSection.style.display = 'none';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
    if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
    if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'flex';
    if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
    if (dom.pageJumpInput) dom.pageJumpInput.value = page;

    try {
      const data = await api().getJSON(`/api/account/history?page=${page}&per_page=280`, { timeoutMs: 12000, signal: controller.signal });
      if (generation !== state.viewGeneration) return;
      state.videos = data.videos || [];
      state.lastPage = data.last_page || 1;

      if (dom.viewTitle) dom.viewTitle.innerText = `🕒 Ostatnio Oglądane (Strona ${page} z ${state.lastPage})`;
      renderVideoGrid(state.videos);
      scheduleThumbnailWarmup(state.videos);
      if (dom.videoCount) dom.videoCount.innerText = `${state.videos.length} na stronie • Łącznie: ${data.total || state.videos.length} w historii`;

      if (dom.paginationSection) {
        if (state.lastPage > 1 || state.videos.length > 0) {
          dom.paginationSection.style.display = 'flex';
          renderPagination();
          prefetchNextPage();
        } else {
          dom.paginationSection.style.display = 'none';
        }
      }

      if (state.videos.length === 0 && dom.videoGrid) {
        dom.videoGrid.innerHTML = `
          <div class="empty-state" style="grid-column: 1 / -1;">
            <div class="empty-state-icon"><i class="fa-solid fa-clock-rotate-left"></i></div>
            <h3>Historia odtwarzania jest pusta</h3>
            <p>Oglądane filmy pojawią się w tym miejscu automatycznie.</p>
          </div>
        `;
      }
    } catch (e) {
      if (generation !== state.viewGeneration || e?.code === 'cancelled') return;
      triggerToast(e?.message || 'Błąd ładowania historii', 'error');
    }
  }

  async function loadFollowing(page = 1) {
    const generation = beginViewRequest();
    const controller = state.viewController;
    state.mode = 'following';
    state.currentPage = page;
    if (global.tabManager && typeof global.tabManager.updateActiveTabInfo === 'function') {
      global.tabManager.updateActiveTabInfo('Obserwowane', 'fa-solid fa-user-group');
    }

    showSkeletons();
    if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
    if (dom.tagsSection) dom.tagsSection.style.display = 'none';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
    if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
    if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'flex';
    if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
    if (dom.pageJumpInput) dom.pageJumpInput.value = page;

    try {
      const data = await api().getJSON(`/api/account/following?page=${page}&per_page=280`, { timeoutMs: 12000, signal: controller.signal });
      if (generation !== state.viewGeneration) return;
      state.videos = data.videos || [];
      state.lastPage = data.last_page || 1;

      if (dom.viewTitle) dom.viewTitle.innerText = `👥 Filmy z Obserwowanych (Strona ${page} z ${state.lastPage})`;
      renderVideoGrid(state.videos);
      scheduleThumbnailWarmup(state.videos);
      if (dom.videoCount) dom.videoCount.innerText = `${data.total || state.videos.length} wideo`;

      if (dom.paginationSection) {
        if (state.lastPage > 1 || state.videos.length > 0) {
          dom.paginationSection.style.display = 'flex';
          renderPagination();
        } else {
          dom.paginationSection.style.display = 'none';
        }
      }

      if (state.videos.length === 0 && dom.videoGrid) {
        dom.videoGrid.innerHTML = `
          <div class="empty-state" style="grid-column: 1 / -1;">
            <div class="empty-state-icon"><i class="fa-solid fa-user-group"></i></div>
            <h3>Brak filmów z obserwowanych</h3>
            <p>Użyj przycisku 'Synchronizuj z Archivebate' w Panelu Konta, aby pobrać listę z serwisu.</p>
          </div>
        `;
      }
    } catch (e) {
      if (generation !== state.viewGeneration || e?.code === 'cancelled') return;
      triggerToast(e?.message || 'Błąd ładowania obserwowanych', 'error');
    }
  }

  function pushNavigationHistory() {
    if (!state.navHistory) state.navHistory = [];
    if (state.mode !== 'model') {
      state.navHistory.push({
        mode: state.mode,
        currentPage: state.currentPage,
        lastPage: state.lastPage,
        feedSnapshotId: state.feedSnapshotId,
        feedSpecKey: state.feedSpecKey,
        currentQuery: state.currentQuery,
        sourceFilter: state.sourceFilter,
        authorFilter: state.authorFilter,
        groupByAuthor: state.groupByAuthor,
        currentModel: state.currentModel,
        videos: Array.isArray(state.videos) ? [...state.videos] : [],
        viewTitle: dom.viewTitle ? dom.viewTitle.innerText : '',
        videoCount: dom.videoCount ? dom.videoCount.innerText : '',
        totalCatalogVideos: state.totalCatalogVideos,
        scrollY: (typeof window !== 'undefined' ? window.scrollY : 0) || (typeof document !== 'undefined' ? document.documentElement.scrollTop : 0) || 0
      });
      if (state.navHistory.length > 20) state.navHistory.shift();
    }
  }

  function updateBackButtonUI() {
    if (!dom.navBackBtn) return;
    if (state.mode === 'model' || (state.navHistory && state.navHistory.length > 0)) {
      dom.navBackBtn.style.display = 'inline-flex';
      const last = state.navHistory && state.navHistory.length > 0 ? state.navHistory[state.navHistory.length - 1] : null;
      if (last) {
        const targetName = last.mode === 'home'
          ? `Strona główna (str. ${last.currentPage})`
          : (last.mode === 'search' ? `${last.currentQuery} (str. ${last.currentPage})` : 'poprzedni widok');
        dom.navBackBtn.title = `Cofnij do: ${targetName}`;
        dom.navBackBtn.innerHTML = `<i class="fa-solid fa-arrow-left"></i> Cofnij`;
      } else {
        dom.navBackBtn.title = 'Wróć do poprzedniego widoku';
        dom.navBackBtn.innerHTML = `<i class="fa-solid fa-arrow-left"></i> Cofnij`;
      }
    } else {
      dom.navBackBtn.style.display = 'none';
    }
  }

  function goBack() {
    beginViewRequest();
    if (!state.navHistory || state.navHistory.length === 0) {
      resetToHome();
      return;
    }
    const prev = state.navHistory.pop();

    state.mode = prev.mode;
    state.currentPage = prev.currentPage;
    state.lastPage = prev.lastPage;
    state.currentModel = prev.currentModel;
    state.currentQuery = prev.currentQuery;
    state.videos = prev.videos;
    state.totalCatalogVideos = prev.totalCatalogVideos;

    updateBackButtonUI();

    if (dom.viewTitle && prev.viewTitle) dom.viewTitle.innerText = prev.viewTitle;
    if (dom.videoCount && prev.videoCount) dom.videoCount.innerText = prev.videoCount;
    if (dom.pageJumpInput) dom.pageJumpInput.value = prev.currentPage;

    if (prev.mode === 'home') {
      if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
      if (dom.tagsSection) dom.tagsSection.style.display = 'block';
      if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'grid';
      if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
      if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'none';
      if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
      if (dom.searchInput) dom.searchInput.value = '';
      if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = 'none';
      setActiveNavTab(dom.navHomeBtn);
      renderVideoGrid(prev.videos);
      renderPagination();
    } else if (prev.mode === 'search') {
      if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
      if (dom.tagsSection) dom.tagsSection.style.display = 'block';
      if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
      if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
      if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'flex';
      if (dom.searchInput) dom.searchInput.value = prev.currentQuery || '';
      if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = dom.searchInput.value ? 'flex' : 'none';
      setActiveNavTab(null);
      renderVideoGrid(prev.videos);
      renderPagination();
    } else if (prev.mode === 'model') {
      loadModelVideos(prev.currentModel, prev.currentPage);
      return;
    } else {
      renderVideoGrid(prev.videos);
      renderPagination();
    }

    if (prev.scrollY && typeof window !== 'undefined') {
      window.scrollTo({ top: prev.scrollY, behavior: 'instant' });
    }
  }

  async function loadModelVideos(username, page = 1) {
    const generation = beginViewRequest();
    const controller = state.viewController;

    pushNavigationHistory();
    state.isLoading = true;
    state.mode = 'model';
    state.currentModel = username;
    state.currentPage = page;
    state.lastPage = 20;
    if (global.tabManager && typeof global.tabManager.updateActiveTabInfo === 'function') {
      global.tabManager.updateActiveTabInfo(username, 'fa-solid fa-circle-user');
    }

    showSkeletons();
    if (dom.accountPanelView) dom.accountPanelView.style.display = 'none';
    if (dom.tagsSection) dom.tagsSection.style.display = 'block';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
    if (dom.contentHeader) dom.contentHeader.style.display = 'flex';
    if (dom.viewTitle) dom.viewTitle.innerText = `Filmy modelki: ${username} (Strona ${page})`;
    updateBackButtonUI();
    if (dom.resetFilterBtn) dom.resetFilterBtn.style.display = 'flex';
    if (dom.matchedProfiles) dom.matchedProfiles.style.display = 'none';
    if (dom.paginationSection) dom.paginationSection.style.display = 'flex';
    if (dom.pageJumpInput) dom.pageJumpInput.value = page;

    try {
      const data = await api().getJSON(`/api/model/${encodeURIComponent(username)}?page=${page}`, { timeoutMs: 15000, signal: controller.signal });
      if (generation !== state.viewGeneration) return;
      state.videos = data.videos || [];

      renderVideoGrid(state.videos);
      scheduleThumbnailWarmup(state.videos);
      if (dom.videoCount) dom.videoCount.innerText = `${state.videos.length} na stronie • Modelka: ${username} • 5.5M+ w serwisach`;

      if (state.videos.length === 0 && page > 1) {
        state.lastPage = page - 1;
      }
      renderPagination();
      prefetchNextPage();
    } catch (e) {
      if (generation !== state.viewGeneration || e?.code === 'cancelled') return;
      triggerToast(e?.message || `Błąd ładowania filmów dla ${username}`, 'error');
    } finally {
      if (generation === state.viewGeneration) state.isLoading = false;
    }
  }

  function resetToHome() {
    state.navHistory = [];
    updateBackButtonUI();
    document.querySelectorAll('.tag-pill').forEach(p => p.classList.remove('active'));
    setActiveNavTab(dom.navHomeBtn);
    loadHomeVideos(1);
  }

  const ArchivebateVideoViews = {
    beginViewRequest,
    showSkeletons,
    setFeedRefreshingIndicator,
    prefetchNextPage,
    loadHomeVideos,
    loadFavorites,
    loadHistory,
    loadFollowing,
    loadModelVideos,
    resetToHome,
    pushNavigationHistory,
    updateBackButtonUI,
    goBack
  };

  global.ArchivebateVideoViews = ArchivebateVideoViews;
  if (!global.loadHomeVideos) global.loadHomeVideos = loadHomeVideos;
  if (!global.loadFavorites) global.loadFavorites = loadFavorites;
  if (!global.loadHistory) global.loadHistory = loadHistory;
  if (!global.loadFollowing) global.loadFollowing = loadFollowing;
  if (!global.loadModelVideos) global.loadModelVideos = loadModelVideos;
  if (!global.resetToHome) global.resetToHome = resetToHome;
  if (!global.showSkeletons) global.showSkeletons = showSkeletons;
  if (!global.beginViewRequest) global.beginViewRequest = beginViewRequest;
  if (!global.setFeedRefreshingIndicator) global.setFeedRefreshingIndicator = setFeedRefreshingIndicator;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoViews;
  }
})(typeof window !== 'undefined' ? window : globalThis);
