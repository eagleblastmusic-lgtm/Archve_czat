(() => {
  'use strict';

  // v2 celowo używa nowej bazy. v1 mogła zawierać wiele kopii tej samej
  // klatki zapisanych zanim dekoder faktycznie zaprezentował seekowany frame.
  const DB_NAME = 'archivebate-storyboards-v2';
  const STORE = 'boards';
  const RECORD_VERSION = 2;
  const MAX_ENTRIES = 60;
  const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: 'key' });
          store.createIndex('createdAt', 'createdAt');
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function boardLooksValid(result) {
    if (!result || result.version !== RECORD_VERSION || !Array.isArray(result.frames) || result.frames.length < 2) return false;
    // Definitywnie odrzucamy uszkodzony "storyboard", jeśli każda zapisana
    // klatka jest bajt w bajt identyczna. To dokładnie objaw zgłoszonego błędu.
    if (result.frames.length >= 4 && new Set(result.frames).size <= 1) return false;
    return true;
  }

  async function get(key) {
    if (!key || !('indexedDB' in window)) return null;
    try {
      const db = await openDB();
      const result = await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).get(key);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
      });
      db.close();
      if (!result) return null;
      if (Date.now() - result.createdAt > MAX_AGE_MS || !boardLooksValid(result)) {
        remove(key).catch(() => {});
        return null;
      }
      return result;
    } catch (_) {
      return null;
    }
  }

  async function remove(key) {
    try {
      const db = await openDB();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(key);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
      });
      db.close();
    } catch (_) { /* noop */ }
  }

  async function put(record) {
    if (!record || !record.key || !('indexedDB' in window)) return;
    try {
      const db = await openDB();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put(record);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
      });
      const all = await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).getAll();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror = () => reject(req.error);
      });
      if (all.length > MAX_ENTRIES) {
        all.sort((a, b) => a.createdAt - b.createdAt);
        const removeKeys = all.slice(0, all.length - MAX_ENTRIES).map(x => x.key);
        await new Promise((resolve, reject) => {
          const tx = db.transaction(STORE, 'readwrite');
          const store = tx.objectStore(STORE);
          removeKeys.forEach(k => store.delete(k));
          tx.oncomplete = resolve;
          tx.onerror = () => reject(tx.error);
        });
      }
      db.close();
    } catch (_) { /* cache failure must never break player */ }
  }

  function once(target, eventName, timeoutMs = 5000) {
    return new Promise((resolve, reject) => {
      let timer;
      const cleanup = () => {
        target.removeEventListener(eventName, onEvent);
        target.removeEventListener('error', onError);
        clearTimeout(timer);
      };
      const onEvent = () => { cleanup(); resolve(); };
      const onError = () => { cleanup(); reject(new Error('video error')); };
      target.addEventListener(eventName, onEvent, { once: true });
      target.addEventListener('error', onError, { once: true });
      timer = setTimeout(() => { cleanup(); reject(new Error('timeout')); }, timeoutMs);
    });
  }

  async function waitForFrame(video, target) {
    if (window.ArchivebatePlayerCore?.waitForPresentedFrame) {
      await window.ArchivebatePlayerCore.waitForPresentedFrame(video, target, 1600);
      return;
    }
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  async function seekAndWaitForDecodedFrame(video, target) {
    const epsilon = 0.02;
    if (Math.abs((video.currentTime || 0) - target) > epsilon) {
      video.currentTime = target;
      await once(video, 'seeked', 7000);
    }
    // Kluczowa różnica względem v1: nie kopiujemy canvasa na samym `seeked`.
    await waitForFrame(video, target);
  }

  async function build({ key, src, frameCount = 20, width = 180, height = 101, signal }) {
    if (!key || !src || signal?.aborted) return null;
    const existing = await get(key);
    if (existing && existing.frames.length >= Math.min(8, frameCount)) return existing;

    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.preload = 'auto';
    video.crossOrigin = 'anonymous';
    video.src = src;
    video.style.position = 'fixed';
    video.style.left = '-10000px';
    video.style.top = '-10000px';
    video.style.width = '2px';
    video.style.height = '2px';
    document.body.appendChild(video);

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d', { alpha: false, willReadFrequently: false });

    try {
      video.load();
      if (video.readyState < 1) await once(video, 'loadedmetadata', 10000);
      const duration = video.duration;
      if (!Number.isFinite(duration) || duration <= 0) return null;
      try { video.pause(); } catch (_) {}

      const frames = [];
      for (let i = 0; i < frameCount; i += 1) {
        if (signal?.aborted) return null;
        // Nie próbkujemy dokładnie 0/duration. Niektóre MP4 mają tam ten sam poster/keyframe.
        const ratio = frameCount === 1 ? 0.5 : (i + 0.35) / frameCount;
        const target = Math.min(Math.max(0.01, duration * ratio), Math.max(0.01, duration - 0.08));
        await seekAndWaitForDecodedFrame(video, target);

        try {
          ctx.drawImage(video, 0, 0, width, height);
          frames.push(canvas.toDataURL('image/jpeg', 0.66));
        } catch (_) {
          return null;
        }
        await new Promise(resolve => setTimeout(resolve, 0));
      }

      // Nie zapisuj ponownie wadliwego storyboardu składającego się z jednego kadru.
      if (frames.length >= 4 && new Set(frames).size <= 1) return null;

      const record = {
        key,
        version: RECORD_VERSION,
        frames,
        duration,
        width,
        height,
        createdAt: Date.now()
      };
      await put(record);
      return record;
    } catch (_) {
      return null;
    } finally {
      video.removeAttribute('src');
      try { video.load(); } catch (_) {}
      video.remove();
    }
  }

  window.ArchivebateStoryboard = { get, put, remove, build };
})();
