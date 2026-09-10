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

  async function block(username) {
    if (!username || username.toLowerCase() === 'model') return;

    const norm = username.toLowerCase().replace(/[^a-z0-9]/g, '');

    // 1. Natychmiastowe zniknięcie kafelków z widoku (0 ms dla użytkownika)
    let visibleCount = 0;
    document.querySelectorAll('.video-card').forEach(c => {
      const link = c.querySelector('.model-profile-link');
      if (link) {
        const u = (link.dataset.username || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        if (u === norm) {
          visibleCount++;
          c.style.transition = 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)';
          c.style.opacity = '0';
          c.style.transform = 'scale(0.85)';
          setTimeout(() => c.remove(), 250);
        }
      }
    });

    // 2. Jeśli odtwarzacz wideo jest otwarty z tą modelką, natychmiast go zamknij
    if (state.currentVideoDetails && (state.currentVideoDetails.username || '').toLowerCase().replace(/[^a-z0-9]/g, '') === norm) {
      closeModal();
    }

    // 3. Wstępny toast informujący o usuwaniu
    const progressToast = showToast(`Usuwanie profilu "${username}"...`, 'info');

    // 4. Wywołanie API (serwer zlicza usunięte filmy i odpowiada w ułamku sekundy)
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

        // Zaktualizuj liczniki w statystykach konta i strony głównej
        updateCount();
        updateHomeStats();

        // Jeśli jesteśmy na stronie głównej, płynnie uzupełnij brakujące kafelki w tle do pełnych 280
        if (state.mode === 'home') {
          setTimeout(async () => {
            try {
              // Reuse the snapshot feed loader; this keeps the active revision,
              // filters and SSE lifecycle in one place instead of reviving the
              // Ponownie pobierz bieżącą rewizję feedu po zmianie preferencji.
              const views = global.ArchivebateVideoViews;
              if (views?.loadHomeVideos) await views.loadHomeVideos(state.currentPage, true);
            } catch (e) {}
          }, 350);
        }
      } else {
        showToast(`Nie udało się zablokować profilu "${username}".`, 'error', progressToast);
      }
    } catch (err) {
      console.error('Błąd blokowania modelki:', err);
      showToast('Błąd sieciowy podczas blokowania profilu.', 'error', progressToast);
    }
  }

  async function unblock(username) {
    try {
      const res = await fetch(`/api/model/${encodeURIComponent(username)}/unblock`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
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
        const norm = b.toLowerCase().replace(/[^a-z0-9]/g, '');
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
