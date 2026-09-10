(() => {
  'use strict';

  const memory = new Map();
  const preloadMemory = new Map();
  const upgradeWatchers = new Map();

  // Współdzielenie upgrade'u QUICK → FULL między kartami tej samej aplikacji.
  // BroadcastChannel przenosi gotowy manifest, a krótka dzierżawa w localStorage
  // wybiera jedną kartę, która odpytuje backend. Gdy przeglądarka blokuje któryś
  // z mechanizmów, watcher działa tak jak wcześniej lokalnie.
  const CROSS_TAB_LEASE_PREFIX = 'archivebate:storyboard-upgrade:';
  const CROSS_TAB_LEASE_MS = 8000;
  const crossTabId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;

  function makeCrossTabBridge() {
    let channel = null;
    let storageAvailable = false;
    try {
      if (typeof BroadcastChannel === 'function') channel = new BroadcastChannel('archivebate-storyboard-upgrade-v1');
      if (typeof localStorage !== 'undefined') {
        const probe = `${CROSS_TAB_LEASE_PREFIX}probe`;
        localStorage.setItem(probe, crossTabId);
        localStorage.removeItem(probe);
        storageAvailable = true;
      }
    } catch (_) {
      try { channel?.close?.(); } catch (_) {}
      channel = null;
      storageAvailable = false;
    }

    const entries = new Map();
    const enabled = Boolean(channel && storageAvailable);
    const storageKey = key => `${CROSS_TAB_LEASE_PREFIX}${encodeURIComponent(key)}`;

    const readLease = key => {
      if (!storageAvailable) return null;
      try {
        const raw = localStorage.getItem(storageKey(key));
        const lease = raw ? JSON.parse(raw) : null;
        return lease && Number(lease.expiresAt) > Date.now() ? lease : null;
      } catch (_) {
        return null;
      }
    };

    const post = message => {
      if (!channel) return;
      try { channel.postMessage({ ...message, sender: crossTabId }); } catch (_) {}
    };

    const claim = key => {
      if (!enabled) return false;
      const now = Date.now();
      const current = readLease(key);
      if (current && current.owner !== crossTabId) return false;
      const candidate = { owner: crossTabId, expiresAt: now + CROSS_TAB_LEASE_MS };
      try {
        localStorage.setItem(storageKey(key), JSON.stringify(candidate));
        const verified = JSON.parse(localStorage.getItem(storageKey(key)) || '{}');
        return verified.owner === crossTabId && Number(verified.expiresAt) >= candidate.expiresAt - 100;
      } catch (_) {
        return false;
      }
    };

    const renew = key => {
      if (!enabled) return 0;
      const current = readLease(key);
      if (!current || current.owner !== crossTabId) return 0;
      const expiresAt = Date.now() + CROSS_TAB_LEASE_MS;
      try {
        localStorage.setItem(storageKey(key), JSON.stringify({ owner: crossTabId, expiresAt }));
        return expiresAt;
      } catch (_) {
        return 0;
      }
    };

    const release = key => {
      if (!storageAvailable) return;
      try {
        const current = JSON.parse(localStorage.getItem(storageKey(key)) || '{}');
        if (current.owner === crossTabId) localStorage.removeItem(storageKey(key));
      } catch (_) {}
    };

    const cacheBoard = async (key, payload, entry) => {
      if (!payload || !payload.sprite_url) return;
      try {
        const board = await normalizeReadyBoard(payload, null);
        memory.set(key, board);
        while (memory.size > 64) memory.delete(memory.keys().next().value);
        entry?.onReady?.(board);
      } catch (_) {
        entry?.onFailed?.();
      }
    };

    const onMessage = event => {
      const message = event?.data || {};
      if (!message || message.sender === crossTabId || !message.key) return;
      const entry = entries.get(message.key);
      if (message.type === 'request') {
        if (entry?.role === 'leader') {
          const expiresAt = renew(message.key);
          post({ type: 'owner', key: message.key, owner: crossTabId, expiresAt });
        }
        return;
      }
      if (message.type === 'owner') {
        if (entry?.role === 'pending' && message.owner && message.owner !== crossTabId) {
          entry.onOwner?.(Number(message.expiresAt) || (Date.now() + CROSS_TAB_LEASE_MS));
        }
        return;
      }
      if (message.type === 'ready') {
        if (entry?.role === 'follower' || entry?.role === 'pending') {
          entry.onReadyMessage?.(message.board);
        } else if (!entry) {
          // Karta może otworzyć storyboard chwilę po publikacji. Zachowaj
          // obraz także wtedy, gdy nie ma już lokalnego subskrybenta.
          cacheBoard(message.key, message.board, null);
        }
        return;
      }
      if (message.type === 'failed' && entry?.role === 'follower') {
        entry.onFailed?.();
      }
    };
    try { channel?.addEventListener('message', onMessage); } catch (_) {}

    return {
      enabled,
      register(key, entry) { if (enabled) entries.set(key, entry); },
      unregister(key, entry) { if (entries.get(key) === entry) entries.delete(key); },
      claim,
      renew,
      release,
      request(key, videoId, duration) { post({ type: 'request', key, videoId, duration }); },
      announceOwner(key, videoId, duration, expiresAt) { post({ type: 'owner', key, videoId, duration, owner: crossTabId, expiresAt }); },
      announceReady(key, board) {
        if (!board) return;
        const { _image, ...payload } = board;
        post({ type: 'ready', key, board: payload });
      },
      announceFailed(key) { post({ type: 'failed', key }); },
      cacheBoard,
    };
  }

  // Testy uruchamiane bez przeglądarkowych API dostają null i zachowują ścieżkę
  // lokalną. W realnej karcie własność obiektu jest tylko pomocnicza i nie trafia
  // do kodu aplikacji poza tym modułem.
  const crossTabBridge = makeCrossTabBridge();
  try { globalThis.__ArchivebateStoryboardCrossTab = crossTabBridge; } catch (_) {}

  const SEGMENT_DURATION = 30;
  const segmentMemory = new Map();
  const segmentInFlight = new Map();

  function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
      const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
      const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
      signal?.addEventListener('abort', abort, { once: true });
    });
  }

  function cacheKey(videoId, duration) {
    return `${videoId}:${Math.max(1, Math.round(Number(duration) || 0))}`;
  }

  function segmentKey(videoId, duration, segmentIndex) {
    return `${cacheKey(videoId, duration)}:seg_${segmentIndex}`;
  }

  function findNearestIndex(times, targetTime) {
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

  function consume(promise, signal) {
    if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
    return new Promise((resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'));
      signal?.addEventListener('abort', abort, {once: true});
      promise.then(resolve, reject).finally(() => signal?.removeEventListener('abort', abort));
    });
  }

  async function preload(url, signal) {
    if (!url) throw new Error('Brak sprite_url');
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    let promise = preloadMemory.get(url);
    if (!promise) {
      promise = new Promise((resolve, reject) => {
        const img = new Image();
        const timer = setTimeout(() => finish(new Error('Sprite timeout')), 15000);
        const finish = error => {
          clearTimeout(timer);
          img.onload = img.onerror = null;
          error ? reject(error) : resolve(img);
        };
        img.onload = () => finish();
        img.onerror = () => finish(new Error('Sprite load failed'));
        img.src = url;
      });
      preloadMemory.set(url, promise);
      promise.catch(() => { if (preloadMemory.get(url) === promise) preloadMemory.delete(url); });
      while (preloadMemory.size > 64) preloadMemory.delete(preloadMemory.keys().next().value);
    }
    return consume(promise, signal);
  }

  async function fetchStatus(videoId, duration, signal) {
    const endpoint = `/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`;
    const res = await fetch(endpoint, { cache: 'no-store', signal });
    if (!res.ok) throw new Error(`Storyboard HTTP ${res.status}`);
    return res.json();
  }

  // Tylko uruchamia generator w tle. Nie polluje i nie pobiera sprite'a.
  function warm({ videoId, duration }) {
    if (!videoId || !Number.isFinite(Number(duration)) || Number(duration) <= 0) return;
    const key = cacheKey(videoId, duration);
    const existing = memory.get(key);
    if (existing?.quality === 'full') return;
    fetch(`/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`, {
      cache: 'no-store',
      priority: 'low', method: 'POST'
    }).catch(() => {});
  }

  async function normalizeReadyBoard(data, signal) {
    const img = await preload(data.sprite_url, signal);
    return { ...data, _image: img };
  }

  function startUpgradeWatcher({ videoId, duration, key, signal, onUpgrade }) {
    if (!onUpgrade) return;
    if (signal?.aborted) return;
    let shared = upgradeWatchers.get(key);
    const subscriber = { onUpgrade, signal };
    const bridge = typeof globalThis !== 'undefined' ? globalThis.__ArchivebateStoryboardCrossTab : null;
    const detach = () => {
      shared.subscribers.delete(subscriber);
      signal?.removeEventListener('abort', detach);
      if (!shared.subscribers.size) {
        globalThis.clearTimeout?.(shared.fallbackTimer);
        globalThis.clearInterval?.(shared.heartbeat);
        if (shared.bridge) {
          shared.bridge.unregister(key, shared.bridgeEntry);
          if (shared.role === 'leader') shared.bridge.release(key);
        }
        shared.controller.abort();
        if (upgradeWatchers.get(key) === shared) upgradeWatchers.delete(key);
      }
    };
    subscriber.detach = detach;
    if (shared) {
      shared.subscribers.add(subscriber);
      signal?.addEventListener('abort', detach, { once: true });
      return;
    }
    shared = {
      controller: new AbortController(),
      subscribers: new Set([subscriber]),
      role: bridge?.enabled ? 'pending' : 'leader',
      bridge: bridge?.enabled ? bridge : null,
      bridgeEntry: null,
      fallbackTimer: null,
      heartbeat: null,
      ownerUntil: 0,
    };
    upgradeWatchers.set(key, shared);
    signal?.addEventListener('abort', detach, { once: true });
    const pollingSignal = shared.controller.signal;

    const notify = board => {
      for (const listener of shared.subscribers) {
        if (!listener.signal?.aborted) {
          try { listener.onUpgrade(board); }
          catch (err) { console.debug('[Storyboard subscriber]', err); }
        }
      }
    };

    const finishFromPeer = board => {
      if (!board || pollingSignal.aborted) return;
      memory.set(key, board);
      while (memory.size > 64) memory.delete(memory.keys().next().value);
      notify(board);
      for (const listener of [...shared.subscribers]) listener.detach();
    };

    const becomeLeader = () => {
      if (pollingSignal.aborted || shared.role === 'leader') return;
      if (shared.bridge && !shared.bridge.claim(key)) {
        shared.role = 'follower';
        shared.bridge.request(key, videoId, duration);
        return;
      }
      shared.role = 'leader';
      if (shared.bridgeEntry) shared.bridgeEntry.role = 'leader';
      const expiresAt = shared.bridge?.renew(key) || (Date.now() + CROSS_TAB_LEASE_MS);
      shared.ownerUntil = expiresAt;
      shared.bridge?.announceOwner(key, videoId, duration, expiresAt);
      if (shared.bridge) {
        shared.heartbeat = setInterval(() => {
          if (pollingSignal.aborted) return;
          const renewed = shared.bridge.renew(key);
          if (renewed) shared.ownerUntil = renewed;
        }, Math.floor(CROSS_TAB_LEASE_MS / 3));
      }
      runPolling();
    };

    const scheduleClaim = delayMs => {
      globalThis.clearTimeout?.(shared.fallbackTimer);
      const delay = Math.max(120, Number(delayMs) || 0);
      shared.fallbackTimer = setTimeout(() => {
        if (pollingSignal.aborted || shared.role === 'leader') return;
        if (shared.bridge?.claim(key)) {
          becomeLeader();
        } else {
          shared.role = 'follower';
          if (shared.bridgeEntry) shared.bridgeEntry.role = 'follower';
          shared.bridge?.request(key, videoId, duration);
          scheduleClaim(CROSS_TAB_LEASE_MS + 100);
        }
      }, delay);
    };

    const runPolling = () => {
      if (shared.promise) return;
      shared.promise = (async () => {
      try {
        for (let attempt = 0; attempt < 90; attempt += 1) {
          if (pollingSignal.aborted) return;
          await sleep(attempt < 8 ? 350 : 700, pollingSignal);
          const data = await fetchStatus(videoId, duration, pollingSignal);
          if (data.status === 'ready' && data.quality === 'full' && data.sprite_url) {
            const board = await normalizeReadyBoard(data, pollingSignal);
            memory.set(key, board);
            while (memory.size > 64) memory.delete(memory.keys().next().value);
            shared.bridge?.announceReady(key, board);
            notify(board);
            return;
          }
          if (data.status === 'error' || data.upgrade_status === 'error') {
            shared.bridge?.announceFailed(key);
            return;
          }
        }
      } catch (err) {
        if (err?.name !== 'AbortError') {
          shared.bridge?.announceFailed(key);
          console.debug('[Storyboard upgrade]', err);
        }
      } finally {
        globalThis.clearTimeout?.(shared.fallbackTimer);
        globalThis.clearInterval?.(shared.heartbeat);
        if (shared.bridge && shared.role === 'leader') shared.bridge.release(key);
        for (const listener of [...shared.subscribers]) listener.detach();
        if (upgradeWatchers.get(key) === shared) upgradeWatchers.delete(key);
      }
      })();
    };

    if (shared.bridge) {
      shared.bridgeEntry = {
        role: shared.role,
        onOwner: expiresAt => {
          if (shared.role !== 'pending') return;
          shared.role = 'follower';
          shared.bridgeEntry.role = 'follower';
          shared.ownerUntil = expiresAt;
          globalThis.clearTimeout?.(shared.fallbackTimer);
          scheduleClaim(Math.max(200, expiresAt - Date.now() + 100));
        },
        onReadyMessage: board => {
          if (shared.role !== 'follower' && shared.role !== 'pending') return;
          shared.role = 'follower';
          shared.bridgeEntry.role = 'follower';
          shared.bridge.cacheBoard(key, board, { onReady: finishFromPeer, onFailed: () => scheduleClaim(250) });
        },
        onFailed: () => scheduleClaim(250),
      };
      shared.bridge.register(key, shared.bridgeEntry);
      shared.bridge.request(key, videoId, duration);
      // Daj istniejącemu liderowi chwilę na odpowiedź, zanim ta karta przejmie
      // dzierżawę. Przy zimnym starcie dokładnie jedna karta wygra claim().
      scheduleClaim(350);
    } else {
      runPolling();
    }
  }

  async function prepare({ videoId, duration, signal, onStatus, onUpgrade }) {
    duration = Number(duration);
    if (!videoId || !Number.isFinite(duration) || duration <= 0) {
      throw new Error('Brak ID lub długości filmu');
    }

    if(signal?.aborted)throw new DOMException('Aborted','AbortError');
    const key = cacheKey(videoId, duration);
    const cached = memory.get(key);
    if (cached?.quality === 'full') return cached;
    const consumer=globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const leaseUrl=`/api/storyboard/demand?id=${encodeURIComponent(videoId)}&consumer=${encodeURIComponent(consumer)}`;
    const leaseResponse=await fetch(leaseUrl,{method:'POST',signal});
    if(!leaseResponse.ok)throw new Error('Storyboard demand unavailable');
    let timer;
    let expiry;
    const release=()=>{
      clearInterval(timer);clearTimeout(expiry);
      signal?.removeEventListener('abort',release);
      fetch(leaseUrl,{method:'DELETE',keepalive:true}).catch(()=>{});
    };
    timer=setInterval(()=>fetch(leaseUrl,{method:'POST'}).catch(()=>{}),20000);
    expiry=setTimeout(release,120000);
    signal?.addEventListener('abort',release,{once:true});
    if(signal?.aborted){release();throw new DOMException('Aborted','AbortError');}
    try {
    const buildResponse = await fetch(`/api/storyboard?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}`, {method:'POST',signal});
    if (!buildResponse.ok) { release(); throw new Error(`Storyboard HTTP ${buildResponse.status}`); }
    if (cached) {
      if (cached.quality !== 'full') startUpgradeWatcher({ videoId, duration, key, signal, onUpgrade });
      return cached;
    }

    let lastError = '';
    for (let attempt = 0; attempt < 180; attempt += 1) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      onStatus?.(attempt === 0 ? 'start' : 'building');
      const data = await fetchStatus(videoId, duration, signal);

      if (data.status === 'ready' && data.sprite_url) {
        const board = await normalizeReadyBoard(data, signal);
        memory.set(key, board);
            while (memory.size > 64) memory.delete(memory.keys().next().value);
        onStatus?.('ready');
        if (board.quality !== 'full') {
          startUpgradeWatcher({ videoId, duration, key, signal, onUpgrade });
        }
        return board;
      }
      if (data.status === 'error') {
        lastError = data.error || 'Nie udało się przygotować storyboardu';
        break;
      }

      // Pierwsze 2 sekundy pollujemy często, żeby nie dodawać sztucznego ~850 ms lag.
      await sleep(attempt < 12 ? 160 : 420, signal);
    }
    throw new Error(lastError || 'Przekroczono czas przygotowania storyboardu');
    } catch (error) {
      release();
      throw error;
    }
  }

  async function fetchSegmentStatus(videoId, duration, segmentIndex, signal) {
    const endpoint = `/api/storyboard/segment?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}&segment=${encodeURIComponent(segmentIndex)}`;
    const res = await fetch(endpoint, { cache: 'no-store', signal });
    if (!res.ok) throw new Error(`Storyboard segment HTTP ${res.status}`);
    return res.json();
  }

  async function prepareSegment({ videoId, duration, segmentIndex, signal }) {
    duration = Number(duration);
    segmentIndex = Number(segmentIndex) || 0;
    if (!videoId || !Number.isFinite(duration) || duration <= 0 || segmentIndex < 0) {
      throw new Error('Niepoprawne parametry segmentu');
    }
    const key = segmentKey(videoId, duration, segmentIndex);
    const cached = segmentMemory.get(key);
    if (cached) return cached;

    let inFlight = segmentInFlight.get(key);
    if (inFlight) {
      return consume(inFlight, signal);
    }

    const execute = (async () => {
      try {
        const postUrl = `/api/storyboard/segment?id=${encodeURIComponent(videoId)}&duration=${encodeURIComponent(duration)}&segment=${encodeURIComponent(segmentIndex)}&prefetch_next=true`;
        const startRes = await fetch(postUrl, { method: 'POST', cache: 'no-store' });
        if (!startRes.ok) throw new Error(`Segment start HTTP ${startRes.status}`);
        const startData = await startRes.json();
        if (startData.status === 'ready' && startData.sprite_url) {
          const board = await normalizeReadyBoard(startData, null);
          segmentMemory.set(key, board);
          while (segmentMemory.size > 32) segmentMemory.delete(segmentMemory.keys().next().value);
          return board;
        }

        for (let attempt = 0; attempt < 80; attempt += 1) {
          await sleep(attempt < 8 ? 120 : 250, null);
          const data = await fetchSegmentStatus(videoId, duration, segmentIndex, null);
          if (data.status === 'ready' && data.sprite_url) {
            const board = await normalizeReadyBoard(data, null);
            segmentMemory.set(key, board);
            while (segmentMemory.size > 32) segmentMemory.delete(segmentMemory.keys().next().value);
            return board;
          }
          if (data.status === 'error') throw new Error(data.error || 'Segment error');
        }
        throw new Error('Przekroczono czas przygotowania segmentu');
      } finally {
        segmentInFlight.delete(key);
      }
    })();

    segmentInFlight.set(key, execute);
    return consume(execute, signal);
  }

  function getSegmentFromCache(videoId, duration, targetTime) {
    const segmentIndex = Math.max(0, Math.floor((Number(targetTime) || 0) / SEGMENT_DURATION));
    const key = segmentKey(videoId, duration, segmentIndex);
    return segmentMemory.get(key) || null;
  }

  function requestSegment({ videoId, duration, targetTime, signal, onReady }) {
    const segmentIndex = Math.max(0, Math.floor((Number(targetTime) || 0) / SEGMENT_DURATION));
    const key = segmentKey(videoId, duration, segmentIndex);
    const cached = segmentMemory.get(key);
    if (cached) {
      if (typeof onReady === 'function') onReady(cached);
      return cached;
    }
    prepareSegment({ videoId, duration, segmentIndex, signal })
      .then(seg => {
        if (!signal?.aborted && typeof onReady === 'function') onReady(seg);
      })
      .catch(() => {});
    return null;
  }

  function ensureSpriteImage(element, board) {
    if (!element || !board || !board.sprite_url) return null;
    let img = element.querySelector(':scope > .timeline-sprite-image');
    const boardIdentity = `${board.sprite_url}|${board.frame_width}|${board.frame_height}|${board.columns}|${board.rows}`;

    if (!img) {
      img = document.createElement('img');
      img.className = 'timeline-sprite-image';
      img.alt = '';
      img.draggable = false;
      element.replaceChildren(img);
    }

    if (element.dataset.boardIdentity !== boardIdentity) {
      element.dataset.boardIdentity = boardIdentity;
      element.dataset.frameIndex = '-1';
      img.src = board.sprite_url;
      img.width = (Number(board.columns) || 1) * (Number(board.frame_width) || 160);
      img.height = (Number(board.rows) || 1) * (Number(board.frame_height) || 90);
      img.style.width = `${img.width}px`;
      img.style.height = `${img.height}px`;
      img.style.transform = 'translate3d(0,0,0)';
    }
    return img;
  }

  function applyFrame(element, board, posOrTime, options = {}) {
    if (!element || !board || !board.sprite_url) return false;
    const count = Number(board.frame_count) || 0;
    const cols = Number(board.columns) || 1;
    const fw = Number(board.frame_width) || 160;
    const fh = Number(board.frame_height) || 90;
    if (!count) return false;

    let targetTime;
    if (options.targetTime !== undefined) {
      targetTime = Number(options.targetTime);
    } else if (board.type === 'segment' || board.segment_index !== undefined) {
      targetTime = Number(posOrTime) || 0;
    } else if (typeof posOrTime === 'number' && posOrTime <= 1.0 && (board.duration || options.duration) > 1.0) {
      targetTime = posOrTime * (board.duration || options.duration);
    } else {
      targetTime = Number(posOrTime) || 0;
    }

    let idx;
    if (Array.isArray(board.times) && board.times.length) {
      idx = findNearestIndex(board.times, targetTime);
    } else {
      const clamped = Math.max(0, Math.min(1, Number(posOrTime) || 0));
      idx = Math.min(count - 1, Math.floor(clamped * count));
    }

    const img = ensureSpriteImage(element, board);
    if (!img) return false;

    element.style.display = 'block';
    if (Number(element.dataset.frameIndex) !== idx) {
      element.dataset.frameIndex = String(idx);
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      // Tylko jeden compositor-friendly zapis na zmianę klatki. Zero clientWidth/layout readów.
      img.style.transform = `translate3d(${-col * fw}px, ${-row * fh}px, 0)`;
    }

    const frameTime = board.times?.[idx] ?? (count > 1 ? (idx / (count - 1)) * (board.duration || 0) : 0);
    const isExact = !!board.times && Math.abs(frameTime - targetTime) <= 1.0;
    return { ok: true, frameIndex: idx, frameTime, targetTime, isExact };
  }

  function clearFrame(element) {
    if (!element) return;
    element.style.display = 'none';
  }

  function attach({video, videoId, timeline, signal, onBoard}) {
    let started=false;
    const begin=()=>{
      if(started || signal?.aborted || !Number.isFinite(video.duration) || video.duration<=0) return;
      started=true;
      const deliver=board=>{if(!signal?.aborted)onBoard(board);};
      prepare({videoId,duration:video.duration,signal,onUpgrade:deliver}).then(deliver).catch(()=>{});
    };
    video.addEventListener('playing',begin,{once:true,signal});
    timeline?.addEventListener('pointerenter',begin,{once:true,signal});
    if (!video.paused && video.readyState>=2) begin();
  }

  const storyboardApi = {
    warm,
    prepare,
    applyFrame,
    clearFrame,
    attach,
    findNearestIndex,
    getSegmentFromCache,
    requestSegment,
    prepareSegment,
    SEGMENT_DURATION
  };
  // Tylko fixture regresyjny może poprosić o bezpośredni watcher. Nie jest to
  // część publicznego API produkcyjnego i flaga nie jest ustawiana przez UI.
  if (globalThis.__ARCHIVEBATE_STORYBOARD_TEST__) storyboardApi.__startUpgradeWatcher = startUpgradeWatcher;
  window.ArchivebateYouTubeStoryboard = storyboardApi;
})();
