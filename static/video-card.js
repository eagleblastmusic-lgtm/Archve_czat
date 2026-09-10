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

  function prefetchVideoDetails(videoId, active = false) {
    if (global.ArchivebateVideoPrefetch && typeof global.ArchivebateVideoPrefetch.prefetchVideoDetails === 'function') {
      return global.ArchivebateVideoPrefetch.prefetchVideoDetails(videoId, active);
    }
    if (typeof global.prefetchVideoDetails === 'function') {
      return global.prefetchVideoDetails(videoId, active);
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

  function createVideoCard(v, idx) {
    const isCamwhores = v.source === 'camwhores' || String(v.id).startsWith('cw_') || (v.platform && v.platform.toLowerCase().includes('camwhores'));
    const isFav = !!v.is_favorite;
    const isFavAuthor = isFavoriteAuthor(v.username) || v.has_favorite_video;
    const isFavCard = isFav || isFavAuthor;

    const isGrouped = Boolean(v.is_grouped || v._isGrouped || (v.group_count && v.group_count > 1) || (v._groupCount && v._groupCount > 1));
    const groupCount = v.group_count || v._groupCount || (v.grouped_videos ? v.grouped_videos.length : (v._groupedVideos ? v._groupedVideos.length : 1));
    const groupedVideosList = v.grouped_videos || v._groupedVideos || [];

    const card = document.createElement('div');
    card.className = `video-card ${isFavCard ? 'is-favorite-card' : ''} ${isGrouped && groupCount > 1 ? 'is-grouped-card' : ''}`;
    card.dataset.videoId = String(v.id);
    card.dataset.username = String(v.username || '').toLowerCase().trim();
    card.dataset.source = isCamwhores ? 'camwhores' : 'archivebate';
    card._videoData = v;
    card._cardIndex = idx;

    const directPoster = v.poster_direct || (v.poster ? v.poster.replace(/\.mp4$/, '.jpg') : '');
    const rawPoster = directPoster || (v.poster ? v.poster.replace(/\.mp4$/, '.jpg') : '');
    const canonicalPoster = thumbnailUrlForVideo(v) || directPoster;
    const displayPoster = canonicalPoster;
    const backupPoster = directPoster;

    const eagerThumb = idx < 12;
    const thumbLoadAttrs = eagerThumb
      ? `src="${displayPoster}" loading="eager" fetchpriority="${idx < 6 ? 'high' : 'auto'}"`
      : `data-src="${displayPoster}" loading="lazy" fetchpriority="low"`;

    card.innerHTML = `
      <div class="thumbnail-wrapper">
        <img class="thumbnail-img" ${thumbLoadAttrs} data-fallback="${backupPoster || ''}" alt="${v.username}" decoding="async">
        ${isCamwhores ? `<img class="hover-preview-frame" style="display: none; position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; z-index: 2; pointer-events: none;" alt="Preview">` : ''}
        <video class="hover-preview-video" muted playsinline preload="none"></video>
        
        <!-- Oś czasu do przeglądania klatek w miniaturce kursorem (czas przybliżony) -->
        <div class="card-scrub-bar">
          <div class="card-scrub-tooltip">00:00</div>
          <div class="card-scrub-progress"></div>
          <div class="card-scrub-thumb"></div>
        </div>

        <span class="badge-platform ${isCamwhores ? 'badge-camwhores' : ''}">${isCamwhores ? '<i class="fa-solid fa-tv"></i> Camwhores' : (v.platform || 'Archive')}</span>
        ${v.views ? `<span class="badge-views"><i class="fa-solid fa-eye"></i> ${v.views}</span>` : ''}
        ${v.duration && v.duration !== 'N/A' ? `<span class="badge-duration">${v.duration}</span>` : ''}
        ${isGrouped && groupCount > 1 ? `
          <span class="badge-group-count" title="Ten autor ma ${groupCount} filmów na liście. Kliknij, aby rozwinąć listę!">
            <i class="fa-solid fa-layer-group"></i> ${groupCount} filmów
          </span>
        ` : ''}
        
        <!-- Szybki przycisk ulubione -->
        <button class="card-fav-btn ${isFav ? 'active' : ''}" title="${isFav ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'}">
          <i class="${isFav ? 'fa-solid' : 'fa-regular'} fa-heart"></i>
        </button>
      </div>

      <div class="card-details">
        <div class="card-header-info">
          <a href="#" class="model-profile-link ${(isFavoriteAuthor(v.username) || v.has_favorite_video) ? 'is-favorite-author' : ''}" data-username="${v.username}">
            <i class="fa-solid fa-circle-user"></i> ${v.username}${(isFavoriteAuthor(v.username) || v.has_favorite_video) ? '<i class="fa-solid fa-star fav-author-star" title="Masz film tej modelki w ulubionych"></i>' : ''}
          </a>
          ${isGrouped && groupCount > 1 ? `<span class="author-group-pill" title="Zgrupowano ${groupCount} nagrań tego twórcy"><i class="fa-solid fa-clone"></i> Grupa (${groupCount})</span>` : ''}
          <span class="card-date-badge" data-video-id="${v.id}" title="Kliknij na datę, aby ustawić punkt kontrolny (checkpoint)"><i class="fa-regular fa-calendar-days"></i> ${v.date || 'Niedawno'}</span>
        </div>

        <div class="card-tags-row">
          ${(v.tags || []).slice(0, 3).map(t => `<span class="card-tag-badge" data-tag="${t.toLowerCase()}">#${t}</span>`).join('')}
        </div>

        <div class="card-actions-row">
          <button class="btn-card primary play-btn">
            <i class="fa-solid fa-play"></i> Odtwórz
          </button>
          <button class="btn-card profile-btn" data-username="${v.username}" title="Zobacz profil i nagrania modelki ${v.username}">
            <i class="fa-solid fa-folder${isGrouped && groupCount > 1 ? '-open' : ''}"></i> ${isGrouped && groupCount > 1 ? `${groupCount} filmów` : 'Filmy'}
          </button>
          ${isGrouped && groupCount > 1 ? `
            <button class="btn-card expand-group-btn" title="Rozwiń podgląd wszystkich ${groupCount} filmów tej grupy">
              <i class="fa-solid fa-chevron-down"></i>
            </button>
          ` : ''}
          <button class="btn-card danger block-model-btn" data-username="${v.username}" title="Zablokuj modelkę: usuń ten profil z katalogu programu i ukryj wszystkie jej nagrania">
            <i class="fa-solid fa-ban"></i>
          </button>
        </div>
        ${isGrouped && groupCount > 1 ? `
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
    if (cardThumbImg) {
      cardThumbImg.addEventListener('error', () => {
        const backup = cardThumbImg.dataset.fallback;
        if (!cardThumbImg.dataset.retried && backup) {
          cardThumbImg.dataset.retried = '1';
          cardThumbImg.src = backup;
        }
      });
      if (!eagerThumb) armLazyThumbnail(cardThumbImg);
    }

    const thumbWrapper = card.querySelector('.thumbnail-wrapper');
    const hoverVideo = card.querySelector('.hover-preview-video');
    const hoverFrame = card.querySelector('.hover-preview-frame');
    const cardSprite = document.createElement('div');
    cardSprite.style.cssText = 'display:none;position:absolute;width:160px;height:90px;overflow:hidden;left:50%;top:50%;transform:translate(-50%,-50%) scale(1.5);pointer-events:none;z-index:3';
    thumbWrapper.appendChild(cardSprite);
    let cardBoardController = null;
    const scrubProgress = card.querySelector('.card-scrub-progress');
    const scrubThumb = card.querySelector('.card-scrub-thumb');
    const scrubTooltip = card.querySelector('.card-scrub-tooltip');

    const timelinePrefix = v.timeline_prefix || (rawPoster && rawPoster.includes('/180x135/') ? rawPoster.substring(0, rawPoster.indexOf('/180x135/') + 9) : null);
    const timelineCount = v.timeline_count || 15;
    const durationSec = parseDurationToSeconds(v.duration);

    let isHovered = false;
    let pendingPos = null;
    let preloadedFrames = false;
    let hoverSeekTimer = null;
    let storyboardWarmTimer = null;
    let cardStoryboard = null;

    function loadCachedCardStoryboard() {
      if (timelinePrefix || !v.id || !durationSec || cardBoardController) return;
      const YouTubeStoryboard = global.ArchivebateYouTubeStoryboard;
      if (!YouTubeStoryboard || typeof YouTubeStoryboard.prepare !== 'function') return;

      const controller = cardBoardController = new AbortController();
      const viewSignal = state.gridController?.signal || state.viewController?.signal;
      const cancel = () => controller.abort();
      viewSignal?.addEventListener('abort', cancel, { once: true });
      controller.signal.addEventListener('abort', () => viewSignal?.removeEventListener('abort', cancel), { once: true });

      const accept = board => {
        if (controller.signal.aborted || !isHovered) return;
        cardStoryboard = board;
        if (hoverVideo) { hoverVideo.pause(); hoverVideo.removeAttribute('src'); hoverVideo.load(); }
        showLocalStoryboardFrame(pendingPos ?? 0);
      };

      storyboardWarmTimer = setTimeout(() => {
        if (!isHovered || controller.signal.aborted) return;
        YouTubeStoryboard.prepare({ videoId: v.id, duration: durationSec, signal: controller.signal, onUpgrade: accept }).then(accept).catch(() => {});
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
          if (isHovered) {
            hoverVideo.style.opacity = '1';
            hoverVideo.style.display = 'block';
          }
        };
        hoverVideo.oncanplay = () => {
          if (isHovered && pendingPos === null) {
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
          prefetchVideoDetails(v.id);
          startVideoPreview();
          if (pendingPos !== null) {
            seekHoverVideo(pendingPos);
          }
        }
      }, 250);
    }

    thumbWrapper.addEventListener('mouseenter', (e) => {
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
      if (e.button === 0) {
        const activeVideo = card._videoData || v;
        const activeIdx = card._cardIndex !== undefined ? card._cardIndex : idx;
        state.lastClickedGridVideo = activeVideo;
        state.lastClickedGridIndex = activeIdx;
        openVideoModal(activeVideo);
      }
    });

    // Otwieranie kółkiem myszy w nowej karcie
    thumbWrapper.addEventListener('auxclick', (e) => {
      if (e.button === 1) {
        e.preventDefault();
        e.stopPropagation();
        const activeVideo = card._videoData || v;
        if (global.tabManager && typeof global.tabManager.openTab === 'function') {
          global.tabManager.openTab({
            title: activeVideo.username ? `${activeVideo.username} (Wideo)` : 'Odtwarzacz',
            icon: 'fa-solid fa-play',
            type: 'watch',
            video: activeVideo,
            inBackground: true
          });
        }
      }
    });
    thumbWrapper.addEventListener('mousedown', (e) => {
      if (e.button === 1) e.preventDefault();
    });

    const playBtn = card.querySelector('.play-btn');
    if (playBtn) {
      playBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const activeVideo = card._videoData || v;
        const activeIdx = card._cardIndex !== undefined ? card._cardIndex : idx;
        state.lastClickedGridVideo = activeVideo;
        state.lastClickedGridIndex = activeIdx;
        openVideoModal(activeVideo);
      });
      playBtn.addEventListener('auxclick', (e) => {
        if (e.button === 1) {
          e.preventDefault();
          e.stopPropagation();
          const activeVideo = card._videoData || v;
          if (global.tabManager && typeof global.tabManager.openTab === 'function') {
            global.tabManager.openTab({
              title: activeVideo.username ? `${activeVideo.username} (Wideo)` : 'Odtwarzacz',
              icon: 'fa-solid fa-play',
              type: 'watch',
              video: activeVideo,
              inBackground: true
            });
          }
        }
      });
      playBtn.addEventListener('mousedown', (e) => {
        if (e.button === 1) e.preventDefault();
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
    if (isGrouped && groupCount > 1) {
      const groupBadge = card.querySelector('.badge-group-count');
      const expandBtn = card.querySelector('.expand-group-btn');
      const drawer = card.querySelector('.grouped-videos-drawer');

      const toggleDrawer = (e) => {
        if (e) {
          e.stopPropagation();
          e.preventDefault();
        }
        if (!drawer) return;
        const isVisible = drawer.style.display !== 'none';
        if (isVisible) {
          drawer.style.display = 'none';
          if (expandBtn) expandBtn.innerHTML = '<i class="fa-solid fa-chevron-down"></i>';
        } else {
          drawer.style.display = 'flex';
          if (expandBtn) expandBtn.innerHTML = '<i class="fa-solid fa-chevron-up"></i>';
          if (drawer.children.length === 0) {
            const header = document.createElement('div');
            header.className = 'grouped-drawer-header';
            header.innerHTML = `<span><i class="fa-solid fa-layer-group"></i> ${groupCount} filmów twórcy (${v.username})</span><span style="font-size: 10px; opacity: 0.7;">LPM: odtwórz | Kółko: nowa karta</span>`;
            drawer.appendChild(header);

            groupedVideosList.forEach((gv, gIdx) => {
              const row = document.createElement('div');
              row.className = 'grouped-item-row';
              row.title = `${gv.title || gv.username} (${gv.duration || 'N/A'}) - LPM: odtwórz, Kółko myszy: nowa karta`;
              const gThumb = thumbnailUrlForVideo(gv) || gv.poster_direct || '';
              const gBackup = gv.poster_direct || '';
              row.innerHTML = `
                <img class="grouped-item-thumb" src="${gThumb}" alt="${gv.username}" loading="lazy" onerror="if(this.dataset.retried){this.style.opacity=0.3;}else{this.dataset.retried='1';this.src='${gBackup}';}">
                <div class="grouped-item-info">
                  <div class="grouped-item-title">${gIdx + 1}. ${gv.date || 'Wideo'} • ${gv.duration || ''}</div>
                  <div class="grouped-item-meta">
                    <span>${gv.platform || (gv.source === 'camwhores' ? 'Camwhores' : 'Archive')}</span>
                    ${gv.views ? `<span>• <i class="fa-solid fa-eye"></i> ${gv.views}</span>` : ''}
                  </div>
                </div>
                <div class="grouped-item-play-btn"><i class="fa-solid fa-play"></i></div>
              `;

              row.addEventListener('click', (ev) => {
                ev.stopPropagation();
                ev.preventDefault();
                state.lastClickedGridVideo = v;
                state.lastClickedGridIndex = idx;
                openVideoModal(gv);
              });

              row.addEventListener('auxclick', (ev) => {
                if (ev.button === 1) {
                  ev.preventDefault();
                  ev.stopPropagation();
                  if (global.tabManager && typeof global.tabManager.openTab === 'function') {
                    global.tabManager.openTab({
                      title: gv.username ? `${gv.username} (Wideo)` : 'Odtwarzacz',
                      icon: 'fa-solid fa-play',
                      type: 'watch',
                      video: gv,
                      inBackground: true
                    });
                  }
                }
              });

              row.addEventListener('mousedown', (ev) => {
                if (ev.button === 1) ev.preventDefault();
              });

              drawer.appendChild(row);
            });
          }
        }
      };

      if (groupBadge) groupBadge.addEventListener('click', toggleDrawer);
      if (expandBtn) expandBtn.addEventListener('click', toggleDrawer);
    }

    let cardPrefetchTimer = null;
    card.addEventListener('pointerenter', () => {
      clearTimeout(cardPrefetchTimer);
      cardPrefetchTimer = setTimeout(() => {
        prefetchVideoDetails((card._videoData || v).id);
      }, 250);
    }, { passive: true });
    card.addEventListener('pointerleave', () => {
      clearTimeout(cardPrefetchTimer);
    }, { passive: true });
    card.addEventListener('focusin', () => {
      prefetchVideoDetails((card._videoData || v).id);
    }, { passive: true, once: true });

    // In-place aktualizacja karty dla reconcilePage (zero-flicker Pakiet A)
    card._updateCard = function(newV, newIdx) {
      if (!newV || typeof newV !== 'object') return;
      if (newIdx !== undefined) {
        card._cardIndex = newIdx;
        idx = newIdx;
      }
      Object.assign(v, newV);
      card._videoData = v;

      // Ulubione
      const isNowFav = !!newV.is_favorite;
      const isNowFavAuthor = (typeof isFavoriteAuthor === 'function' && isFavoriteAuthor(newV.username)) || Boolean(newV.has_favorite_video);
      const isNowFavCard = isNowFav || isNowFavAuthor;
      card.classList.toggle('is-favorite-card', isNowFavCard);
      const fb = card.querySelector('.card-fav-btn');
      if (fb) {
        fb.classList.toggle('active', isNowFav);
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
      const isNowGrouped = Boolean(newV.is_grouped || newV._isGrouped || (newV.group_count && newV.group_count > 1) || (newV._groupCount && newV._groupCount > 1));
      const nowGroupCount = newV.group_count || newV._groupCount || (newV.grouped_videos ? newV.grouped_videos.length : (newV._groupedVideos ? newV._groupedVideos.length : 1));
      card.classList.toggle('is-grouped-card', isNowGrouped && nowGroupCount > 1);
      const gBadge = card.querySelector('.badge-group-count');
      if (gBadge && nowGroupCount > 1) {
        gBadge.innerHTML = `<i class="fa-solid fa-layer-group"></i> ${nowGroupCount} filmów`;
        gBadge.title = `Ten autor ma ${nowGroupCount} filmów na liście. Kliknij, aby rozwinąć listę!`;
      }
      const aPill = card.querySelector('.author-group-pill');
      if (aPill && nowGroupCount > 1) {
        aPill.innerHTML = `<i class="fa-solid fa-clone"></i> Grupa (${nowGroupCount})`;
      }
      const pBtn = card.querySelector('.profile-btn');
      if (pBtn) {
        pBtn.innerHTML = `<i class="fa-solid fa-folder${isNowGrouped && nowGroupCount > 1 ? '-open' : ''}"></i> ${isNowGrouped && nowGroupCount > 1 ? `${nowGroupCount} filmów` : 'Filmy'}`;
      }

      // Wyświetlenia, długość, data
      const vwBadge = card.querySelector('.badge-views');
      if (vwBadge && newV.views && vwBadge.innerText !== String(newV.views)) {
        vwBadge.innerHTML = `<i class="fa-solid fa-eye"></i> ${newV.views}`;
      }
      const dBadge = card.querySelector('.badge-duration');
      if (dBadge && newV.duration && newV.duration !== 'N/A' && dBadge.innerText !== newV.duration) {
        dBadge.innerText = newV.duration;
      }
      const dtBadge = card.querySelector('.card-date-badge');
      if (dtBadge && newV.date && dtBadge.dataset.origDate !== newV.date) {
        dtBadge.dataset.origDate = newV.date;
        dtBadge.innerHTML = `<i class="fa-regular fa-calendar-days"></i> ${newV.date}`;
      }

      // Miniatura: stabilność img.src
      const dirPoster = newV.poster_direct || (newV.poster ? newV.poster.replace(/\.mp4$/, '.jpg') : '');
      const canPoster = (typeof thumbnailUrlForVideo === 'function' ? thumbnailUrlForVideo(newV) : '') || dirPoster;
      const targetPoster = canPoster || dirPoster;
      const tImg = card.querySelector('.thumbnail-img');
      if (tImg && targetPoster) {
        const activeSrc = tImg.getAttribute('src');
        const lazySrc = tImg.getAttribute('data-src');
        if (activeSrc && activeSrc !== targetPoster) {
          tImg.src = targetPoster;
        } else if (!activeSrc && lazySrc && lazySrc !== targetPoster) {
          tImg.setAttribute('data-src', targetPoster);
        }
      }
    };

    return card;
  }

  const ArchivebateVideoCard = {
    createVideoCard,
    create: createVideoCard
  };

  global.ArchivebateVideoCard = ArchivebateVideoCard;
  if (!global.createVideoCard) {
    global.createVideoCard = createVideoCard;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ArchivebateVideoCard;
  }
})(typeof window !== 'undefined' ? window : globalThis);
