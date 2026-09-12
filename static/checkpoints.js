(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let showToast;
  let setActiveNavTab;
  let performSearch;
  let loadModelVideos;
  let loadFavorites;
  let loadHistory;
  let loadFollowing;
  let loadHomeVideos;

  function init(dependencies = {}) {
    showToast = dependencies.showToast;
    setActiveNavTab = dependencies.setActiveNavTab;
    performSearch = dependencies.performSearch;
    loadModelVideos = dependencies.loadModelVideos;
    loadFavorites = dependencies.loadFavorites;
    loadHistory = dependencies.loadHistory;
    loadFollowing = dependencies.loadFollowing;
    loadHomeVideos = dependencies.loadHomeVideos;
  }

  function getSavedCheckpoint() {
    try {
      const raw = localStorage.getItem('archivebate_checkpoint');
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function setCheckpoint(v) {
    const source = String(v.source || (String(v.id || '').startsWith('cw_') ? 'camwhores' : 'archivebate'));
    const providerId = String(v.provider_id || v.id || '').replace(/^cw_/, '');
    const checkpoint = {
      videoId: providerId,
      source,
      videoKey: `${source}:id:${providerId}`,
      videoTitle: String(v.username || 'Film'),
      videoDate: String(v.date || ''),
      page: state.currentPage || 1,
      mode: state.mode || 'home',
      query: state.currentQuery || '',
      currentModel: state.currentModel || '',
      searchScope: dom.searchScopeSelect?.value || 'online',
      sourceFilter: state.sourceFilter || 'all',
      authorFilter: state.authorFilter || 'all',
      groupByAuthor: Boolean(state.groupByAuthor),
      catalogRevision: Number(state.catalogRevision || state.currentRevision || 0) || 0,
      snapshotId: state.feedSnapshotId || null,
      timestamp: Date.now()
    };
    localStorage.setItem('archivebate_checkpoint', JSON.stringify(checkpoint));
    updateUI();
    showToast(`📍 Zapisano checkpoint: ${v.username} (Strona ${checkpoint.page})`, 'success');
  }

  function updateUI() {
    const cp = getSavedCheckpoint();
    if (cp) {
      if (dom.headerCheckpointBtn) {
        dom.headerCheckpointBtn.style.display = 'inline-flex';
        if (dom.checkpointText) {
          dom.checkpointText.innerText = `(Strona ${cp.page} • ${cp.videoTitle})`;
        }
      }
      if (dom.navCheckpointBtn) {
        dom.navCheckpointBtn.style.display = 'inline-flex';
        dom.navCheckpointBtn.title = `Przejdź do: ${cp.videoTitle} (Strona ${cp.page})`;
      }
    } else {
      if (dom.headerCheckpointBtn) dom.headerCheckpointBtn.style.display = 'none';
      if (dom.navCheckpointBtn) dom.navCheckpointBtn.style.display = 'none';
    }

    document.querySelectorAll('.card-date-badge').forEach(badge => {
      const card = badge.closest('.video-card');
      const source = card?.dataset?.source || 'archivebate';
      const bVidId = String(badge.dataset.videoId || '').replace(/^cw_/, '');
      const currentKey = `${source}:id:${bVidId}`;
      if (cp && (currentKey === cp.videoKey || (!cp.videoKey && source === (cp.source || 'archivebate') && bVidId === cp.videoId))) {
        badge.classList.add('is-checkpoint');
        badge.replaceChildren();
        const icon = document.createElement('i');
        icon.className = 'fa-solid fa-location-dot';
        icon.setAttribute('aria-hidden', 'true');
        badge.append(icon, document.createTextNode(' Checkpoint'));
        badge.title = `Ten film to Twój aktywny punkt kontrolny (Strona ${cp.page})`;
      } else {
        badge.classList.remove('is-checkpoint');
        if (badge.dataset.origDate) {
          badge.replaceChildren();
          const icon = document.createElement('i');
          icon.className = 'fa-regular fa-calendar-days';
          icon.setAttribute('aria-hidden', 'true');
          badge.append(icon, document.createTextNode(` ${badge.dataset.origDate}`));
          badge.title = 'Kliknij na datę, aby ustawić punkt kontrolny (checkpoint)';
        }
      }
    });
  }

  function navigate() {
    const cp = getSavedCheckpoint();
    if (!cp) {
      showToast('Brak zapisanego punktu kontrolnego', 'info');
      return;
    }

    state.targetCheckpointId = cp.videoId;
    state.targetCheckpointKey = cp.videoKey || `${cp.source || 'archivebate'}:id:${cp.videoId}`;
    state.sourceFilter = cp.sourceFilter || 'all';
    state.authorFilter = cp.authorFilter || 'all';
    state.groupByAuthor = Boolean(cp.groupByAuthor);
    state.catalogRevision = Number(cp.catalogRevision) || null;
    if (dom.searchScopeSelect) dom.searchScopeSelect.value = cp.searchScope || 'online';
    global.ArchivebateFilters?.updateCamwhoresToggleUI?.();
    global.ArchivebateFilters?.updateAuthorFilterUI?.();
    global.ArchivebateFilters?.updateGroupToggleUI?.();
    state.checkpointContext = {
      catalogRevision: Number(cp.catalogRevision) || 0,
      snapshotId: cp.snapshotId || null,
      sourceFilter: cp.sourceFilter || 'all',
      authorFilter: cp.authorFilter || 'all',
      groupByAuthor: Boolean(cp.groupByAuthor)
    };

    if (cp.mode === 'search' && cp.query) {
      dom.searchInput.value = cp.query;
      dom.clearSearchBtn.style.display = 'flex';
      performSearch(cp.query, cp.page);
    } else if (cp.mode === 'model' && (cp.currentModel || cp.query)) {
      loadModelVideos(cp.currentModel || cp.query, cp.page);
    } else if (cp.mode === 'favorites') {
      setActiveNavTab(dom.navFavoritesBtn);
      loadFavorites(cp.page);
    } else if (cp.mode === 'history') {
      setActiveNavTab(dom.navHistoryBtn);
      loadHistory(cp.page);
    } else if (cp.mode === 'following') {
      setActiveNavTab(dom.navFollowingBtn);
      loadFollowing(cp.page);
    } else {
      setActiveNavTab(dom.navHomeBtn);
      loadHomeVideos(cp.page);
    }
  }

  function checkAndHighlight() {
    if (state.targetCheckpointId) {
      const targetCard = Array.from(dom.videoGrid.querySelectorAll('.video-card')).find(card => {
        const source = card.dataset?.source || 'archivebate';
        const id = String(card.dataset?.videoId || '').replace(/^cw_/, '');
        return `${source}:id:${id}` === state.targetCheckpointKey;
      });
      if (targetCard) {
        targetCard.classList.add('checkpoint-highlight');
        setTimeout(() => {
          targetCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 150);
        showToast('📍 Dotarto do zapisanego punktu kontrolnego!', 'success');
        state.targetCheckpointId = null;
      }
    }
  }

  global.ArchivebateCheckpoints = {
    init,
    getSavedCheckpoint,
    setCheckpoint,
    updateUI,
    navigate,
    checkAndHighlight
  };
})(typeof window !== 'undefined' ? window : globalThis);
