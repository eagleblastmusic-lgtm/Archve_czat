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
  let diagnosticsText = '';
  let jobsRequestGeneration = 0;
  let jobsPollTimer = null;

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
    dom.accountExportBtn?.addEventListener('click', exportAccount);
    dom.accountRestoreBtn?.addEventListener('click', () => dom.accountRestoreFile?.click());
    dom.accountRestoreFile?.addEventListener('change', restoreFromFile);
    dom.diagnosticsBtn?.addEventListener('click', toggleDiagnostics);
    dom.diagnosticsRefreshBtn?.addEventListener('click', loadDiagnostics);
    dom.diagnosticsCopyBtn?.addEventListener('click', copyDiagnostics);
    dom.jobsRefreshBtn?.addEventListener('click', refreshJobs);
    dom.jobsDeepStartBtn?.addEventListener('click', startDeepJob);
    dom.jobsDeepStopBtn?.addEventListener('click', stopDeepJob);

    dom.reloginBtn.addEventListener('click', async () => {
      showToast('Synchronizacja konta...', 'info');
      try {
        const data = await global.ArchivebateAPI.postJSON('/api/relogin', {});
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
      // A client-side timeout is a transport/liveness problem, not evidence
      // that authentication failed. Keep the error visible without lying
      // about the session; a later retry can still promote the real status.
      dom.userEmail.innerText = 'Status chwilowo niedostępny';
      dom.userEmail.title = 'Nie udało się odczytać lokalnego statusu. Spróbuj ponownie za chwilę.';
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

    const readCount = (value, fallback) => {
      const numeric = Number(value);
      return Number.isFinite(numeric) && numeric >= 0 ? Math.floor(numeric) : fallback;
    };
    const displayCount = value => value == null ? '--' : value.toLocaleString('pl-PL');
    state.favoritesCount = readCount(status.favorites_count, state.favoritesCount);
    state.historyCount = readCount(status.history_count, state.historyCount);
    state.followingCount = readCount(status.following_count, state.followingCount);

    dom.navFavCount.textContent = displayCount(state.favoritesCount);
    dom.navHistCount.textContent = displayCount(state.historyCount);
    dom.statFavCount.textContent = displayCount(state.favoritesCount);
    dom.statHistCount.textContent = displayCount(state.historyCount);
    dom.statFollCount.textContent = displayCount(state.followingCount);

    if (status.favorite_authors) {
      state.favoriteAuthors = new Set(status.favorite_authors.map(a => String(a).toLowerCase().trim()));
      updateAllAuthorNameColors();
    }

    if (status.last_synced) {
      dom.panelLastSync.replaceChildren();
      const icon = document.createElement('i');
      icon.className = 'fa-solid fa-clock';
      icon.setAttribute('aria-hidden', 'true');
      dom.panelLastSync.append(icon, document.createTextNode(` Ostatnia synchronizacja: ${status.last_synced}`));
    }
    if (dom.accountStatusBadge) {
      const configured = Boolean(status.account_configured);
      dom.accountStatusBadge.textContent = status.logged_in ? 'Połączone' : (configured ? 'Offline' : 'Anonimowe');
      dom.accountStatusBadge.classList.toggle('is-online', Boolean(status.logged_in));
      dom.accountStatusBadge.classList.toggle('is-offline', !status.logged_in);
      dom.accountStatusBadge.setAttribute('aria-label', `Stan konta: ${dom.accountStatusBadge.textContent}`);
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
    if (dom.paginationSectionTop) dom.paginationSectionTop.style.display = 'none';
    initUserStatus();
    void refreshJobs();
  }

  function jobLabel(status) {
    const labels = {
      idle: 'Bezczynne',
      queued: 'W kolejce',
      running: 'W toku',
      cancelling: 'Zatrzymywanie…',
      completed: 'Zakończone',
      cancelled: 'Anulowane',
      failed: 'Błąd',
      unavailable: 'Niedostępne',
      unknown: 'Nieznany'
    };
    return labels[String(status || 'unknown')] || String(status);
  }

  function renderJobs(report) {
    const jobs = report?.jobs || {};
    const sync = jobs.account_sync || {};
    const quick = jobs.quick_scan || {};
    const deep = jobs.deep_archivebate || {};
    const deepRunning = Boolean(deep.running);
    const quickStatus = String(quick.status || 'idle');
    const overall = String(report?.status || 'unknown');

    if (dom.jobsOverallStatus) dom.jobsOverallStatus.textContent = `Stan: ${jobLabel(overall)}`;
    if (dom.jobSyncStatus) dom.jobSyncStatus.textContent = jobLabel(sync.status);
    if (dom.jobQuickStatus) dom.jobQuickStatus.textContent = jobLabel(quickStatus);
    if (dom.jobDeepStatus) dom.jobDeepStatus.textContent = jobLabel(deepRunning ? 'running' : (deep.status || (deep.last_error ? 'failed' : 'idle')));
    if (dom.jobDeepProgress) {
      if (deep.last_error) {
        dom.jobDeepProgress.textContent = 'Ostatnia próba zakończyła się błędem; szczegóły są w diagnostyce.';
      } else if (deepRunning) {
        const complete = Number(deep.models_complete);
        const total = Number(deep.models_discovered);
        const items = Number(deep.deep_items);
        const counts = Number.isFinite(complete) && Number.isFinite(total) ? `Modele: ${complete}/${total}` : 'Przetwarzanie modeli';
        dom.jobDeepProgress.textContent = Number.isFinite(items) ? `${counts} • wpisy: ${items}` : counts;
      } else {
        dom.jobDeepProgress.textContent = 'Brak aktywnego zadania.';
      }
    }
    if (dom.jobsDeepStartBtn) dom.jobsDeepStartBtn.disabled = deepRunning;
    if (dom.jobsDeepStopBtn) dom.jobsDeepStopBtn.disabled = !deepRunning;
  }

  function scheduleJobsPoll() {
    if (jobsPollTimer !== null) clearTimeout(jobsPollTimer);
    jobsPollTimer = state.mode === 'account' ? setTimeout(() => {
      jobsPollTimer = null;
      void refreshJobs();
    }, 6000) : null;
  }

  async function refreshJobs() {
    if (!dom.jobsOverallStatus || !global.ArchivebateAPI?.getJSON) return;
    const generation = ++jobsRequestGeneration;
    try {
      const report = await global.ArchivebateAPI.getJSON('/api/jobs', { timeoutMs: 8000 });
      if (generation !== jobsRequestGeneration) return;
      renderJobs(report);
    } catch (error) {
      if (generation !== jobsRequestGeneration) return;
      renderJobs({ status: 'unavailable', jobs: { account_sync: { status: 'unavailable' }, quick_scan: { status: 'unavailable' }, deep_archivebate: { status: 'unavailable' } } });
    } finally {
      if (generation === jobsRequestGeneration) scheduleJobsPoll();
    }
  }

  async function startDeepJob() {
    if (!global.ArchivebateAPI?.postJSON) return;
    if (dom.jobsDeepStartBtn) dom.jobsDeepStartBtn.disabled = true;
    try {
      const result = await global.ArchivebateAPI.postJSON('/api/deep-archivebate/start', {});
      renderJobs({ status: result?.running ? 'running' : 'idle', jobs: { account_sync: { status: 'idle' }, quick_scan: { status: 'idle' }, deep_archivebate: result || {} } });
      showToast(result?.started ? 'Głębokie odkrywanie uruchomione.' : 'Głębokie odkrywanie już działa.', 'info');
    } catch (error) {
      showToast(error?.message || 'Nie udało się uruchomić głębokiego odkrywania.', 'error');
    } finally {
      void refreshJobs();
    }
  }

  async function stopDeepJob() {
    if (!global.ArchivebateAPI?.postJSON) return;
    if (dom.jobsDeepStopBtn) dom.jobsDeepStopBtn.disabled = true;
    try {
      await global.ArchivebateAPI.postJSON('/api/deep-archivebate/stop', {});
      showToast('Zatrzymywanie głębokiego odkrywania zostało zlecone.', 'info');
    } catch (error) {
      showToast(error?.message || 'Nie udało się zatrzymać głębokiego odkrywania.', 'error');
    } finally {
      void refreshJobs();
    }
  }

  async function sync() {
    showToast('Pobieranie wszystkich stron z konta Archivebate...', 'info');
    dom.panelSyncBtn.disabled = true;
    try {
      const data = await global.ArchivebateAPI.postJSON('/api/account/sync', {});
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
      await global.ArchivebateAPI.postJSON('/api/account/history/clear', {});
      state.historyCount = 0;
      dom.navHistCount.innerText = '0';
      dom.statHistCount.innerText = '0';
      showToast('Historia została wyczyszczona', 'info');
      if (state.mode === 'history') loadHistory(1);
    } catch (e) {
      showToast('Błąd czyszczenia historii', 'error');
    }
  }

  async function exportAccount() {
    if (!global.ArchivebateAPI?.getJSON) return;
    const button = dom.accountExportBtn;
    if (button) button.disabled = true;
    try {
      const snapshot = await global.ArchivebateAPI.getJSON('/api/account/export', { timeoutMs: 12000 });
      const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `archivebite-account-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast('Eksport danych został przygotowany.', 'success');
    } catch (error) {
      showToast(error?.message || 'Nie udało się wyeksportować danych.', 'error');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function restoreFromFile(event) {
    const input = event?.target;
    const file = input?.files?.[0];
    if (!file || !global.ArchivebateAPI?.postJSON) return;
    try {
      const snapshot = JSON.parse(await file.text());
      const preview = await global.ArchivebateAPI.postJSON('/api/account/restore/preview', snapshot, { timeoutMs: 12000 });
      if (!preview?.success) throw new Error('Plik nie przeszedł walidacji.');
      const confirmed = confirm(
        `Przywrócić ${preview.favorites} ulubionych, ${preview.history} wpisów historii i ${preview.following} obserwowanych?\n\n` +
        'Istniejący lokalny magazyn zostanie zastąpiony kopią z pliku. Operacja nie usuwa pliku źródłowego.'
      );
      if (!confirmed) return;
      const result = await global.ArchivebateAPI.postJSON('/api/account/restore', snapshot, { timeoutMs: 12000 });
      if (!result?.success) throw new Error('Przywracanie nie zostało potwierdzone przez serwer.');
      showToast('Dane zostały przywrócone. Widoki zostaną odświeżone.', 'success');
      await initUserStatus();
      if (state.mode === 'favorites') loadFavorites(1);
      if (state.mode === 'history') loadHistory(1);
      if (state.mode === 'following') loadFollowing(1);
    } catch (error) {
      showToast(error?.message || 'Nie udało się przywrócić danych.', 'error');
    } finally {
      if (input) input.value = '';
    }
  }

  async function loadDiagnostics() {
    if (!dom.diagnosticsOutput || !global.ArchivebateAPI?.getJSON) return;
    dom.diagnosticsOutput.textContent = 'Pobieranie raportu…';
    if (dom.diagnosticsCopyBtn) dom.diagnosticsCopyBtn.disabled = true;
    try {
      const report = await global.ArchivebateAPI.getJSON('/api/diagnostics', { timeoutMs: 12000 });
      diagnosticsText = JSON.stringify(report, null, 2);
      dom.diagnosticsOutput.textContent = diagnosticsText;
      if (dom.diagnosticsCopyBtn) dom.diagnosticsCopyBtn.disabled = false;
    } catch (error) {
      diagnosticsText = '';
      dom.diagnosticsOutput.textContent = error?.message || 'Nie udało się pobrać diagnostyki.';
      showToast(dom.diagnosticsOutput.textContent, 'error');
    }
  }

  function toggleDiagnostics() {
    if (!dom.diagnosticsPanel) return;
    const opening = dom.diagnosticsPanel.hidden;
    dom.diagnosticsPanel.hidden = false;
    dom.diagnosticsPanel.open = opening;
    if (opening) void loadDiagnostics();
  }

  async function copyDiagnostics() {
    if (!diagnosticsText) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Schowek jest niedostępny w tej przeglądarce.');
      await navigator.clipboard.writeText(diagnosticsText);
      showToast('Raport diagnostyczny skopiowany.', 'success');
    } catch (error) {
      showToast(error?.message || 'Nie udało się skopiować raportu.', 'error');
    }
  }

  global.ArchivebateAccount = {
    init,
    initUserStatus,
    updateUserStatus,
    showPanel,
    sync,
    clearHistory,
    exportAccount,
    restoreFromFile,
    loadDiagnostics,
    toggleDiagnostics,
    copyDiagnostics
  };
})(typeof window !== 'undefined' ? window : globalThis);
