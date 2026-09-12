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

  function hasActiveFeedProjection() {
    if (state.mode !== 'home') return false;
    const revision = Number(state.lastAppliedFeedRevision);
    return Number.isFinite(revision) && revision >= 0;
  }

  async function update() {
    const generation = ++requestGeneration;
    try {
      const res = await fetch('/api/stats', { cache: 'no-store' });
      if (!res.ok) throw new Error(`stats HTTP ${res.status}`);
      const data = await res.json();
      if (generation !== requestGeneration) return;
      const finiteCount = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString('pl-PL') : '--';

      if (dom.statGlobalVideos) {
        dom.statGlobalVideos.innerText = data.external_catalog_estimate == null ? '--' : Number(data.external_catalog_estimate).toLocaleString('pl-PL');
      }
      // /api/stats is intentionally unscoped. Once the current feed has
      // applied a revision, its filtered count is authoritative for the
      // visible home projection and must not be replaced by the global count.
      // video-views.js updates that value when each feed batch is committed.
      const feedProjectionActive = hasActiveFeedProjection();
      if (dom.statCatalogVideos && !feedProjectionActive) {
        dom.statCatalogVideos.innerText = finiteCount(data.catalog_videos);
      }
      if (dom.statCatalogVideosLbl && !feedProjectionActive) {
        const totalPages = Number.isFinite(Number(data.catalog_pages))
          ? Number(data.catalog_pages)
          : (Number.isFinite(Number(data.catalog_videos)) ? Math.ceil(Number(data.catalog_videos) / 280) : null);
        dom.statCatalogVideosLbl.innerText = formatCatalogStatus(data, totalPages);
      }
      if (dom.statPageVideos) {
        dom.statPageVideos.innerText = String(state.videos?.length || 0);
      }
      if (dom.statGlobalProfiles && data.total_models !== undefined && data.total_models !== null) {
        dom.statGlobalProfiles.innerText = finiteCount(data.total_models);
        if (dom.scannedModelsCount) {
          dom.scannedModelsCount.innerText = `${finiteCount(data.total_models)} profili`;
        }
      }
      if (dom.statUserLibrary) {
        const favs = finiteCount(data.favorites_count);
        const hist = finiteCount(data.history_count);
        dom.statUserLibrary.innerText = favs === '--' || hist === '--' ? '--' : `${favs} ulub. • ${hist} hist.`;
      }
      if (dom.statBlockedInfo) {
        const authors = finiteCount(data.blocked_authors_count);
        dom.statBlockedInfo.innerText = authors === '--' ? '--' : `${authors} autorów`;
      }
      if (dom.statBlockedVideosLbl) {
        const vids = finiteCount(data.blocked_videos_total);
        dom.statBlockedVideosLbl.innerText = vids === '--' ? 'Zakres ukrycia: --' : `szacunkowo ukryto ${vids} filmów`;
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
