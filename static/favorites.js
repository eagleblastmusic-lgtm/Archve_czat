(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let showToast;
  const pendingMutations = new Set();

  function videoKey(video) {
    const source = String(video?.source || (String(video?.id || '').startsWith('cw_') ? 'camwhores' : '')).toLowerCase();
    const providerId = String(video?.provider_id || video?.id || '').replace(/^cw_/, '').trim();
    return source && providerId ? `${source}:id:${providerId}` : null;
  }

  function init(dependencies = {}) {
    showToast = dependencies.showToast;
  }

  function isFavoriteAuthor(username) {
    if (!username) return false;
    const norm = String(username).toLowerCase().trim();
    return state.favoriteAuthors && state.favoriteAuthors.has(norm);
  }

  function updateAllAuthorNameColors() {
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

      if (shouldHighlight) {
        card.classList.add('is-favorite-card');
      } else {
        card.classList.remove('is-favorite-card');
      }
    });

    if (state.currentVideoDetails && dom.modalModelName) {
      const currU = String(state.currentVideoDetails.username || '').toLowerCase().trim();
      const isModelFav = state.favoriteAuthors && state.favoriteAuthors.has(currU);
      const isVidFav = !!state.currentVideoDetails.is_favorite;
      const isFav = isModelFav || isVidFav;
      if (isFav) {
        dom.modalModelName.classList.add('is-favorite-author');
      } else {
        dom.modalModelName.classList.remove('is-favorite-author');
      }
      const modalContent = dom.videoModal?.querySelector('.modal-content');
      if (modalContent) {
        if (isFav) modalContent.classList.add('is-favorite-modal');
        else modalContent.classList.remove('is-favorite-modal');
      }
    }
  }

  async function toggleVideo(video, buttonEl = null) {
    const key = videoKey(video);
    if (!key || pendingMutations.has(key)) return Boolean(video?.is_favorite);
    pendingMutations.add(key);
    if (buttonEl) buttonEl.disabled = true;
    try {
      const data = await global.ArchivebateAPI.postJSON('/api/account/favorites/toggle', video);
      if (!data || typeof data.is_favorite !== 'boolean' || data.local_committed !== true) {
        throw new Error('invalid mutation response');
      }
      const isFav = data.is_favorite;

      state.favoritesCount = data.total_favorites;
      dom.navFavCount.innerText = state.favoritesCount;
      dom.statFavCount.innerText = state.favoritesCount;

      if (buttonEl) {
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

      const vid = (Array.isArray(state.videos) ? state.videos : []).find(v => videoKey(v) === key);
      if (vid) vid.is_favorite = isFav;
      if (state.currentVideoDetails && videoKey(state.currentVideoDetails) === key) {
        state.currentVideoDetails.is_favorite = isFav;
      }

      if (data.favorite_authors) {
        state.favoriteAuthors = new Set(data.favorite_authors.map(a => String(a).toLowerCase().trim()));
      }
      updateAllAuthorNameColors();

      if (data.remote_state === 'failed' || data.remote_state === 'unknown' || data.sync_state === 'remote_failed') {
        showToast('Zmiana zapisana lokalnie, ale synchronizacja z kontem zdalnym nie powiodła się.', 'warning');
      } else if (data.sync_state === 'local_only') {
        showToast((isFav ? 'Dodano' : 'Usunięto') + ' lokalnie (tryb anonimowy).', 'info');
      } else {
        showToast(isFav ? 'Dodano do ulubionych ❤️' : 'Usunięto z ulubionych', isFav ? 'success' : 'info');
      }
      return isFav;
    } catch (e) {
      showToast(e?.message || 'Błąd aktualizacji ulubionych', 'error');
      return false;
    } finally {
      pendingMutations.delete(key);
      if (buttonEl) buttonEl.disabled = false;
    }
  }

  function updateModalButton(isFav) {
    dom.modalFavBtn?.setAttribute?.('aria-pressed', isFav ? 'true' : 'false');
    dom.modalFavBtn?.setAttribute?.('aria-label', isFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych');
    if (isFav) {
      dom.modalFavBtn.classList.add('active');
      dom.modalFavBtn.innerHTML = '<i class="fa-solid fa-heart" style="color:#ef4444;"></i> Usuń z ulubionych';
    } else {
      dom.modalFavBtn.classList.remove('active');
      dom.modalFavBtn.innerHTML = '<i class="fa-regular fa-heart"></i> Dodaj do ulubionych';
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
