from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def write(path, text):
    (ROOT / path).write_text(text, encoding='utf-8')


# 1) One authoritative owner for the catalog stat card + polling while partial.
home_stats = r'''(function (global) {
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
        if (data.catalog_complete === false) {
          dom.statCatalogVideosLbl.innerText = totalPages
            ? `Indeksowanie… (${totalPages.toLocaleString('pl-PL')} stron)`
            : 'Indeksowanie…';
        } else {
          dom.statCatalogVideosLbl.innerText = totalPages
            ? `W katalogu (${totalPages.toLocaleString('pl-PL')} stron)`
            : 'W katalogu';
        }
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

  global.ArchivebateHomeStats = { update, cancelPoll };
})(typeof window !== 'undefined' ? window : globalThis);
'''
write('static/home-stats.js', home_stats)


# The feed header owns filtered counters; the global catalog card is owned only by home-stats.
views = read('static/video-views.js')
old = """        if (dom.statPageVideos) dom.statPageVideos.innerText = state.videos.length;\n        if (dom.statCatalogVideos) dom.statCatalogVideos.innerText = totalVids.toLocaleString('pl-PL');\n        if (dom.statCatalogVideosLbl) dom.statCatalogVideosLbl.innerText = isComplete ? `W katalogu (${pageCount.toLocaleString('pl-PL')} stron)` : 'Indeksowanie…';\n        renderPagination();\n"""
new = """        if (dom.statPageVideos) dom.statPageVideos.innerText = state.videos.length;\n        renderPagination();\n"""
if old not in views:
    raise SystemExit('video-views catalog-counter anchor missing')
views = views.replace(old, new, 1)
write('static/video-views.js', views)


# 2) Pagination has exactly one event owner. Both top and bottom controls are wired here.
pagination = r'''(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let loadHomeVideos;
  let performSearch;
  let loadModelVideos;
  let loadFavorites;
  let loadHistory;
  let loadFollowing;

  function init(dependencies = {}) {
    loadHomeVideos = dependencies.loadHomeVideos;
    performSearch = dependencies.performSearch;
    loadModelVideos = dependencies.loadModelVideos;
    loadFavorites = dependencies.loadFavorites;
    loadHistory = dependencies.loadHistory;
    loadFollowing = dependencies.loadFollowing;
  }

  function changePage(newPage) {
    const maxP = Math.max(1, Number(state.lastPage) || 1);
    const parsed = Number.parseInt(newPage, 10);
    if (!Number.isFinite(parsed)) return;
    const target = Math.min(maxP, Math.max(1, parsed));
    state.currentPage = target;
    if (global.scrollTo) global.scrollTo({ top: 0, behavior: 'smooth' });

    if (state.mode === 'home' && typeof loadHomeVideos === 'function') {
      loadHomeVideos(target);
    } else if (state.mode === 'search' && typeof performSearch === 'function') {
      performSearch(state.currentQuery, target);
    } else if (state.mode === 'model' && typeof loadModelVideos === 'function') {
      loadModelVideos(state.currentModel, target);
    } else if (state.mode === 'favorites' && typeof loadFavorites === 'function') {
      loadFavorites(target);
    } else if (state.mode === 'history' && typeof loadHistory === 'function') {
      loadHistory(target);
    } else if (state.mode === 'following' && typeof loadFollowing === 'function') {
      loadFollowing(target);
    }
  }

  function render() {
    const current = Math.max(1, Number(state.currentPage) || 1);
    const maxP = Math.max(1, Number(state.lastPage) || 1);

    function handleJump(inputEl) {
      if (!inputEl) return;
      const value = Number.parseInt(inputEl.value, 10);
      if (Number.isFinite(value)) changePage(value);
    }

    function renderControls(prevBtn, nextBtn, lastBtn, lastNum, jumpInput, jumpBtn, listEl) {
      if (prevBtn) {
        prevBtn.disabled = current <= 1;
        prevBtn.onclick = () => { if (current > 1) changePage(current - 1); };
      }
      if (nextBtn) {
        nextBtn.disabled = current >= maxP;
        nextBtn.onclick = () => { if (current < maxP) changePage(current + 1); };
      }
      if (lastBtn && lastNum) {
        lastNum.innerText = maxP.toLocaleString('pl-PL');
        lastBtn.disabled = current >= maxP;
        lastBtn.onclick = () => changePage(maxP);
      }
      if (jumpInput) {
        jumpInput.max = maxP;
        jumpInput.value = current;
        jumpInput.onkeydown = (event) => {
          if (event.key === 'Enter') handleJump(jumpInput);
        };
      }
      if (jumpBtn) {
        jumpBtn.onclick = () => handleJump(jumpInput);
      }
      if (!listEl) return;
      listEl.innerHTML = '';

      const startPage = Math.max(1, current - 2);
      const endPage = Math.min(maxP, startPage + 4);

      if (startPage > 1) {
        addPageButton(1, listEl);
        if (startPage > 2) {
          const dots = document.createElement('span');
          dots.className = 'page-num-dots';
          dots.innerText = '...';
          listEl.appendChild(dots);
        }
      }

      for (let p = startPage; p <= endPage; p++) {
        addPageButton(p, listEl);
      }

      if (endPage < maxP) {
        const dotsEnd = document.createElement('span');
        dotsEnd.className = 'page-num-dots';
        dotsEnd.innerText = '...';
        listEl.appendChild(dotsEnd);
        addPageButton(maxP, listEl);
      }
    }

    function addPageButton(pageNumber, listEl) {
      const btn = document.createElement('button');
      btn.className = 'page-num-btn';
      if (pageNumber === current) btn.classList.add('active');
      btn.innerText = pageNumber.toLocaleString('pl-PL');
      btn.onclick = () => changePage(pageNumber);
      listEl.appendChild(btn);
    }

    if (dom.paginationSection) dom.paginationSection.style.display = 'flex';
    renderControls(
      dom.prevPageBtn, dom.nextPageBtn, dom.lastPageBtn, dom.lastPageNumber,
      dom.pageJumpInput, dom.pageJumpBtn, dom.pageNumbersList
    );

    if (dom.paginationSectionTop) dom.paginationSectionTop.style.display = 'flex';
    renderControls(
      dom.prevPageBtnTop, dom.nextPageBtnTop, dom.lastPageBtnTop, dom.lastPageNumberTop,
      dom.pageJumpInputTop, dom.pageJumpBtnTop, dom.pageNumbersListTop
    );
  }

  global.ArchivebatePagination = { init, changePage, render };
})(typeof window !== 'undefined' ? window : globalThis);
'''
write('static/pagination.js', pagination)


events = read('static/app-events.js')
pattern = re.compile(r"\n    // Paginacja\n.*?\n    // Modal events", re.S)
events, count = pattern.subn(
    "\n    // Paginacją zarządza wyłącznie ArchivebatePagination. Nie dokładamy tu\n"
    "    // drugiego zestawu listenerów, bo jedno kliknięcie nie może uruchamiać\n"
    "    // dwóch równoległych zmian strony.\n\n    // Modal events",
    events,
    count=1,
)
if count != 1:
    raise SystemExit('app-events pagination block anchor missing')
write('static/app-events.js', events)


# 3) Native browser lazy loading is the fail-safe; no thumbnail may depend solely on IO.
prefetch = read('static/video-prefetch.js')
old = """  function armLazyThumbnail(img) {\n    if (!img || !img.dataset.src) return;\n    if (lazyThumbObserver) {\n      lazyThumbObserver.observe(img);\n    } else {\n      img.src = img.dataset.src;\n      delete img.dataset.src;\n    }\n  }\n"""
new = """  function armLazyThumbnail(img) {\n    if (!img || !img.dataset.src) return;\n    const src = img.dataset.src;\n    // Ustaw src od razu, ale pozostaw loading=lazy. Przeglądarka sama decyduje,\n    // kiedy rozpocząć transfer; miniatura nie zależy już wyłącznie od\n    // IntersectionObserver, który w długiej siatce potrafił zostawić dalsze\n    // kafelki bez obrazu.\n    img.loading = 'lazy';\n    img.src = src;\n    delete img.dataset.src;\n    if (lazyThumbObserver) lazyThumbObserver.unobserve(img);\n  }\n"""
if old not in prefetch:
    raise SystemExit('video-prefetch armLazyThumbnail anchor missing')
prefetch = prefetch.replace(old, new, 1)
write('static/video-prefetch.js', prefetch)


grid = read('static/video-grid.js')
old_inner = """      if (cursor < displayVideos.length) {\n        if ('requestIdleCallback' in global) {\n          global.requestIdleCallback(appendNextChunk, { timeout: 250 });\n        } else {\n          setTimeout(appendNextChunk, 16);\n        }\n      } else {\n"""
new_inner = """      if (cursor < displayVideos.length) {\n        // Gwarantowany kolejny tick zamiast requestIdleCallback: przy ciężkim\n        // ładowaniu miniatur idle callback potrafił zbyt długo nie dostać czasu.\n        setTimeout(appendNextChunk, 0);\n      } else {\n"""
if old_inner not in grid:
    raise SystemExit('video-grid inner chunk scheduler anchor missing')
grid = grid.replace(old_inner, new_inner, 1)
old_initial = """    if (cursor < displayVideos.length) {\n      if ('requestIdleCallback' in global) {\n        global.requestIdleCallback(appendNextChunk, { timeout: 200 });\n      } else {\n        setTimeout(appendNextChunk, 16);\n      }\n    }\n"""
new_initial = """    if (cursor < displayVideos.length) {\n      setTimeout(appendNextChunk, 0);\n    }\n"""
if old_initial not in grid:
    raise SystemExit('video-grid initial chunk scheduler anchor missing')
grid = grid.replace(old_initial, new_initial, 1)
write('static/video-grid.js', grid)


# Regression: top paginator must work and one click must mean one navigation.
pag_test = read('audit/regression_pagination.cjs')
append = r'''

// 4) Top paginator owns its handlers exactly once, including jump/last.
{
  function btn() { return { disabled: false, onclick: null, onkeydown: null }; }
  const state = { mode: 'home', currentPage: 1, lastPage: 5 };
  const topNext = btn();
  const topPrev = btn();
  const topLast = btn();
  const topJump = btn();
  const topInput = { value: 1, max: 1, onkeydown: null };
  const dom = {
    paginationSection: null,
    paginationSectionTop: { style: {} },
    prevPageBtnTop: topPrev,
    nextPageBtnTop: topNext,
    lastPageBtnTop: topLast,
    lastPageNumberTop: { innerText: '' },
    pageJumpInputTop: topInput,
    pageJumpBtnTop: topJump,
    pageNumbersListTop: null
  };
  const calls = [];
  const window = { ArchivebateAppContext: { state, dom }, scrollTo() {} };
  const ctx = { window, globalThis: window, console, document: { createElement() { throw new Error('not needed'); } } };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/pagination.js', 'utf8'), ctx);
  window.ArchivebatePagination.init({
    loadHomeVideos: page => calls.push(page), performSearch() {}, loadModelVideos() {},
    loadFavorites() {}, loadHistory() {}, loadFollowing() {}
  });
  window.ArchivebatePagination.render();
  topNext.onclick();
  assert.equal(state.currentPage, 2);
  assert.deepEqual(calls, [2], 'one top Next click must trigger exactly one navigation');

  state.currentPage = 2;
  window.ArchivebatePagination.render();
  topInput.value = '4';
  topJump.onclick();
  assert.equal(state.currentPage, 4);
  assert.deepEqual(calls, [2, 4]);

  state.currentPage = 4;
  window.ArchivebatePagination.render();
  topLast.onclick();
  assert.equal(state.currentPage, 5);
  assert.deepEqual(calls, [2, 4, 5]);

  const eventsSource = fs.readFileSync('static/app-events.js', 'utf8');
  assert.doesNotMatch(eventsSource, /nextPageBtnTop\.addEventListener/, 'top paginator must not have a second event owner');
}
'''
if '// 4) Top paginator owns its handlers exactly once' not in pag_test:
    pag_test += append
write('audit/regression_pagination.cjs', pag_test)


front_test = read('audit/regression_frontend.cjs')
append = r'''

// Runtime UI ownership / long-page loading regressions.
{
  const homeStatsSource = fs.readFileSync('static/home-stats.js', 'utf8');
  assert.doesNotMatch(homeStatsSource, /!hasFeedCounters/, 'global catalog card must not freeze behind feed counters');
  assert.match(homeStatsSource, /catalog_complete === false/, 'partial catalog must schedule live stats polling');

  const viewsSource = fs.readFileSync('static/video-views.js', 'utf8');
  assert.doesNotMatch(viewsSource, /statCatalogVideos\.innerText = totalVids/, 'feed snapshot must not overwrite global catalog card');

  const prefetchSource = fs.readFileSync('static/video-prefetch.js', 'utf8');
  assert.match(prefetchSource, /img\.loading = 'lazy'/, 'lazy thumbnails must use native browser loading');
  assert.match(prefetchSource, /img\.src = src/, 'lazy thumbnail must always receive a real src');

  const gridSource = fs.readFileSync('static/video-grid.js', 'utf8');
  assert.doesNotMatch(gridSource, /requestIdleCallback\(appendNextChunk/, 'long-grid completion must not depend on idle callbacks');
  assert.match(gridSource, /setTimeout\(appendNextChunk, 0\)/, 'long-grid chunks must have a deterministic scheduler');
}
'''
if '// Runtime UI ownership / long-page loading regressions.' not in front_test:
    front_test += append
write('audit/regression_frontend.cjs', front_test)

print('UI runtime hotfix applied')
