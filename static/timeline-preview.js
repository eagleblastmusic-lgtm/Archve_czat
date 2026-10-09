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

  class TimelinePreviewManager {
    constructor() {
      this.cache = new Map(); // videoKey -> { manifest, sheets: Map, sheetUrls: Map, sheetRevs: Map, activeRev, generation, fetching, controller, listeners: Set, lastAccessed }
      this.maxCachedEntries = 12;
      this.activeVideoKey = null;
      this.activeInstances = new Set();
    }

    getOrCreateEntry(videoKey, source) {
      const key = canonicalKey(videoKey, source);
      if (!this.cache.has(key)) {
        this.cache.set(key, {
          manifest: null,
          sheets: new Map(),
          sheetUrls: new Map(),
          sheetRevs: new Map(),
          activeRev: 0,
          generation: 0,
          fetching: false,
          controller: null,
          listeners: new Set(),
          lastAccessed: Date.now(),
        });
        this.evictLru(key);
      }
      const entry = this.cache.get(key);
      if (!entry.sheetUrls) entry.sheetUrls = new Map();
      if (!entry.sheetRevs) entry.sheetRevs = new Map();
      if (!entry.listeners) entry.listeners = new Set();
      entry.lastAccessed = Date.now();
      return entry;
    }

    evictLru(protectedKey) {
      if (this.cache.size <= this.maxCachedEntries) return;
      const candidates = [];
      for (const [k, e] of this.cache.entries()) {
        if (k === protectedKey || k === this.activeVideoKey) continue;
        if (e.fetching || (e.listeners && e.listeners.size > 0)) continue;
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

    notifyListeners(entry, manifest) {
      if (!entry || !entry.listeners || entry.listeners.size === 0) return;
      for (const listener of Array.from(entry.listeners)) {
        try {
          listener(manifest);
        } catch (err) {
          console.error('[TimelinePreview] listener error:', err);
        }
      }
    }

    async pollManifest(videoKey, videoId, duration, source, onUpdate) {
      if (typeof document !== 'undefined' && document.hidden) return;
      duration = Number(duration) || 0;
      if ((!videoKey && !videoId) || duration <= 0) return;

      const key = canonicalKey(videoKey || videoId, source);
      const entry = this.getOrCreateEntry(key);
      entry.lastAccessed = Date.now();

      if (onUpdate && typeof onUpdate === 'function') {
        entry.listeners.add(onUpdate);
        // Immediate notification if manifest with ready sheets is already in cache
        if (entry.manifest && entry.sheets && entry.sheets.size > 0) {
          try { onUpdate(entry.manifest); } catch (_) {}
        }
      }

      if (entry.fetching) return;
      entry.fetching = true;
      const controller = new AbortController();
      entry.controller = controller;
      const targetGeneration = entry.generation || 0;

      let sinceRev = entry.manifest ? (entry.manifest.rev || 0) : 0;
      let failureCount = 0;

      try {
        // Trigger background build if needed
        const token = getMutationToken();
        const buildHeaders = token ? { 'X-Archivebate-Mutation-Token': token } : {};
        fetch(`/api/storyboard/v2/build?id=${encodeURIComponent(videoId)}&duration=${duration}&source=${encodeURIComponent(source || '')}`, {
          method: 'POST',
          headers: buildHeaders,
          signal: controller.signal,
        }).catch(() => {});

        let loopIter = 0;
        while (!controller.signal.aborted && entry.generation === targetGeneration) {
          loopIter++;
          if (loopIter % 8 === 0 && (!entry.manifest || !entry.manifest.complete)) {
            fetch(`/api/storyboard/v2/build?id=${encodeURIComponent(videoId)}&duration=${duration}&source=${encodeURIComponent(source || '')}`, {
              method: 'POST',
              headers: buildHeaders,
              signal: controller.signal,
            }).catch(() => {});
          }
          const waitMs = sinceRev > 0 ? 1500 : 0;
          const url = `/api/storyboard/v2?id=${encodeURIComponent(videoId)}&duration=${duration}&since_rev=${sinceRev}&wait_ms=${waitMs}&source=${encodeURIComponent(source || '')}`;
          
          try {
            const resp = await fetch(url, { signal: controller.signal });
            if (!resp.ok) {
              failureCount++;
              if (failureCount > 5) break;
              await new Promise(r => setTimeout(r, 1000));
              continue;
            }
            failureCount = 0;
            const manifest = await resp.json();
            if (manifest && manifest.version === 11 && entry.generation === targetGeneration) {
              const manifestRev = Number(manifest.rev || 0);
              if (manifestRev >= (entry.activeRev || 0)) {
                entry.manifest = manifest;
                entry.activeRev = manifestRev;
                sinceRev = manifestRev || sinceRev;

                // Preload new sheets or updated revisions without race conditions
                if (Array.isArray(manifest.sheets)) {
                  for (const s of manifest.sheets) {
                    if (s && s.url) {
                      const currentUrl = entry.sheetUrls.get(s.index);
                      const currentRev = entry.sheetRevs.get(s.index) || 0;
                      if (currentUrl !== s.url || manifestRev > currentRev) {
                        this.preloadSheet(s.url, manifestRev, s.index, targetGeneration, entry).then(loadedImg => {
                          if (loadedImg && entry.generation === targetGeneration) {
                            this.notifyListeners(entry, entry.manifest);
                          }
                        });
                      }
                    }
                  }
                }

                this.notifyListeners(entry, manifest);
              }
              if (manifest.complete) break;
            }
          } catch (err) {
            if (controller.signal.aborted || entry.generation !== targetGeneration) break;
            failureCount++;
            if (failureCount > 5) break;
            await new Promise(r => setTimeout(r, 1000));
          }
        }
      } finally {
        if (entry.generation === targetGeneration) {
          entry.fetching = false;
          if (entry.controller === controller) {
            entry.controller = null;
          }
        }
      }
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
    stopPolling: (key, src) => manager.stopPolling(key, src),
    binarySearchNearest,
  };

})(typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : this));
