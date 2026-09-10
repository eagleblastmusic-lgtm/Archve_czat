/**
 * Archivebate - Application Events Module
 * Obsługa zdarzeń globalnych, delegowanych zdarzeń siatki filmów,
 * nawigacji, wyszukiwania, paginacji i scrollu.
 */
(function (global) {
  'use strict';

  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);

  const stateProxy = new Proxy({}, {
    get(_, prop) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.state && prop in g.ArchivebateAppContext.state) {
        return g.ArchivebateAppContext.state[prop];
      }
      if (g.state && prop in g.state) {
        return g.state[prop];
      }
      return undefined;
    },
    set(_, prop, val) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.state) {
        g.ArchivebateAppContext.state[prop] = val;
      }
      if (g.state) {
        g.state[prop] = val;
      }
      return true;
    }
  });

  const domProxy = new Proxy({}, {
    get(_, prop) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.dom && prop in g.ArchivebateAppContext.dom) {
        return g.ArchivebateAppContext.dom[prop];
      }
      if (g.dom && prop in g.dom) {
        return g.dom[prop];
      }
      return undefined;
    },
    set(_, prop, val) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.dom) {
        g.ArchivebateAppContext.dom[prop] = val;
      }
      if (g.dom) {
        g.dom[prop] = val;
      }
      return true;
    }
  });

  const state = stateProxy;
  const dom = domProxy;

  let toggleFavoriteVideo = (v, btn) => (g.ArchivebateFavorites ? g.ArchivebateFavorites.toggleVideo(v, btn) : undefined);
  let setCheckpoint = (v) => (g.ArchivebateCheckpoints ? g.ArchivebateCheckpoints.setCheckpoint(v) : undefined);
  let navigateToCheckpoint = () => (g.ArchivebateCheckpoints ? g.ArchivebateCheckpoints.navigate() : undefined);
  let performSearch = (q, p) => (g.ArchivebateSearchResults ? g.ArchivebateSearchResults.performSearch(q, p) : undefined);
  let loadModelVideos = (u, p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadModelVideos(u, p) : undefined);
  let blockModel = (u) => (g.ArchivebateBlockedModels ? g.ArchivebateBlockedModels.block(u) : undefined);
  let openVideoModal = (v) => (g.ArchivebateVideoModal ? g.ArchivebateVideoModal.openVideoModal(v) : undefined);
  let closeModal = () => (g.ArchivebateVideoModal ? g.ArchivebateVideoModal.closeModal() : undefined);
  let showBlockedModelsManager = () => (g.ArchivebateBlockedModels ? g.ArchivebateBlockedModels.showManager() : undefined);
  let loadHomeVideos = (p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadHomeVideos(p) : undefined);
  let loadFavorites = (p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadFavorites(p) : undefined);
  let loadHistory = (p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadHistory(p) : undefined);
  let loadFollowing = (p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadFollowing(p) : undefined);
  let resetToHome = () => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.resetToHome() : undefined);
  let changePage = (p) => (g.ArchivebatePagination ? g.ArchivebatePagination.changePage(p) : undefined);
  let updateModalFavButton = (fav) => (g.ArchivebateFavorites ? g.ArchivebateFavorites.updateModalButton(fav) : undefined);
  let showToast = (msg, type) => (g.ArchivebateToast ? g.ArchivebateToast.show(msg, type) : console.log(msg));

  function init(dependencies = {}) {
    if (dependencies.toggleFavoriteVideo) toggleFavoriteVideo = dependencies.toggleFavoriteVideo;
    if (dependencies.setCheckpoint) setCheckpoint = dependencies.setCheckpoint;
    if (dependencies.navigateToCheckpoint) navigateToCheckpoint = dependencies.navigateToCheckpoint;
    if (dependencies.performSearch) performSearch = dependencies.performSearch;
    if (dependencies.loadModelVideos) loadModelVideos = dependencies.loadModelVideos;
    if (dependencies.blockModel) blockModel = dependencies.blockModel;
    if (dependencies.openVideoModal) openVideoModal = dependencies.openVideoModal;
    if (dependencies.closeModal) closeModal = dependencies.closeModal;
    if (dependencies.showBlockedModelsManager) showBlockedModelsManager = dependencies.showBlockedModelsManager;
    if (dependencies.loadHomeVideos) loadHomeVideos = dependencies.loadHomeVideos;
    if (dependencies.loadFavorites) loadFavorites = dependencies.loadFavorites;
    if (dependencies.loadHistory) loadHistory = dependencies.loadHistory;
    if (dependencies.loadFollowing) loadFollowing = dependencies.loadFollowing;
    if (dependencies.resetToHome) resetToHome = dependencies.resetToHome;
    if (dependencies.changePage) changePage = dependencies.changePage;
    if (dependencies.updateModalFavButton) updateModalFavButton = dependencies.updateModalFavButton;
    if (dependencies.showToast) showToast = dependencies.showToast;
  }

  function setActiveNavTab(tabBtn) {
    if (typeof document !== 'undefined') {
      document.querySelectorAll('.nav-link').forEach(btn => btn.classList.remove('active'));
      if (tabBtn) tabBtn.classList.add('active');
    }
  }

  // ============================================================
  // DELEGOWANA OBSŁUGA ZDARZEŃ SIATKI WIDEO (BŁYSKAWICZNY DOM)
  // ============================================================
  function handleGridClick(e) {
    const card = e.target.closest('.video-card');
    if (!card) return;

    const v = card._videoData || (state.videoById && state.videoById.get(card.dataset.videoId));
    if (!v) return;

    // 1. Dodawanie/usuwanie z ulubionych
    const favBtn = e.target.closest('.card-fav-btn');
    if (favBtn) {
      e.stopPropagation();
      toggleFavoriteVideo(v, favBtn);
      return;
    }

    // 2. Kliknięcie na datę -> punkt kontrolny (checkpoint)
    const dateBadge = e.target.closest('.card-date-badge');
    if (dateBadge) {
      e.stopPropagation();
      e.preventDefault();
      setCheckpoint(v);
      return;
    }

    // 3. Kliknięcie w tag na kafelku
    const tagBadge = e.target.closest('.card-tag-badge');
    if (tagBadge) {
      e.stopPropagation();
      e.preventDefault();
      const clickedTag = tagBadge.dataset.tag;
      if (clickedTag) {
        if (dom.searchInput) dom.searchInput.value = `#${clickedTag}`;
        if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = 'flex';
        setActiveNavTab(null);
        performSearch(clickedTag, 1);
      }
      return;
    }

    // 4. Kliknięcie w profil modelki
    const profileLink = e.target.closest('.model-profile-link, .profile-btn');
    if (profileLink) {
      e.preventDefault();
      e.stopPropagation();
      const modelName = profileLink.dataset.username || v.username;
      if (modelName) loadModelVideos(modelName, 1);
      return;
    }

    // 5. Zablokowanie modelki
    const blockBtn = e.target.closest('.block-model-btn');
    if (blockBtn) {
      e.stopPropagation();
      e.preventDefault();
      blockModel(blockBtn.dataset.username || v.username);
      return;
    }

    // 6. Odtwarzanie filmu (kliknięcie w przycisk 'Odtwórz' lub w miniaturkę)
    const playBtn = e.target.closest('.play-btn');
    const thumbWrapper = e.target.closest('.thumbnail-wrapper');
    if (playBtn || thumbWrapper) {
      if (e.button === 0) {
        e.stopPropagation();
        openVideoModal(v);
      }
    }
  }

  function handleGridAuxClick(e) {
    if (e.button !== 1) return; // tylko środkowy przycisk myszy
    const card = e.target.closest('.video-card');
    if (!card) return;
    const v = card._videoData || (state.videoById && state.videoById.get(card.dataset.videoId));
    if (!v || !v.id) return;

    const playBtn = e.target.closest('.play-btn');
    const thumbWrapper = e.target.closest('.thumbnail-wrapper');
    if (playBtn || thumbWrapper) {
      e.preventDefault();
      e.stopPropagation();
      try {
        sessionStorage.setItem('archivebate_bootstrap_' + v.id, JSON.stringify({
          id: v.id,
          username: v.username,
          thumbnail: v.poster_proxy || v.thumbnail_proxy || v.poster,
          date: v.date,
          duration: v.duration,
          platform: v.platform,
          url: v.url
        }));
      } catch (_) {}
      window.open(`/watch/${v.id}`, '_blank');
    }
  }

  function setupEvents() {
    // Checkpoint navigators
    if (dom.headerCheckpointBtn) dom.headerCheckpointBtn.addEventListener('click', navigateToCheckpoint);
    if (dom.navCheckpointBtn) dom.navCheckpointBtn.addEventListener('click', navigateToCheckpoint);

    // Delegowane zdarzenia siatki kafelków (wysoka wydajność, brak tysięcy listenerów na kartach)
    if (dom.videoGrid) {
      dom.videoGrid.addEventListener('click', handleGridClick);
      dom.videoGrid.addEventListener('auxclick', handleGridAuxClick);
    }

    // Inicjalizacja autouzupełniania wyszukiwarki
    const SearchAutocomplete = g.ArchivebateSearchAutocomplete;
    if (SearchAutocomplete && typeof SearchAutocomplete.init === 'function') {
      SearchAutocomplete.init({
        setActiveNavTab,
        performSearch
      });
    }

    // Nawigacja zakładek
    if (dom.navHomeBtn) {
      dom.navHomeBtn.addEventListener('click', () => {
        setActiveNavTab(dom.navHomeBtn);
        loadHomeVideos(1);
      });
    }

    if (dom.navFavoritesBtn) {
      dom.navFavoritesBtn.addEventListener('click', () => {
        setActiveNavTab(dom.navFavoritesBtn);
        loadFavorites(1);
      });
    }

    if (dom.navHistoryBtn) {
      dom.navHistoryBtn.addEventListener('click', () => {
        setActiveNavTab(dom.navHistoryBtn);
        loadHistory(1);
      });
    }

    if (dom.navFollowingBtn) {
      dom.navFollowingBtn.addEventListener('click', () => {
        setActiveNavTab(dom.navFollowingBtn);
        loadFollowing(1);
      });
    }

    // Przyciski w Panelu Konta
    if (dom.statBtnFavs) {
      dom.statBtnFavs.addEventListener('click', () => {
        setActiveNavTab(dom.navFavoritesBtn);
        loadFavorites(1);
      });
    }

    if (dom.statBtnHist) {
      dom.statBtnHist.addEventListener('click', () => {
        setActiveNavTab(dom.navHistoryBtn);
        loadHistory(1);
      });
    }

    if (dom.statBtnFoll) {
      dom.statBtnFoll.addEventListener('click', () => {
        setActiveNavTab(dom.navFollowingBtn);
        loadFollowing(1);
      });
    }

    if (dom.statBtnBlocked) {
      dom.statBtnBlocked.addEventListener('click', showBlockedModelsManager);
    }

    // Wyszukiwarka z debounce i natychmiastowym klawiszem Enter
    let debounceTimeout = null;
    if (dom.searchInput) {
      dom.searchInput.addEventListener('input', (e) => {
        const val = e.target.value.trim();
        if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = val ? 'flex' : 'none';

        clearTimeout(debounceTimeout);
        debounceTimeout = setTimeout(() => {
          if (val.length >= 2) {
            setActiveNavTab(null);
            performSearch(val, 1);
          } else if (val.length === 0) {
            resetToHome();
          }
        }, 450);
      });

      dom.searchInput.addEventListener('keydown', (e) => {
        const isAutoOpen = SearchAutocomplete && typeof SearchAutocomplete.isOpen === 'function' && SearchAutocomplete.isOpen();
        const activeIdx = SearchAutocomplete ? SearchAutocomplete.activeIdx : -1;
        if (e.key === 'Enter' && (!isAutoOpen || activeIdx === -1)) {
          clearTimeout(debounceTimeout);
          const val = dom.searchInput.value.trim();
          if (SearchAutocomplete && typeof SearchAutocomplete.hide === 'function') SearchAutocomplete.hide();
          if (val.length >= 2) {
            setActiveNavTab(null);
            performSearch(val, 1);
          } else if (val.length === 0) {
            resetToHome();
          }
        }
      });
    }

    if (dom.clearSearchBtn) {
      dom.clearSearchBtn.addEventListener('click', () => {
        if (dom.searchInput) dom.searchInput.value = '';
        dom.clearSearchBtn.style.display = 'none';
        if (SearchAutocomplete && typeof SearchAutocomplete.hide === 'function') SearchAutocomplete.hide();
        resetToHome();
      });
    }

    if (dom.resetFilterBtn) dom.resetFilterBtn.addEventListener('click', resetToHome);
    if (dom.logoBtn) dom.logoBtn.addEventListener('click', resetToHome);

    // Paginacja
    const handlePrev = () => {
      if (state.currentPage > 1) {
        changePage(state.currentPage - 1);
      }
    };
    const handleNext = () => {
      if (state.currentPage < state.lastPage) {
        changePage(state.currentPage + 1);
      }
    };
    const handleJump = (inputEl) => {
      if (inputEl) {
        const pageVal = parseInt(inputEl.value, 10);
        if (pageVal >= 1) {
          changePage(pageVal);
        }
      }
    };

    if (dom.prevPageBtn) dom.prevPageBtn.addEventListener('click', handlePrev);
    if (dom.prevPageBtnTop) dom.prevPageBtnTop.addEventListener('click', handlePrev);

    if (dom.nextPageBtn) dom.nextPageBtn.addEventListener('click', handleNext);
    if (dom.nextPageBtnTop) dom.nextPageBtnTop.addEventListener('click', handleNext);

    if (dom.pageJumpBtn) dom.pageJumpBtn.addEventListener('click', () => handleJump(dom.pageJumpInput));
    if (dom.pageJumpBtnTop) dom.pageJumpBtnTop.addEventListener('click', () => handleJump(dom.pageJumpInputTop));

    if (dom.pageJumpInput) {
      dom.pageJumpInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleJump(dom.pageJumpInput);
      });
    }
    if (dom.pageJumpInputTop) {
      dom.pageJumpInputTop.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleJump(dom.pageJumpInputTop);
      });
    }

    // Modal events
    if (dom.modalCloseBtn) dom.modalCloseBtn.addEventListener('click', closeModal);
    if (dom.videoModal) {
      dom.videoModal.addEventListener('click', (e) => {
        if (e.target === dom.videoModal) closeModal();
      });
    }

    if (typeof document !== 'undefined') {
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && dom.videoModal && dom.videoModal.classList.contains('active')) {
          closeModal();
        }
      });
    }

    if (dom.modalFavBtn) {
      dom.modalFavBtn.addEventListener('click', async () => {
        if (!state.currentVideoDetails) return;
        const isFav = await toggleFavoriteVideo(state.currentVideoDetails);
        updateModalFavButton(isFav);
      });
    }

    // Automatyczny przeskok do kolejnej strony po zjechaniu na sam dół i kolejnym scrollu
    if (typeof window !== 'undefined') {
      let lastScrollJumpTime = 0;
      window.addEventListener('wheel', (e) => {
        if (e.deltaY <= 0) return;
        if (state.isLoading) return;
        if (dom.videoModal && dom.videoModal.classList.contains('active')) return;

        const scrollPos = window.innerHeight + window.scrollY;
        const maxScroll = document.documentElement.scrollHeight;
        const atBottom = scrollPos >= (maxScroll - 50);

        if (atBottom && state.currentPage < (state.lastPage || 100)) {
          const now = Date.now();
          if (now - lastScrollJumpTime > 1200) {
            lastScrollJumpTime = now;
            showToast(`Przeskakiwanie do strony ${state.currentPage + 1}...`, 'info');
            changePage(state.currentPage + 1);
          }
        }
      }, { passive: true });

      let touchStartY = 0;
      window.addEventListener('touchstart', (e) => {
        if (e.touches && e.touches[0]) {
          touchStartY = e.touches[0].clientY;
        }
      }, { passive: true });

      window.addEventListener('touchend', (e) => {
        if (e.changedTouches && e.changedTouches[0]) {
          const deltaY = touchStartY - e.changedTouches[0].clientY;
          if (deltaY > 50) {
            const atBottom = (window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 50);
            if (atBottom && !state.isLoading && state.currentPage < (state.lastPage || 100)) {
              const now = Date.now();
              if (now - lastScrollJumpTime > 1200) {
                lastScrollJumpTime = now;
                showToast(`Przeskakiwanie do strony ${state.currentPage + 1}...`, 'info');
                changePage(state.currentPage + 1);
              }
            }
          }
        }
      }, { passive: true });
    }
  }

  const moduleExports = {
    init,
    setActiveNavTab,
    handleGridClick,
    handleGridAuxClick,
    setupEvents
  };

  g.ArchivebateAppEvents = moduleExports;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = moduleExports;
  }
})(typeof window !== 'undefined' ? window : global);
