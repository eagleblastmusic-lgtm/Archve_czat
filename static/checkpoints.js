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
    const checkpoint = {
      videoId: String(v.id),
      videoTitle: v.username || 'Film',
      videoDate: v.date || '',
      page: state.currentPage || 1,
      mode: state.mode || 'home',
      query: state.currentQuery || '',
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
      const bVidId = badge.dataset.videoId;
      if (cp && bVidId === cp.videoId) {
        badge.classList.add('is-checkpoint');
        badge.innerHTML = `<i class="fa-solid fa-location-dot"></i> Checkpoint`;
        badge.title = `Ten film to Twój aktywny punkt kontrolny (Strona ${cp.page})`;
      } else {
        badge.classList.remove('is-checkpoint');
        if (badge.dataset.origDate) {
          badge.innerHTML = `<i class="fa-regular fa-calendar-days"></i> ${badge.dataset.origDate}`;
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

    if (cp.mode === 'search' && cp.query) {
      dom.searchInput.value = cp.query;
      dom.clearSearchBtn.style.display = 'flex';
      performSearch(cp.query, cp.page);
    } else if (cp.mode === 'model' && cp.query) {
      loadModelVideos(cp.query, cp.page);
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
      const targetCard = dom.videoGrid.querySelector(`[data-video-id="${state.targetCheckpointId}"]`);
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
