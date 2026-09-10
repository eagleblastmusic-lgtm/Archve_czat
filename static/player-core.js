(() => {
  'use strict';

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function ratioFromPointer(event, element) {
    const rect = element.getBoundingClientRect();
    if (!rect.width) return 0;
    return clamp((event.clientX - rect.left) / rect.width, 0, 1);
  }

  function tooltipX(event, element, halfWidth = 84) {
    const rect = element.getBoundingClientRect();
    if (!rect.width) return 0;
    const raw = event.clientX - rect.left;
    if (rect.width <= halfWidth * 2) return rect.width / 2;
    return clamp(raw, halfWidth, rect.width - halfWidth);
  }

  function nextAnimationFrame() {
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  /**
   * `seeked` oznacza tylko koniec operacji seek. Nie gwarantuje, że nowa klatka
   * została już wysłana do kompozytora. To było źródłem "jednego kadru".
   * requestVideoFrameCallback daje nam faktycznie zaprezentowaną klatkę.
   */
  function waitForPresentedFrame(video, expectedTime, timeoutMs = 1200, signal) {
    if (!video || signal?.aborted) return Promise.resolve(false);

    if (typeof video.requestVideoFrameCallback === 'function') {
      return new Promise(resolve => {
        let finished = false;
        let callbackId = null;
        let timer = null;
        const finish = (presented = false) => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          signal?.removeEventListener('abort', onAbort);
          if (callbackId !== null && typeof video.cancelVideoFrameCallback === 'function') {
            try { video.cancelVideoFrameCallback(callbackId); } catch (_) {}
          }
          resolve(presented);
        };
        const onAbort = () => finish(false);
        signal?.addEventListener('abort', onAbort, { once: true });

        // Callback rejestrujemy już po `seeked`, więc pierwszy zaprezentowany frame
        // jest dokładnie tym, na który chcemy czekać. Nie wymagamy idealnego mediaTime:
        // GOP/keyframe może przesunąć go o kilka klatek.
        try {
          callbackId = video.requestVideoFrameCallback(() => finish(true));
          timer = setTimeout(finish, timeoutMs);
        } catch (_) {
          finish();
        }
      });
    }

    return nextAnimationFrame().then(() => !signal?.aborted && video.readyState >= 2 && !video.seeking);
  }

  function createPreviewSeeker(video, options = {}) {
    const minInterval = options.minInterval ?? 45;
    const watchdogMs = options.watchdogMs ?? 1800;
    let requestedTime = null;
    let inFlight = false;
    let timer = null;
    let watchdog = null;
    let lastSeekAt = 0;
    let destroyed = false;
    let seekSerial = 0;
    let activeTarget = null;

    const dispatchPresentedFrame = async (serial, targetTime) => {
      try {
        const presented = await waitForPresentedFrame(video, targetTime, Math.min(1300, watchdogMs));
        if (!presented) return;
      } catch (_) {}
      if (destroyed || serial !== seekSerial) return;
      clearTimeout(watchdog);
      inFlight = false;
      activeTarget = null;
      if (typeof options.onFrame === 'function') {
        options.onFrame(video.currentTime, targetTime, { isLatest: requestedTime === null });
      }
      if (requestedTime !== null) request(requestedTime);
    };

    const onSeeked = () => {
      if (destroyed || !inFlight) return;
      const serial = seekSerial;
      const target = activeTarget;
      dispatchPresentedFrame(serial, target);
    };

    const onError = () => {
      clearTimeout(watchdog);
      inFlight = false;
      activeTarget = null;
      if (typeof options.onError === 'function') options.onError();
      if (requestedTime !== null) request(requestedTime);
    };

    const onMetadata = () => {
      if (requestedTime !== null) request(requestedTime);
    };

    function perform() {
      if (destroyed || !video || inFlight || requestedTime === null) return;
      if (video.readyState < 1 || !Number.isFinite(video.duration) || video.duration <= 0) return;

      const maxTime = Math.max(0, video.duration - 0.05);
      const seekTime = clamp(requestedTime, 0, maxTime);
      requestedTime = null;
      lastSeekAt = performance.now();

      // Nawet gdy target jest bardzo blisko currentTime, poczekaj na prezentację klatki.
      // Dzięki temu po zmianie src / metadata nie pokazujemy starego poster-frame.
      if (Math.abs((video.currentTime || 0) - seekTime) < 0.02) {
        const serial = ++seekSerial;
        inFlight = true;
        activeTarget = seekTime;
        clearTimeout(watchdog);
        watchdog = setTimeout(() => {
          if (serial !== seekSerial) return;
          seekSerial += 1;
          inFlight = false;
          activeTarget = null;
          if (requestedTime !== null) request(requestedTime);
        }, watchdogMs);
        dispatchPresentedFrame(serial, seekTime);
        return;
      }

      const serial = ++seekSerial;
      inFlight = true;
      activeTarget = seekTime;
      clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        if (serial !== seekSerial) return;
        seekSerial += 1;
        inFlight = false;
        activeTarget = null;
        if (requestedTime !== null) request(requestedTime);
      }, watchdogMs);

      try {
        // Nie używamy fastSeek: może stale wybierać ten sam wcześniejszy keyframe.
        video.currentTime = seekTime;
      } catch (_) {
        onError();
      }
    }

    function request(targetTime) {
      if (destroyed || !video || !Number.isFinite(targetTime)) return;
      requestedTime = Math.max(0, targetTime);
      if (video.readyState < 1 || !Number.isFinite(video.duration) || video.duration <= 0) return;
      if (inFlight || video.seeking) return;
      clearTimeout(timer);
      const delay = Math.max(0, minInterval - (performance.now() - lastSeekAt));
      timer = setTimeout(perform, delay);
    }

    function reset() {
      requestedTime = null;
      inFlight = false;
      activeTarget = null;
      seekSerial += 1; // unieważnia oczekujące callbacki starego hovera
      clearTimeout(timer);
      clearTimeout(watchdog);
    }

    function destroy() {
      destroyed = true;
      reset();
      video.removeEventListener('seeked', onSeeked);
      video.removeEventListener('loadedmetadata', onMetadata);
      video.removeEventListener('error', onError);
    }

    video.addEventListener('seeked', onSeeked);
    video.addEventListener('loadedmetadata', onMetadata);
    video.addEventListener('error', onError);

    return { request, reset, destroy };
  }

  function findNearestFrameIndex(times, targetTime) {
    if (!Array.isArray(times) || !times.length) return 0;
    const target = Number(targetTime) || 0;
    let low = 0;
    let high = times.length - 1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      const diff = times[mid] - target;
      if (Math.abs(diff) < 0.001) return mid;
      if (diff < 0) low = mid + 1;
      else high = mid - 1;
    }
    if (low >= times.length) return times.length - 1;
    if (high < 0) return 0;
    return Math.abs(times[low] - target) < Math.abs(times[high] - target) ? low : high;
  }

  window.ArchivebatePlayerCore = {
    clamp,
    ratioFromPointer,
    tooltipX,
    waitForPresentedFrame,
    createPreviewSeeker,
    findNearestFrameIndex
  };
})();
