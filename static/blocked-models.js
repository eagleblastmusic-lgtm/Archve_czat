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
  const pendingBlocks = new Set();
  let lastBlock = null;

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
          card.remove?.();
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
    if (!norm || pendingBlocks.has(norm)) return;
    pendingBlocks.add(norm);

    // Do czasu potwierdzenia API nie usuwamy kart ani rekordów z lokalnej biblioteki.
    let visibleCount = 0;
    document.querySelectorAll('.video-card').forEach(c => {
      const link = c.querySelector('.model-profile-link');
      if (link) {
        const u = normalizeUsername(link.dataset.username || '');
        if (u === norm) {
          visibleCount++;
        }
      }
    });

    const progressToast = showToast(`Blokowanie profilu "${username}"...`, 'info');

    // 4. Wywołanie API
    try {
      const url = `/api/model/${encodeURIComponent(username)}/block?count=${visibleCount}`;
      const data = await global.ArchivebateAPI.postJSON(url, {});
      if (data && data.success) {
        clientBlockedAuthors().add(norm);
        if (state.currentVideoDetails && normalizeUsername(state.currentVideoDetails.username) === norm) closeModal();
        const hiddenVids = data.hidden_videos || visibleCount || 0;
        const msg = hiddenVids > 0
          ? `Profil "${username}" zablokowany. Ukryto szacunkowo ${hiddenVids.toLocaleString('pl-PL')} filmów; dane zachowano.`
          : `Profil "${username}" został zablokowany jako odwracalny filtr.`;

        lastBlock = {
          username,
          norm,
          expiresAt: Date.now() + 10000,
          previousVideos: Array.isArray(state.videos) ? state.videos.map(video => ({ ...video })) : null
        };
        showToast(`${msg} Możesz cofnąć przez 10 sekund.`, 'success', progressToast, [
          { label: 'Cofnij', onClick: () => undoLastBlock() }
        ]);

        pruneBlockedAuthorFromClientState(norm);

        // Zaktualizuj wyłącznie liczniki/statystyki.
        updateCount();
        updateHomeStats();
      } else {
        showToast(`Nie udało się zablokować profilu "${username}".`, 'error', progressToast);
      }
    } catch (err) {
      console.error('Błąd blokowania modelki:', err);
      showToast(err?.message || 'Błąd sieciowy podczas blokowania profilu.', 'error', progressToast);
    } finally {
      pendingBlocks.delete(norm);
    }
  }

  async function unblock(username) {
    try {
      const data = await global.ArchivebateAPI.postJSON(`/api/model/${encodeURIComponent(username)}/unblock`, {});
      if (data.success) {
        clientBlockedAuthors().delete(normalizeUsername(username));
        showToast(`Odblokowano profil "${username}". Będzie teraz ponownie widoczny w programie.`, 'success');
        updateCount();
        return true;
      }
    } catch (e) {
      showToast(e?.message || 'Błąd odblokowywania profilu', 'error');
    }
    return false;
  }

  async function undoLastBlock() {
    const pending = lastBlock;
    if (!pending || pending.expiresAt < Date.now()) {
      lastBlock = null;
      showToast('Okno cofnięcia blokady wygasło.', 'info');
      return false;
    }
    lastBlock = null;
    const restored = await unblock(pending.username);
    if (restored && pending.previousVideos) {
      state.videos = pending.previousVideos;
      renderVideoGrid(state.videos);
    }
    return restored;
  }

  async function updateCount() {
    try {
      const data = await global.ArchivebateAPI.getJSON('/api/blocked_models');
      const count = (data.blocked_models || []).length;
      const totalVids = Number(data.blocked_videos_total);
      if (dom.statBlockedCount) dom.statBlockedCount.textContent = Number.isFinite(count) ? String(count) : '--';
      const subEl = document.getElementById('statBlockedVideosSub');
      if (subEl) {
        subEl.textContent = Number.isFinite(totalVids)
          ? `szacunkowo ukryto ${totalVids.toLocaleString('pl-PL')} filmów`
          : 'szacunkowo ukryto -- filmów';
      }
    } catch (e) {}
  }

  async function showManager() {
    try {
      const data = await global.ArchivebateAPI.getJSON('/api/blocked_models');
      const blocked = data.blocked_models || [];
      if (blocked.length === 0) {
        showToast('Nie masz obecnie żadnych zablokowanych profili.', 'info');
        return;
      }
      const totalVids = data.blocked_videos_total || 0;
      const counts = data.blocked_model_video_counts || {};

      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-labelledby', 'blockedModelsTitle');
      overlay.style.display = 'flex';
      const panel = document.createElement('div');
      panel.className = 'modal-content';
      panel.style.cssText = 'max-width:620px;width:min(620px,calc(100vw - 32px));padding:24px;';
      const heading = document.createElement('h2');
      heading.id = 'blockedModelsTitle';
      heading.textContent = 'Zablokowane profile';
      const summary = document.createElement('p');
      summary.textContent = `${blocked.length} autorów • szacunkowo ukryto ${totalVids.toLocaleString('pl-PL')} filmów. Dane biblioteki są zachowane.`;
      summary.style.color = 'var(--text-muted)';
      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Szukaj profilu…';
      search.setAttribute('aria-label', 'Szukaj zablokowanego profilu');
      search.style.cssText = 'width:100%;margin:12px 0;padding:10px;border-radius:8px;';
      const list = document.createElement('div');
      list.className = 'blocked-models-manager-list';
      list.style.cssText = 'display:flex;flex-direction:column;gap:8px;max-height:48vh;overflow:auto;';
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn-card';
      close.textContent = 'Zamknij';
      close.style.marginTop = '16px';
      const closeManager = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
      const onKey = event => { if (event.key === 'Escape') closeManager(); };
      const render = () => {
        list.replaceChildren();
        const query = normalizeUsername(search.value);
        blocked.filter(name => !query || normalizeUsername(name).includes(query)).forEach(name => {
          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;background:rgba(255,255,255,.04);border-radius:8px;';
          const label = document.createElement('span');
          const count = counts[normalizeUsername(name)] || 0;
          label.textContent = count > 0 ? `${name} (${count.toLocaleString('pl-PL')} filmów)` : name;
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'btn-card danger';
          button.textContent = 'Odblokuj';
          button.addEventListener('click', async () => {
            button.disabled = true;
            if (await unblock(name)) {
              const index = blocked.indexOf(name);
              if (index >= 0) blocked.splice(index, 1);
              render();
              if (blocked.length === 0) closeManager();
            } else button.disabled = false;
          });
          row.append(label, button);
          list.appendChild(row);
        });
        if (!list.children.length) {
          const empty = document.createElement('p');
          empty.textContent = 'Brak profili pasujących do wyszukiwania.';
          list.appendChild(empty);
        }
      };
      search.addEventListener('input', render);
      close.addEventListener('click', closeManager);
      overlay.addEventListener('click', event => { if (event.target === overlay) closeManager(); });
      panel.append(heading, summary, search, list, close);
      overlay.appendChild(panel);
      document.body.appendChild(overlay);
      document.addEventListener('keydown', onKey);
      search.focus();
      render();
    } catch (e) {
      showToast('Błąd pobierania listy zablokowanych profili', 'error');
    }
  }

  global.ArchivebateBlockedModels = {
    init,
    block,
    unblock,
    updateCount,
    showManager,
    undoLastBlock
  };
})(typeof window !== 'undefined' ? window : globalThis);
