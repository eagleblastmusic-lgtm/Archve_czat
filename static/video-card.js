/**
 * Archivebate Video Browser - Video Card & Hover Preview Module
 * Odpowiada za tworzenie elementów DOM karty wideo, obsługę plakatów i fallbacków,
 * podgląd wideo po najechaniu (hover preview), przeglądanie klatek (timeline scrub),
 * integrację ze storyboardem (Camwhores / Archivebate YouTube-style) oraz prefetch metadanych.
 */

(function (global) {
  'use strict';

  const state = new Proxy({}, {
    get(target, prop) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.state && prop in ctx.state) return ctx.state[prop];
      if (global.state && prop in global.state) return global.state[prop];
      return target[prop];
    },
    set(target, prop, value) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.state) { ctx.state[prop] = value; return true; }
      if (global.state) { global.state[prop] = value; return true; }
      target[prop] = value;
      return true;
    }
  });

  const dom = new Proxy({}, {
    get(target, prop) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.dom && prop in ctx.dom) return ctx.dom[prop];
      if (global.dom && prop in global.dom) return global.dom[prop];
      return target[prop];
    },
    set(target, prop, value) {
      const ctx = global.ArchivebateAppContext;
      if (ctx && ctx.dom) { ctx.dom[prop] = value; return true; }
      if (global.dom) { global.dom[prop] = value; return true; }
      target[prop] = value;
      return true;
    }
  });

  let activeHoverVideo = null;
  const groupMembersInflight = new Map();

  function getSharedGroupMembers(url, signal) {
    if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
    const requestKey = new URL(url, global.location?.href || 'http://localhost/').href;
    let entry = groupMembersInflight.get(requestKey);
    if (!entry) {
      const controller = new AbortController();
      entry = { controller, consumers: 0, settled: false, promise: null };
      entry.promise = global.ArchivebateAPI.getJSON(url, { timeoutMs: 12000, signal: controller.signal })
        .finally(() => {
          entry.settled = true;
          if (groupMembersInflight.get(requestKey) === entry) groupMembersInflight.delete(requestKey);
        });
      groupMembersInflight.set(requestKey, entry);
    }
    entry.consumers += 1;
    return new Promise((resolve, reject) => {
      let finished = false;
      const release = cancelled => {
        if (finished) return;
        finished = true;
        signal?.removeEventListener?.('abort', onAbort);
        entry.consumers = Math.max(0, entry.consumers - 1);
        if (cancelled && entry.consumers === 0 && !entry.settled) entry.controller.abort();
      };
      const onAbort = () => {
        release(true);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      signal?.addEventListener?.('abort', onAbort, { once: true });
      entry.promise.then(
        data => { release(false); resolve(data); },
        error => { release(false); reject(error); }
      );
      if (signal?.aborted) onAbort();
    });
  }

  const formatPlayerTime = (seconds) => {
    if (global.ArchivebatePlayerCore && typeof global.ArchivebatePlayerCore.formatTime === 'function') {
      return global.ArchivebatePlayerCore.formatTime(seconds);
    }
    if (typeof global.formatPlayerTime === 'function') {
      return global.formatPlayerTime(seconds);
    }
    return String(seconds || '00:00');
  };

  const parseDurationToSeconds = (durStr) => {
    if (global.ArchivebatePlayerCore && typeof global.ArchivebatePlayerCore.parseDurationToSeconds === 'function') {
      return global.ArchivebatePlayerCore.parseDurationToSeconds(durStr);
    }
    if (typeof global.parseDurationToSeconds === 'function') {
      return global.parseDurationToSeconds(durStr);
    }
    return 0;
  };

  function isFavoriteAuthor(username) {
    if (global.ArchivebateFavorites && typeof global.ArchivebateFavorites.isFavoriteAuthor === 'function') {
      return global.ArchivebateFavorites.isFavoriteAuthor(username);
    }
    if (typeof global.isFavoriteAuthor === 'function') {
      return global.isFavoriteAuthor(username);
    }
    return false;
  }

  function armLazyThumbnail(img) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.armLazyThumbnail === 'function') {
      return global.ArchivebateVideoPrefetch.armLazyThumbnail(img);
    }
    if (typeof global.armLazyThumbnail === 'function') {
      return global.armLazyThumbnail(img);
    }
  }

  function prefetchVideoDetails(video, options = {}) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.prefetchVideoDetails === 'function') {
      return global.ArchivebateVideoPrefetch.prefetchVideoDetails(video, options);
    }
    if (typeof global.prefetchVideoDetails === 'function') {
      return global.prefetchVideoDetails(video, options);
    }
    return Promise.resolve(null);
  }

  function thumbnailUrlForVideo(v) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.thumbnailUrlForVideo === 'function') {
      return global.ArchivebateVideoPrefetch.thumbnailUrlForVideo(v);
    }
    if (typeof global.thumbnailUrlForVideo === 'function') {
      return global.thumbnailUrlForVideo(v);
    }
    if (!v) return '';
    return v.poster_proxy || v.thumbnail_proxy || (v.poster ? `/api/thumb?url=${encodeURIComponent(v.poster)}` : '');
  }

  function setCheckpoint(v) {
    if (global.ArchivebateCheckpoints && typeof global.ArchivebateCheckpoints.setCheckpoint === 'function') {
      return global.ArchivebateCheckpoints.setCheckpoint(v);
    }
    if (typeof global.setCheckpoint === 'function') {
      return global.setCheckpoint(v);
    }
  }

  function performSearch(query, page) {
    if (global.ArchivebateSearchResults && typeof global.ArchivebateSearchResults.performSearch === 'function') {
      return global.ArchivebateSearchResults.performSearch(query, page);
    }
    if (typeof global.performSearch === 'function') {
      return global.performSearch(query, page);
    }
  }

  function setActiveNavTab(tabBtn) {
    if (global.ArchivebateAppEvents && typeof global.ArchivebateAppEvents.setActiveNavTab === 'function') {
      return global.ArchivebateAppEvents.setActiveNavTab(tabBtn);
    }
    if (typeof global.setActiveNavTab === 'function') {
      return global.setActiveNavTab(tabBtn);
    }
  }

  function toggleFavoriteVideo(video, btn) {
    if (global.ArchivebateFavorites && typeof global.ArchivebateFavorites.toggleVideo === 'function') {
      return global.ArchivebateFavorites.toggleVideo(video, btn);
    }
    if (typeof global.toggleFavoriteVideo === 'function') {
      return global.toggleFavoriteVideo(video, btn);
    }
  }

  function openVideoModal(video) {
    if (global.ArchivebateVideoModal && typeof global.ArchivebateVideoModal.openVideoModal === 'function') {
      return global.ArchivebateVideoModal.openVideoModal(video);
    }
    if (typeof global.openVideoModal === 'function') {
      return global.openVideoModal(video);
    }
  }

  function loadModelVideos(username, page) {
    if (global.ArchivebateVideoViews && typeof global.ArchivebateVideoViews.loadModelVideos === 'function') {
      return global.ArchivebateVideoViews.loadModelVideos(username, page);
    }
    if (typeof global.loadModelVideos === 'function') {
      return global.loadModelVideos(username, page);
    }
  }

  function blockModel(username) {
    if (global.ArchivebateBlockedModels && typeof global.ArchivebateBlockedModels.block === 'function') {
      return global.ArchivebateBlockedModels.block(username);
    }
    if (typeof global.blockModel === 'function') {
      return global.blockModel(username);
    }
  }

  function escapeHtml(value) {
    if (global.ArchivebateDOM && typeof global.ArchivebateDOM.escapeHtml === 'function') {
      return global.ArchivebateDOM.escapeHtml(value);
    }
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function safeUrl(value) {
    const raw = safeUrlValue(value);
    return raw ? escapeHtml(raw) : '';
  }

  function safeUrlValue(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    if (/^https?:\/\//i.test(raw) || /^\/(?!\/)/.test(raw) || raw === '#') return raw;
    return '';
  }

  function createVideoCard(v, idx) {
    const isLocalScope = () => v._mediaScope === 'local_catalog';
    let isCamwhores = v.source === 'camwhores' || String(v.id).startsWith('cw_') || (v.platform && v.platform.toLowerCase().includes('camwhores'));
    const isFav = !!v.is_favorite;
    const isFavAuthor = isFavoriteAuthor(v.username) || v.has_favorite_video;
    const isFavCard = isFav || isFavAuthor;

    let isGrouped = Boolean(v.is_grouped || v._isGrouped || v.group_members_lazy || (v.group_count && v.group_count > 1) || (v._groupCount && v._groupCount > 1));
    let groupCount = v.group_count ?? v._groupCount ?? (v.grouped_videos ? v.grouped_videos.length : (v._groupedVideos ? v._groupedVideos.length : 1));
    let groupedVideosList = v.grouped_videos || v._groupedVideos || [];
    let hasGroupDrawer = isGrouped && (groupCount > 1 || v.group_members_lazy || v.group_members_url);

    const card = document.createElement('div');
    card.className = `video-card ${isFavCard ? 'is-favorite-card' : ''} ${isGrouped && groupCount > 1 ? 'is-grouped-card' : ''}`;
    card.dataset.videoId = String(v.id);
    card.dataset.username = String(v.username || '').toLowerCase().trim();
    card.dataset.source = isCamwhores ? 'camwhores' : 'archivebate';
    card._videoData = v;
    card._cardIndex = idx;

    const directPoster = isLocalScope() ? '' : (v.poster_direct || (v.poster ? v.poster.replace(/\.mp4$/, '.jpg') : ''));
    const rawPoster = directPoster || (v.poster ? v.poster.replace(/\.mp4$/, '.jpg') : '');
    const canonicalPoster = thumbnailUrlForVideo(v) || directPoster;
    const displayPoster = canonicalPoster;
    const backupPoster = directPoster;
    const safeId = escapeHtml(v.id);
    const safeUsername = escapeHtml(v.username || 'Model');
    const safeDate = escapeHtml(v.date || 'Niedawno');
    const safeViews = escapeHtml(v.views || '');
    const safeDuration = escapeHtml(v.duration || '');
    const safePlatform = escapeHtml(v.platform || 'Archive');
    const safeGroupCount = escapeHtml(groupCount);
    const safeDisplayPoster = safeUrl(displayPoster);
    const safeBackupPoster = safeUrl(backupPoster);
    let thumbnailSource = safeUrlValue(displayPoster);
    const watchUrl = video => `/watch/${encodeURIComponent(
      video.source === 'camwhores' && !String(video.id).startsWith('cw_') ? `cw_${video.id}` : video.id
    )}`;

    const eagerThumb = idx < 16;
    const thumbLoadAttrs = eagerThumb
      ? `src="${safeDisplayPoster}" loading="eager" fetchpriority="${idx < 4 ? 'high' : 'auto'}"`
      : `data-src="${safeDisplayPoster}" loading="lazy" fetchpriority="low"`;

    card.innerHTML = `
      <div class="thumbnail-wrapper">
        <a class="video-open-link" href="${watchUrl(v)}" aria-label="Odtwórz film ${safeUsername}" title="Odtwórz · środkowy przycisk: nowa karta"></a>
        <img class="thumbnail-img" ${thumbLoadAttrs} data-fallback="${safeBackupPoster}" alt="${safeUsername}" decoding="async">
        <img class="hover-preview-frame" style="display: none; position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; z-index: 2; pointer-events: none;" alt="Preview">
        <video class="hover-preview-video" muted playsinline preload="none"></video>
        
        <!-- Oś czasu do przeglądania klatek w miniaturce kursorem (czas przybliżony) -->
        <div class="card-scrub-bar">
          <div class="card-scrub-tooltip">00:00</div>
          <div class="card-scrub-progress"></div>
          <div class="card-scrub-thumb"></div>
        </div>

        <span class="badge-platform ${isCamwhores ? 'badge-camwhores' : ''}">${isCamwhores ? '<i class="fa-solid fa-tv"></i> Camwhores' : safePlatform}</span>
        ${v.views ? `<span class="badge-views"><i class="fa-solid fa-eye"></i> ${safeViews}</span>` : ''}
        ${v.duration && v.duration !== 'N/A' ? `<span class="badge-duration">${safeDuration}</span>` : ''}
        ${hasGroupDrawer ? `
          <span class="badge-group-count" title="Ten autor ma ${escapeHtml(groupCount)} filmów na liście. Kliknij, aby rozwinąć listę!">
            <i class="fa-solid fa-layer-group"></i> ${safeGroupCount} filmów
          </span>
        ` : ''}
        
        <!-- Szybki przycisk ulubione -->
        <button type="button" class="card-fav-btn ${isFav ? 'active' : ''}" aria-label="${isFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'}" aria-pressed="${isFav ? 'true' : 'false'}" title="${isFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'}">
          <i class="${isFav ? 'fa-solid' : 'fa-regular'} fa-heart"></i>
        </button>
      </div>

      <div class="card-details">
        <div class="card-header-info">
          <a href="#" class="model-profile-link ${(isFavoriteAuthor(v.username) || v.has_favorite_video) ? 'is-favorite-author' : ''}" data-username="${safeUsername}">
            <i class="fa-solid fa-circle-user"></i> ${safeUsername}${(isFavoriteAuthor(v.username) || v.has_favorite_video) ? '<i class="fa-solid fa-star fav-author-star" title="Masz film tej modelki w ulubionych"></i>' : ''}
          </a>
          ${hasGroupDrawer ? `<span class="author-group-pill" title="Zgrupowano ${safeGroupCount} nagrań tego twórcy"><i class="fa-solid fa-clone"></i> Grupa (${safeGroupCount})</span>` : ''}
          <button type="button" class="card-date-badge" data-video-id="${safeId}" aria-label="Ustaw checkpoint dla filmu z datą ${safeDate}" title="Kliknij na datę, aby ustawić punkt kontrolny (checkpoint)"><i class="fa-regular fa-calendar-days"></i> ${safeDate}</button>
        </div>

        <div class="card-tags-row">
          ${(v.tags || []).slice(0, 3).map(t => `<button type="button" class="card-tag-badge" data-tag="${escapeHtml(String(t).toLowerCase())}" aria-label="Filtruj tag ${escapeHtml(t)}">#${escapeHtml(t)}</button>`).join('')}
        </div>

        <div class="card-actions-row">
          <a class="btn-card primary play-btn" href="${watchUrl(v)}">
            <i class="fa-solid fa-play"></i> Odtwórz
          </a>
          <button class="btn-card profile-btn" data-username="${safeUsername}" title="Zobacz profil i nagrania modelki ${safeUsername}">
            <i class="fa-solid fa-folder${hasGroupDrawer ? '-open' : ''}"></i> ${hasGroupDrawer ? `${safeGroupCount} filmów` : 'Filmy'}
          </button>
          ${hasGroupDrawer ? `
            <button type="button" class="btn-card expand-group-btn" aria-label="Rozwiń grupę ${escapeHtml(groupCount)} filmów" title="Rozwiń podgląd wszystkich ${escapeHtml(groupCount)} filmów tej grupy">
              <i class="fa-solid fa-chevron-down"></i>
            </button>
          ` : ''}
          <button type="button" class="btn-card danger block-model-btn" data-username="${safeUsername}" aria-label="Zablokuj profil ${safeUsername}" title="Zablokuj modelkę: ukryj wszystkie jej nagrania">
            <i class="fa-solid fa-ban"></i>
          </button>
        </div>
        ${hasGroupDrawer ? `
          <div class="grouped-videos-drawer" style="display: none;"></div>
        ` : ''}
      </div>
    `;

    // Punkt kontrolny (checkpoint) po kliknięciu na datę
    const dateBadge = card.querySelector('.card-date-badge');
    if (dateBadge) {
      dateBadge.dataset.origDate = v.date || 'Niedawno';
      dateBadge.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        setCheckpoint(card._videoData || v);
      });
    }

    // Kliknięcie w tag na kafelku
    card.querySelectorAll('.card-tag-badge').forEach(tagBadge => {
      const clickedTag = tagBadge.dataset.tag;
      tagBadge.title = `Filtruj tag #${clickedTag} (LPM) lub otwórz w nowej karcie (Kółko myszy)`;
      tagBadge.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (clickedTag) {
          if (dom.searchInput) dom.searchInput.value = `#${clickedTag}`;
          if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = 'flex';
          setActiveNavTab(null);
          performSearch(clickedTag, 1);
        }
      });
      tagBadge.addEventListener('auxclick', (e) => {
        if (e.button === 1) {
          e.preventDefault();
          e.stopPropagation();
          if (clickedTag && global.tabManager && typeof global.tabManager.openTab === 'function') {
            global.tabManager.openTab({
              title: `#${clickedTag}`,
              icon: 'fa-solid fa-tag',
              type: 'search',
              query: `#${clickedTag}`,
              inBackground: true
            });
          }
        }
      });
      tagBadge.addEventListener('mousedown', (e) => {
        if (e.button === 1) e.preventDefault();
      });
    });

    // Szybkie dodawanie do ulubionych
    const favBtn = card.querySelector('.card-fav-btn');
    if (favBtn) {
      favBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleFavoriteVideo(card._videoData || v, favBtn);
      });
    }

    // Płynny podgląd wideo po najechaniu myszką
    const cardThumbImg = card.querySelector('.thumbnail-img');
    const thumbWrapper = card.querySelector('.thumbnail-wrapper');
    let thumbnailError = null;
    let thumbnailRetryTimer = null;
    let thumbnailRetryCount = 0;
    let thumbnailRecoveryController = null;
    const clearThumbnailError = () => {
      clearTimeout(thumbnailRetryTimer);
      thumbnailError?.remove();
      thumbnailError = null;
      if (cardThumbImg) cardThumbImg.style.visibility = '';
    };
    const showThumbnailError = () => {
      if (thumbnailError || !cardThumbImg) return;
      cardThumbImg.style.visibility = 'hidden';
      thumbnailError = document.createElement(isLocalScope() ? 'span' : 'button');
      thumbnailError.type = 'button';
      thumbnailError.className = 'thumbnail-retry';
      thumbnailError.textContent = isLocalScope() ? 'Brak lokalnej miniatury · Odtwórz, aby pobrać media' : 'Miniatura niedostępna · Ponów';
      thumbnailError.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        clearThumbnailError();
        thumbnailRecoveryController?.abort();
        thumbnailRecoveryController = null;
        thumbnailRetryCount = 0;
        delete cardThumbImg.dataset.retried;
        cardThumbImg.loading = 'eager';
        const current = card._videoData || v;
        const url = thumbnailUrlForVideo(current) || current.poster_direct || '';
        if (url) cardThumbImg.src = safeUrlValue(url);
        else showThumbnailError();
      });
      thumbWrapper.appendChild(thumbnailError);
    };
    const recoverThumbnail = () => {
      if (isLocalScope()) { showThumbnailError(); return; }
      const current = card._videoData || v;
      const preview = String(current.preview_video || '');
      // Only the provider's tiny thumbnail clip is eligible. Never open the
      // full movie merely to repair a poster, especially for Camwhores.
      let parsed;
      try { parsed = new URL(preview, global.location?.href); } catch (_) {}
      if (thumbnailRecoveryController || !parsed ||
          !(parsed.hostname === 'freefile.io' || parsed.hostname.endsWith('.freefile.io')) ||
          !parsed.pathname.includes('/thumbnails/') || !parsed.pathname.endsWith('.mp4')) {
        showThumbnailError();
        return;
      }
      const source = thumbnailSource;
      const controller = thumbnailRecoveryController = new AbortController();
      const viewSignal = state.gridController?.signal || state.viewController?.signal;
      const abort = () => controller.abort();
      viewSignal?.addEventListener?.('abort', abort, { once: true });
      if (viewSignal?.aborted) controller.abort();
      const decode = () => new Promise(resolve => {
        if (controller.signal.aborted || card.isConnected === false) { resolve(false); return; }
        const clip = document.createElement('video');
        clip.muted = true;
        clip.playsInline = true;
        clip.preload = 'auto';
        let settled = false;
        let timer;
        const finish = ok => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          controller.signal.removeEventListener('abort', cancelled);
          clip.onloadeddata = clip.onerror = null;
          clip.pause();
          clip.removeAttribute('src');
          clip.load();
          resolve(ok);
        };
        const cancelled = () => finish(false);
        controller.signal.addEventListener('abort', cancelled, { once: true });
        timer = setTimeout(() => finish(false), 7000);
        clip.onerror = () => finish(false);
        clip.onloadeddata = () => {
          if (controller.signal.aborted || source !== thumbnailSource || card.isConnected === false) { finish(false); return; }
          try {
            const canvas = document.createElement('canvas');
            canvas.width = 320;
            canvas.height = 180;
            canvas.getContext('2d').drawImage(clip, 0, 0, 320, 180);
            cardThumbImg.dataset.recovered = source;
            cardThumbImg.src = canvas.toDataURL('image/jpeg', 0.8);
            clearThumbnailError();
            finish(true);
          } catch (_) { finish(false); }
        };
        clip.src = `/api/video/stream?url=${encodeURIComponent(preview)}&owner=thumbnail&priority=low&reason=poster_recovery`;
        clip.load();
      });
      const run = global.ArchivebatePerf?.schedule
        ? global.ArchivebatePerf.schedule(decode, { priority: 1, signal: controller.signal }) : decode();
      run.then(ok => { if (!ok && !controller.signal.aborted) showThumbnailError(); }).catch(() => {})
        .finally(() => { viewSignal?.removeEventListener?.('abort', abort); });
    };
    if (cardThumbImg) {
      cardThumbImg.addEventListener('load', clearThumbnailError);
      cardThumbImg.addEventListener('error', () => {
        if (isLocalScope()) { showThumbnailError(); return; }
        const backup = cardThumbImg.dataset.fallback;
        if (!cardThumbImg.dataset.retried && backup && cardThumbImg.getAttribute('src') !== backup) {
          cardThumbImg.dataset.retried = '1';
          cardThumbImg.src = backup;
        } else if (thumbnailRetryCount < 2) {
          const source = thumbnailSource;
          thumbnailRetryCount += 1;
          clearTimeout(thumbnailRetryTimer);
          thumbnailRetryTimer = setTimeout(() => {
            if (card.isConnected === false || source !== thumbnailSource) return;
            const rect = card.getBoundingClientRect?.();
            if (rect && (rect.top > global.innerHeight + 900 || rect.bottom < -900)) {
              showThumbnailError();
              return;
            }
            cardThumbImg.loading = 'eager';
            const url = safeUrlValue(source);
            if (!url) { showThumbnailError(); return; }
            cardThumbImg.src = `${url}${url.includes('?') ? '&' : '?'}thumb_retry=${thumbnailRetryCount}`;
          }, thumbnailRetryCount * 750);
        } else recoverThumbnail();
      });
      if (!eagerThumb) armLazyThumbnail(cardThumbImg);
      if (!displayPoster) showThumbnailError();
      (state.gridController?.signal || state.viewController?.signal)?.addEventListener?.('abort', () => {
        clearTimeout(thumbnailRetryTimer);
      }, { once: true });
    }

    const hoverVideo = card.querySelector('.hover-preview-video');
    const hoverFrame = card.querySelector('.hover-preview-frame');
    const cardSprite = document.createElement('div');
    cardSprite.style.cssText = 'display:none;position:absolute;width:160px;height:90px;overflow:hidden;left:50%;top:50%;transform:translate(-50%,-50%) scale(1.5);pointer-events:none;z-index:3';
    thumbWrapper.appendChild(cardSprite);
    let cardBoardController = null;
    const scrubProgress = card.querySelector('.card-scrub-progress');
    const scrubThumb = card.querySelector('.card-scrub-thumb');
    const scrubTooltip = card.querySelector('.card-scrub-tooltip');

    const getTimelinePrefix = video => {
      const poster = video?.poster_direct || (video?.poster ? video.poster.replace(/\.mp4$/, '.jpg') : '');
      return video?.timeline_prefix || (poster && poster.includes('/180x135/') ? poster.substring(0, poster.indexOf('/180x135/') + 9) : null);
    };
    let timelinePrefix = getTimelinePrefix(v);
    let timelineCount = Number(v.timeline_count) || 15;
    let durationSec = parseDurationToSeconds(v.duration);
    let mediaGeneration = 0;

    let isHovered = false;
    let pendingPos = null;
    let preloadedFrames = false;
    let hoverSeekTimer = null;
    let storyboardWarmTimer = null;
    let cardStoryboard = null;
    card._mediaSignature = `${v.source || (isCamwhores ? 'camwhores' : 'archivebate')}:${v.id || ''}:${v.timeline_prefix || ''}:${v.preview_video || ''}:${v.poster || ''}:${v.duration || ''}`;

    function loadCachedCardStoryboard() {
      if (timelinePrefix || !v.id || !durationSec || cardBoardController) return;
      const YouTubeStoryboard = global.ArchivebateYouTubeStoryboard;
      if (!YouTubeStoryboard || typeof YouTubeStoryboard.prepare !== 'function') return;

      const generation = mediaGeneration;
      const videoId = v.id;
      const videoDuration = durationSec;
      const controller = cardBoardController = new AbortController();
      const viewSignal = state.gridController?.signal || state.viewController?.signal;
      const cancel = () => controller.abort();
      viewSignal?.addEventListener('abort', cancel, { once: true });
      controller.signal.addEventListener('abort', () => viewSignal?.removeEventListener('abort', cancel), { once: true });

      const accept = board => {
        if (controller.signal.aborted || generation !== mediaGeneration || !isHovered) return;
        cardStoryboard = board;
        if (hoverVideo) { hoverVideo.pause(); hoverVideo.removeAttribute('src'); hoverVideo.load(); }
        showLocalStoryboardFrame(pendingPos ?? 0);
      };

      storyboardWarmTimer = setTimeout(() => {
        if (!isHovered || controller.signal.aborted || generation !== mediaGeneration) return;
        YouTubeStoryboard.prepare({ videoId, duration: videoDuration, signal: controller.signal, onUpgrade: accept }).then(accept).catch(() => {});
      }, 500);
    }

    function preloadFrames() {
      if (preloadedFrames || !timelinePrefix) return;
      preloadedFrames = true;
      if (global.ArchivebatePerf && typeof global.ArchivebatePerf.prefetchUrls === 'function') {
        global.ArchivebatePerf.prefetchUrls(Array.from({ length: Math.min(timelineCount, 40) }, (_, i) => `${timelinePrefix}${i + 1}.jpg`), { concurrency: 2, signal: state.viewController?.signal });
      }
    }

    function showCwFrame(pos) {
      if (!hoverFrame || !timelinePrefix) return;
      const frameIdx = Math.min(timelineCount, Math.max(1, Math.round(pos * (timelineCount - 1)) + 1));
      hoverFrame.src = `${timelinePrefix}${frameIdx}.jpg`;
      hoverFrame.style.display = 'block';
    }

    function showLocalStoryboardFrame(pos) {
      if (!cardStoryboard?.sprite_url) return false;
      if (hoverVideo) hoverVideo.style.opacity = '0';
      const YouTubeStoryboard = global.ArchivebateYouTubeStoryboard;
      return YouTubeStoryboard && typeof YouTubeStoryboard.applyFrame === 'function'
        ? YouTubeStoryboard.applyFrame(cardSprite, cardStoryboard, pos)
        : false;
    }

    function startVideoPreview() {
      if (!hoverVideo) return;
      if (hoverVideo.src || cardStoryboard) return;

      const generation = mediaGeneration;
      const previewSrc = v.preview_video || (v.id ? `/api/video/stream?id=${encodeURIComponent(v.id)}` : null);
      if (previewSrc) {
        if (activeHoverVideo && activeHoverVideo !== hoverVideo) {
          try {
            activeHoverVideo.pause();
            activeHoverVideo.removeAttribute('src');
            activeHoverVideo.load();
          } catch (_) {}
        }
        activeHoverVideo = hoverVideo;

        hoverVideo.src = previewSrc;
        hoverVideo.preload = 'auto';
        hoverVideo.muted = true;
        hoverVideo.playsInline = true;
        hoverVideo.onplaying = () => {
          if (generation === mediaGeneration && isHovered) {
            hoverVideo.style.opacity = '1';
            hoverVideo.style.display = 'block';
          }
        };
        hoverVideo.oncanplay = () => {
          if (generation === mediaGeneration && isHovered && pendingPos === null) {
            hoverVideo.style.opacity = '1';
            hoverVideo.style.display = 'block';
          }
        };
        hoverVideo.load();
        hoverVideo.play().catch(() => {});
      }
    }

    function seekHoverVideo(pos) {
      if (!hoverVideo || !hoverVideo.src) return;
      try {
        const pDur = hoverVideo.duration || 3;
        hoverVideo.currentTime = Math.max(0, Math.min(pDur, pos * pDur));
        hoverVideo.style.opacity = '1';
        hoverVideo.style.display = 'block';
      } catch (e) {}
    }

    function doSeek(pos) {
      const totalDuration = durationSec || 0;
      if (scrubTooltip && totalDuration > 0) {
        scrubTooltip.innerText = cardStoryboard ? `≈ ${formatPlayerTime(cardStoryboard.times[Math.min(cardStoryboard.frame_count - 1, Math.floor(pos * cardStoryboard.frame_count))])}` : 'Podgląd';
        scrubTooltip.style.left = `${pos * 100}%`;
        scrubTooltip.style.display = 'block';
      }

      if (timelinePrefix) {
        showCwFrame(pos);
        return;
      }

      if (showLocalStoryboardFrame(pos)) {
        return;
      }

      pendingPos = pos;
      if (hoverVideo && hoverVideo.src) {
        seekHoverVideo(pos);
      }
    }

    if (hoverVideo) {
      hoverVideo.addEventListener('error', () => {
        hoverVideo.style.display = 'none';
      });
    }

    let hoverPreviewTimer = null;

    function scheduleVideoPreview() {
      if (timelinePrefix) return;
      clearTimeout(hoverPreviewTimer);
      hoverPreviewTimer = setTimeout(() => {
        if (isHovered) {
          prefetchVideoDetails(v, { source: v.source || (isCamwhores ? 'camwhores' : 'archivebate') });
          startVideoPreview();
          if (pendingPos !== null) {
            seekHoverVideo(pendingPos);
          }
        }
      }, 250);
    }

    thumbWrapper.addEventListener('mouseenter', (e) => {
      if (isLocalScope()) return;
      isHovered = true;
      if (timelinePrefix) preloadFrames();
      else {
        loadCachedCardStoryboard();
      }
      scheduleVideoPreview();

      const rect = thumbWrapper.getBoundingClientRect();
      const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      if (scrubProgress) scrubProgress.style.width = `${pos * 100}%`;
      if (scrubThumb) scrubThumb.style.left = `${pos * 100}%`;
      doSeek(pos);
    });

    thumbWrapper.addEventListener('mousemove', (e) => {
      if (isLocalScope()) return;
      isHovered = true;
      const rect = thumbWrapper.getBoundingClientRect();
      const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      if (scrubProgress) scrubProgress.style.width = `${pos * 100}%`;
      if (scrubThumb) scrubThumb.style.left = `${pos * 100}%`;
      doSeek(pos);
    });

    thumbWrapper.addEventListener('mouseleave', () => {
      cardBoardController?.abort();
      cardBoardController = null;
      clearTimeout(storyboardWarmTimer);
      cardSprite.style.display = 'none';
      isHovered = false;
      pendingPos = null;
      clearTimeout(hoverPreviewTimer);
      clearTimeout(hoverSeekTimer);
      clearTimeout(storyboardWarmTimer);
      if (activeHoverVideo === hoverVideo) {
        activeHoverVideo = null;
      }
      if (hoverVideo) {
        hoverVideo.pause();
        hoverVideo.currentTime = 0;
        hoverVideo.style.opacity = '0';
        hoverVideo.removeAttribute('src');
        hoverVideo.load();
      }
      if (hoverFrame) hoverFrame.style.display = 'none';
      if (scrubProgress) scrubProgress.style.width = '0%';
      if (scrubThumb) scrubThumb.style.left = '0%';
      if (scrubTooltip) scrubTooltip.style.display = 'none';
    });

    // Otwieranie lewym przyciskiem myszy w oknie modalnym
    thumbWrapper.addEventListener('pointerdown', (e) => {
      if (e.button === 0) clearTimeout(hoverPreviewTimer);
    });
    thumbWrapper.addEventListener('click', (e) => {
      if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
        if (e.target.closest?.('button')) return;
        e.preventDefault();
        e.stopPropagation();
        const activeVideo = card._videoData || v;
        const activeIdx = card._cardIndex !== undefined ? card._cardIndex : idx;
        state.lastClickedGridVideo = activeVideo;
        state.lastClickedGridIndex = activeIdx;
        openVideoModal(activeVideo);
      }
    });

    const playBtn = card.querySelector('.play-btn');
    if (playBtn) {
      playBtn.addEventListener('click', (e) => {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        e.stopPropagation();
        const activeVideo = card._videoData || v;
        const activeIdx = card._cardIndex !== undefined ? card._cardIndex : idx;
        state.lastClickedGridVideo = activeVideo;
        state.lastClickedGridIndex = activeIdx;
        openVideoModal(activeVideo);
      });
    }

    const modelLink = card.querySelector('.model-profile-link');
    if (modelLink) {
      modelLink.title = `Zobacz profil ${v.username} (LPM) lub otwórz w nowej karcie (Kółko myszy)`;
      modelLink.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        loadModelVideos(v.username, 1);
      });
      modelLink.addEventListener('auxclick', (e) => {
        if (e.button === 1) {
          e.preventDefault();
          e.stopPropagation();
          if (global.tabManager && typeof global.tabManager.openTab === 'function') {
            global.tabManager.openTab({
              title: v.username,
              icon: 'fa-solid fa-circle-user',
              type: 'model',
              username: v.username,
              inBackground: true
            });
          }
        }
      });
      modelLink.addEventListener('mousedown', (e) => {
        if (e.button === 1) e.preventDefault();
      });
    }

    const profileBtn = card.querySelector('.profile-btn');
    if (profileBtn) {
      profileBtn.title = `Zobacz nagrania modelki ${v.username} (LPM) lub otwórz w nowej karcie (Kółko myszy)`;
      profileBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        loadModelVideos(v.username, 1);
      });
      profileBtn.addEventListener('auxclick', (e) => {
        if (e.button === 1) {
          e.preventDefault();
          e.stopPropagation();
          if (global.tabManager && typeof global.tabManager.openTab === 'function') {
            global.tabManager.openTab({
              title: v.username,
              icon: 'fa-solid fa-circle-user',
              type: 'model',
              username: v.username,
              inBackground: true
            });
          }
        }
      });
      profileBtn.addEventListener('mousedown', (e) => {
        if (e.button === 1) e.preventDefault();
      });
    }

    const blockBtn = card.querySelector('.block-model-btn');
    if (blockBtn) {
      blockBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        blockModel(v.username);
      });
    }

    // Obsługa rozwijanej szuflady z filmami danej grupy (autora)
    if (hasGroupDrawer) {
      const groupBadge = card.querySelector('.badge-group-count');
      const expandBtn = card.querySelector('.expand-group-btn');
      const drawer = card.querySelector('.grouped-videos-drawer');

      const appendLazyGroupRow = (gv, gIdx) => {
        const row = document.createElement('a');
        row.className = 'grouped-item-row';
        row.href = watchUrl(gv);
        row.title = `${gv.title || gv.username} (${gv.duration || 'N/A'}) - LPM: odtwórz, Kółko myszy: nowa karta`;
        const gThumb = thumbnailUrlForVideo(gv) || gv.poster_direct || '';
        const gBackup = gv.poster_direct || '';
        const safeGThumb = safeUrl(gThumb);
        const safeGBackup = safeUrl(gBackup);
        const safeGUser = escapeHtml(gv.username || 'Model');
        const safeGDate = escapeHtml(gv.date || 'Wideo');
        const safeGDuration = escapeHtml(gv.duration || '');
        const safeGPlatform = escapeHtml(gv.platform || (gv.source === 'camwhores' ? 'Camwhores' : 'Archive'));
        const safeGViews = escapeHtml(gv.views || '');
        row.innerHTML = `
          <img class="grouped-item-thumb" src="${safeGThumb}" data-fallback="${safeGBackup}" alt="${safeGUser}" loading="lazy">
          <div class="grouped-item-info">
            <div class="grouped-item-title">${escapeHtml(gIdx + 1)}. ${safeGDate} • ${safeGDuration}</div>
            <div class="grouped-item-meta">
              <span>${safeGPlatform}</span>
              ${gv.views ? `<span>• <i class="fa-solid fa-eye"></i> ${safeGViews}</span>` : ''}
            </div>
          </div>
          <div class="grouped-item-play-btn"><i class="fa-solid fa-play"></i></div>
        `;
        const groupedImage = row.querySelector('.grouped-item-thumb');
        groupedImage?.addEventListener('error', () => {
          if (groupedImage.dataset.retried || !groupedImage.dataset.fallback) {
            groupedImage.style.opacity = '0.3';
            return;
          }
          groupedImage.dataset.retried = '1';
          groupedImage.src = groupedImage.dataset.fallback;
        });
        row.addEventListener('click', (ev) => {
          if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
          ev.stopPropagation();
          ev.preventDefault();
          state.lastClickedGridVideo = v;
          state.lastClickedGridIndex = idx;
          openVideoModal(gv);
        });
        drawer.appendChild(row);
      };

      const loadLazyGroupMembers = () => {
        if (!v.group_members_url || !global.ArchivebateAPI?.getJSON) return;
        card._groupMembersController?.abort?.();
        const controller = new AbortController();
        card._groupMembersController = controller;
        const loadGeneration = (card._groupMembersGeneration || 0) + 1;
        card._groupMembersGeneration = loadGeneration;
        const viewSignal = state.viewController?.signal;
        const cancelForView = () => controller.abort();
        if (viewSignal?.aborted) controller.abort();
        else viewSignal?.addEventListener('abort', cancelForView, { once: true });
        controller.signal.addEventListener('abort', () => viewSignal?.removeEventListener('abort', cancelForView), { once: true });
        const status = document.createElement('div');
        status.className = 'grouped-members-status';
        status.textContent = 'Ładowanie nagrań grupy…';
        drawer.appendChild(status);

        const loadPage = async (page) => {
          const target = new URL(v.group_members_url, window.location.href);
          target.searchParams.set('page', String(page));
          target.searchParams.set('per_page', '50');
          if (v.revision) target.searchParams.set('revision', String(v.revision));
          if (!target.searchParams.has('source')) target.searchParams.set('source', state.sourceFilter || v.source || 'all');
          target.searchParams.set('author_filter', state.authorFilter || 'all');
          if (Number(state.preferencesVersion) > 0) target.searchParams.set('preferences_version', String(state.preferencesVersion));
          const data = await getSharedGroupMembers(`${target.pathname}${target.search}`, controller.signal);
          if (loadGeneration !== card._groupMembersGeneration) return;
          if (!data || !Array.isArray(data.items)) throw new Error('Nieprawidłowa odpowiedź grupy.');
          status.remove();
          const knownUnavailable = global.ArchivebateVideoPrefetch?.isKnownUnavailableVideo;
          const identityOf = global.ArchivebateVideoPrefetch?.videoIdentity;
          if (typeof identityOf === 'function') {
            const members = Array.isArray(v.grouped_videos) ? v.grouped_videos : [];
            const seenMembers = new Set(members.map(member => identityOf(member)?.key).filter(Boolean));
            for (const member of data.items) {
              const key = identityOf(member)?.key;
              if (key && !seenMembers.has(key)) {
                members.push(member);
                seenMembers.add(key);
              }
            }
            v.grouped_videos = members;
            v._groupedVideos = members;
            groupedVideosList = members;
            global.ArchivebateVideoPrefetch.reconcileKnownUnavailableGroup?.(v);
            if (typeof card._updateCard === 'function') card._updateCard(v, card._cardIndex, { refreshDrawer: false });
            const currentCount = Math.max(0, Number(v.group_count || v._groupCount || 0));
            groupCount = currentCount;
            const badge = card.querySelector('.badge-group-count');
            if (badge) badge.innerHTML = `<i class="fa-solid fa-layer-group"></i> ${escapeHtml(currentCount)} ${currentCount === 1 ? 'film' : 'filmów'}`;
            const pill = card.querySelector('.author-group-pill');
            if (pill) pill.innerHTML = `<i class="fa-solid fa-clone"></i> Grupa (${escapeHtml(currentCount)})`;
            const profileButton = card.querySelector('.profile-btn');
            if (profileButton) profileButton.innerHTML = `<i class="fa-solid fa-folder-open"></i> ${escapeHtml(currentCount)} ${currentCount === 1 ? 'film' : 'filmów'}`;
            const headerTitle = drawer.querySelector('.grouped-drawer-header span');
            if (headerTitle) headerTitle.innerHTML = `<i class="fa-solid fa-layer-group"></i> ${escapeHtml(currentCount)} filmów twórcy (${escapeHtml(v.username || 'Model')})`;
          }
          const visibleItems = typeof knownUnavailable === 'function'
            ? data.items.filter(member => !knownUnavailable(member))
            : data.items;
          data.items.forEach((member, offset) => {
            if (!visibleItems.includes(member)) return;
            appendLazyGroupRow(member, ((page - 1) * 50) + offset);
          });
          drawer.querySelector('.grouped-load-more')?.remove();
          if (data.has_more) {
            const more = document.createElement('button');
            more.type = 'button';
            more.className = 'grouped-load-more btn-card';
            more.textContent = 'Pokaż następne nagrania';
            more.addEventListener('click', (event) => {
              event.stopPropagation();
              more.disabled = true;
              loadPage(Number(data.page || page) + 1).catch(showError);
            });
            drawer.appendChild(more);
          }
        };
        const showError = (error) => {
          if (loadGeneration !== card._groupMembersGeneration) return;
          if (controller.signal.aborted) return;
          status.textContent = error?.message || 'Nie udało się pobrać nagrań grupy.';
          status.classList.add('error');
          const retry = document.createElement('button');
          retry.type = 'button';
          retry.className = 'grouped-load-more btn-card';
          retry.textContent = 'Ponów';
          retry.addEventListener('click', event => {
            event.stopPropagation();
            drawer.innerHTML = '';
            loadLazyGroupMembers();
          }, { once: true });
          drawer.appendChild(retry);
        };
        loadPage(1).catch(showError);
      };

      const toggleDrawer = (e) => {
        if (e) {
          e.stopPropagation();
          e.preventDefault();
        }
        if (!drawer) return;
        const isVisible = drawer.style.display !== 'none';
        if (isVisible) {
          drawer.style.display = 'none';
          if (card._groupMembersController && !card._groupMembersController.signal.aborted) {
            card._groupMembersGeneration = (card._groupMembersGeneration || 0) + 1;
            card._groupMembersController.abort();
            if (drawer.querySelector('.grouped-members-status')) drawer.innerHTML = '';
          }
          if (expandBtn) expandBtn.innerHTML = '<i class="fa-solid fa-chevron-down"></i>';
        } else {
          drawer.style.display = 'flex';
          if (expandBtn) expandBtn.innerHTML = '<i class="fa-solid fa-chevron-up"></i>';
          if (drawer.children.length === 0) {
            const header = document.createElement('div');
            header.className = 'grouped-drawer-header';
            header.innerHTML = `<span><i class="fa-solid fa-layer-group"></i> ${escapeHtml(groupCount)} filmów twórcy (${safeUsername})</span><span style="font-size: 10px; opacity: 0.7;">LPM: odtwórz | Kółko: nowa karta</span>`;
            drawer.appendChild(header);

            if (v.group_members_lazy && v.group_members_url) {
              loadLazyGroupMembers();
            } else {
              const isUnavailable = global.ArchivebateVideoPrefetch?.isKnownUnavailableVideo;
              const visibleMembers = typeof isUnavailable === 'function'
                ? groupedVideosList.filter(member => !isUnavailable(member))
                : groupedVideosList;
              visibleMembers.forEach((gv, gIdx) => appendLazyGroupRow(gv, gIdx));
            }
          }
        }
      };

      card._refreshGroupDrawer = () => {
        if (!drawer) return;
        const wasOpen = drawer.style.display !== 'none';
        card._groupMembersGeneration = (card._groupMembersGeneration || 0) + 1;
        card._groupMembersController?.abort?.();
        drawer.innerHTML = '';
        if (!wasOpen) return;
        const header = document.createElement('div');
        header.className = 'grouped-drawer-header';
        header.innerHTML = `<span><i class="fa-solid fa-layer-group"></i> ${escapeHtml(groupCount)} filmów twórcy (${escapeHtml(v.username || 'Model')})</span><span style="font-size: 10px; opacity: 0.7;">LPM: odtwórz | Kółko: nowa karta</span>`;
        drawer.appendChild(header);
        if (v.group_members_lazy && v.group_members_url) loadLazyGroupMembers();
        else {
          const isUnavailable = global.ArchivebateVideoPrefetch?.isKnownUnavailableVideo;
          const visibleMembers = typeof isUnavailable === 'function'
            ? groupedVideosList.filter(member => !isUnavailable(member))
            : groupedVideosList;
          visibleMembers.forEach((member, memberIndex) => appendLazyGroupRow(member, memberIndex));
        }
      };

      if (groupBadge) groupBadge.addEventListener('click', toggleDrawer);
      if (expandBtn) expandBtn.addEventListener('click', toggleDrawer);
    }

    let cardPrefetchTimer = null;
    card.addEventListener('pointerenter', () => {
      if (isLocalScope()) return;
      clearTimeout(cardPrefetchTimer);
      cardPrefetchTimer = setTimeout(() => {
        const activeVideo = card._videoData || v;
        prefetchVideoDetails(activeVideo, { source: activeVideo.source || (String(activeVideo.id).startsWith('cw_') ? 'camwhores' : 'archivebate') });
      }, 250);
    }, { passive: true });
    card.addEventListener('pointerleave', () => {
      clearTimeout(cardPrefetchTimer);
    }, { passive: true });
    card.addEventListener('focusin', () => {
      if (isLocalScope()) return;
      const activeVideo = card._videoData || v;
      prefetchVideoDetails(activeVideo, { source: activeVideo.source || (String(activeVideo.id).startsWith('cw_') ? 'camwhores' : 'archivebate') });
    }, { passive: true, once: true });

    // In-place aktualizacja karty dla reconcilePage (zero-flicker Pakiet A)
    card._updateCard = function(newV, newIdx, updateOptions = {}) {
      if (!newV || typeof newV !== 'object') return;
      if (newIdx !== undefined) {
        card._cardIndex = newIdx;
        idx = newIdx;
      }
      const priorMediaSignature = card._mediaSignature;
      const priorGroupSignature = card._groupSignature;
      let mediaChanged = false;
      Object.assign(v, newV);
      card._videoData = v;
      const nextIsCamwhores = v.source === 'camwhores' || String(v.id).startsWith('cw_') || (v.platform && String(v.platform).toLowerCase().includes('camwhores'));
      const nextMediaSignature = `${v.source || (nextIsCamwhores ? 'camwhores' : 'archivebate')}:${v.id || ''}:${v.timeline_prefix || ''}:${v.preview_video || ''}:${v.poster || ''}:${v.duration || ''}`;
      if (priorMediaSignature !== nextMediaSignature) {
        mediaChanged = true;
        mediaGeneration += 1;
        cardBoardController?.abort?.();
        cardBoardController = null;
        cardStoryboard = null;
        clearTimeout(storyboardWarmTimer);
        clearTimeout(hoverPreviewTimer);
        clearTimeout(hoverSeekTimer);
        if (activeHoverVideo === hoverVideo) activeHoverVideo = null;
        if (hoverVideo) {
          hoverVideo.onplaying = null;
          hoverVideo.oncanplay = null;
          hoverVideo.pause();
          hoverVideo.removeAttribute('src');
          hoverVideo.load();
          hoverVideo.style.opacity = '0';
          hoverVideo.style.display = 'none';
        }
        if (hoverFrame) {
          hoverFrame.removeAttribute('src');
          hoverFrame.style.display = 'none';
        }
        cardSprite.style.display = 'none';
        cardSprite.style.backgroundImage = '';
        timelinePrefix = getTimelinePrefix(v);
        timelineCount = Number(v.timeline_count) || 15;
        durationSec = parseDurationToSeconds(v.duration);
        pendingPos = null;
        preloadedFrames = false;
        card._mediaSignature = nextMediaSignature;
      }
      isCamwhores = nextIsCamwhores;
      card.dataset.videoId = String(v.id || '');
      card.querySelectorAll('.video-open-link, .play-btn').forEach(link => { link.href = watchUrl(v); });
      card.dataset.source = isCamwhores ? 'camwhores' : 'archivebate';
      card.dataset.username = String(v.username || '').toLowerCase().trim();

      // Ulubione
      const isNowFav = !!newV.is_favorite;
      const isNowFavAuthor = (typeof isFavoriteAuthor === 'function' && isFavoriteAuthor(newV.username)) || Boolean(newV.has_favorite_video);
      const isNowFavCard = isNowFav || isNowFavAuthor;
      card.classList.toggle('is-favorite-card', isNowFavCard);
      const fb = card.querySelector('.card-fav-btn');
      if (fb) {
        fb.classList.toggle('active', isNowFav);
        fb.setAttribute('aria-label', isNowFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych');
        fb.setAttribute('aria-pressed', isNowFav ? 'true' : 'false');
        fb.title = isNowFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych';
        const fIcon = fb.querySelector('i');
        if (fIcon) {
          fIcon.className = `${isNowFav ? 'fa-solid' : 'fa-regular'} fa-heart`;
        }
      }

      // Profil i gwiazdka polubionej modelki
      const mLink = card.querySelector('.model-profile-link');
      if (mLink) {
        mLink.classList.toggle('is-favorite-author', isNowFavAuthor);
        const star = mLink.querySelector('.fav-author-star');
        if (isNowFavAuthor && !star) {
          const sEl = document.createElement('i');
          sEl.className = 'fa-solid fa-star fav-author-star';
          sEl.title = 'Masz film tej modelki w ulubionych';
          mLink.appendChild(sEl);
        } else if (!isNowFavAuthor && star) {
          star.remove();
        }
      }

      // Grupowanie
      const isNowGrouped = Boolean(newV.is_grouped || newV._isGrouped || newV.group_members_lazy || (newV.group_count && newV.group_count > 1) || (newV._groupCount && newV._groupCount > 1));
      const nowGroupCount = newV.group_count ?? newV._groupCount ?? (newV.grouped_videos ? newV.grouped_videos.length : (newV._groupedVideos ? newV._groupedVideos.length : 1));
      isGrouped = isNowGrouped;
      groupCount = nowGroupCount;
      groupedVideosList = newV.grouped_videos || newV._groupedVideos || groupedVideosList;
      hasGroupDrawer = isNowGrouped && (nowGroupCount > 0 || newV.group_members_lazy || newV.group_members_url);
      const memberSignature = (groupedVideosList || []).map(member => `${member.source || ''}:${member.id || ''}`).join(',');
      const groupSignature = `${hasGroupDrawer}:${nowGroupCount}:${newV.group_members_url || ''}:${newV.revision || ''}:${memberSignature}`;
      card._groupSignature = groupSignature;
      card.classList.toggle('is-grouped-card', isNowGrouped && nowGroupCount > 1);
      const gBadge = card.querySelector('.badge-group-count');
      const safeNowGroupCount = escapeHtml(nowGroupCount);
      if (gBadge) {
        gBadge.hidden = !hasGroupDrawer;
        gBadge.innerHTML = `<i class="fa-solid fa-layer-group"></i> ${safeNowGroupCount} ${nowGroupCount === 1 ? 'film' : 'filmów'}`;
        gBadge.title = `Ten autor ma ${nowGroupCount} ${nowGroupCount === 1 ? 'film' : 'filmów'} na liście. Kliknij, aby rozwinąć listę!`;
      }
      const aPill = card.querySelector('.author-group-pill');
      if (aPill) {
        aPill.hidden = !hasGroupDrawer;
        aPill.innerHTML = `<i class="fa-solid fa-clone"></i> Grupa (${safeNowGroupCount})`;
      }
      const pBtn = card.querySelector('.profile-btn');
      if (pBtn) {
        pBtn.innerHTML = `<i class="fa-solid fa-folder${hasGroupDrawer ? '-open' : ''}"></i> ${hasGroupDrawer ? `${safeNowGroupCount} ${nowGroupCount === 1 ? 'film' : 'filmów'}` : 'Filmy'}`;
      }
      const platformBadge = card.querySelector('.badge-platform');
      if (platformBadge) {
        platformBadge.classList.toggle('badge-camwhores', isCamwhores);
        platformBadge.innerHTML = isCamwhores ? '<i class="fa-solid fa-tv"></i> Camwhores' : escapeHtml(v.platform || 'Archive');
      }
      mLink?.setAttribute('data-username', String(v.username || ''));
      pBtn?.setAttribute('data-username', String(v.username || ''));
      card.querySelector('.block-model-btn')?.setAttribute('data-username', String(v.username || ''));

      // Wyświetlenia, długość, data
      const vwBadge = card.querySelector('.badge-views');
      if (vwBadge && newV.views && vwBadge.innerText !== String(newV.views)) {
        vwBadge.innerHTML = `<i class="fa-solid fa-eye"></i> ${escapeHtml(newV.views)}`;
      }
      const dBadge = card.querySelector('.badge-duration');
      if (newV.duration && newV.duration !== 'N/A') {
        if (dBadge) dBadge.innerText = newV.duration;
        else {
          const nextDuration = document.createElement('span');
          nextDuration.className = 'badge-duration';
          nextDuration.innerText = newV.duration;
          thumbWrapper.appendChild(nextDuration);
        }
      } else if (dBadge) {
        dBadge.remove();
      }
      const dtBadge = card.querySelector('.card-date-badge');
      if (dtBadge && newV.date && dtBadge.dataset.origDate !== newV.date) {
        dtBadge.dataset.origDate = newV.date;
        dtBadge.setAttribute('aria-label', `Ustaw checkpoint dla filmu z datą ${escapeHtml(newV.date)}`);
        dtBadge.innerHTML = `<i class="fa-regular fa-calendar-days"></i> ${escapeHtml(newV.date)}`;
      }

      // Miniatura: stabilność img.src
      const dirPoster = isLocalScope() ? '' : (newV.poster_direct || (newV.poster ? newV.poster.replace(/\.mp4$/, '.jpg') : ''));
      const canPoster = (typeof thumbnailUrlForVideo === 'function' ? thumbnailUrlForVideo(newV) : '') || dirPoster;
      const targetPoster = canPoster || dirPoster;
      const tImg = card.querySelector('.thumbnail-img');
      if (tImg) {
        tImg.dataset.fallback = safeUrlValue(dirPoster);
        tImg.alt = String(newV.username || 'Model');
      }
      const safeTargetPoster = safeUrlValue(targetPoster);
      if (tImg && safeTargetPoster) {
        const activeSrc = tImg.getAttribute('src');
        const lazySrc = tImg.getAttribute('data-src');
        if (safeTargetPoster !== thumbnailSource || (!activeSrc && !lazySrc)) {
          thumbnailRecoveryController?.abort();
          thumbnailRecoveryController = null;
          delete tImg.dataset.recovered;
          thumbnailRetryCount = 0;
          clearThumbnailError();
          tImg.removeAttribute('data-retried');
          if (lazySrc) tImg.setAttribute('data-src', safeTargetPoster);
          else tImg.src = safeTargetPoster;
        } else if (activeSrc && activeSrc !== safeTargetPoster && !tImg.dataset.retried && !tImg.dataset.recovered) {
          tImg.src = safeTargetPoster;
        } else if (!activeSrc && lazySrc && lazySrc !== safeTargetPoster) {
          tImg.setAttribute('data-src', safeTargetPoster);
        }
        thumbnailSource = safeTargetPoster;
      }
      if (priorGroupSignature !== groupSignature && updateOptions.refreshDrawer !== false) card._refreshGroupDrawer?.();
      if (mediaChanged && isHovered) {
        if (timelinePrefix) preloadFrames();
        else loadCachedCardStoryboard();
        scheduleVideoPreview();
        doSeek(0);
      }
    };

    global.ArchivebateVideoPrefetch?.observeAvailability?.(card);
    return card;
  }

  const ArchivebateVideoCard = {
    createVideoCard,
    create: createVideoCard,
    getGroupMembers: getSharedGroupMembers
  };

  global.ArchivebateVideoCard = ArchivebateVideoCard;
  if (!global.createVideoCard) {
    global.createVideoCard = createVideoCard;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoCard;
  }
})(typeof window !== 'undefined' ? window : globalThis);
