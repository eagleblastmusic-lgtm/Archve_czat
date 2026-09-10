(function (global) {
  'use strict';

  function init(deps = {}) {
    const showToast = deps.showToast;
    const updateHomeStats = deps.updateHomeStats;
    const btn = document.getElementById('quickScanBtn');
    const countSpan = document.getElementById('scannedModelsCount');

    async function updateCount() {
      try {
        const res = await fetch('/api/scan/status');
        if (res.ok) {
          const data = await res.json();
          if (countSpan && data.indexed_models_count) {
            countSpan.innerText = `${data.indexed_models_count} profili`;
          }
        }
      } catch (e) {}
    }

    updateCount();
    setInterval(updateCount, 6000);

    if (btn) {
      btn.addEventListener('click', async () => {
        btn.classList.add('scanning');
        showToast('⚡ Uruchomiono szybkie skanowanie i wzbogacanie profili w tle!', 'info');
        try {
          await fetch('/api/scan/start', { method: 'POST' });
        } catch (e) {}
        setTimeout(() => {
          btn.classList.remove('scanning');
          updateCount();
          updateHomeStats();
        }, 5000);
      });
    }
  }

  global.ArchivebateProfileScanner = { init };
})(typeof window !== 'undefined' ? window : globalThis);
