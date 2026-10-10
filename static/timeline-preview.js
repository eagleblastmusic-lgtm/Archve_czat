/**
 * Archivebate - Instant Timeline Preview V2
 * 
 * Provides YouTube-style instant hover previews on video timelines using
 * 10x10 sprite sheets and progressive van der Corput bisection order.
 * 
 * Invariants:
 * 1. ZERO network fetch during pointermove/hover.
 * 2. Preloads sheets with img.decode() so frame rendering is instantaneous.
 * 3. Layer 0 provides immediate 0ms previews via native CDN thumbs.
 * 4. Cooperative integration across both modal player and /watch/ page.
 */
(function(global) {
  'use strict';

  const FRAME_WIDTH = 160;
  const FRAME_HEIGHT = 90;
  const COLUMNS = 10;
  const ROWS = 10;

  function binarySearchNearest(frames, targetPts) {
    if (!frames || frames.length === 0) return null;
    let low = 0;
    let high = frames.length - 1;
    if (targetPts <= frames[0][0]) return frames[0];
    if (targetPts >= frames[high][0]) return frames[high];

    while (low <= high) {
      const mid = (low + high) >> 1;
      const pts = frames[mid][0];
      if (pts === targetPts) return frames[mid];
      if (pts < targetPts) {
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    const left = frames[Math.max(0, high)];
    const right = frames[Math.min(frames.length - 1, low)];
    return (Math.abs(left[0] - targetPts) <= Math.abs(right[0] - targetPts)) ? left : right;
  }

  function formatTime(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    const remM = m % 60;
    const remS = s % 60;
    if (h > 0) {
      return `${h}:${String(remM).padStart(2, '0')}:${String(remS).padStart(2, '0')}`;
    }
    return `${remM}:${String(remS).padStart(2, '0')}`;
  }

  function canonicalKey(videoIdOrKey, source) {
    let s = String(source || '').trim().toLowerCase();
    let id = String(videoIdOrKey || '').trim();
    if (id.includes(':id:')) {
      const parts = id.split(':id:');
      s = s || parts[0];
      id = parts.slice(1).join(':id:');
    }
    if (!s) {
      s = id.startsWith('cw_') ? 'camwhores' : 'archivebate';
    }
    if (s === 'camwhores') {
      id = id.replace(/^cw_/i, '');
    }
    return `${s}:id:${id}`;
  }

  function getMutationToken() {
    if (typeof document === 'undefined') return '';
    const meta = document.querySelector('meta[name="archivebate-mutation-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  const LIFECYCLE = {
    UNINITIALIZED: 'UNINITIALIZED',
    LOADING: 'LOADING',
    PARTIAL: 'PARTIAL',
    COMPLETE: 'COMPLETE',
    ERROR: 'ERROR',
    CANCELLED: 'CANCELLED',
  };

  class TimelinePreviewManager {
    constructor() {
      this.cache = new Map(); // videoKey -> { manifest, sheets: Map, sheetUrls: Map, sheetRevs: Map, activeRev, generation, fetching, fetchPromise, controller, subscribers: Map, listeners: Set, lastAccessed, lifecycle }
      this.maxCachedEntries = 12;
      this.activeVideoKey = null;
      this.activeInstances = new Set();
      this.consumerId = 'tab_' + Math.random().toString(36).slice(2, 9) + '_' + Date.now().toString(36);
      this.noProgressTimeoutMs = 90000;
      this.maxBuildFailures = 3;
    }

    getOrCreateEntry(videoKey, source) {
      const key = canonicalKey(videoKey, source);
      if (!this.cache.has(key)) {
        this.cache.set(key, {
          lifecycle: LIFECYCLE.UNINITIALIZED,
          manifest: null,
          sheets: new Map(),
          sheetUrls: new Map(),
          sheetRevs: new Map(),
          activeRev: 0,
          generation: 0,
          sessionId: null,
          terminalFailure: false,
          errorMessage: '',
          fetching: false,
          fetchPromise: null,
          controller: null,
          subscribers: new Map(), // subscriberId -> onUpdate callback
          listeners: new Set(),   // backwards compat
          rafScheduled: 0,
          retryCount: 0,
          lastErrorTime: 0,
          lastAccessed: Date.now(),
        });
        this.evictLru(key);
      }
      const entry = this.cache.get(key);
      if (!entry.sheetUrls) entry.sheetUrls = new Map();
      if (!entry.sheetRevs) entry.sheetRevs = new Map();
      if (!entry.subscribers) entry.subscribers = new Map();
      if (!entry.listeners) entry.listeners = new Set();
      if (!entry.lifecycle) entry.lifecycle = LIFECYCLE.UNINITIALIZED;
      entry.lastAccessed = Date.now();
      return entry;
    }

    evictLru(protectedKey) {
      if (this.cache.size <= this.maxCachedEntries) return;
      const candidates = [];
      for (const [k, e] of this.cache.entries()) {
        if (k === protectedKey || k === this.activeVideoKey) continue;
        if (e.fetching || (e.subscribers && e.subscribers.size > 0) || (e.listeners && e.listeners.size > 0)) continue;
        candidates.push({ key: k, entry: e, time: e.lastAccessed || 0 });
      }
      candidates.sort((a, b) => a.time - b.time);
      while (this.cache.size > this.maxCachedEntries && candidates.length > 0) {
        const victim = candidates.shift();
        if (victim && victim.entry) {
          if (victim.entry.controller) {
            try { victim.entry.controller.abort(); } catch (_) {}
          }
          if (victim.entry.sheets) victim.entry.sheets.clear();
          if (victim.entry.sheetUrls) victim.entry.sheetUrls.clear();
          if (victim.entry.sheetRevs) victim.entry.sheetRevs.clear();
          this.cache.delete(victim.key);
        }
      }
    }

    hasEntry(videoKey, source) {
      if (!videoKey) return false;
      const key = canonicalKey(videoKey, source);
      return this.cache.has(key);
    }

    stopPolling(videoKey, source) {
      const key = canonicalKey(videoKey, source);
      const entry = this.cache.get(key);
      if (entry) {
        entry.generation = (entry.generation || 0) + 1;
        if (entry.controller) {
          try { entry.controller.abort(); } catch (_) {}
          entry.controller = null;
        }
        entry.fetching = false;
        entry.fetchPromise = null;
        entry.lifecycle = LIFECYCLE.CANCELLED;
        entry.terminalFailure = false; // Explicit reopen is allowed to retry.
        entry.errorMessage = '';
        if (entry.rafScheduled) {
          if (typeof cancelAnimationFrame === 'function') {
            cancelAnimationFrame(entry.rafScheduled);
          } else {
            clearTimeout(entry.rafScheduled);
          }
          entry.rafScheduled = 0;
        }
        if (entry.subscribers) entry.subscribers.clear();
        if (entry.listeners) entry.listeners.clear();
      }
    }

    hasFrames(videoId, source) {
      if (!videoId) return false;
      const key = canonicalKey(videoId, source);
      const direct = this.cache.get(key);
      if (direct) {
        if (direct.manifest && Array.isArray(direct.manifest.frames) && direct.manifest.frames.length > 0 && direct.sheets.size > 0) {
          return true;
        }
        if (direct.manifest?.native?.kind === 'prefix' || (direct.manifest?.native?.frames && direct.manifest.native.frames.length > 0)) {
          return true;
        }
      }
      const cleanId = String(videoId).replace(/^cw_/i, '');
      for (const [k, entry] of this.cache.entries()) {
        if (k.includes(videoId) || k.includes(cleanId)) {
          if (entry.manifest && Array.isArray(entry.manifest.frames) && entry.manifest.frames.length > 0 && entry.sheets.size > 0) {
            return true;
          }
          if (entry.manifest?.native?.kind === 'prefix' || (entry.manifest?.native?.frames && entry.manifest.native.frames.length > 0)) {
            return true;
          }
        }
      }
      return false;
    }

    preloadSheet(sheetUrl, targetRev, sheetIndex, targetGeneration, entry) {
      return new Promise((resolve) => {
        if (typeof Image === 'undefined') {
          resolve(null);
          return;
        }
        const img = new Image();
        const finish = (loaded) => {
          if (!loaded) {
            resolve(null);
            return;
          }
          // Race-condition guard: check generation, active controller, and revision monotonicity
          if (entry) {
            if (entry.generation !== targetGeneration) {
              resolve(null);
              return;
            }
            if (entry.controller && entry.controller.signal.aborted) {
              resolve(null);
              return;
            }
            const currentSheetRev = entry.sheetRevs ? (entry.sheetRevs.get(sheetIndex) || 0) : 0;
            if (targetRev !== undefined && targetRev < currentSheetRev) {
              // Obsolete older revision finished decoding after newer revision; reject to prevent rollback
              resolve(null);
              return;
            }
            if (entry.sheets && entry.sheetUrls) {
              entry.sheets.set(sheetIndex, loaded);
              entry.sheetUrls.set(sheetIndex, sheetUrl);
              if (entry.sheetRevs) entry.sheetRevs.set(sheetIndex, targetRev || 0);
            }
          }
          resolve(loaded);
        };

        img.onload = () => {
          if (img.decode) {
            img.decode().then(() => finish(img)).catch(() => finish(img));
          } else {
            finish(img);
          }
        };
        img.onerror = () => resolve(null);
        img.src = sheetUrl;
      });
    }

    subscribe(videoKey, subscriberId, onUpdate, source) {
      if (!videoKey || !subscriberId || typeof onUpdate !== 'function') return;
      const key = canonicalKey(videoKey, source);
      const entry = this.getOrCreateEntry(key, source);
      entry.subscribers.set(String(subscriberId), onUpdate);
      this.activeVideoKey = key;
      // Dispatch ready state asynchronously to prevent synchronous recursion
      if (entry.manifest && entry.sheets && entry.sheets.size > 0) {
        Promise.resolve().then(() => {
          if (entry.subscribers.has(String(subscriberId))) {
            try { onUpdate(entry.manifest); } catch (_) {}
          }
        });
      }
    }

    unsubscribe(videoKey, subscriberId, source) {
      if (!videoKey || !subscriberId) return;
      const key = canonicalKey(videoKey, source);
      const entry = this.cache.get(key);
      if (entry && entry.subscribers) {
        entry.subscribers.delete(String(subscriberId));
        if (this.activeVideoKey === key && entry.subscribers.size === 0) {
          this.activeVideoKey = null;
        }
        if (entry.subscribers.size === 0 && (!entry.listeners || entry.listeners.size === 0)) {
          this.stopPolling(videoKey, source);
          this.releaseLease(videoKey, source);
        }
      }
    }

    getSessionId(videoKey, source, createIfMissing = false) {
      if (!videoKey) return null;
      const key = canonicalKey(videoKey, source);
      if (createIfMissing) {
        const entry = this.getOrCreateEntry(key, source);
        if (!entry.sessionId) {
          entry.generation = (entry.generation || 0) + 1;
          entry.sessionId = `${this.consumerId}_s${entry.generation}`;
        }
        return entry.sessionId;
      }
      const entry = this.cache.get(key);
      return entry ? entry.sessionId : null;
    }

    releaseLease(videoKey, source) {
      if (!videoKey) return;
      const key = canonicalKey(videoKey, source);
      const entry = this.cache.get(key);
      const sessionId = entry ? entry.sessionId : null;
      if (entry) {
        entry.sessionId = null;
      }
      const parts = String(videoKey).split(':');
      const videoId = parts.length >= 3 ? parts.slice(2).join(':') : parts[parts.length - 1];
      const parsedSource = parts.length >= 3 ? parts[0] : (source || (videoId.startsWith('cw_') ? 'camwhores' : 'archivebate'));
      const token = getMutationToken();
      const headers = {
        'Content-Type': 'application/json',
        ...(token ? { 'X-Archivebate-Mutation-Token': token } : {}),
      };
      const sessionParam = sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : '';
      fetch(`/api/runtime/v452/storyboard/cancel?id=${encodeURIComponent(videoId)}&consumer_id=${encodeURIComponent(this.consumerId)}${sessionParam}&source=${encodeURIComponent(parsedSource || '')}&reason=unsubscribe`, {
        method: 'POST',
        headers,
        keepalive: true,
      }).catch(() => {});
    }

    releaseAllLeases() {
      for (const [key, entry] of this.cache.entries()) {
        if (entry && (entry.subscribers?.size > 0 || entry.listeners?.size > 0)) {
          this.stopPolling(key, entry.source);
          this.releaseLease(key, entry.source);
          entry.subscribers?.clear();
          entry.listeners?.clear();
        }
      }
      this.activeVideoKey = null;
    }

    hasSubscribers(videoKey, source) {
      if (!videoKey) return false;
      const key = canonicalKey(videoKey, source);
      const entry = this.cache.get(key);
      return !!(entry && ((entry.subscribers && entry.subscribers.size > 0) || (entry.listeners && entry.listeners.size > 0)));
    }

    notifySubscribers(entry, manifest) {
      if (!entry) return;
      const subs = Array.from(entry.subscribers?.values() || []);
      const legacy = Array.from(entry.listeners || []);
      const allCallbacks = subs.concat(legacy);
      if (allCallbacks.length === 0) return;

      for (const cb of allCallbacks) {
        try {
          cb(manifest);
        } catch (err) {
          console.error('[TimelinePreview] subscriber error:', err);
        }
      }
    }

    notifyListeners(entry, manifest) {
      this.notifySubscribers(entry, manifest);
    }

    async ensureStoryboard(videoKey, videoId, duration, source) {
      if (typeof document !== 'undefined' && document.hidden) return null;
      duration = Number(duration) || 0;
      if ((!videoKey && !videoId) || duration <= 0) return null;

      const key = canonicalKey(videoKey || videoId, source);
      const entry = this.getOrCreateEntry(key, source);
      entry.lastAccessed = Date.now();

      // Short-circuit: already complete (0 network calls)
      if (entry.lifecycle === LIFECYCLE.COMPLETE || (entry.manifest && entry.manifest.complete)) {
        entry.lifecycle = LIFECYCLE.COMPLETE;
        return entry.manifest;
      }

      // Single-flight deduplication: return existing promise if already loading
      if (entry.fetching && entry.fetchPromise) {
        return entry.fetchPromise;
      }

      // Exponential backoff check on error
      if (entry.terminalFailure) return entry.manifest;
      if (entry.lifecycle === LIFECYCLE.ERROR) {
        const now = Date.now();
        const minWait = Math.min(30000, 2000 * Math.pow(2, entry.retryCount || 0));
        if (now - (entry.lastErrorTime || 0) < minWait) {
          return entry.manifest;
        }
      }

      if (!entry.sessionId) {
        entry.generation = (entry.generation || 0) + 1;
        entry.sessionId = `${this.consumerId}_s${entry.generation}`;
      }
      const sessionId = entry.sessionId;
      const targetGeneration = entry.generation || 0;

      entry.fetching = true;
      entry.lifecycle = LIFECYCLE.LOADING;
      const controller = new AbortController();
      entry.controller = controller;

      entry.fetchPromise = (async () => {
        let sinceRev = entry.manifest ? (entry.manifest.rev || 0) : 0;
        let failureCount = 0;
        let buildFailures = 0;
        let buildInFlight = false;
        let nextBuildAt = 0;
        let lastProgressAt = Date.now();
        let lastFrameCount = Number(entry.manifest?.frames?.length || 0)
          + Number(entry.manifest?.native?.frames?.length || 0);

        try {
          const token = getMutationToken();
          const buildHeaders = token ? { 'X-Archivebate-Mutation-Token': token } : {};
          const requestBuild = async () => {
            if (buildInFlight || controller.signal.aborted || entry.generation !== targetGeneration) return;
            buildInFlight = true;
            try {
              const response = await fetch(`/api/storyboard/v2/build?id=${encodeURIComponent(videoId)}&duration=${duration}&source=${encodeURIComponent(source || '')}&consumer_id=${encodeURIComponent(this.consumerId)}&session_id=${encodeURIComponent(sessionId)}`, {
                method: 'POST', headers: buildHeaders, signal: controller.signal,
              });
              if (!response.ok) throw new Error('build_http_error');
              const result = await response.json();
              if (controller.signal.aborted || entry.generation !== targetGeneration) return;
              if (result?.build_status === 'terminal_error') {
                entry.terminalFailure = true;
                entry.errorMessage = 'Nie udało się rozpocząć generowania podglądu.';
              } else if (result?.build_status === 'retryable_error') {
                throw new Error(result.error_code || 'build_busy');
              } else {
                buildFailures = 0;
              }
            } catch (_) {
              if (!controller.signal.aborted && entry.generation === targetGeneration) {
                buildFailures += 1;
                nextBuildAt = Date.now() + Math.min(30000, 2000 * 2 ** Math.min(4, buildFailures));
              }
            } finally {
              buildInFlight = false;
            }
          };
          void requestBuild();

          let loopIter = 0;
          while (!controller.signal.aborted && entry.generation === targetGeneration) {
            if (entry.terminalFailure || buildFailures >= this.maxBuildFailures
                || Date.now() - lastProgressAt > this.noProgressTimeoutMs) {
              entry.lifecycle = LIFECYCLE.ERROR;
              entry.terminalFailure = true;
              entry.errorMessage ||= 'Podgląd niedostępny — generowanie nie zrobiło postępu.';
              entry.lastErrorTime = Date.now();
              entry.retryCount = 5;
              this.releaseLease(videoKey || videoId, source);
              break;
            }
            loopIter++;
            if (loopIter % 4 === 0 && (!entry.manifest || !entry.manifest.complete)) {
              fetch(`/api/storyboard/v2/lease/renew?id=${encodeURIComponent(videoId)}&consumer_id=${encodeURIComponent(this.consumerId)}&session_id=${encodeURIComponent(sessionId)}&source=${encodeURIComponent(source || '')}`, {
                method: 'POST',
                headers: buildHeaders,
                signal: controller.signal,
              }).catch(() => {});
            }
            if (loopIter % 8 === 0 && Date.now() >= nextBuildAt
                && (!entry.manifest || !entry.manifest.complete)) {
              void requestBuild();
            }
            const waitMs = sinceRev > 0 ? 1500 : 0;
            const url = `/api/storyboard/v2?id=${encodeURIComponent(videoId)}&duration=${duration}&since_rev=${sinceRev}&wait_ms=${waitMs}&source=${encodeURIComponent(source || '')}`;

            try {
              const resp = await fetch(url, { signal: controller.signal });
              if (!resp.ok) {
                failureCount++;
                if (failureCount > 5) {
                  entry.lifecycle = LIFECYCLE.ERROR;
                  entry.lastErrorTime = Date.now();
                  entry.retryCount = (entry.retryCount || 0) + 1;
                  break;
                }
                await new Promise(r => setTimeout(r, 1000));
                continue;
              }
              failureCount = 0;
              entry.retryCount = 0;
              const manifest = await resp.json();
              const newFrameCount = Number(manifest?.frames?.length || 0)
                + Number(manifest?.native?.frames?.length || 0);
              if (newFrameCount > lastFrameCount) {
                lastFrameCount = newFrameCount;
                lastProgressAt = Date.now();
              }
              if (manifest && (manifest.version === 11 || manifest.version === 12) && entry.generation === targetGeneration) {
                const manifestRev = Number(manifest.rev || 0);
                if (manifestRev >= (entry.activeRev || 0)) {
                  entry.manifest = manifest;
                  entry.activeRev = manifestRev;
                  sinceRev = manifestRev || sinceRev;
                  entry.lifecycle = manifest.complete ? LIFECYCLE.COMPLETE : LIFECYCLE.PARTIAL;

                  // Preload new sheets or updated revisions without race conditions
                  if (Array.isArray(manifest.sheets)) {
                    for (const s of manifest.sheets) {
                      if (s && s.url) {
                        const currentUrl = entry.sheetUrls.get(s.index);
                        const currentRev = entry.sheetRevs.get(s.index) || 0;
                        if (currentUrl !== s.url || manifestRev > currentRev) {
                          this.preloadSheet(s.url, manifestRev, s.index, targetGeneration, entry).then(loadedImg => {
                            if (loadedImg && entry.generation === targetGeneration) {
                              this.notifySubscribers(entry, entry.manifest);
                            }
                          });
                        }
                      }
                    }
                  }

                  this.notifySubscribers(entry, manifest);
                }
                if (manifest.complete) {
                  entry.lifecycle = LIFECYCLE.COMPLETE;
                  break;
                }
              }
              // Bound empty-manifest polling even when the server responds 200
              // immediately (rev 1 > since_rev 0).
              if (sinceRev === 0) {
                await new Promise(resolve => setTimeout(resolve, 250));
              }
            } catch (err) {
              if (controller.signal.aborted || entry.generation !== targetGeneration) {
                entry.lifecycle = LIFECYCLE.CANCELLED;
                break;
              }
              failureCount++;
              if (failureCount > 5) {
                entry.lifecycle = LIFECYCLE.ERROR;
                entry.lastErrorTime = Date.now();
                entry.retryCount = (entry.retryCount || 0) + 1;
                break;
              }
              await new Promise(r => setTimeout(r, 1000));
            }
          }
        } finally {
          if (entry.generation === targetGeneration) {
            entry.fetching = false;
            entry.fetchPromise = null;
            if (entry.controller === controller) {
              entry.controller = null;
            }
          }
        }
        return entry.manifest;
      })();

      return entry.fetchPromise;
    }

    async pollManifest(videoKey, videoId, duration, source, onUpdate) {
      if (onUpdate && typeof onUpdate === 'function') {
        const subId = 'legacy_' + canonicalKey(videoKey || videoId, source);
        this.subscribe(videoKey || videoId, subId, onUpdate, source);
      }
      return this.ensureStoryboard(videoKey, videoId, duration, source);
    }

    render(options) {
      const {
        videoKey,
        videoId,
        targetTime,
        duration,
        pos,
        spriteEl,
        imgEl,
        timeTextEl,
        statusEl,
        statusText,
        nativePrefix,
        nativeCount,
        poster,
      } = options;

      const key = canonicalKey(videoKey || videoId, options.source);
      const entry = this.cache.get(key);
      const manifest = entry?.manifest;

      // 1. Try progressive v2 sprite sheet
      if (manifest && Array.isArray(manifest.frames) && manifest.frames.length > 0) {
        const closest = binarySearchNearest(manifest.frames, targetTime);
        if (closest) {
          const [pts, sheetIdx, cellIdx] = closest;
          const sheetImg = entry.sheets.get(sheetIdx);
          if (sheetImg) {
            const col = cellIdx % COLUMNS;
            const row = Math.floor(cellIdx / COLUMNS);
            const x = col * FRAME_WIDTH;
            const y = row * FRAME_HEIGHT;

            const diff = Math.abs(pts - targetTime);

            if (spriteEl) {
              spriteEl.style.backgroundImage = `url("${sheetImg.src}")`;
              spriteEl.style.backgroundPosition = `-${x}px -${y}px`;
              spriteEl.style.backgroundSize = `${COLUMNS * FRAME_WIDTH}px ${ROWS * FRAME_HEIGHT}px`;
              spriteEl.style.display = 'block';
              if (!spriteEl.dataset) spriteEl.dataset = {};
              spriteEl.dataset.frameTime = String(pts);

              if (typeof spriteEl.querySelector === 'function') {
                let img = spriteEl.querySelector('.timeline-sprite-image');
                if (!img && typeof document !== 'undefined' && document.createElement) {
                  img = document.createElement('img');
                  img.className = 'timeline-sprite-image';
                  img.alt = '';
                  img.draggable = false;
                  spriteEl.replaceChildren(img);
                }
                if (img) {
                  const atlasW = COLUMNS * FRAME_WIDTH;
                  const atlasH = ROWS * FRAME_HEIGHT;
                  if (img.src !== sheetImg.src) {
                    img.src = sheetImg.src;
                    img.width = atlasW;
                    img.height = atlasH;
                    img.style.width = `${atlasW}px`;
                    img.style.height = `${atlasH}px`;
                  }
                  img.style.transform = `translate3d(-${x}px, -${y}px, 0)`;
                  img.style.display = 'block';
                }
              }
            }
            if (imgEl) imgEl.style.display = 'none';

            if (timeTextEl) {
              const formatted = formatTime(targetTime);
              const frameTimeStr = diff > 4.0 ? ` • klatka ≈ ${formatTime(pts)}` : '';
              timeTextEl.innerText = `${formatted}${frameTimeStr}`;
            }
            if (statusEl) {
              if (statusText && statusText.includes('bufor')) {
                statusEl.textContent = statusText;
                statusEl.style.display = 'block';
              } else {
                statusEl.style.display = 'none';
              }
            }
            return true;
          }
        }
      }

      // 2. Layer 0: Native CDN prefix (Camwhores) or preview video frames (Archivebate)
      const prefix = nativePrefix || (manifest?.native?.kind === 'prefix' ? manifest.native.prefix : null);
      if (prefix) {
        const count = nativeCount || manifest?.native?.count || 15;
        const frameIdx = Math.min(count, Math.max(1, Math.round(pos * (count - 1)) + 1));
        const src = `${prefix}${frameIdx}.jpg`;

        if (imgEl) {
          if (imgEl.src !== src) imgEl.src = src;
          imgEl.style.display = 'block';
        }
        if (spriteEl) spriteEl.style.display = 'none';

        if (timeTextEl) timeTextEl.innerText = formatTime(targetTime);
        if (statusEl) {
          if (statusText && statusText.includes('bufor')) {
            statusEl.textContent = statusText;
          } else {
            statusEl.textContent = 'Podgląd przybliżony';
          }
          statusEl.style.display = 'block';
        }
        return true;
      }

      if (manifest?.native?.frames && Array.isArray(manifest.native.frames) && manifest.native.frames.length > 0) {
        const nFrames = manifest.native.frames;
        const frameIdx = Math.min(nFrames.length - 1, Math.max(0, Math.round(pos * (nFrames.length - 1))));
        const frameEntry = nFrames[frameIdx];
        const src = Array.isArray(frameEntry) ? frameEntry[1] : frameEntry;
        if (src) {
          if (imgEl) {
            if (imgEl.src !== src) imgEl.src = src;
            imgEl.style.display = 'block';
          }
          if (spriteEl) spriteEl.style.display = 'none';
          if (timeTextEl) timeTextEl.innerText = formatTime(targetTime);
          if (statusEl) {
            if (statusText && statusText.includes('bufor')) {
              statusEl.textContent = statusText;
            } else {
              statusEl.textContent = 'Podgląd przybliżony';
            }
            statusEl.style.display = 'block';
          }
          return true;
        }
      }

      if (statusEl && entry?.terminalFailure) {
        statusEl.textContent = entry.errorMessage || 'Podgląd chwilowo niedostępny.';
        statusEl.style.display = 'block';
      }
      return false;
    }

    attach(config) {
      const {
        timeline,
        tooltip,
        spriteEl,
        imgEl,
        timeTextEl,
        statusEl,
        getVideoId,
        getDuration,
        getSource,
        getNativePrefix,
        getNativeCount,
        getPoster,
      } = config;

      let rafId = 0;
      let lastClientX = 0;
      let isHovering = false;

      const triggerRepaint = () => {
        if (!isHovering || !timeline) return;
        const rect = timeline.getBoundingClientRect();
        if (!rect.width) return;

        const rawX = lastClientX - rect.left;
        const pos = Math.max(0, Math.min(1, rawX / rect.width));
        const duration = getDuration ? getDuration() : 0;
        if (duration <= 0) return;
        const targetTime = pos * duration;

        if (tooltip) {
          const tooltipX = rect.width <= 168 ? rect.width / 2 : Math.max(84, Math.min(rect.width - 84, rawX));
          tooltip.style.setProperty('--timeline-preview-x', `${tooltipX}px`);
          tooltip.style.display = 'flex';
        }

        const videoId = getVideoId ? getVideoId() : '';
        const source = getSource ? getSource() : (videoId.startsWith('cw_') ? 'camwhores' : 'archivebate');
        const videoKey = `${source}:id:${videoId}`;

        this.render({
          videoKey,
          videoId,
          targetTime,
          duration,
          pos,
          spriteEl,
          imgEl,
          timeTextEl,
          statusEl,
          nativePrefix: getNativePrefix ? getNativePrefix() : null,
          nativeCount: getNativeCount ? getNativeCount() : null,
          poster: getPoster ? getPoster() : null,
        });
      };

      const onPointerMove = (e) => {
        lastClientX = e.clientX;
        isHovering = true;
        if (rafId) return;

        rafId = requestAnimationFrame(() => {
          rafId = 0;
          triggerRepaint();
        });
      };

      const onPointerLeave = () => {
        isHovering = false;
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = 0;
        }
        if (tooltip) tooltip.style.display = 'none';
      };

      if (timeline) {
        timeline.addEventListener('pointermove', onPointerMove, { passive: true });
        timeline.addEventListener('pointerleave', onPointerLeave, { passive: true });
      }

      // Prewarm manifest on attach with automatic repaint subscription
      const videoId = getVideoId ? getVideoId() : '';
      const duration = getDuration ? getDuration() : 0;
      const source = getSource ? getSource() : (videoId.startsWith('cw_') ? 'camwhores' : 'archivebate');
      if (videoId && duration > 0) {
        const videoKey = `${source}:id:${videoId}`;
        this.pollManifest(videoKey, videoId, duration, source, () => {
          if (isHovering) {
            triggerRepaint();
          }
        });
      }

      return {
        dispose: () => {
          isHovering = false;
          if (timeline) {
            timeline.removeEventListener('pointermove', onPointerMove);
            timeline.removeEventListener('pointerleave', onPointerLeave);
          }
          if (rafId) {
            cancelAnimationFrame(rafId);
            rafId = 0;
          }
          const vid = getVideoId ? getVideoId() : '';
          const src = getSource ? getSource() : (vid.startsWith('cw_') ? 'camwhores' : 'archivebate');
          if (vid) {
            this.stopPolling(vid, src);
          }
        },
      };
    }
  }

  const manager = new TimelinePreviewManager();
  global.ArchivebateTimelinePreview = {
    manager,
    canonicalKey,
    render: (opts) => manager.render(opts),
    attach: (cfg) => manager.attach(cfg),
    hasFrames: (vid, src) => manager.hasFrames(vid, src),
    subscribe: (key, subId, cb, src) => manager.subscribe(key, subId, cb, src),
    unsubscribe: (key, subId, src) => manager.unsubscribe(key, subId, src),
    stopPolling: (key, src) => manager.stopPolling(key, src),
    getSessionId: (key, src) => manager.getSessionId(key, src),
    binarySearchNearest,
  };

  if (typeof global !== 'undefined' && global.addEventListener) {
    let leasesReleased = false;
    const handleTeardown = () => {
      if (leasesReleased) return;
      leasesReleased = true;
      manager.releaseAllLeases();
    };
    global.addEventListener('pagehide', handleTeardown, { capture: true });
    global.addEventListener('beforeunload', handleTeardown, { capture: true });
  }

})(typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : this));
