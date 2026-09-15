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

  // V4.3 browser-mode repair: Font Awesome is loaded with media="print" and an
  // inline onload handler. V4.2 CSP intentionally blocks inline script, so in a
  // normal browser the handler cannot switch the stylesheet to screen media and
  // every <i class="fa-..."> stays blank. Switch it from our allowed self-hosted
  // script and provide a tiny local glyph fallback when the CDN/webfont is slow
  // or unavailable. This does not touch feed/grid loading.
  const LOCAL_ICON_GLYPHS = {
    'fa-house': '⌂', 'fa-heart': '♥', 'fa-clock-rotate-left': '↶', 'fa-user-group': '●●',
    'fa-circle-user': '●', 'fa-location-dot': '●', 'fa-magnifying-glass': '⌕', 'fa-xmark': '×',
    'fa-rotate-right': '↻', 'fa-rotate-left': '↺', 'fa-rotate': '↻', 'fa-arrows-rotate': '↻',
    'fa-tags': '#', 'fa-tag': '#', 'fa-user-tag': '#', 'fa-user-shield': '◆', 'fa-trash': '×',
    'fa-file-export': '⇥', 'fa-file-import': '⇤', 'fa-stethoscope': '+', 'fa-film': '▣',
    'fa-database': '▤', 'fa-users': '●●', 'fa-layer-group': '≡', 'fa-bookmark': '◆',
    'fa-ban': '⊘', 'fa-bolt': '⚡', 'fa-stop': '■', 'fa-chevron-left': '‹', 'fa-chevron-right': '›',
    'fa-chevron-down': '⌄', 'fa-chevron-up': '⌃', 'fa-angles-right': '»', 'fa-angles-down': '⌄',
    'fa-arrow-left': '←', 'fa-play': '▶', 'fa-pause': 'Ⅱ', 'fa-backward-step': '◀', 'fa-forward-step': '▶',
    'fa-user-minus': '−', 'fa-user-plus': '+', 'fa-volume-high': '◖', 'fa-volume-xmark': '×',
    'fa-clone': '▣', 'fa-expand': '⛶', 'fa-compress': '⊡', 'fa-video': '▶', 'fa-download': '↓',
    'fa-arrow-up-right-from-square': '↗', 'fa-arrow-right': '→', 'fa-lock': '■', 'fa-eye': '◉',
    'fa-calendar-days': '□', 'fa-star': '★', 'fa-tv': '▣', 'fa-folder': '▰', 'fa-folder-open': '▱',
    'fa-boxes-stacked': '▦', 'fa-circle-exclamation': '!', 'fa-triangle-exclamation': '!',
    'fa-check': '✓', 'fa-plus': '+', 'fa-minus': '−', 'fa-spinner': '↻'
  };

  function activateLocalIconFallback() {
    const doc = globalThis.document;
    const root = doc?.documentElement;
    if (!doc || !root || root.classList.contains('archivebate-local-icons')) return;
    const style = doc.createElement('style');
    style.id = 'archivebate-local-icon-style';
    const rules = [
      "html.archivebate-local-icons i.fa-solid::before,html.archivebate-local-icons i.fa-regular::before{font-family:'Segoe UI Symbol','Arial Unicode MS',sans-serif!important;font-style:normal!important;font-weight:700!important;display:inline-block!important;min-width:1em;text-align:center;line-height:1;content:'•'!important}",
      "html.archivebate-local-icons i.fa-spin{animation:archivebateLocalIconSpin 1s linear infinite}",
      '@keyframes archivebateLocalIconSpin{to{transform:rotate(360deg)}}'
    ];
    for (const [name, glyph] of Object.entries(LOCAL_ICON_GLYPHS)) {
      const safe = String(glyph).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      rules.push(`html.archivebate-local-icons i.${name}::before{content:'${safe}'!important}`);
    }
    style.textContent = rules.join('');
    doc.head?.appendChild(style);
    root.classList.add('archivebate-local-icons');
  }

  async function fontAwesomeUsable() {
    const doc = globalThis.document;
    if (!doc?.body || typeof getComputedStyle !== 'function') return false;
    const probe = doc.createElement('i');
    probe.className = 'fa-solid fa-house';
    probe.style.cssText = 'position:absolute;left:-10000px;top:-10000px;visibility:hidden';
    doc.body.appendChild(probe);
    try {
      const pseudo = getComputedStyle(probe, '::before');
      const content = String(pseudo?.content || '');
      const family = String(pseudo?.fontFamily || '');
      if (!content || content === 'none' || content === 'normal' || content === '""' || !/Font Awesome/i.test(family)) return false;
      if (!doc.fonts?.load) return true;
      const loaded = await Promise.race([
        doc.fonts.load('900 16px "Font Awesome 6 Free"', '\uf015'),
        new Promise(resolve => setTimeout(() => resolve([]), 500))
      ]);
      return Boolean(loaded?.length);
    } catch (_) {
      return false;
    } finally {
      probe.remove();
    }
  }

  function repairBrowserIcons() {
    const doc = globalThis.document;
    if (!doc) return;
    const link = Array.from(doc.querySelectorAll('link[rel="stylesheet"]'))
      .find(node => String(node.href || '').includes('font-awesome'));
    if (link && String(link.media || '').toLowerCase() === 'print') {
      link.media = 'all';
    }
    const check = () => setTimeout(async () => {
      if (!(await fontAwesomeUsable())) activateLocalIconFallback();
    }, 900);
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', check, { once: true });
    else check();
  }
  repairBrowserIcons();

  // Passive home-load probe. It does not alter the feed or DOM; it only records
  // when cards become visible and exposes resource timings so a real 8-second
  // load can be split into backend/SSE/render/thumbnail costs instead of guessed.
  const homeProbe = {
    started_ms: performance.now(),
    first_card_ms: null,
    cards_16_ms: null,
    cards_280_ms: null,
    max_cards: 0
  };

  function setupHomeProbe() {
    const doc = globalThis.document;
    const grid = doc?.getElementById?.('videoGrid');
    if (!grid || typeof MutationObserver === 'undefined') return;
    const sample = () => {
      const count = grid.querySelectorAll?.('.video-card')?.length || 0;
      homeProbe.max_cards = Math.max(homeProbe.max_cards, count);
      const t = performance.now();
      if (count >= 1 && homeProbe.first_card_ms === null) homeProbe.first_card_ms = t;
      if (count >= 16 && homeProbe.cards_16_ms === null) homeProbe.cards_16_ms = t;
      if (count >= 280 && homeProbe.cards_280_ms === null) homeProbe.cards_280_ms = t;
    };
    sample();
    const observer = new MutationObserver(sample);
    observer.observe(grid, { childList: true });
  }
  if (globalThis.document?.readyState === 'loading') {
    globalThis.document.addEventListener('DOMContentLoaded', setupHomeProbe, { once: true });
  } else {
    setupHomeProbe();
  }

  function getHomeLoadSnapshot() {
    const nav = performance.getEntriesByType?.('navigation')?.[0] || null;
    const resources = (performance.getEntriesByType?.('resource') || [])
      .filter(entry => /\/api\/(feed|feed\/stream|thumb)|cdnjs\.cloudflare\.com/.test(String(entry.name || '')))
      .map(entry => ({
        name: String(entry.name || '').replace(globalThis.location?.origin || '', ''),
        initiator: entry.initiatorType,
        start_ms: Math.round(entry.startTime * 10) / 10,
        response_start_ms: Math.round(entry.responseStart * 10) / 10,
        end_ms: Math.round(entry.responseEnd * 10) / 10,
        duration_ms: Math.round(entry.duration * 10) / 10,
        transfer_size: Number(entry.transferSize || 0)
      }));
    const now = performance.now();
    const currentCards = globalThis.document?.querySelectorAll?.('#videoGrid .video-card')?.length || 0;
    return {
      elapsed_ms: Math.round(now * 10) / 10,
      cards: currentCards,
      first_card_ms: homeProbe.first_card_ms === null ? null : Math.round(homeProbe.first_card_ms * 10) / 10,
      cards_16_ms: homeProbe.cards_16_ms === null ? null : Math.round(homeProbe.cards_16_ms * 10) / 10,
      cards_280_ms: homeProbe.cards_280_ms === null ? null : Math.round(homeProbe.cards_280_ms * 10) / 10,
      max_cards: homeProbe.max_cards,
      navigation: nav ? {
        dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd * 10) / 10,
        load_event_ms: Math.round(nav.loadEventEnd * 10) / 10,
        response_end_ms: Math.round(nav.responseEnd * 10) / 10
      } : null,
      resources
    };
  }

  // V4.3 compatibility bridge: the modal keeps the active item in
  // currentVideoDetails, while storyboard prewarm/hover historically reads
  // currentVideoId. Home/feed/grid behavior remains unchanged.
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
        const token = document.querySelector('meta[name="archivebate-mutation-token"]')?.content || '';
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
    getHomeLoadSnapshot,
    getActiveSession: () => activeSession
  };
})();
