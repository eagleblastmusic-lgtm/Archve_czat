(function (global) {
  'use strict';

  const RUNNING_POLL_MS = 2000;
  const IDLE_POLL_MS = 6000;
  let timer = null;
  let inFlight = false;

  function appState() {
    return global.ArchivebateAppContext?.state || global.state || {};
  }

  function dom() {
    return global.ArchivebateAppContext?.dom || global.dom || {};
  }

  function fmt(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.floor(number)).toLocaleString('pl-PL') : '--';
  }

  function render(deep) {
    const elements = dom();
    if (!elements.jobDeepProgress || !deep || typeof deep !== 'object') return false;

    const running = Boolean(deep.running);
    const complete = Number(deep.models_complete);
    const total = Number(deep.models_discovered);
    const pending = Number(deep.models_pending);
    const items = Number(deep.deep_items);
    const discovery = deep.discovery || {};
    const done = Number(discovery.done);
    const discoveryPending = Number(discovery.pending);
    const split = Number(discovery.split);
    const errors = Number(discovery.error);
    const percent = Number.isFinite(complete) && Number.isFinite(total) && total > 0
      ? (complete / total) * 100
      : null;

    if (elements.jobDeepStatus) {
      elements.jobDeepStatus.textContent = running ? 'W toku' : (deep.last_error ? 'Błąd' : 'Bezczynne');
    }

    if (deep.last_error) {
      elements.jobDeepProgress.textContent = 'Ostatni błąd Deep Archivebate — szczegóły są w diagnostyce.';
      elements.jobDeepProgress.title = String(deep.last_error);
      return running;
    }

    if (!running) {
      elements.jobDeepProgress.textContent = 'Brak aktywnego zadania.';
      elements.jobDeepProgress.title = '';
      return false;
    }

    const parts = [];
    if (Number.isFinite(complete) && Number.isFinite(total)) {
      parts.push(`Modele: ${fmt(complete)}/${fmt(total)}${percent !== null ? ` (${percent.toFixed(1).replace('.', ',')}% znanych)` : ''}`);
    }
    if (Number.isFinite(pending)) parts.push(`oczekuje: ${fmt(pending)}`);
    if (Number.isFinite(items)) parts.push(`wpisy: ${fmt(items)}`);
    if (Number.isFinite(done) || Number.isFinite(discoveryPending)) {
      parts.push(`odkrywanie: ${fmt(done)} gotowe / ${fmt(discoveryPending)} oczekuje`);
    }
    if (Number.isFinite(split)) parts.push(`podziały: ${fmt(split)}`);
    if (Number.isFinite(errors) && errors > 0) parts.push(`błędy gałęzi: ${fmt(errors)}`);
    if (deep.current_model) parts.push(`model: ${deep.current_model}`);
    else if (deep.current_prefix) parts.push(`prefiks: ${deep.current_prefix}`);

    elements.jobDeepProgress.textContent = parts.join(' • ');
    elements.jobDeepProgress.title = 'Postęp odświeża się automatycznie co 2 sekundy podczas pracy Deep Archivebate.';
    return true;
  }

  function schedule(delay) {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(tick, delay);
  }

  async function tick() {
    timer = null;
    const state = appState();
    const elements = dom();

    if (document.hidden || state.mode !== 'account' || !elements.jobDeepProgress || !global.ArchivebateAPI?.getJSON) {
      schedule(IDLE_POLL_MS);
      return;
    }
    if (inFlight) {
      schedule(RUNNING_POLL_MS);
      return;
    }

    inFlight = true;
    try {
      const report = await global.ArchivebateAPI.getJSON('/api/jobs', { timeoutMs: 5000 });
      const running = render(report?.jobs?.deep_archivebate || {});
      schedule(running ? RUNNING_POLL_MS : IDLE_POLL_MS);
    } catch (_) {
      schedule(IDLE_POLL_MS);
    } finally {
      inFlight = false;
    }
  }

  function start() {
    if (timer !== null) return;
    schedule(250);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();

  global.ArchivebateDeepProgressLive = { render, start };
})(typeof window !== 'undefined' ? window : globalThis);
