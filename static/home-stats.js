(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { dom: {} };
  const dom = context.dom || {};
  const state = context.state || {};
  let requestGeneration = 0;
  let pollTimer = null;

  function cancelPoll() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function scheduleCatalogPoll(data, delay = 1200) {
    cancelPoll();
    if (state.mode !== 'home' || !data || data.catalog_complete !== false) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      update();
    }, delay);
  }

  function formatCatalogStatus(data, totalPages) {
    if (data.catalog_complete === false) {
      const updatedAt = Number(data.updated_at || 0);
      const ageSeconds = updatedAt > 0 ? Math.max(0, Math.floor(Date.now() / 1000 - updatedAt)) : 0;
      if (ageSeconds >= 90) {
        return totalPages
          ? `Indeksowanie… • ${totalPages.toLocaleString('pl-PL')} stron katalogu • ostatni zapis ${ageSeconds}s temu`
          : `Indeksowanie… • ostatni zapis ${ageSeconds}s temu`;
      }
      return totalPages
        ? `Indeksowanie trwa • ${totalPages.toLocaleString('pl-PL')} stron katalogu`
        : 'Indeksowanie trwa';
    }

    if (data.catalog_limited) {
      return totalPages
        ? `Gotowe do limitu źródła • ${totalPages.toLocaleString('pl-PL')} stron katalogu`
        : 'Gotowe do limitu źródła';
    }

    return totalPages
      ? `Gotowe • ${totalPages.toLocaleString('pl-PL')} stron katalogu`
      : 'Gotowe';
  }

  async function update() {
    const generation = ++requestGeneration;
    try {
      const res = await fetch('/api/stats', { cache: 'no-store' });
      if (!res.ok) throw new Error(`stats HTTP ${res.status}`);
      const data = await res.json();
      if (generation !== requestGeneration) return;

      if (dom.statGlobalVideos) {
        dom.statGlobalVideos.innerText = '5 500 000+';
      }
      if (dom.statCatalogVideos) {
        const totalVids = data.catalog_videos !== undefined ? data.catalog_videos : 0;
        dom.statCatalogVideos.innerText = totalVids.toLocaleString('pl-PL');
      }
      if (dom.statCatalogVideosLbl) {
        const totalPages = data.catalog_pages || (data.catalog_videos ? Math.ceil(data.catalog_videos / 280) : 0);
        dom.statCatalogVideosLbl.innerText = formatCatalogStatus(data, totalPages);
      }
      if (dom.statPageVideos) {
        dom.statPageVideos.innerText = String(state.videos?.length || 0);
      }
      if (dom.statGlobalProfiles && data.total_models) {
        dom.statGlobalProfiles.innerText = `${data.total_models.toLocaleString('pl-PL')}`;
        if (dom.scannedModelsCount) {
          dom.scannedModelsCount.innerText = `${data.total_models} profili`;
        }
      }
      if (dom.statUserLibrary) {
        const favs = data.favorites_count || 0;
        const hist = data.history_count || 0;
        dom.statUserLibrary.innerText = `${favs} ulub. • ${hist} hist.`;
      }
      if (dom.statBlockedInfo) {
        const authors = data.blocked_authors_count || 0;
        dom.statBlockedInfo.innerText = `${authors} autorów`;
      }
      if (dom.statBlockedVideosLbl) {
        const vids = data.blocked_videos_total || 0;
        dom.statBlockedVideosLbl.innerText = `${vids.toLocaleString('pl-PL')} filmów usuniętych z katalogu`;
      }

      scheduleCatalogPoll(data);
    } catch (e) {
      if (generation !== requestGeneration) return;
      cancelPoll();
      if (state.mode === 'home') {
        pollTimer = setTimeout(() => {
          pollTimer = null;
          update();
        }, 2500);
      }
    }
  }

  global.ArchivebateHomeStats = { update, cancelPoll, formatCatalogStatus };
})(typeof window !== 'undefined' ? window : globalThis);
