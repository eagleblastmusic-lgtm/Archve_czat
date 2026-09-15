(() => {
  'use strict';

  class LRUCache {
    constructor(limit = 180) {
      this.limit = Math.max(1, limit);
      this.map = new Map();
    }
    has(key) { return this.map.has(key); }
    get(key) {
      if (!this.map.has(key)) return undefined;
      const value = this.map.get(key);
      this.map.delete(key);
      this.map.set(key, value);
      return value;
    }
    set(key, value) {
      if (this.map.has(key)) this.map.delete(key);
      this.map.set(key, value);
      while (this.map.size > this.limit) {
        const first = this.map.keys().next().value;
        this.map.delete(first);
      }
      return this;
    }
    delete(key) { return this.map.delete(key); }
    clear() { this.map.clear(); }
    get size() { return this.map.size; }
  }

  function idle(callback, timeout = 500) {
    if ('requestIdleCallback' in window) return requestIdleCallback(callback, { timeout });
    return setTimeout(callback, 32);
  }

  const timings=[];
  function measure(name,started) {
    timings.push({name,ms:performance.now()-started});
    if(timings.length>200)timings.shift();
  }
  let active = 0;
  let playbackBusy = false;
  const pending = [];
  const transfers = new Map();
  function drain() {
    pending.sort((a,b) => a.priority-b.priority);
    while (active < 4 && pending.length) {
      const index = pending.findIndex(job => job.signal?.aborted || (job.priority === 0 || (!playbackBusy && !globalThis.document?.hidden && !globalThis.navigator?.connection?.saveData)));
      if (index < 0) return;
      const job = pending.splice(index,1)[0];
      if (job.signal?.aborted) { job.reject(new DOMException('Aborted','AbortError')); continue; }
      active++;
      Promise.resolve().then(job.run).then(job.resolve,job.reject).finally(() => {active--;drain();});
    }
  }
  function schedule(run, {priority=2, signal}={}) {
    return new Promise((resolve,reject) => { pending.push({run,priority,signal,resolve,reject}); drain(); });
  }
  function setPlaybackBusy(value) { playbackBusy=!!value; drain(); }
  globalThis.document?.addEventListener('visibilitychange',drain);

  // Home recovery: on a published SQLite catalog the browser should receive
  // the complete 280-item page in the first response. The older 16-item
  // handshake required an immediate SSE replacement and could cancel the
  // deferred grid chunks before all cards reached the DOM. Keep the API itself
  // generic; only the normal /api/feed JSON request is upgraded here.
  function installHomeFeedFastPath() {
    const api = globalThis.ArchivebateAPI;
    if (!api || typeof api.getJSON !== 'function' || api.__v43FullHomeFeed) return;
    const originalGetJSON = api.getJSON.bind(api);
    api.getJSON = async (url, options = {}) => {
      let requestUrl = url;
      let isHomeFeed = false;
      if (typeof url === 'string' && url.startsWith('/api/feed?') && !url.startsWith('/api/feed/stream?')) {
        try {
          const parsed = new URL(url, globalThis.location?.origin || 'http://127.0.0.1:8000');
          if (parsed.pathname === '/api/feed') {
            isHomeFeed = true;
            parsed.searchParams.delete('initial_items');
            requestUrl = `${parsed.pathname}${parsed.search}`;
          }
        } catch (_) {}
      }
      const data = await originalGetJSON(requestUrl, options);
      if (isHomeFeed && data && typeof data === 'object' && data.catalog_complete === true && data.page_complete !== false) {
        // A complete catalog page does not need a second EventSource round-trip.
        data.complete = true;
      }
      return data;
    };
    api.__v43FullHomeFeed = true;
  }
  installHomeFeedFastPath();

  // A fresh browser profile may not have the remote Font Awesome webfont in
  // cache. Do not leave blank icon slots when cdnjs is slow/offline: after a
  // short grace period switch only the icon glyphs to a tiny local symbol set.
  // The external stylesheet remains asynchronous and therefore never blocks
  // the first useful home render.
  const FALLBACK_ICON_CONTENT = {
    'fa-house': '⌂', 'fa-heart': '♥', 'fa-clock-rotate-left': '↶', 'fa-user-group': '●●',
    'fa-circle-user': '●', 'fa-location-dot': '●', 'fa-magnifying-glass': '⌕', 'fa-xmark': '×',
    'fa-rotate-right': '↻', 'fa-rotate-left': '↺', 'fa-rotate': '↻', 'fa-arrows-rotate': '↻',
    'fa-tags': '#', 'fa-tag': '#', 'fa-user-tag': '#', 'fa-user-shield': '◆', 'fa-trash': '×',
    'fa-file-export': '⇥', 'fa-file-import': '⇤', 'fa-stethoscope': '+', 'fa-film': '▣',
    'fa-database': '▤', 'fa-users': '●●', 'fa-layer-group': '≡', 'fa-bookmark': '◆',
    'fa-ban': '⊘', 'fa-bolt': '⚡', 'fa-chevron-left': '‹', 'fa-chevron-right': '›',
    'fa-chevron-down': '⌄', 'fa-chevron-up': '⌃', 'fa-angles-right': '»', 'fa-angles-down': '⌄',
    'fa-play': '▶', 'fa-pause': 'Ⅱ', 'fa-backward-step': '◀', 'fa-forward-step': '▶',
    'fa-user-minus': '−', 'fa-user-plus': '+', 'fa-volume-high': '◖', 'fa-volume-xmark': '×',
    'fa-clone': '▣', 'fa-expand': '⛶', 'fa-compress': '⊡', 'fa-video': '▶', 'fa-download': '↓',
    'fa-arrow-up-right-from-square': '↗', 'fa-arrow-right': '→', 'fa-lock': '■', 'fa-eye': '◉',
    'fa-calendar-days': '□', 'fa-star': '★', 'fa-tv': '▣', 'fa-folder': '▰', 'fa-folder-open': '▱',
    'fa-circle-exclamation': '!', 'fa-triangle-exclamation': '!', 'fa-check': '✓', 'fa-plus': '+',
    'fa-minus': '−', 'fa-spinner': '↻'
  };

  function activateLocalIconFallback() {
    const doc = globalThis.document;
    const root = doc?.documentElement;
    if (!doc || !root || root.classList.contains('archivebate-icon-fallback')) return;
    const style = doc.createElement('style');
    style.id = 'archivebate-local-icon-fallback';
    const rules = [
      "html.archivebate-icon-fallback i.fa-solid::before,html.archivebate-icon-fallback i.fa-regular::before{font-family:'Segoe UI Symbol','Arial Unicode MS',sans-serif!important;font-style:normal!important;font-weight:700!important;display:inline-block!important;min-width:1em;text-align:center;line-height:1;content:'•'!important}",
      "html.archivebate-icon-fallback i.fa-spin{animation:archivebateFallbackSpin 1s linear infinite}",
      '@keyframes archivebateFallbackSpin{to{transform:rotate(360deg)}}'
    ];
    for (const [name, glyph] of Object.entries(FALLBACK_ICON_CONTENT)) {
      const safe = String(glyph).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      rules.push(`html.archivebate-icon-fallback i.${name}::before{content:'${safe}'!important}`);
    }
    style.textContent = rules.join('');
    doc.head?.appendChild(style);
    root.classList.add('archivebate-icon-fallback');
  }

  async function fontAwesomeIsUsable() {
    const doc = globalThis.document;
    if (!doc?.body) return false;
    const probe = doc.createElement('i');
    probe.className = 'fa-solid fa-house';
    probe.style.cssText = 'position:absolute;left:-9999px;top:-9999px;visibility:hidden';
    doc.body.appendChild(probe);
    try {
      const pseudo = globalThis.getComputedStyle?.(probe, '::before');
      const content = String(pseudo?.content || '');
      const family = String(pseudo?.fontFamily || '');
      if (!content || content === 'none' || content === 'normal' || content === '""' || !/Font Awesome/i.test(family)) return false;
      if (!doc.fonts?.load) return true;
      const loaded = await Promise.race([
        doc.fonts.load('900 16px "Font Awesome 6 Free"', '\uf015'),
        new Promise(resolve => setTimeout(() => resolve([]), 450))
      ]);
      return Array.isArray(loaded) ? loaded.length > 0 : Boolean(loaded?.length);
    } catch (_) {
      return false;
    } finally {
      probe.remove?.();
    }
  }

  function scheduleIconFallbackCheck() {
    const doc = globalThis.document;
    if (!doc) return;
    const run = async () => {
      if (!(await fontAwesomeIsUsable())) activateLocalIconFallback();
    };
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', () => setTimeout(run, 120), { once: true });
    else setTimeout(run, 120);
  }
  scheduleIconFallbackCheck();

  // The first eight card thumbnails are already eager. Promote only the next
  // sixteen on first home paint so the visible rows fill quickly without the
  // old 280-request thumbnail burst.
  function installFirstScreenThumbnailBoost() {
    const doc = globalThis.document;
    if (!doc) return;
    const install = () => {
      const mod = globalThis.ArchivebateVideoPrefetch;
      if (!mod || typeof mod.armLazyThumbnail !== 'function' || mod.__v43FirstScreenBoost) return;
      const originalArm = mod.armLazyThumbnail.bind(mod);
      let budget = 16;
      mod.armLazyThumbnail = img => {
        if (budget > 0 && img?.dataset?.src) {
          budget -= 1;
          img.loading = 'eager';
          img.fetchPriority = 'auto';
          img.src = img.dataset.src;
          delete img.dataset.src;
          return;
        }
        return originalArm(img);
      };
      mod.__v43FirstScreenBoost = true;
    };
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', install, { once: true });
    else install();
  }
  installFirstScreenThumbnailBoost();

  // V4.3 compatibility bridge: the modular modal stores the active video in
  // currentVideoDetails, while exact storyboard hover/prewarm historically read
  // currentVideoId. Synchronize the identity at the earliest media lifecycle
  // event, before `playing` and before a user can hover the timeline.
  function bridgeModalVideoIdentity(video) {
    if (!video || video.id !== 'modalVideo') return '';
    const appState = globalThis.ArchivebateAppContext?.state || null;
    const legacyState = globalThis.state || null;
    const videoId = String(
      appState?.currentVideoDetails?.id ||
      appState?.currentVideoId ||
      legacyState?.currentVideoDetails?.id ||
      legacyState?.currentVideoId ||
      video.dataset?.videoId ||
      ''
    ).trim();
    if (!videoId) return '';
    if (appState) appState.currentVideoId = videoId;
    if (legacyState && legacyState !== appState) legacyState.currentVideoId = videoId;
    if (video.dataset) video.dataset.videoId = videoId;
    return videoId;
  }

  globalThis.document?.addEventListener?.('play', event => {
    bridgeModalVideoIdentity(event?.target);
  }, true);

  async function prefetchUrls(urls, {concurrency=4,signal}={}) {
    const queue=[...new Set((urls||[]).filter(Boolean))];
    let cursor=0;
    const worker=async()=>{
      while(cursor<queue.length && !signal?.aborted) {
        const url=queue[cursor++];
        try {
          let transfer=transfers.get(url);
          if (!transfer) {
            transfer=schedule(async()=>{const res=await fetch(url,{cache:'force-cache',priority:'low',signal});await res.arrayBuffer();},{signal});
            transfers.set(url,transfer);
            transfer.finally(()=>{if(transfers.get(url)===transfer)transfers.delete(url);}).catch(()=>{});
          }
          await transfer;
        } catch (_) {}
      }
    };
    await Promise.all(Array.from({length:Math.min(concurrency,queue.length)},worker));
  }
  const sessionHistory = [];
  let activeSession = null;

  function sanitizeHost(url) {
    if (!url) return '';
    try {
      const base = (typeof window !== 'undefined' && window.location && window.location.href) ? window.location.href : 'http://localhost';
      const parsed = (typeof URL !== 'undefined') ? new URL(url, base) : null;
      return parsed ? parsed.hostname : '';
    } catch (_) {
      return '';
    }
  }

  function startPlaybackSession(arg1, arg2 = {}) {
    const opts = (typeof arg1 === 'object' && arg1 !== null) ? arg1 : { id: arg1, ...(arg2 || {}) };
    const { id, owner = 'player', priority = 'high', reason = 'click' } = opts;
    if (activeSession && !activeSession.completed && !activeSession.aborted) {
      activeSession.abort('superseded');
    }
    const t_click = performance.now();
    const session = {
      id: String(id || ''),
      owner,
      priority,
      reason,
      host: '',
      t_click,
      t_resolve: null,
      t_connect: null,
      t_first_byte: null,
      t_metadata: null,
      t_first_frame: null,
      completed: false,
      aborted: false,
      error: null,
      stages: {},

      markUrlResolved(url, cached = false) {
        if (this.aborted || this.completed) return;
        this.t_resolve = performance.now();
        this.host = sanitizeHost(url);
        this.stages.click_to_resolve_ms = Math.max(0, this.t_resolve - this.t_click);
        this.stages.url_cached = !!cached;
      },
      markResolved(url, cached = false) {
        return this.markUrlResolved(url, cached);
      },

      markConnectStart() {
        if (this.aborted || this.completed) return;
        this.t_connect = performance.now();
        const base = this.t_resolve || this.t_click;
        this.stages.resolve_to_connect_ms = Math.max(0, this.t_connect - base);
      },
      markConnect() {
        return this.markConnectStart();
      },

      markFirstByte() {
        if (this.aborted || this.completed) return;
        this.t_first_byte = performance.now();
        const base = this.t_connect || this.t_resolve || this.t_click;
        this.stages.connect_to_first_byte_ms = Math.max(0, this.t_first_byte - base);
      },

      markMetadata() {
        if (this.aborted || this.completed) return;
        this.t_metadata = performance.now();
        const base = this.t_first_byte || this.t_connect || this.t_resolve || this.t_click;
        this.stages.first_byte_to_metadata_ms = Math.max(0, this.t_metadata - base);
      },

      markFirstFrame() {
        if (this.aborted || this.completed) return;
        this.t_first_frame = performance.now();
        this.completed = true;
        const base = this.t_metadata || this.t_first_byte || this.t_connect || this.t_click;
        this.stages.metadata_to_first_frame_ms = Math.max(0, this.t_first_frame - base);
        this.stages.total_click_to_first_frame_ms = Math.max(0, this.t_first_frame - this.t_click);

        sessionHistory.push({
          id: this.id,
          host: this.host,
          owner: this.owner,
          priority: this.priority,
          reason: this.reason,
          stages: { ...this.stages }
        });
        if (sessionHistory.length > 50) sessionHistory.shift();

        try {
          console.info(
            `[PlaybackSession] id=${this.id} host=${this.host} owner=${this.owner} total=${this.stages.total_click_to_first_frame_ms.toFixed(1)}ms ` +
            `(resolve: ${this.stages.click_to_resolve_ms?.toFixed(1) ?? '-'}ms, ` +
            `ttfb: ${this.stages.connect_to_first_byte_ms?.toFixed(1) ?? '-'}ms, ` +
            `metadata: ${this.stages.first_byte_to_metadata_ms?.toFixed(1) ?? '-'}ms, ` +
            `first_frame: ${this.stages.metadata_to_first_frame_ms?.toFixed(1) ?? '-'}ms)`
          );
        } catch (_) {}
      },

      abort(abortReason = 'cancelled') {
        if (this.completed || this.aborted) return;
        this.aborted = true;
        this.error = { category: 'aborted', message: abortReason };
      },

      markError(category, message) {
        this.error = { category, message };
      }
    };

    activeSession = session;
    return session;
  }

  function getBufferedAhead(video) {
    if (!video || !video.buffered || video.buffered.length === 0) return 0;
    const current = video.currentTime || 0;
    const duration = video.duration;
    if (Number.isFinite(duration) && current >= duration - 0.5) return 5.0;
    for (let i = 0; i < video.buffered.length; i++) {
      if (video.buffered.start(i) <= current + 0.1 && video.buffered.end(i) >= current) {
        return Math.max(0, video.buffered.end(i) - current);
      }
    }
    return 0;
  }

  let lastStatusReport = 0;
  function reportPlaybackStatus(isBusy, buffered) {
    const now = performance.now();
    if (now - lastStatusReport < 800) return;
    lastStatusReport = now;
    try {
      const body = { is_busy: !!isBusy, buffered_seconds: buffered };
      if (globalThis.ArchivebateAPI?.postJSON) {
        globalThis.ArchivebateAPI.postJSON('/api/playback/status', body, {
          keepalive: true,
          timeoutMs: 3000
        }).catch(() => {});
      } else if (typeof fetch === 'function') {
        const token = globalThis.document?.querySelector?.('meta[name="archivebate-mutation-token"]')?.content || '';
        fetch('/api/playback/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Archivebate-Mutation-Token': token } : {}) },
          body: JSON.stringify(body),
          keepalive: true
        }).catch(() => {});
      }
    } catch (_) {}
  }

  function updatePlaybackBuffer(video, targetAhead = 5.0) {
    if (!video) {
      setPlaybackBusy(false);
      reportPlaybackStatus(false, 0);
      return;
    }
    const ahead = getBufferedAhead(video);
    const isPlaying = !video.paused && !video.seeking && video.readyState >= 2;
    const isBusy = !isPlaying || ahead < targetAhead;
    setPlaybackBusy(isBusy);
    reportPlaybackStatus(isBusy, ahead);
    return { ahead, isBusy };
  }

  function calculatePercentiles(metricKey = 'total_click_to_first_frame_ms') {
    const values = sessionHistory
      .map(s => s.stages?.[metricKey])
      .filter(v => typeof v === 'number' && Number.isFinite(v))
      .sort((a, b) => a - b);
    if (values.length === 0) return { count: 0, p50: 0, p95: 0 };
    const p50 = values[Math.floor(values.length * 0.50)];
    const p95 = values[Math.min(values.length - 1, Math.floor(values.length * 0.95))];
    return { count: values.length, p50, p95 };
  }

  window.ArchivebatePerf = {
    LRUCache,
    idle,
    prefetchUrls,
    schedule,
    setPlaybackBusy,
    measure,
    timings,
    startPlaybackSession,
    getBufferedAhead,
    updatePlaybackBuffer,
    calculatePercentiles,
    sessionHistory,
    bridgeModalVideoIdentity,
    getActiveSession: () => activeSession
  };
})();