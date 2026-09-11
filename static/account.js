(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let showToast;
  let setActiveNavTab;
  let loadFavorites;
  let loadHistory;
  let loadFollowing;
  let updateAllAuthorNameColors;
  let updateBlockedModelsCount;
  let userStatusRetryCount = 0;
  let accountBootstrapStarted = false;

  function init(dependencies = {}) {
    showToast = dependencies.showToast;
    setActiveNavTab = dependencies.setActiveNavTab;
    loadFavorites = dependencies.loadFavorites;
    loadHistory = dependencies.loadHistory;
    loadFollowing = dependencies.loadFollowing;
    updateAllAuthorNameColors = dependencies.updateAllAuthorNameColors;
    updateBlockedModelsCount = dependencies.updateBlockedModelsCount;

    dom.navAccountBtn.addEventListener('click', () => {
      setActiveNavTab(dom.navAccountBtn);
      showPanel();
    });

    dom.panelSyncBtn.addEventListener('click', sync);
    dom.panelClearHistoryBtn.addEventListener('click', clearHistory);

    dom.reloginBtn.addEventListener('click', async () => {
      showToast('Synchronizacja konta...', 'info');
      try {
        const res = await fetch('/api/relogin', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast('Zsynchronizowano pomyślnie!', 'success');
          updateUserStatus(data.status);
        } else {
          showToast(data?.status?.login_error || 'Błąd logowania!', 'error');
        }
      } catch (e) {
        showToast('Błąd połączenia', 'error');
      }
    });
  }

  async function refreshAccountBootstrap(baseStatus) {
    if (accountBootstrapStarted || !baseStatus || (!baseStatus.logged_in && !baseStatus.last_synced)) return;
    accountBootstrapStarted = true;
    try {
      const summary = await ArchivebateAPI.getJSON('/api/account/summary', { timeoutMs: 120000 });
      updateUserStatus({ ...baseStatus, ...summary });
      if (global.ArchivebateHomeStats && typeof global.ArchivebateHomeStats.update === 'function') {
        await global.ArchivebateHomeStats.update();
      }
    } catch (e) {
      accountBootstrapStarted = false;
    }
  }

  async function initUserStatus() {
    try {
      const data = await ArchivebateAPI.getJSON('/api/status', { timeoutMs: 5000 });
      updateUserStatus(data);
      void refreshAccountBootstrap(data);

      if (data.account_configured && !data.logged_in && !data.login_error && userStatusRetryCount < 5) {
        userStatusRetryCount += 1;
        dom.userEmail.innerText = 'Łączenie...';
        dom.statusDot.classList.remove('error');
        setTimeout(initUserStatus, 1200 + userStatusRetryCount * 500);
      } else {
        userStatusRetryCount = 0;
      }
    } catch (e) {
      if (userStatusRetryCount < 3) {
        userStatusRetryCount += 1;
        setTimeout(initUserStatus, 1500);
        return;
      }
      dom.userEmail.innerText = 'Błąd sesji';
      dom.statusDot.classList.add('error');
    }
  }

  function updateUserStatus(status) {
    if (status.logged_in) {
      dom.userEmail.innerText = status.email;
      dom.statusDot.classList.remove('error');
      dom.userEmail.title = 'Zalogowano pomyślnie jako ' + status.email;
      dom.panelEmail.innerText = status.email;
    } else if (!status.account_configured) {
      dom.userEmail.innerText = 'Tryb anonimowy';
      dom.userEmail.title = 'Dodaj dane konta w .env.local, aby włączyć synchronizację konta.';
      dom.panelEmail.innerText = 'Konto nieskonfigurowane';
      dom.statusDot.classList.add('error');
    } else {
      dom.userEmail.innerText = `${status.email || 'Konto'} (offline)`;
      dom.userEmail.title = status.login_error || 'Nie udało się zalogować do Archivebate.';
      dom.statusDot.classList.add('error');
    }

    state.favoritesCount = status.favorites_count || 0;
    state.historyCount = status.history_count || 0;
    state.followingCount = status.following_count || 0;

    dom.navFavCount.innerText = state.favoritesCount;
    dom.navHistCount.innerText = state.historyCount;
    dom.statFavCount.innerText = state.favoritesCount;
    dom.statHistCount.innerText = state.historyCount;
    dom.statFollCount.innerText = state.followingCount;

    if (status.favorite_authors) {
      state.favoriteAuthors = new Set(status.favorite_authors.map(a => String(a).toLowerCase().trim()));
      updateAllAuthorNameColors();
    }

    if (status.last_synced) {
      dom.panelLastSync.innerHTML = `<i class="fa-solid fa-clock"></i> Ostatnia synchronizacja: ${status.last_synced}`;
    }
    updateBlockedModelsCount();
  }

  function showPanel() {
    state.mode = 'account';
    dom.accountPanelView.style.display = 'block';
    dom.tagsSection.style.display = 'none';
    if (dom.homeStatsBar) dom.homeStatsBar.style.display = 'none';
    dom.contentHeader.style.display = 'none';
    dom.matchedProfiles.style.display = 'none';
    dom.videoGrid.innerHTML = '';
    dom.paginationSection.style.display = 'none';
    initUserStatus();
  }

  async function sync() {
    showToast('Pobieranie wszystkich stron z konta Archivebate...', 'info');
    dom.panelSyncBtn.disabled = true;
    try {
      const res = await fetch('/api/account/sync', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast(`Pobrano: ${data.favorites_count} ulubionych, ${data.history_count} historii, ${data.following_count} obserwowanych!`, 'success');
        updateUserStatus(data);
        if (global.ArchivebateHomeStats && typeof global.ArchivebateHomeStats.update === 'function') {
          await global.ArchivebateHomeStats.update();
        }
        if (state.mode === 'favorites') loadFavorites(1);
        if (state.mode === 'history') loadHistory(1);
        if (state.mode === 'following') loadFollowing(1);
      } else {
        const reason = data.error || (data.status === 'not_configured' ? 'Konto nie jest skonfigurowane.' : `Synchronizacja nieukończona (${data.status || 'unknown'}).`);
        showToast(reason, 'error');
      }
    } catch (e) {
      showToast('Błąd synchronizacji', 'error');
    } finally {
      dom.panelSyncBtn.disabled = false;
    }
  }

  async function clearHistory() {
    if (!confirm('Czy na pewno chcesz wyczyścić historię oglądania?')) return;
    try {
      await fetch('/api/account/history/clear', { method: 'POST' });
      state.historyCount = 0;
      dom.navHistCount.innerText = '0';
      dom.statHistCount.innerText = '0';
      showToast('Historia została wyczyszczona', 'info');
      if (state.mode === 'history') loadHistory(1);
    } catch (e) {
      showToast('Błąd czyszczenia historii', 'error');
    }
  }

  global.ArchivebateAccount = {
    init,
    initUserStatus,
    updateUserStatus,
    showPanel,
    sync,
    clearHistory
  };
})(typeof window !== 'undefined' ? window : globalThis);
