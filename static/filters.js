/**
 * Archivebate Video Browser - Filters Module
 * Obsługa filtrów źródeł (Archivebate / Camwhores) oraz autorów (wszyscy / polubieni / bez polubionych).
 */

(function (global) {
  'use strict';

  let _deps = {
    showToast: null,
    loadHomeVideos: null
  };

  let _customContext = null;

  function getContext() {
    if (_customContext) return _customContext;
    return (typeof window !== 'undefined' && window.ArchivebateAppContext)
      ? window.ArchivebateAppContext
      : (typeof ArchivebateAppContext !== 'undefined' ? ArchivebateAppContext : { state: {}, dom: {} });
  }

  function getState() {
    const ctx = getContext();
    return ctx && ctx.state ? ctx.state : {};
  }

  function getDom() {
    const ctx = getContext();
    return ctx && ctx.dom ? ctx.dom : {};
  }

  function triggerToast(message, type = 'info') {
    if (_deps.showToast) {
      _deps.showToast(message, type);
    } else if (typeof global.showToast === 'function') {
      global.showToast(message, type);
    }
  }

  function triggerLoadHomeVideos(page = 1) {
    if (_deps.loadHomeVideos) {
      _deps.loadHomeVideos(page);
    } else if (typeof global.loadHomeVideos === 'function') {
      global.loadHomeVideos(page);
    }
  }

  function updateCamwhoresToggleUI() {
    const state = getState();
    const dom = getDom();
    const mode = state.sourceFilter || 'all';

    if (typeof document !== 'undefined' && document.body && document.body.classList) {
      document.body.classList.remove('source-only-camwhores', 'source-only-archivebate', 'hide-camwhores');
    }
    if (dom.toggleCamwhoresBtn && dom.toggleCamwhoresBtn.classList) {
      dom.toggleCamwhoresBtn.classList.remove('mode-all', 'mode-only-camwhores', 'mode-only-archivebate', 'active', 'disabled');
    }

    if (mode === 'only-camwhores') {
      if (typeof document !== 'undefined' && document.body && document.body.classList) {
        document.body.classList.add('source-only-camwhores');
      }
      if (dom.toggleCamwhoresBtn && dom.toggleCamwhoresBtn.classList) {
        dom.toggleCamwhoresBtn.classList.add('mode-only-camwhores');
      }
      if (dom.camwhoresToggleLabel) dom.camwhoresToggleLabel.innerText = 'Tylko Camwhores';
      if (dom.sourceToggleIcon) dom.sourceToggleIcon.className = 'fa-solid fa-tv';
    } else if (mode === 'only-archivebate') {
      if (typeof document !== 'undefined' && document.body && document.body.classList) {
        document.body.classList.add('source-only-archivebate');
      }
      if (dom.toggleCamwhoresBtn && dom.toggleCamwhoresBtn.classList) {
        dom.toggleCamwhoresBtn.classList.add('mode-only-archivebate');
      }
      if (dom.camwhoresToggleLabel) dom.camwhoresToggleLabel.innerText = 'Tylko Archivebate';
      if (dom.sourceToggleIcon) dom.sourceToggleIcon.className = 'fa-solid fa-film';
    } else {
      // 'all'
      if (dom.toggleCamwhoresBtn && dom.toggleCamwhoresBtn.classList) {
        dom.toggleCamwhoresBtn.classList.add('mode-all');
      }
      if (dom.camwhoresToggleLabel) dom.camwhoresToggleLabel.innerText = 'Wszystkie';
      if (dom.sourceToggleIcon) dom.sourceToggleIcon.className = 'fa-solid fa-layer-group';
    }
  }

  function initCamwhoresToggle(options = {}) {
    if (options && options.showToast) _deps.showToast = options.showToast;
    if (options && options.loadHomeVideos) _deps.loadHomeVideos = options.loadHomeVideos;

    updateCamwhoresToggleUI();
    const dom = getDom();
    if (dom.toggleCamwhoresBtn && dom.toggleCamwhoresBtn.addEventListener) {
      dom.toggleCamwhoresBtn.addEventListener('click', () => {
        const state = getState();
        if (state.sourceFilter === 'all') {
          state.sourceFilter = 'only-camwhores';
          triggerToast('Źródło: Wyświetlam TYLKO filmy z Camwhores.tv (280 filmów na stronę)', 'info');
        } else if (state.sourceFilter === 'only-camwhores') {
          state.sourceFilter = 'only-archivebate';
          triggerToast('Źródło: Wyświetlam TYLKO filmy z Archivebate (280 filmów na stronę)', 'info');
        } else {
          state.sourceFilter = 'all';
          triggerToast('Źródło: Wyświetlam WSZYSTKIE źródła (Archivebate + Camwhores, 280 filmów)', 'info');
        }
        if (typeof localStorage !== 'undefined' && localStorage.setItem) {
          localStorage.setItem('archivebate_source_filter', state.sourceFilter);
        }
        updateCamwhoresToggleUI();
        if (state.mode === 'home') {
          triggerLoadHomeVideos(1);
        }
      });
    }
  }

  function updateAuthorFilterUI() {
    const state = getState();
    const dom = getDom();
    const mode = state.authorFilter || 'all';

    if (typeof document !== 'undefined' && document.body && document.body.classList) {
      document.body.classList.remove('author-filter-only-fav', 'author-filter-exclude-fav');
    }
    if (dom.toggleAuthorFilterBtn && dom.toggleAuthorFilterBtn.classList) {
      dom.toggleAuthorFilterBtn.classList.remove('mode-all', 'mode-only-fav', 'mode-exclude-fav');
    }

    if (mode === 'only_fav') {
      if (typeof document !== 'undefined' && document.body && document.body.classList) {
        document.body.classList.add('author-filter-only-fav');
      }
      if (dom.toggleAuthorFilterBtn && dom.toggleAuthorFilterBtn.classList) {
        dom.toggleAuthorFilterBtn.classList.add('mode-only-fav');
      }
      if (dom.authorFilterLabel) dom.authorFilterLabel.innerText = 'Tylko polubieni';
      if (dom.authorFilterIcon) dom.authorFilterIcon.className = 'fa-solid fa-star';
    } else if (mode === 'exclude_fav') {
      if (typeof document !== 'undefined' && document.body && document.body.classList) {
        document.body.classList.add('author-filter-exclude-fav');
      }
      if (dom.toggleAuthorFilterBtn && dom.toggleAuthorFilterBtn.classList) {
        dom.toggleAuthorFilterBtn.classList.add('mode-exclude-fav');
      }
      if (dom.authorFilterLabel) dom.authorFilterLabel.innerText = 'Bez polubionych';
      if (dom.authorFilterIcon) dom.authorFilterIcon.className = 'fa-solid fa-user-slash';
    } else {
      // 'all'
      if (dom.toggleAuthorFilterBtn && dom.toggleAuthorFilterBtn.classList) {
        dom.toggleAuthorFilterBtn.classList.add('mode-all');
      }
      if (dom.authorFilterLabel) dom.authorFilterLabel.innerText = 'Wszyscy';
      if (dom.authorFilterIcon) dom.authorFilterIcon.className = 'fa-solid fa-users';
    }
  }

  function initAuthorFilterToggle(options = {}) {
    if (options && options.showToast) _deps.showToast = options.showToast;
    if (options && options.loadHomeVideos) _deps.loadHomeVideos = options.loadHomeVideos;

    updateAuthorFilterUI();
    const dom = getDom();
    if (dom.toggleAuthorFilterBtn && dom.toggleAuthorFilterBtn.addEventListener) {
      dom.toggleAuthorFilterBtn.addEventListener('click', () => {
        const state = getState();
        if (state.authorFilter === 'all') {
          state.authorFilter = 'only_fav';
          triggerToast('Autorzy: Pokazuję TYLKO nagrania od polubionych autorów (280 filmów)', 'info');
        } else if (state.authorFilter === 'only_fav') {
          state.authorFilter = 'exclude_fav';
          triggerToast('Autorzy: Odfiltrowano polubionych — odkrywaj NOWYCH twórców! (280 filmów)', 'info');
        } else {
          state.authorFilter = 'all';
          triggerToast('Autorzy: Wyświetlam WSZYSTKICH autorów (280 filmów)', 'info');
        }
        if (typeof localStorage !== 'undefined' && localStorage.setItem) {
          localStorage.setItem('archivebate_author_filter', state.authorFilter);
        }
        updateAuthorFilterUI();
        if (state.mode === 'home') {
          triggerLoadHomeVideos(1);
        }
      });
    }
  }

  function updateGroupToggleUI() {
    const state = getState();
    const dom = getDom();
    const isGrouped = !!state.groupByAuthor;
    if (dom.toggleGroupBtn) {
      dom.toggleGroupBtn.classList.remove('mode-ungrouped', 'mode-grouped');
      if (isGrouped) {
        dom.toggleGroupBtn.classList.add('mode-grouped');
        if (dom.groupToggleLabel) dom.groupToggleLabel.innerText = 'Włączone';
        if (dom.groupToggleIcon) dom.groupToggleIcon.className = 'fa-solid fa-layer-group';
      } else {
        dom.toggleGroupBtn.classList.add('mode-ungrouped');
        if (dom.groupToggleLabel) dom.groupToggleLabel.innerText = 'Wyłączone';
        if (dom.groupToggleIcon) dom.groupToggleIcon.className = 'fa-solid fa-boxes-stacked';
      }
    }
  }

  function initGroupToggle() {
    updateGroupToggleUI();
    const dom = getDom();
    const state = getState();
    if (dom.toggleGroupBtn) {
      dom.toggleGroupBtn.addEventListener('click', () => {
        state.groupByAuthor = !state.groupByAuthor;
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem('archivebate_group_by_author', state.groupByAuthor ? 'true' : 'false');
        }
        updateGroupToggleUI();
        if (state.groupByAuthor) {
          triggerToast('Grupowanie filmów włączone: 1 kafelek na autora (najnowszy film + lista)', 'info');
        } else {
          triggerToast('Grupowanie filmów wyłączone: wszystkie filmy wyświetlane osobno', 'info');
        }
        if (state.mode === 'home') {
          triggerLoadHomeVideos(1);
        } else if (state.mode === 'search') {
          if (_deps.performSearch) _deps.performSearch(state.currentQuery, 1);
          else if (typeof global.performSearch === 'function') global.performSearch(state.currentQuery, 1);
        }
      });
    }
  }

  function groupVideosByAuthor(videos) {
    if (!videos || !Array.isArray(videos)) return [];
    const authorMap = new Map();
    const result = [];

    for (const v of videos) {
      if (!v) continue;
      const authorRaw = (v.username || '').trim();
      const norm = authorRaw.toLowerCase().replace(/[^a-z0-9]/g, '');

      if (v.is_grouped && Array.isArray(v.grouped_videos) && v.grouped_videos.length > 1) {
        if (!authorMap.has(norm)) {
          const leader = {
            ...v,
            _isGrouped: true,
            _groupCount: v.grouped_videos.length,
            _groupedVideos: [...v.grouped_videos]
          };
          authorMap.set(norm, leader);
          result.push(leader);
        } else {
          const leader = authorMap.get(norm);
          for (const gv of v.grouped_videos) {
            if (!leader._groupedVideos.some(item => String(item.id) === String(gv.id))) {
              leader._groupedVideos.push(gv);
            }
          }
          leader._groupCount = leader._groupedVideos.length;
          leader.group_count = leader._groupCount;
          leader.is_grouped = leader._groupCount > 1;
          leader._isGrouped = leader.is_grouped;
        }
        continue;
      }

      if (!norm || norm === 'model') {
        result.push({
          ...v,
          _isGrouped: false,
          _groupCount: 1,
          _groupedVideos: [v]
        });
        continue;
      }

      if (!authorMap.has(norm)) {
        const leader = {
          ...v,
          _isGrouped: false,
          _groupCount: 1,
          _groupedVideos: [v]
        };
        authorMap.set(norm, leader);
        result.push(leader);
      } else {
        const leader = authorMap.get(norm);
        leader._groupCount = (leader._groupCount || leader.group_count || 1) + 1;
        leader.group_count = leader._groupCount;
        leader._isGrouped = true;
        leader.is_grouped = true;
        if (!leader._groupedVideos) leader._groupedVideos = [leader];
        leader._groupedVideos.push(v);
        leader.grouped_videos = leader._groupedVideos;
      }
    }

    return result;
  }

  function init(deps = {}) {
    if (deps) {
      if (deps.showToast) _deps.showToast = deps.showToast;
      if (deps.loadHomeVideos) _deps.loadHomeVideos = deps.loadHomeVideos;
      if (deps.performSearch) _deps.performSearch = deps.performSearch;
      if (deps.context) _customContext = deps.context;
    }
    initCamwhoresToggle(_deps);
    initAuthorFilterToggle(_deps);
    initGroupToggle();
  }

  const ArchivebateFilters = {
    init,
    updateCamwhoresToggleUI,
    initCamwhoresToggle,
    updateAuthorFilterUI,
    initAuthorFilterToggle,
    updateGroupToggleUI,
    initGroupToggle,
    groupVideosByAuthor
  };

  global.ArchivebateFilters = ArchivebateFilters;
  global.groupVideosByAuthor = groupVideosByAuthor;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateFilters;
  }
})(typeof window !== 'undefined' ? window : globalThis);
