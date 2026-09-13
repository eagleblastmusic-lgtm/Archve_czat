(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let showToast;
  const pendingMutations = new Set();

  function canonicalVideo(video, buttonEl = null) {
    if (!video || typeof video !== 'object') return null;

    const card = buttonEl?.closest?.('.video-card') || null;
    const cardSource = String(card?.dataset?.source || '').trim().toLowerCase();
    const cardId = String(card?.dataset?.videoId || '').trim();
    const rawId = String(video.provider_id || video.id || cardId || '').trim();
    const platform = String(video.platform || '').trim().toLowerCase();
    const url = String(video.url || '').trim().toLowerCase();

    let source = String(video.source || cardSource || '').trim().toLowerCase();
    if (!source) {
      if (rawId.toLowerCase().startsWith('cw_') || platform.includes('camwhores') || url.includes('camwhores')) {
        source = 'camwhores';
      } else if (platform.includes('archive') || url.includes('archivebate')) {
        source = 'archivebate';
      }
    }

    if (source !== 'archivebate' && source !== 'camwhores') return null;

    const providerId = rawId.replace(/^cw_/i, '').trim();
    if (!providerId) return null;

    return {
      ...video,
      source,
      provider_id: providerId,
      id: source === 'camwhores' ? `cw_${providerId}` : providerId
    };
  }

  function videoKey(video, buttonEl = null) {
    const canonical = canonicalVideo(video, buttonEl);
    return canonical ? `${canonical.source}:id:${canonical.provider_id}` : null;
  }

  function dockMainNavigation() {
    if (typeof document === 'undefined') return;
    const navbar = document.querySelector('.navbar');
    if (!navbar) return;
    const tabs = document.querySelector('.app-tabs-bar');
    const tabsVisible = tabs && typeof global.getComputedStyle === 'function'
      ? global.getComputedStyle(tabs).display !== 'none'
      : Boolean(tabs);
    navbar.style.position = 'sticky';
    navbar.style.top = tabsVisible ? `${tabs.offsetHeight || 38}px` : '0px';
  }

  function init(dependencies = {}) {
    showToast = dependencies.showToast;
    dockMainNavigation();
  }

  function isFavoriteAuthor(username) {
    if (!username) return false;
    const norm = String(username).toLowerCase().trim();
    return state.favoriteAuthors && state.favoriteAuthors.has(norm);
  }

  function updateFavoriteButton(buttonEl, isFav) {
    if (!buttonEl) return;
    buttonEl.setAttribute?.('aria-label', isFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych');
    buttonEl.setAttribute?.('aria-pressed', isFav ? 'true' : 'false');
    if (isFav) {
      buttonEl.classList.add('active');
      buttonEl.innerHTML = '<i class="fa-solid fa-heart"></i>';
    } else {
      buttonEl.classList.remove('active');
      buttonEl.innerHTML = '<i class="fa-regular fa-heart"></i>';
    }
  }

  function applyFavoriteState(video, key, buttonEl, isFav, totalFavorites = null) {
    if (Number.isFinite(totalFavorites)) {
      state.favoritesCount = Math.max(0, Number(totalFavorites));
    }
    if (dom.navFavCount && Number.isFinite(state.favoritesCount)) {
      dom.navFavCount.innerText = state.favoritesCount;
    }
    if (dom.statFavCount && Number.isFinite(state.favoritesCount)) {
      dom.statFavCount.innerText = state.favoritesCount;
    }

    updateFavoriteButton(buttonEl, isFav);

    if (video && typeof video === 'object') video.is_favorite = isFav;
    const vid = (Array.isArray(state.videos) ? state.videos : []).find(v => videoKey(v) === key);
    if (vid) vid.is_favorite = isFav;
    if (state.currentVideoDetails && videoKey(state.currentVideoDetails) === key) {
      state.currentVideoDetails.is_favorite = isFav;
    }
    updateAllAuthorNameColors();
  }

  async function reconcileLocalFavorite(video, key, buttonEl, previousState, error) {
    try {
      const snapshot = await global.ArchivebateAPI.getJSON('/api/account/favorites?page=1&per_page=1000', { timeoutMs: 5000 });
      const videos = Array.isArray(snapshot?.videos) ? snapshot.videos : [];
      const isFav = videos.some(item => videoKey(item) === key);
      const intendedState = !previousState;
      applyFavoriteState(video, key, buttonEl, isFav, Number(snapshot?.total));

      if (isFav === intendedState) {
        showToast?.(
          isFav
            ? 'Dodano do ulubionych lokalnie. Synchronizacja z kontem może jeszcze trwać.'
            : 'Usunięto z ulubionych lokalnie. Synchronizacja z kontem może jeszcze trwać.',
          'warning'
        );
      } else {
        showToast?.(error?.message || 'Błąd aktualizacji ulubionych', 'error');
      }
      return isFav;
    } catch (_) {
      applyFavoriteState(video, key, buttonEl, previousState);
      showToast?.(error?.message || 'Błąd aktualizacji ulubionych', 'error');
      return previousState;
    }
  }

  function updateAllAuthorNameColors() {
    if (typeof document === 'undefined') return;
    document.querySelectorAll('.video-card').forEach(card => {
      const link = card.querySelector('.model-profile-link');
      const u = String(link?.dataset.username || card.dataset.username || '').toLowerCase().trim();
      const vidId = card.dataset.videoId;
      const isModelFav = state.favoriteAuthors && state.favoriteAuthors.has(u);
      const cardKey = videoKey({ source: card.dataset.source, id: vidId });
      const vid = (Array.isArray(state.videos) ? state.videos : []).find(v => videoKey(v) === cardKey);
      const isVidFav = vid ? !!vid.is_favorite : (card.querySelector('.card-fav-btn.active') !== null);
      const shouldHighlight = isModelFav || isVidFav;

      if (link) {
        if (isModelFav) {
          link.classList.add('is-favorite-author');
          if (!link.querySelector('.fav-author-star')) {
            const star = document.createElement('i');
            star.className = 'fa-solid fa-star fav-author-star';
            star.title = 'Masz film tej modelki w ulubionych';
            link.appendChild(star);
          }
        } else {
          link.classList.remove('is-favorite-author');
          const star = link.querySelector('.fav-author-star');
          if (star) star.remove();
        }
      }

      if (shouldHighlight) card.classList.add('is-favorite-card');
      else card.classList.remove('is-favorite-card');
    });

    if (state.currentVideoDetails && dom.modalModelName) {
      const currU = String(state.currentVideoDetails.username || '').toLowerCase().trim();
      const isModelFav = state.favoriteAuthors && state.favoriteAuthors.has(currU);
      const isVidFav = !!state.currentVideoDetails.is_favorite;
      const isFav = isModelFav || isVidFav;
      if (isFav) dom.modalModelName.classList.add('is-favorite-author');
      else dom.modalModelName.classList.remove('is-favorite-author');
      const modalContent = dom.videoModal?.querySelector('.modal-content');
      if (modalContent) {
        if (isFav) modalContent.classList.add('is-favorite-modal');
        else modalContent.classList.remove('is-favorite-modal');
      }
    }
  }

  async function toggleVideo(video, buttonEl = null) {
    const mutationVideo = canonicalVideo(video, buttonEl);
    const key = mutationVideo ? videoKey(mutationVideo) : null;
    if (!key) {
      showToast?.('Nie można ustalić źródła tego filmu. Odśwież widok i spróbuj ponownie.', 'error');
      return Boolean(video?.is_favorite);
    }
    if (pendingMutations.has(key)) return Boolean(video?.is_favorite);

    const previousIsFav = typeof video?.is_favorite === 'boolean'
      ? video.is_favorite
      : Boolean(buttonEl?.classList?.contains?.('active'));
    const optimisticIsFav = !previousIsFav;
    const previousCount = Number.isFinite(state.favoritesCount) ? Number(state.favoritesCount) : null;
    const optimisticCount = previousCount === null
      ? null
      : Math.max(0, previousCount + (optimisticIsFav ? 1 : -1));

    pendingMutations.add(key);
    if (buttonEl) buttonEl.disabled = true;
    applyFavoriteState(video, key, buttonEl, optimisticIsFav, optimisticCount);

    try {
      const data = await global.ArchivebateAPI.postJSON('/api/account/favorites/toggle', mutationVideo);
      if (!data || typeof data.is_favorite !== 'boolean' || data.local_committed !== true) {
        throw new Error('invalid mutation response');
      }
      const isFav = data.is_favorite;

      applyFavoriteState(video, key, buttonEl, isFav, Number(data.total_favorites));

      if (data.favorite_authors) {
        state.favoriteAuthors = new Set(data.favorite_authors.map(a => String(a).toLowerCase().trim()));
        updateAllAuthorNameColors();
      }

      if (data.remote_state === 'failed' || data.remote_state === 'unknown' || data.sync_state === 'remote_failed') {
        showToast?.('Zmiana zapisana lokalnie, ale synchronizacja z kontem zdalnym nie powiodła się.', 'warning');
      } else if (data.sync_state === 'local_only') {
        showToast?.((isFav ? 'Dodano' : 'Usunięto') + ' lokalnie (tryb anonimowy).', 'info');
      } else {
        showToast?.(isFav ? 'Dodano do ulubionych ❤️' : 'Usunięto z ulubionych', isFav ? 'success' : 'info');
      }
      return isFav;
    } catch (e) {
      return await reconcileLocalFavorite(video, key, buttonEl, previousIsFav, e);
    } finally {
      pendingMutations.delete(key);
      if (buttonEl) buttonEl.disabled = false;
    }
  }

  function updateModalButton(isFav) {
    dom.modalFavBtn?.setAttribute?.('aria-pressed', isFav ? 'true' : 'false');
    dom.modalFavBtn?.setAttribute?.('aria-label', isFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych');
    if (isFav) {
      dom.modalFavBtn?.classList?.add('active');
      if (dom.modalFavBtn) dom.modalFavBtn.innerHTML = '<i class="fa-solid fa-heart" style="color:#ef4444;"></i> Usuń z ulubionych';
    } else {
      dom.modalFavBtn?.classList?.remove('active');
      if (dom.modalFavBtn) dom.modalFavBtn.innerHTML = '<i class="fa-regular fa-heart"></i> Dodaj do ulubionych';
    }
    const currU = String(state.currentVideoDetails?.username || '').toLowerCase().trim();
    const isModelFav = state.favoriteAuthors && state.favoriteAuthors.has(currU);
    const shouldHighlight = isFav || isModelFav;
    const modalContent = dom.videoModal?.querySelector('.modal-content');
    if (modalContent) {
      if (shouldHighlight) modalContent.classList.add('is-favorite-modal');
      else modalContent.classList.remove('is-favorite-modal');
    }
  }

  global.ArchivebateFavorites = {
    init,
    isFavoriteAuthor,
    updateAllAuthorNameColors,
    toggleVideo,
    updateModalButton
  };
})(typeof window !== 'undefined' ? window : globalThis);
