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

  function formatTime(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    if (hours > 0) {
      return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
    }
    return `${minutes}:${String(remainder).padStart(2, '0')}`;
  }

  function parseDurationToSeconds(value) {
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number') return Number.isFinite(value) ? Math.max(0, value) : 0;
    const parts = String(value).trim().split(':').map(Number);
    if (parts.some(part => !Number.isFinite(part))) return 0;
    if (parts.length === 3) return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2]);
    if (parts.length === 2) return Math.max(0, parts[0] * 60 + parts[1]);
    return Number.isFinite(parts[0]) ? Math.max(0, parts[0]) : 0;
  }

  function setButtonLabel(button, label) {
    if (!button || typeof button.setAttribute !== 'function') return;
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
  }

  function updateVolumeIcon(video, button) {
    if (!button || !video) return;
    const muted = Boolean(video.muted) || Number(video.volume) <= 0;
    const volume = Number(video.volume) || 0;
    if (muted) {
      button.innerHTML = '<i class="fa-solid fa-volume-xmark"></i>';
      setButtonLabel(button, 'Włącz dźwięk (M)');
    } else if (volume < 0.5) {
      button.innerHTML = '<i class="fa-solid fa-volume-low"></i>';
      setButtonLabel(button, 'Wycisz (M)');
    } else {
      button.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
      setButtonLabel(button, 'Wycisz (M)');
    }
    if (button.setAttribute) button.setAttribute('aria-pressed', muted ? 'true' : 'false');
  }

  function setupVolumeControls(video, slider, button) {
    if (!video) return () => {};

    const updateSlider = () => {
      if (slider) {
        slider.value = String(clamp(Number(video.volume) || 0, 0, 1));
        if (slider.setAttribute) slider.setAttribute('aria-valuenow', slider.value);
      }
      updateVolumeIcon(video, button);
    };
    const onSliderInput = (event) => {
      const value = clamp(Number(event?.target?.value) || 0, 0, 1);
      video.volume = value;
      video.muted = value === 0;
      if (value > 0) video._archivebateLastVolume = value;
      updateSlider();
    };
    const onButtonClick = (event) => {
      event?.stopPropagation?.();
      if (video.muted || Number(video.volume) <= 0) {
        const restored = clamp(Number(video._archivebateLastVolume) || 0.8, 0.05, 1);
        video.volume = restored;
        video.muted = false;
      } else {
        video._archivebateLastVolume = clamp(Number(video.volume) || 0.8, 0.05, 1);
        video.muted = true;
      }
      updateSlider();
    };
    const onVolumeChange = () => updateSlider();

    if (slider?.addEventListener) slider.addEventListener('input', onSliderInput);
    if (button?.addEventListener) button.addEventListener('click', onButtonClick);
    video.addEventListener?.('volumechange', onVolumeChange);
    if (!Number.isFinite(Number(video.volume))) video.volume = 1;
    if (Number(video.volume) > 0) video._archivebateLastVolume = clamp(Number(video.volume), 0.05, 1);
    updateSlider();

    return () => {
      slider?.removeEventListener?.('input', onSliderInput);
      button?.removeEventListener?.('click', onButtonClick);
      video.removeEventListener?.('volumechange', onVolumeChange);
    };
  }

  function setupSpeedToggle(video, button) {
    if (!video || !button?.addEventListener) return () => {};
    const speeds = [1, 1.25, 1.5, 2, 0.75];
    let index = Math.max(0, speeds.findIndex(speed => Math.abs(speed - (Number(video.playbackRate) || 1)) < 0.01));
    if (index < 0) index = 0;

    const render = () => {
      const speed = speeds[index];
      video.playbackRate = speed;
      button.innerText = `${speed}x`;
      setButtonLabel(button, `Prędkość odtwarzania: ${speed}x`);
    };
    const onClick = (event) => {
      event?.stopPropagation?.();
      index = (index + 1) % speeds.length;
      render();
    };
    button.addEventListener('click', onClick);
    render();
    return () => button.removeEventListener?.('click', onClick);
  }

  function setupFullscreenToggle(wrapper, button) {
    if (!wrapper || !button?.addEventListener) return () => {};
    const doc = wrapper.ownerDocument || (typeof document !== 'undefined' ? document : null);
    if (!doc) return () => {};

    const render = () => {
      const active = doc.fullscreenElement === wrapper;
      button.innerHTML = active
        ? '<i class="fa-solid fa-compress"></i>'
        : '<i class="fa-solid fa-expand"></i>';
      setButtonLabel(button, active ? 'Wyjdź z pełnego ekranu (F)' : 'Pełny ekran (F)');
      if (button.setAttribute) button.setAttribute('aria-pressed', active ? 'true' : 'false');
    };
    const onClick = async (event) => {
      event?.stopPropagation?.();
      try {
        if (doc.fullscreenElement) {
          await doc.exitFullscreen?.();
        } else if (typeof wrapper.requestFullscreen === 'function') {
          await wrapper.requestFullscreen();
        } else if (typeof wrapper.webkitRequestFullscreen === 'function') {
          wrapper.webkitRequestFullscreen();
        }
      } catch (_) {
        // Przeglądarka może odmówić fullscreen bez gestu użytkownika.
      } finally {
        render();
      }
    };
    button.addEventListener('click', onClick);
    doc.addEventListener?.('fullscreenchange', render);
    render();
    return () => {
      button.removeEventListener?.('click', onClick);
      doc.removeEventListener?.('fullscreenchange', render);
    };
  }

  function setupIdleTimer(wrapper, controls, video, delayMs = 2500) {
    let timer = null;
    let controlsHovered = false;
    const clear = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    };
    const arm = () => {
      clear();
      if (!video?.paused && !video?.ended && !controlsHovered) {
        timer = setTimeout(() => controls?.classList?.add?.('idle'), delayMs);
      }
    };
    const reset = () => {
      controls?.classList?.remove?.('idle');
      arm();
    };
    const onControlsEnter = () => {
      controlsHovered = true;
      clear();
      controls?.classList?.remove?.('idle');
    };
    const onControlsLeave = () => {
      controlsHovered = false;
      reset();
    };
    const onPause = () => {
      clear();
      controls?.classList?.remove?.('idle');
    };
    const onPointerLeave = () => {
      controlsHovered = false;
      if (!video?.paused && !video?.ended) controls?.classList?.add?.('idle');
    };

    wrapper?.addEventListener?.('pointermove', reset, { passive: true });
    wrapper?.addEventListener?.('pointerenter', reset, { passive: true });
    wrapper?.addEventListener?.('pointerleave', onPointerLeave, { passive: true });
    controls?.addEventListener?.('pointerenter', onControlsEnter, { passive: true });
    controls?.addEventListener?.('pointerleave', onControlsLeave, { passive: true });
    video?.addEventListener?.('play', reset);
    video?.addEventListener?.('pause', onPause);
    reset();

    return reset;
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
    formatTime,
    parseDurationToSeconds,
    updateVolumeIcon,
    setupVolumeControls,
    setupSpeedToggle,
    setupFullscreenToggle,
    setupIdleTimer,
    waitForPresentedFrame,
    createPreviewSeeker,
    findNearestFrameIndex
  };
})();
