(() => {
  'use strict';

  class ApiError extends Error {
    constructor(message, status = 0, code = 'network_error', details = null) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  function friendlyMessage(status, fallback = 'Nie udało się wykonać żądania.') {
    if (status === 401 || status === 403) return 'Sesja wygasła albo źródło odmówiło dostępu.';
    if (status === 404) return 'Żądany materiał nie jest już dostępny.';
    if (status === 408 || status === 504) return 'Serwer odpowiada zbyt wolno. Spróbuj ponownie.';
    if (status === 429) return 'Za dużo zapytań w krótkim czasie. Spróbuj ponownie za chwilę.';
    if (status >= 500) return 'Źródło lub lokalny serwer chwilowo nie odpowiada.';
    return fallback;
  }

  async function request(url, options = {}, decodeJSON = false) {
    const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : 12000;
    const controller = new AbortController();
    const externalSignal = options.signal;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort('timeout'); }, timeoutMs);
    const onAbort = () => controller.abort(externalSignal.reason);

    if (externalSignal) {
      if (externalSignal.aborted) controller.abort(externalSignal.reason);
      else externalSignal.addEventListener('abort', onAbort, { once: true });
    }

    const init = { ...options, signal: controller.signal };
    delete init.timeoutMs;

    try {
      const res = await fetch(url, init);
      if (!res.ok) {
        let details = null;
        try { details = await res.json(); } catch (err) { if (controller.signal.aborted) throw err; }
        throw new ApiError(
          (details && (details.detail || details.message)) || friendlyMessage(res.status),
          res.status,
          'http_error',
          details
        );
      }
      return decodeJSON ? await res.json() : res;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (controller.signal.aborted) {
        throw new ApiError(timedOut ? 'Przekroczono czas oczekiwania na odpowiedź.' : 'Anulowano żądanie.', timedOut ? 408 : 0, timedOut ? 'timeout' : 'cancelled');
      }
      if (!navigator.onLine) {
        throw new ApiError('Brak połączenia z internetem.', 0, 'offline');
      }
      throw new ApiError(err && err.message ? err.message : 'Błąd połączenia.', 0, 'network_error');
    } finally {
      clearTimeout(timer);
      externalSignal?.removeEventListener('abort', onAbort);
    }
  }

  async function getJSON(url, options = {}) {
    return request(url, { cache: 'no-store', ...options }, true);
  }

  async function postJSON(url, body, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    return request(url, {
      method: 'POST',
      ...options,
      headers,
      body: JSON.stringify(body ?? {})
    }, true);
  }

  window.ArchivebateAPI = { ApiError, request, getJSON, postJSON, friendlyMessage };
})();
