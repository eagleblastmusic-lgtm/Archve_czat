(function (global) {
  'use strict';

  function init(deps = {}) {
    const showToast = deps.showToast;
    const updateHomeStats = deps.updateHomeStats;
    const btn = document.getElementById('quickScanBtn');
    const stopBtn = document.getElementById('quickScanStopBtn');
    const countSpan = document.getElementById('scannedModelsCount');
    const statusSpan = document.getElementById('quickScanStatus');

    function applyJobStatus(data) {
      const status = String(data?.status || 'idle');
      const active = ['queued', 'running', 'cancelling'].includes(status);
      btn?.classList.toggle('scanning', active);
      if (stopBtn) stopBtn.hidden = !active;
      if (statusSpan) {
        const labels = { queued: 'Skan oczekuje', running: 'Skanowanie w toku', cancelling: 'Zatrzymywanie…', completed: 'Skan zakończony', cancelled: 'Skan anulowany', failed: 'Skan zakończony błędem', idle: '' };
        statusSpan.textContent = labels[status] || status;
      }
    }

    async function updateCount() {
      try {
        const data = global.ArchivebateAPI?.getJSON
          ? await global.ArchivebateAPI.getJSON('/api/scan/status', { timeoutMs: 5000 })
          : await (async () => {
            const res = await fetch('/api/scan/status');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
          })();
        if (countSpan && Number.isFinite(Number(data.indexed_models_count))) {
          countSpan.textContent = `${data.indexed_models_count} profili`;
        }
        applyJobStatus(data);
      } catch (e) {}
    }

    updateCount();
    setInterval(updateCount, 6000);

    if (btn) {
      btn.addEventListener('click', async () => {
        btn.classList.add('scanning');
        try {
          const data = global.ArchivebateAPI?.postJSON
            ? await global.ArchivebateAPI.postJSON('/api/scan/start', {})
            : await (async () => {
              const token = document.querySelector('meta[name="archivebate-mutation-token"]')?.content || '';
              const response = await fetch('/api/scan/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Archivebate-Mutation-Token': token } : {}) },
                body: '{}'
              });
              if (!response.ok) throw new Error(`HTTP ${response.status}`);
              return response.json();
            })();
          if (!data || typeof data.status !== 'string') throw new Error('Nieprawidłowe potwierdzenie skanowania.');
          applyJobStatus(data);
          if (data.accepted === true) showToast('⚡ Uruchomiono szybkie skanowanie i wzbogacanie profili w tle!', 'info');
          else if (data.status === 'already_running') showToast('Skanowanie profili już trwa.', 'info');
          else throw new Error('Nie udało się uruchomić skanowania.');
        } catch (e) {
          showToast(e?.message || 'Nie udało się uruchomić skanowania.', 'error');
        }
        setTimeout(() => {
          btn.classList.remove('scanning');
          updateCount();
          updateHomeStats();
        }, 5000);
      });
    }
    stopBtn?.addEventListener('click', async () => {
      stopBtn.disabled = true;
      try {
        const data = await global.ArchivebateAPI.postJSON('/api/scan/stop', {});
        if (!data || typeof data.status !== 'string') throw new Error('Nieprawidłowe potwierdzenie zatrzymania skanowania.');
        applyJobStatus(data);
        showToast(data.accepted ? 'Zatrzymywanie skanowania zostało zlecone.' : 'Skanowanie nie było aktywne.', 'info');
      } catch (error) {
        showToast(error?.message || 'Nie udało się zatrzymać skanowania.', 'error');
      } finally {
        stopBtn.disabled = false;
      }
    });
  }

  global.ArchivebateProfileScanner = { init };
})(typeof window !== 'undefined' ? window : globalThis);
