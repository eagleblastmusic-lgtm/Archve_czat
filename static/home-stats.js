(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { dom: {} };
  const dom = context.dom || {};
  const state = context.state || {};
  let requestGeneration = 0;

  async function update() {
    const generation = ++requestGeneration;
    try {
      const res = await fetch('/api/stats');
      if (res.ok) {
        const data = await res.json();
        if (generation !== requestGeneration) return;
        const hasFeedCounters = state.mode === 'home' && state.lastAppliedFeedRevision >= 0;
        if (dom.statGlobalVideos) {
          dom.statGlobalVideos.innerText = '5 500 000+';
        }
        if (dom.statCatalogVideos && !hasFeedCounters) {
          const totalVids = data.catalog_videos !== undefined ? data.catalog_videos : 0;
          dom.statCatalogVideos.innerText = totalVids.toLocaleString('pl-PL');
        }
        if (dom.statCatalogVideosLbl && !hasFeedCounters) {
          const totalPages = data.catalog_pages || (data.catalog_videos ? Math.ceil(data.catalog_videos / 280) : 0);
          dom.statCatalogVideosLbl.innerText = totalPages ? `W katalogu (${totalPages.toLocaleString('pl-PL')} stron)` : 'W katalogu';
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
        // Licznik zablokowanych autorów i usuniętych filmów
        if (dom.statBlockedInfo) {
          const authors = data.blocked_authors_count || 0;
          dom.statBlockedInfo.innerText = `${authors} autorów`;
        }
        if (dom.statBlockedVideosLbl) {
          const vids = data.blocked_videos_total || 0;
          dom.statBlockedVideosLbl.innerText = `${vids.toLocaleString('pl-PL')} filmów usuniętych z katalogu`;
        }
      }
    } catch (e) {}
  }

  global.ArchivebateHomeStats = { update };
})(typeof window !== 'undefined' ? window : globalThis);
