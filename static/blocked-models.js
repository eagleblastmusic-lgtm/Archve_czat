(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let showToast;
  let closeModal;
  let updateHomeStats;
  let deduplicateVideos;
  let renderVideoGrid;

  function init(dependencies = {}) {
    showToast = dependencies.showToast;
    closeModal = dependencies.closeModal;
    updateHomeStats = dependencies.updateHomeStats;
    deduplicateVideos = dependencies.deduplicateVideos;
    renderVideoGrid = dependencies.renderVideoGrid;
  }

  function normalizeUsername(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function clientBlockedAuthors() {
    if (!(state.clientBlockedAuthors instanceof Set)) {
      state.clientBlockedAuthors = new Set();
    }
    return state.clientBlockedAuthors;
  }

  function pruneBlockedAuthorFromClientState(norm) {
    if (Array.isArray(state.videos)) {
      state.videos = state.videos.filter(v => normalizeUsername(v && v.username) !== norm);
    }

    if (state.gridCardMap && typeof state.gridCardMap.entries === 'function') {
      for (const [key, card] of state.gridCardMap.entries()) {
        const username = card && card._videoData
          ? card._videoData.username
          : (card && card.dataset ? card.dataset.username : '');
        if (normalizeUsername(username) === norm) {
          state.gridCardMap.delete(key);
        }
      }
    }

    const visibleCards = dom.videoGrid && typeof dom.videoGrid.querySelectorAll === 'function'
      ? dom.videoGrid.querySelectorAll('.video-card').length
      : (Array.isArray(state.videos) ? state.videos.length : 0);

    if (dom.statPageVideos) dom.statPageVideos.innerText = visibleCards;
    if (dom.videoCount && typeof dom.videoCount.innerText === 'string') {
      dom.videoCount.innerText = dom.videoCount.innerText.replace(/^\d+(?=\s+na stronie)/, String(visibleCards));
    }
  }

  function restoreOptimisticRemoval(norm) {
    clientBlockedAuthors().delete(norm);
    if (typeof renderVideoGrid === 'function' && Array.isArray(state.videos)) {
      renderVideoGrid(state.videos);
    }
  }

  async function block(username) {
    if (!username || username.toLowerCase() === 'model') return;

    const norm = normalizeUsername(username);
    clientBlockedAuthors().add(norm);

    // 1. Natychmiastowe zniknięcie kafelków z widoku. Po usunięciu elementu
    // CSS Grid sam przesuwa kolejne karty na zwolnione miejsce — bez reloadu.
    let visibleCount = 0;
    document.querySelectorAll('.video-card').forEach(c => {
      const link = c.querySelector('.model-profile-link');
      if (link) {
        const u = normalizeUsername(link.dataset.username || '');
        if (u === norm) {
          visibleCount++;
          c.style.transition = 'opacity 0.18s ease, transform 0.18s ease';
          c.style.opacity = '0';
          c.style.transform = 'scale(0.92)';
          setTimeout(() => c.remove(), 180);
        }
      }
    });

    // 2. Jeśli odtwarzacz wideo jest otwarty z tą modelką, natychmiast go zamknij
    if (state.currentVideoDetails && normalizeUsername(state.currentVideoDetails.username) === norm) {
      closeModal();
    }

    // 3. Wstępny toast informujący o usuwaniu
    const progressToast = showToast(`Usuwanie profilu "${username}"...`, 'info');

    // 4. Wywołanie API
    try {
      const url = `/api/model/${encodeURIComponent(username)}/block?count=${visibleCount}`;
      const res = await fetch(url, { method: 'POST' });
      const data = await res.json();
      if (data && data.success) {
        const removedVids = data.removed_videos || visibleCount || 0;
        const msg = removedVids > 0
          ? `Profil "${username}" zablokowany. Usunięto ${removedVids.toLocaleString('pl-PL')} filmów z katalogu.`
          : `Profil "${username}" został usunięty i zablokowany w całym programie.`;

        showToast(msg, 'success', progressToast);

        // Usuń autora również z bieżącego modelu klienta. Nie przeładowujemy
        // strony i nie pobieramy ponownie 280 kafelków — siatka po prostu się
        // domyka po fizycznym usunięciu odpowiednich kart.
        pruneBlockedAuthorFromClientState(norm);

        // Zaktualizuj wyłącznie liczniki/statystyki.
        updateCount();
        updateHomeStats();
      } else {
        restoreOptimisticRemoval(norm);
        showToast(`Nie udało się zablokować profilu "${username}".`, 'error', progressToast);
      }
    } catch (err) {
      console.error('Błąd blokowania modelki:', err);
      restoreOptimisticRemoval(norm);
      showToast('Błąd sieciowy podczas blokowania profilu.', 'error', progressToast);
    }
  }

  async function unblock(username) {
    try {
      const res = await fetch(`/api/model/${encodeURIComponent(username)}/unblock`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        clientBlockedAuthors().delete(normalizeUsername(username));
        showToast(`Odblokowano profil "${username}". Będzie teraz ponownie widoczny w programie.`, 'success');
        updateCount();
      }
    } catch (e) {
      showToast('Błąd odblokowywania profilu', 'error');
    }
  }

  async function updateCount() {
    try {
      const res = await fetch('/api/blocked_models');
      const data = await res.json();
      const count = (data.blocked_models || []).length;
      const totalVids = data.blocked_videos_total || 0;
      if (dom.statBlockedCount) dom.statBlockedCount.innerText = count;
      const subEl = document.getElementById('statBlockedVideosSub');
      if (subEl) {
        subEl.innerText = `usunięto ${totalVids.toLocaleString('pl-PL')} filmów`;
      }
    } catch (e) {}
  }

  async function showManager() {
    try {
      const res = await fetch('/api/blocked_models');
      const data = await res.json();
      const blocked = data.blocked_models || [];
      if (blocked.length === 0) {
        alert('Nie masz obecnie żadnych zablokowanych profili.');
        return;
      }
      const totalVids = data.blocked_videos_total || 0;
      const counts = data.blocked_model_video_counts || {};

      const formattedList = blocked.map(b => {
        const norm = normalizeUsername(b);
        const cnt = counts[norm] || 0;
        return cnt > 0 ? `${b} (${cnt.toLocaleString('pl-PL')} filmów)` : b;
      }).join('\n• ');

      const unblockTarget = prompt(
        `Aktualnie zablokowane profile (${blocked.length} autorów, łącznie usunięto ${totalVids.toLocaleString('pl-PL')} filmów):\n\n• ` +
        formattedList +
        `\n\nWpisz nazwę profilu, który chcesz ODBLOKOWAĆ (lub zostaw puste i Anuluj):`
      );
      if (unblockTarget && unblockTarget.trim()) {
        await unblock(unblockTarget.trim());
      }
    } catch (e) {
      showToast('Błąd pobierania listy zablokowanych profili', 'error');
    }
  }

  global.ArchivebateBlockedModels = {
    init,
    block,
    unblock,
    updateCount,
    showManager
  };
})(typeof window !== 'undefined' ? window : globalThis);
