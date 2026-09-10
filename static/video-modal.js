/**
 * Archivebate - Video Modal Core Module
 * Obsługa głównego okna modalnego wideo, bezpośredniego strumienia MP4,
 * trybu iframe, pobierania metadanych, podglądu klatek (previewSeeker)
 * oraz nawigacji po filmach i profilach autorów.
 */
(function (global) {
  'use strict';

  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);

  const state = new Proxy({}, {
    get(_, prop) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.state && prop in g.ArchivebateAppContext.state) {
        return g.ArchivebateAppContext.state[prop];
      }
      if (g.state && prop in g.state) {
        return g.state[prop];
      }
      return undefined;
    },
    set(_, prop, val) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.state) {
        g.ArchivebateAppContext.state[prop] = val;
      }
      if (g.state) {
        g.state[prop] = val;
      }
      return true;
    }
  });

  const dom = new Proxy({}, {
    get(_, prop) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.dom && prop in g.ArchivebateAppContext.dom) {
        return g.ArchivebateAppContext.dom[prop];
      }
      if (g.dom && prop in g.dom) {
        return g.dom[prop];
      }
      return undefined;
    },
    set(_, prop, val) {
      if (g.ArchivebateAppContext && g.ArchivebateAppContext.dom) {
        g.ArchivebateAppContext.dom[prop] = val;
      }
      if (g.dom) {
        g.dom[prop] = val;
      }
      return true;
    }
  });

  const api = () => g.ArchivebateAPI || {
    getJSON: (url, opts) => fetch(url, opts).then(r => r.json()),
    postJSON: (url, body, opts) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), ...opts }).then(r => r.json())
  };

  const perf = () => g.ArchivebatePerf || {
    measure: () => {},
    setPlaybackBusy: () => {}
  };

  const parseDurationToSeconds = (durStr) => {
    if (g.ArchivebatePlayerCore && typeof g.ArchivebatePlayerCore.parseDurationToSeconds === 'function') {
      return g.ArchivebatePlayerCore.parseDurationToSeconds(durStr);
    }
    if (!durStr) return 0;
    const parts = String(durStr).trim().split(':').map(Number);
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return Number(durStr) || 0;
  };

  let showToast = (msg, type, duration) => (g.ArchivebateToast ? g.ArchivebateToast.show(msg, type, duration) : (g.showToast ? g.showToast(msg, type, duration) : console.log(msg)));
  let isFavoriteAuthor = (u) => (g.ArchivebateFavorites ? g.ArchivebateFavorites.isFavoriteAuthor(u) : (g.isFavoriteAuthor ? g.isFavoriteAuthor(u) : false));
  let blockModel = (u) => (g.ArchivebateBlockedModels ? g.ArchivebateBlockedModels.block(u) : (g.blockModel ? g.blockModel(u) : undefined));
  let loadModelVideos = (u, p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadModelVideos(u, p) : (g.loadModelVideos ? g.loadModelVideos(u, p) : undefined));
  let performSearch = (q, p) => (g.ArchivebateSearchResults ? g.ArchivebateSearchResults.performSearch(q, p) : (g.performSearch ? g.performSearch(q, p) : undefined));
  let toggleFavoriteVideo = (v, b) => (g.ArchivebateFavorites ? g.ArchivebateFavorites.toggleVideo(v, b) : (g.toggleFavoriteVideo ? g.toggleFavoriteVideo(v, b) : undefined));
  let updateModalFavButton = (fav) => (g.ArchivebateFavorites ? g.ArchivebateFavorites.updateModalButton(fav) : (g.updateModalFavButton ? g.updateModalFavButton(fav) : undefined));
  let changePage = (p) => (g.ArchivebatePagination ? g.ArchivebatePagination.changePage(p) : (g.changePage ? g.changePage(p) : undefined));
  let loadVideos = (p) => (g.ArchivebateVideoViews ? g.ArchivebateVideoViews.loadHomeVideos(p) : (g.loadHomeVideos ? g.loadHomeVideos(p) : undefined));

  function init(dependencies = {}) {
    if (dependencies.showToast) showToast = dependencies.showToast;
    if (dependencies.isFavoriteAuthor) isFavoriteAuthor = dependencies.isFavoriteAuthor;
    if (dependencies.blockModel) blockModel = dependencies.blockModel;
    if (dependencies.loadModelVideos) loadModelVideos = dependencies.loadModelVideos;
    if (dependencies.performSearch) performSearch = dependencies.performSearch;
    if (dependencies.toggleFavoriteVideo) toggleFavoriteVideo = dependencies.toggleFavoriteVideo;
    if (dependencies.updateModalFavButton) updateModalFavButton = dependencies.updateModalFavButton;
    if (dependencies.changePage) changePage = dependencies.changePage;
    if (dependencies.loadVideos) loadVideos = dependencies.loadVideos;
  }

  function getEffectiveVideoUsername(video) {
    if (!video) return '';
    const u = (video.username || '').trim();
    if (u && u.toLowerCase() !== 'model' && u.toLowerCase() !== 'unknown') return u;

    // Walidacja identyfikatora autora na podstawie linku profilu/znanego pola (Pakiet C, punkt 8)
    const profUrl = video.profile_url || video.url || '';
    if (profUrl) {
      const m = profUrl.match(/(?:profile|models|search)\/([a-zA-Z0-9_\-\.]+)/);
      if (m && m[1] && !['model', 'models', 'search', 'videos'].includes(m[1].toLowerCase())) {
        return m[1];
      }
    }
    return '';
  }

  function isModelBlocked(username) {
    if (!username || !state.blockedModels) return false;
    const raw = String(username).trim().toLowerCase();
    const clean = raw.replace(/[^a-z0-9]/g, '');
    if (state.blockedModels instanceof Set) {
      return state.blockedModels.has(raw) || state.blockedModels.has(clean);
    }
    if (Array.isArray(state.blockedModels)) {
      return state.blockedModels.some(b => {
        const bNorm = String(b).trim().toLowerCase();
        return bNorm === raw || bNorm.replace(/[^a-z0-9]/g, '') === clean;
      });
    }
    return false;
  }

  const authorPlaylists = new Map();
  let loadingMarkup;

  async function getAuthorVideosList(username) {
    if (!username || username.toLowerCase() === 'model') return null;
    const normUser = username.trim().toLowerCase();

    let playlist = authorPlaylists.get(normUser);
    if (!playlist) {
      playlist = {
        username: username,
        videos: [],
        page: 1,
        hasMore: true,
        isLoading: false
      };
      authorPlaylists.set(normUser, playlist);

      if (state.currentVideoDetails && (state.currentVideoDetails.username || '').trim().toLowerCase() === normUser) {
        playlist.videos.push(state.currentVideoDetails);
      }
    }

    if (state.mode === 'model' && (state.currentModel || '').trim().toLowerCase() === normUser && Array.isArray(state.videos) && state.videos.length > 0) {
      const existingIds = new Set(playlist.videos.map(v => String(v.id || v.url)));
      for (const v of state.videos) {
        const vidKey = String(v.id || v.url);
        if (!existingIds.has(vidKey)) {
          playlist.videos.push(v);
          existingIds.add(vidKey);
        }
      }
    }

    if (playlist.videos.length <= 1 && playlist.hasMore && !playlist.isLoading) {
      playlist.isLoading = true;
      try {
        const data = await api().getJSON(`/api/model/${encodeURIComponent(username)}?page=1`, { timeoutMs: 12000 });
        const fetched = data?.videos || [];
        if (fetched.length > 0) {
          const existingIds = new Set(playlist.videos.map(v => String(v.id || v.url)));
          for (const v of fetched) {
            const vidKey = String(v.id || v.url);
            if (!existingIds.has(vidKey)) {
              playlist.videos.push(v);
              existingIds.add(vidKey);
            }
          }
        } else {
          playlist.hasMore = false;
        }
      } catch (e) {
        console.warn('Nie udało się pobrać filmów autora:', username, e);
      } finally {
        playlist.isLoading = false;
      }
    }

    return playlist;
  }

  function getAuthorVideoIndex(playlist, currentVideo) {
    if (!playlist || !Array.isArray(playlist.videos) || playlist.videos.length === 0 || !currentVideo) return -1;
    const currId = String(currentVideo.id || '');
    const currUrl = currentVideo.url || '';
    return playlist.videos.findIndex(v => (currId && String(v.id || '') === currId) || (currUrl && v.url === currUrl));
  }

  async function playNextAuthorVideo() {
    const generation = state.playerGeneration;
    const current = state.currentVideoDetails;
    const username = getEffectiveVideoUsername(current);
    if (!username || username.toLowerCase() === 'model') {
      showToast('Brak profilu autora dla tego filmu', 'info');
      return;
    }

    const playlist = await getAuthorVideosList(username);
    if (generation !== state.playerGeneration) return;
    if (!playlist || playlist.videos.length === 0) {
      showToast(`Brak filmów w profilu autora ${username}`, 'info');
      return;
    }

    let idx = getAuthorVideoIndex(playlist, current);
    if (idx === -1) {
      idx = 0;
      if (String(playlist.videos[0].id || '') !== String(current.id || '')) {
        const nextVid = playlist.videos[0];
        showToast(`Profil: ${username} (1/${playlist.videos.length})`, 'info', 1400);
        openVideoModal(nextVid);
        return;
      }
    }

    if (idx < playlist.videos.length - 1) {
      const nextVid = playlist.videos[idx + 1];
      showToast(`Profil: ${username} (${idx + 2}/${playlist.videos.length}) • ${nextVid.date || ''}`, 'info', 1400);
      openVideoModal(nextVid);
      return;
    }

    if (playlist.hasMore && !playlist.isLoading) {
      playlist.isLoading = true;
      showToast(`Dociąganie kolejnych filmów dla ${username}...`, 'info', 1200);
      try {
        const nextPage = (playlist.page || 1) + 1;
        const data = await api().getJSON(`/api/model/${encodeURIComponent(username)}?page=${nextPage}`, { timeoutMs: 12000 });
        if (generation !== state.playerGeneration) return;
        const fetched = data?.videos || [];
        playlist.page = nextPage;
        if (fetched.length === 0) {
          playlist.hasMore = false;
          showToast(`To jest ostatni film w profilu ${username}. Naciśnij ↑ aby cofnąć.`, 'info', 2000);
        } else {
          const existingIds = new Set(playlist.videos.map(v => String(v.id || v.url)));
          let addedCount = 0;
          for (const v of fetched) {
            const vidKey = String(v.id || v.url);
            if (!existingIds.has(vidKey)) {
              playlist.videos.push(v);
              existingIds.add(vidKey);
              addedCount++;
            }
          }
          if (addedCount > 0 && idx < playlist.videos.length - 1) {
            const nextVid = playlist.videos[idx + 1];
            showToast(`Profil: ${username} (${idx + 2}/${playlist.videos.length}) • ${nextVid.date || ''}`, 'info', 1400);
            openVideoModal(nextVid);
            return;
          } else {
            playlist.hasMore = false;
            showToast(`To jest ostatni film w profilu ${username}. Naciśnij ↑ aby cofnąć.`, 'info', 2000);
          }
        }
      } catch (e) {
        showToast(`Błąd pobierania kolejnych filmów dla ${username}`, 'error');
      } finally {
        playlist.isLoading = false;
      }
      return;
    }

    showToast(`To jest ostatni film w profilu ${username}. Naciśnij ↑ aby cofnąć.`, 'info', 2000);
  }

  async function playPrevAuthorVideo() {
    const generation = state.playerGeneration;
    const current = state.currentVideoDetails;
    const username = getEffectiveVideoUsername(current);
    if (!username || username.toLowerCase() === 'model') {
      showToast('Brak profilu autora dla tego filmu', 'info');
      return;
    }

    const playlist = await getAuthorVideosList(username);
    if (generation !== state.playerGeneration) return;
    if (!playlist || playlist.videos.length === 0) {
      showToast(`Brak filmów w profilu autora ${username}`, 'info');
      return;
    }

    let idx = getAuthorVideoIndex(playlist, current);
    if (idx > 0) {
      const prevVid = playlist.videos[idx - 1];
      showToast(`Profil: ${username} (${idx}/${playlist.videos.length}) • ${prevVid.date || ''}`, 'info', 1400);
      openVideoModal(prevVid);
      return;
    }

    showToast(`To jest najnowszy (pierwszy) film w profilu ${username}. Naciśnij ↓ aby przejść do kolejnego.`, 'info', 2200);
  }

  async function openVideoModal(video, options = {}) {
    const openedAt = performance.now();
    let firstPlaying = true;
    const forceRefresh = !!options?.forceRefresh;
    const generation = state.playerGeneration = (state.playerGeneration || 0) + 1;

    // Anulowanie intencji poprzedniego filmu (Pakiet C - Odbiór: Zmiana A->B anuluje intencję A)
    state.playerController?.abort();
    state.activePlaybackSession?.abort('switched_video');
    state.previewSeeker?.destroy();

    const sessionController = state.playerController = new AbortController();

    // Inicjalizacja sesji pomiarowej otwarcia wideo (Pakiet C, punkt 1)
    const currentSession = state.activePlaybackSession = (g.ArchivebatePerf?.startPlaybackSession ? g.ArchivebatePerf.startPlaybackSession({
      id: video?.id,
      owner: 'player',
      priority: 'high',
      reason: options?.reason || 'click'
    }) : null);

    if (g.ArchivebatePlayerCore && typeof g.ArchivebatePlayerCore.createPreviewSeeker === 'function' && dom.modalTimelinePreviewVideo) {
      state.previewSeeker = g.ArchivebatePlayerCore.createPreviewSeeker(dom.modalTimelinePreviewVideo, {
        onFrame: () => {
          if (dom.modalTimelinePreviewVideo) dom.modalTimelinePreviewVideo.style.display = 'block';
        }
      });
    }
    state.isIframeMode = false;
    perf().setPlaybackBusy(true);
    if (state.storyboardBuildController) state.storyboardBuildController.abort();
    state.storyboardBuildController = null;
    state.localStoryboard = null;
    if (state.timelineSpriteAbort) state.timelineSpriteAbort.abort();
    state.timelineSpriteAbort = null;
    state.timelineSpriteBoard = null;
    state.currentStoryboardKey = video?.id ? `video:${video.id}` : null;
    state.currentVideoDetails = { ...video };

    const groupedList = video?.grouped_videos || video?._groupedVideos;
    if (groupedList && groupedList.length > 1 && video?.username) {
      const normU = video.username.trim().toLowerCase();
      authorPlaylists.set(normU, {
        username: video.username,
        videos: [...groupedList],
        page: 1,
        hasMore: true,
        isLoading: false
      });
    }

    if (dom.modalVideo) {
      dom.modalVideo.pause();
      dom.modalVideo.removeAttribute('src');
      dom.modalVideo.load();
      dom.modalVideo.style.display = 'block';
    }
    if (dom.modalIframe) {
      dom.modalIframe.style.display = 'none';
      dom.modalIframe.src = '';
    }
    if (dom.videoLoader) {
      if (loadingMarkup === undefined) loadingMarkup = dom.videoLoader.innerHTML;
      dom.videoLoader.innerHTML = loadingMarkup;
      dom.videoLoader.style.display = 'flex';
    }
    if (dom.modalCenterPlay) dom.modalCenterPlay.style.display = 'none';

    const hideLoadingPoster = () => {
      if (generation !== state.playerGeneration) return;
      if (dom.modalLoadingPoster) {
        dom.modalLoadingPoster.style.opacity = '0';
        setTimeout(() => {
          if (generation === state.playerGeneration && dom.modalLoadingPoster && dom.modalLoadingPoster.style.opacity === '0') {
            dom.modalLoadingPoster.style.display = 'none';
          }
        }, 350);
      }
      if (dom.videoLoader) dom.videoLoader.style.display = 'none';
    };

    if (dom.modalVideo) {
      dom.modalVideo.onloadedmetadata = () => {
        if (generation !== state.playerGeneration) return;
        currentSession?.markMetadata();
      };
      dom.modalVideo.onloadeddata = hideLoadingPoster;
      dom.modalVideo.oncanplay = hideLoadingPoster;
      dom.modalVideo.ontimeupdate = () => {
        if (generation !== state.playerGeneration) return;
        if (dom.modalVideo.currentTime > 0) hideLoadingPoster();
        if (g.ArchivebatePerf?.updatePlaybackBuffer) {
          g.ArchivebatePerf.updatePlaybackBuffer(dom.modalVideo, 5.0);
        }
      };
      dom.modalVideo.onprogress = () => {
        if (generation !== state.playerGeneration) return;
        if (g.ArchivebatePerf?.updatePlaybackBuffer) {
          g.ArchivebatePerf.updatePlaybackBuffer(dom.modalVideo, 5.0);
        }
      };
      dom.modalVideo.onplaying = () => {
        if (generation !== state.playerGeneration) return;
        if (firstPlaying) {
          perf().measure('modal_first_playing', openedAt);
          currentSession?.markFirstByte();
          firstPlaying = false;
        }
        hideLoadingPoster();
        // Sprawdzenie 5 sekund bufora wokół pozycji (Pakiet C, punkt 6)
        if (g.ArchivebatePerf?.updatePlaybackBuffer) {
          g.ArchivebatePerf.updatePlaybackBuffer(dom.modalVideo, 5.0);
        } else {
          perf().setPlaybackBusy(false);
        }
      };
      dom.modalVideo.onwaiting = dom.modalVideo.onstalled = () => {
        if (generation !== state.playerGeneration) return;
        perf().setPlaybackBusy(true);
      };
      dom.modalVideo.onerror = () => {
        if (generation !== state.playerGeneration) return;
        hideLoadingPoster();
        perf().setPlaybackBusy(false);
        currentSession?.markError('video_stream_error', 'Błąd strumienia wideo');
        // Kontrolowany komunikat i przycisk ponowienia (Pakiet C, punkt 3)
        if (dom.videoLoader) {
          dom.videoLoader.style.display = 'flex';
          dom.videoLoader.innerHTML = `
            <div style="text-align: center; color: #f87171; padding: 20px; background: rgba(0,0,0,0.8); border-radius: 8px;">
              <div style="font-size: 24px; margin-bottom: 8px;">⚠️</div>
              <div style="font-weight: 600; margin-bottom: 12px;">Nie udało się załadować wideo (błąd źródła)</div>
              <button id="modalRetryPlaybackBtn" style="background: #2563eb; color: #fff; border: none; padding: 8px 18px; border-radius: 6px; cursor: pointer; font-weight: 600;">
                Ponów próbę
              </button>
            </div>
          `;
          const retryBtn = document.getElementById('modalRetryPlaybackBtn');
          if (retryBtn) {
            retryBtn.onclick = (e) => {
              e.stopPropagation();
              openVideoModal(video, { forceRefresh: true, reason: 'retry_button' });
            };
          }
        }
        showToast('Błąd strumienia. Kliknij „Ponów próbę”.', 'error');
      };
    }

    const observeFirstFrame = () => {
      g.ArchivebatePlayerCore?.waitForPresentedFrame(dom.modalVideo, undefined, 30000, sessionController.signal).then(presented => {
        if (presented && generation === state.playerGeneration) currentSession?.markFirstFrame();
      });
    };
    const immediateStream = (video && video.id) ? `/api/video/stream?id=${encodeURIComponent(video.id)}&owner=player&priority=high&reason=click` : '';
    if (immediateStream && dom.modalVideo && !forceRefresh) {
      currentSession?.markConnectStart();
      dom.modalVideo.src = immediateStream;
      dom.modalVideo.preload = 'auto';
      dom.modalVideo.load();
      observeFirstFrame();
      const p1 = dom.modalVideo.play();
      if (p1 !== undefined) {
        p1.catch(() => {
          if (generation === state.playerGeneration && dom.modalCenterPlay) dom.modalCenterPlay.style.display = 'flex';
        });
      }
    }

    let posterSrc = (video.thumbnail || video.poster || '').replace('.mp4', '.jpg');
    if (posterSrc.includes('/180x135/')) {
      posterSrc = posterSrc.replace(/\/180x135\/\d+\.jpg/, '/preview.jpg');
    }
    if (dom.modalLoadingPoster) {
      dom.modalLoadingPoster.src = posterSrc || '';
      dom.modalLoadingPoster.style.display = posterSrc ? 'block' : 'none';
      dom.modalLoadingPoster.style.opacity = '1';
    }
    if (dom.modalVideo) {
      dom.modalVideo.poster = posterSrc || '';
    }

    if (dom.modalPlatform) dom.modalPlatform.innerText = video.platform || 'Chaturbate';
    if (dom.modalModelName) {
      dom.modalModelName.innerText = `${video.username} • ${video.date || ''}`;
      dom.modalModelName.style.cursor = 'pointer';
      dom.modalModelName.title = `Zobacz profil ${video.username} (LPM) lub otwórz w nowej karcie (Kółko myszy)`;
      dom.modalModelName.onclick = (e) => {
        e.stopPropagation();
        closeModal();
        loadModelVideos(video.username, 1);
      };
      dom.modalModelName.onauxclick = (e) => {
        if (e.button === 1) {
          e.preventDefault();
          e.stopPropagation();
          if (g.tabManager && typeof g.tabManager.openTab === 'function') {
            g.tabManager.openTab({
              title: video.username,
              icon: 'fa-solid fa-circle-user',
              type: 'model',
              username: video.username,
              inBackground: true
            });
          }
        }
      };
      dom.modalModelName.onmousedown = (e) => {
        if (e.button === 1) e.preventDefault();
      };
    }

    const isFavAuthor = isFavoriteAuthor(video.username) || video.has_favorite_video;
    const isFav = isFavAuthor || !!video.is_favorite;
    if (dom.modalModelName) {
      if (isFav) dom.modalModelName.classList.add('is-favorite-author');
      else dom.modalModelName.classList.remove('is-favorite-author');
    }
    const modalContent = dom.videoModal?.querySelector('.modal-content');
    if (modalContent) {
      if (isFav) modalContent.classList.add('is-favorite-modal');
      else modalContent.classList.remove('is-favorite-modal');
    }
    if (dom.modalOriginalBtn) dom.modalOriginalBtn.href = video.url;
    if (dom.modalPopoutBtn) {
      const popDur = parseDurationToSeconds(video?.duration);
      dom.modalPopoutBtn.href = `/watch/${video.id}${popDur > 0 ? `?duration=${popDur}` : ''}`;
    }
    if (dom.modalDownloadBtn) {
      dom.modalDownloadBtn.href = '#';
      dom.modalDownloadBtn.style.display = 'none';
    }
    if (dom.modalKeywords) {
      dom.modalKeywords.innerHTML = '<span style="color: var(--text-dim); font-size: 12px;">Pobieranie bezpośredniego strumienia wideo...</span>';
    }

    const recordStartedVideo = () => {
      if (generation !== state.playerGeneration) return;
      api().postJSON('/api/account/history/record', video).then(d => {
        state.historyCount = d.total_history;
        if (dom.navHistCount) dom.navHistCount.innerText = state.historyCount;
        if (dom.statHistCount) dom.statHistCount.innerText = state.historyCount;
      }).catch(() => {});
    };
    if (dom.modalVideo) {
      dom.modalVideo.addEventListener('playing', recordStartedVideo, { once: true, signal: sessionController.signal });
    }

    if (dom.modalViewModelVideosBtn) {
      dom.modalViewModelVideosBtn.onclick = () => {
        closeModal();
        loadModelVideos(video.username, 1);
      };
      dom.modalViewModelVideosBtn.onauxclick = (e) => {
        if (e.button === 1) {
          e.preventDefault();
          e.stopPropagation();
          if (g.tabManager && typeof g.tabManager.openTab === 'function') {
            g.tabManager.openTab({
              title: video.username,
              icon: 'fa-solid fa-circle-user',
              type: 'model',
              username: video.username,
              inBackground: true
            });
          }
        }
      };
      dom.modalViewModelVideosBtn.onmousedown = (e) => {
        if (e.button === 1) e.preventDefault();
      };
    }

    if (dom.modalBlockModelBtn) {
      dom.modalBlockModelBtn.onclick = (e) => {
        e.stopPropagation();
        blockModel(video.username);
      };
    }

    if (dom.videoModal) dom.videoModal.classList.add('active');
    if (typeof document !== 'undefined' && document.body) document.body.style.overflow = 'hidden';

    try {
      let details = null;
      const prefetchMod = g.ArchivebateVideoPrefetch;
      if (!forceRefresh && video && video.id && prefetchMod && prefetchMod.detailsCache) {
        details = prefetchMod.detailsCache.get(video.id);
      }
      if (!details || (!details.proxy_stream_url && !details.direct_url) || forceRefresh) {
        if (forceRefresh) {
          details = await api().getJSON(`/api/video/details?id=${encodeURIComponent(video.id || video.url)}&force_refresh=true`, { signal: sessionController.signal });
        } else if (prefetchMod && typeof prefetchMod.prefetchVideoDetails === 'function') {
          details = await prefetchMod.prefetchVideoDetails(video.id || video.url, { signal: sessionController.signal });
        } else if (typeof g.prefetchVideoDetails === 'function') {
          details = await g.prefetchVideoDetails(video.id || video.url, true);
        }
        if (!details && !sessionController.signal.aborted) {
          details = await api().getJSON(`/api/video/details?id=${encodeURIComponent(video.id || video.url)}${forceRefresh ? '&force_refresh=true' : ''}`, { signal: sessionController.signal });
        }
        if (sessionController.signal.aborted || generation !== state.playerGeneration) return;
        if (!details) throw new Error('Nie udało się pobrać detali filmu');
        if (video && video.id && (details.proxy_stream_url || details.direct_url) && prefetchMod && prefetchMod.detailsCache) {
          prefetchMod.detailsCache.set(video.id, details);
        }
      }
      if (sessionController.signal.aborted || generation !== state.playerGeneration) return;

      currentSession?.markUrlResolved(details.proxy_stream_url || details.direct_url, !forceRefresh);
      const resolvedUsername = getEffectiveVideoUsername(video) || getEffectiveVideoUsername(details) || 'Model';
      state.currentVideoDetails = {
        ...video,
        ...details,
        username: resolvedUsername
      };

      // Przygotowanie co najwyżej 1 kandydata na intencję użytkownika (Pakiet C, punkt 7)
      try {
        const playlist = authorPlaylists.get(resolvedUsername?.toLowerCase());
        if (playlist && Array.isArray(playlist.videos)) {
          const nextIdx = getAuthorVideoIndex(playlist, video) + 1;
          if (nextIdx > 0 && nextIdx < playlist.videos.length) {
            const candidate = playlist.videos[nextIdx];
            if (candidate?.id && prefetchMod?.prefetchVideoDetails) {
              g.ArchivebatePerf?.schedule?.(
                () => prefetchMod.prefetchVideoDetails(candidate.id, { signal: sessionController.signal }),
                { signal: sessionController.signal }
              ).catch(() => {});
            }
          }
        }
      } catch (_) {}

      if (resolvedUsername && resolvedUsername.toLowerCase() !== 'model') {
        if (dom.modalModelName) dom.modalModelName.innerText = `${resolvedUsername} • ${video.date || details.date || ''}`;
        if (dom.modalViewModelVideosBtn) {
          dom.modalViewModelVideosBtn.title = `Zobacz nagrania modelki ${resolvedUsername} (LPM) lub otwórz w nowej karcie (Kółko myszy)`;
          dom.modalViewModelVideosBtn.onclick = () => {
            closeModal();
            loadModelVideos(resolvedUsername, 1);
          };
        }
        if (dom.modalBlockModelBtn) {
          dom.modalBlockModelBtn.onclick = (e) => {
            e.stopPropagation();
            blockModel(resolvedUsername);
          };
        }
      }

      updateModalFavButton(!!details.is_favorite);

      if (details.is_private) {
        perf().setPlaybackBusy(false);
        if (dom.videoLoader) dom.videoLoader.style.display = 'none';
        if (dom.modalLoadingPoster) dom.modalLoadingPoster.style.display = 'none';
        if (dom.modalVideo) {
          dom.modalVideo.pause();
          dom.modalVideo.removeAttribute('src');
          dom.modalVideo.load();
        }
        showToast('Ten film jest oznaczony jako prywatny na Camwhores (dostępny tylko dla członków serwisu)', 'error', 6000);
        if (dom.modalKeywords) {
          dom.modalKeywords.innerHTML = '<div style="color: #f87171; font-weight: 600; padding: 6px 0;"><i class="fa-solid fa-lock"></i> Film prywatny na Camwhores (dostępny wyłącznie dla zalogowanych autorów serwisu).</div>';
        }
        return;
      }

      const rawStream = immediateStream || details.proxy_stream_url || details.direct_url;
      const streamSource = rawStream ? (forceRefresh ? `${rawStream}${rawStream.includes('?') ? '&' : '?'}retry=${generation}` : rawStream) : '';
      if (streamSource && dom.modalVideo) {
        const currentSrc = dom.modalVideo.getAttribute('src') || dom.modalVideo.src || '';
        const normalize = (u) => {
          try {
            const parsed = new URL(u, window.location.href);
            return parsed.pathname + parsed.search;
          } catch(e) { return u; }
        };
        if (!currentSrc || normalize(currentSrc) !== normalize(streamSource)) {
          dom.modalVideo.src = streamSource;
          dom.modalVideo.preload = 'auto';
          dom.modalVideo.load();
          observeFirstFrame();
          const p2 = dom.modalVideo.play();
          if (p2 !== undefined) {
            p2.catch(() => {
              if (generation === state.playerGeneration && dom.modalCenterPlay) dom.modalCenterPlay.style.display = 'flex';
            });
          }
        }
      }

      if (dom.modalDownloadBtn && (details.direct_url || details.proxy_stream_url)) {
        dom.modalDownloadBtn.href = details.proxy_stream_url || details.direct_url;
        dom.modalDownloadBtn.style.display = 'flex';
      }

      if (dom.modalKeywords) {
        if (Array.isArray(details.keywords) && details.keywords.length > 0) {
          dom.modalKeywords.innerHTML = details.keywords.map(kw => `<span class="tag-badge" data-tag="${kw.toLowerCase()}">#${kw}</span>`).join('');
          dom.modalKeywords.querySelectorAll('.tag-badge').forEach(badge => {
            const kwTag = badge.dataset.tag;
            badge.onclick = (e) => {
              e.stopPropagation();
              closeModal();
              if (dom.searchInput) dom.searchInput.value = `#${kwTag}`;
              if (dom.clearSearchBtn) dom.clearSearchBtn.style.display = 'flex';
              performSearch(kwTag, 1);
            };
          });
        } else {
          dom.modalKeywords.innerHTML = '<span style="color: var(--text-dim); font-size: 12px;">Brak tagów</span>';
        }
      }
    } catch (err) {
      if (generation !== state.playerGeneration) return;
      console.warn('Błąd detali filmu:', err);
      if (forceRefresh || !immediateStream) dom.modalVideo?.onerror?.();
    }
  }

  function togglePlayerMode() {
    if (state.isIframeMode) {
      state.isIframeMode = false;
      if (dom.modalIframe) {
        dom.modalIframe.style.display = 'none';
        dom.modalIframe.src = '';
      }
      if (dom.modalVideo) {
        dom.modalVideo.style.display = 'block';
        if (state.currentVideoDetails && (state.currentVideoDetails.direct_url || state.currentVideoDetails.proxy_stream_url)) {
          dom.modalVideo.src = state.currentVideoDetails.proxy_stream_url || state.currentVideoDetails.direct_url;
          dom.modalVideo.play().catch(() => {});
        }
      }
      showToast('Włączono bezpośredni odtwarzacz wideo', 'info');
    } else {
      enableIframeMode();
      showToast('Włączono tryb awaryjny Iframe', 'info');
    }
  }

  function enableIframeMode() {
    state.isIframeMode = true;
    if (dom.modalVideo) {
      dom.modalVideo.pause();
      dom.modalVideo.removeAttribute('src');
      dom.modalVideo.load();
      dom.modalVideo.style.display = 'none';
    }
    if (dom.videoLoader) dom.videoLoader.style.display = 'none';
    if (dom.modalIframe) {
      dom.modalIframe.style.display = 'block';
      if (state.currentVideoDetails && state.currentVideoDetails.embed_url) {
        dom.modalIframe.src = state.currentVideoDetails.embed_url;
      }
    }
  }

  function getActiveFilteredVideos() {
    if (!state.videos || state.videos.length === 0) return [];
    const mode = state.sourceFilter || 'all';
    const af = state.authorFilter || 'all';
    const favAuthors = state.favoriteAuthors || new Set();

    const groupFn = (g.ArchivebateFilters && g.ArchivebateFilters.groupVideosByAuthor) || g.groupVideosByAuthor || (x => x);
    const baseList = (state.groupByAuthor && state.mode === 'search')
      ? groupFn(state.videos)
      : state.videos;

    return baseList.filter(v => {
      const authorNorm = (v.username || '').toLowerCase().trim();
      const authorClean = authorNorm.replace(/[^a-z0-9]/g, '');
      if (isModelBlocked(v.username)) {
        return false;
      }
      const isCamwhores = v.source === 'camwhores' || String(v.id).startsWith('cw_') || (v.platform && v.platform.toLowerCase().includes('camwhores'));
      if (mode === 'only-camwhores' && !isCamwhores) {
        return false;
      } else if (mode === 'only-archivebate' && isCamwhores) {
        return false;
      }

      const isFav = v.is_favorite || favAuthors.has(authorNorm) || favAuthors.has(authorClean);
      if (af === 'only_fav' && !isFav) {
        return false;
      } else if (af === 'exclude_fav' && isFav) {
        return false;
      }

      return true;
    });
  }

  function getCurrentVideoIndex() {
    const list = getActiveFilteredVideos();
    if (!state.currentVideoDetails || list.length === 0) return -1;
    const currId = String(state.currentVideoDetails.id || '');
    let idx = list.findIndex(v => String(v.id || '') === currId);
    if (idx !== -1) return idx;
    const currUrl = state.currentVideoDetails.url;
    if (currUrl) {
      idx = list.findIndex(v => v.url === currUrl);
      if (idx !== -1) return idx;
    }
    return -1;
  }

  async function playNextVideo() {
    const list = getActiveFilteredVideos();
    if (list.length === 0) {
      showToast('Brak filmów do odtworzenia', 'info');
      return;
    }
    let idx = getCurrentVideoIndex();
    if (idx === -1) idx = 0;

    if (idx < list.length - 1) {
      const nextVideo = list[idx + 1];
      showToast(`Następny film (${idx + 2}/${list.length}): ${nextVideo.username}`, 'info', 1200);
      openVideoModal(nextVideo);
      return;
    }
    if (idx === list.length - 1 && state.currentPage < state.lastPage) {
      showToast('Ładowanie następnej strony filmów...', 'info', 1500);
      const nextPage = state.currentPage + 1;
      await loadVideos(nextPage);
      const updatedList = getActiveFilteredVideos();
      if (updatedList.length > 0) {
        openVideoModal(updatedList[0]);
      }
      return;
    }
    showToast('To jest ostatni film na tej stronie', 'info', 1500);
  }

  async function playPrevVideo() {
    const list = getActiveFilteredVideos();
    if (list.length === 0) {
      showToast('Brak filmów do odtworzenia', 'info');
      return;
    }
    let idx = getCurrentVideoIndex();
    if (idx > 0) {
      const prevVideo = list[idx - 1];
      showToast(`Poprzedni film (${idx}/${list.length}): ${prevVideo.username}`, 'info', 1200);
      openVideoModal(prevVideo);
      return;
    }
    if (idx <= 0 && state.currentPage > 1) {
      showToast('Ładowanie poprzedniej strony filmów...', 'info', 1500);
      const prevPage = state.currentPage - 1;
      await loadVideos(prevPage);
      const updatedList = getActiveFilteredVideos();
      if (updatedList.length > 0) {
        openVideoModal(updatedList[updatedList.length - 1]);
      }
      return;
    }
    showToast('To jest pierwszy film na tej stronie', 'info', 1500);
  }

  function closeModal() {
    state.activePlaybackSession?.abort('closed');
    state.previewSeeker?.destroy();
    state.previewSeeker = null;
    perf().setPlaybackBusy(false);
    state.playerGeneration = (state.playerGeneration || 0) + 1;
    state.playerController?.abort();
    if (state.timelineSpriteAbort) state.timelineSpriteAbort.abort();
    state.timelineSpriteAbort = null;
    state.timelineSpriteBoard = null;
    if (dom.modalTimelineSprite && g.ArchivebateYouTubeStoryboard) {
      g.ArchivebateYouTubeStoryboard.clearFrame(dom.modalTimelineSprite);
    }
    if (dom.modalTimelinePreviewStatus) dom.modalTimelinePreviewStatus.style.display = 'none';
    if (state.storyboardBuildController) state.storyboardBuildController.abort();
    state.storyboardBuildController = null;
    state.localStoryboard = null;
    state.currentStoryboardKey = null;
    if (dom.videoModal) dom.videoModal.classList.remove('active');
    if (dom.modalVideo) {
      dom.modalVideo.pause();
      dom.modalVideo.removeAttribute('src');
      dom.modalVideo.load();
    }
    if (dom.modalLoadingPoster) {
      dom.modalLoadingPoster.style.display = 'none';
      dom.modalLoadingPoster.src = '';
    }
    if (dom.modalTimelinePreviewVideo) {
      dom.modalTimelinePreviewVideo.pause();
      dom.modalTimelinePreviewVideo.removeAttribute('src');
      dom.modalTimelinePreviewVideo.load();
    }
    if (dom.modalIframe) dom.modalIframe.src = '';
    if (typeof document !== 'undefined' && document.body) document.body.style.overflow = '';
  }

  const moduleExports = {
    init,
    open: openVideoModal,
    openVideoModal,
    close: closeModal,
    closeModal,
    togglePlayerMode,
    enableIframeMode,
    getActiveFilteredVideos,
    getCurrentVideoIndex,
    getAuthorVideosList,
    getAuthorVideoIndex,
    playNextAuthorVideo,
    playPrevAuthorVideo,
    playNextVideo,
    playPrevVideo,
    getEffectiveVideoUsername,
    authorPlaylists
  };

  g.ArchivebateVideoModal = moduleExports;
  if (!g.openVideoModal) g.openVideoModal = openVideoModal;
  if (!g.closeModal) g.closeModal = closeModal;
  if (!g.togglePlayerMode) g.togglePlayerMode = togglePlayerMode;
  if (!g.enableIframeMode) g.enableIframeMode = enableIframeMode;
  if (!g.getActiveFilteredVideos) g.getActiveFilteredVideos = getActiveFilteredVideos;
  if (!g.getCurrentVideoIndex) g.getCurrentVideoIndex = getCurrentVideoIndex;
  if (!g.playNextVideo) g.playNextVideo = playNextVideo;
  if (!g.playPrevVideo) g.playPrevVideo = playPrevVideo;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = moduleExports;
  }
})(typeof window !== 'undefined' ? window : globalThis);
