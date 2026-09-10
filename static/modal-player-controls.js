/**
 * Archivebate - Modal Player Controls & Timeline Module
 * Obsługa elementów sterujących odtwarzacza w oknie modalnym:
 * timeline hover frame preview (GPU sprite / Camwhores storyboard),
 * buforowanie, scrub, skróty klawiszowe, timer bezczynności kontrolek.
 */
(function (global) {
  'use strict';

  const g = typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : globalThis);

  const stateProxy = new Proxy({}, {
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

  const domProxy = new Proxy({}, {
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

  const state = stateProxy;
  const dom = domProxy;

  const formatPlayerTime = (seconds) => {
    if (g.ArchivebatePlayerCore && typeof g.ArchivebatePlayerCore.formatTime === 'function') {
      return g.ArchivebatePlayerCore.formatTime(seconds);
    }
    const s = Math.floor(seconds || 0);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    const remM = m % 60;
    const remS = s % 60;
    if (h > 0) {
      return `${h}:${remM.toString().padStart(2, '0')}:${remS.toString().padStart(2, '0')}`;
    }
    return `${remM}:${remS.toString().padStart(2, '0')}`;
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

  let playNextVideo = () => (g.ArchivebateVideoModal ? g.ArchivebateVideoModal.playNextVideo() : undefined);
  let playPrevVideo = () => (g.ArchivebateVideoModal ? g.ArchivebateVideoModal.playPrevVideo() : undefined);

  function init(dependencies = {}) {
    if (dependencies.playNextVideo) playNextVideo = dependencies.playNextVideo;
    if (dependencies.playPrevVideo) playPrevVideo = dependencies.playPrevVideo;
  }

  let isDraggingModalTimeline = false;
  let modalIdleTimeout = null;

  function initModalPlayerControls() {
    const vid = dom.modalVideo;
    if (!vid) return;

    function toggleModalPlay() {
      if (vid.paused || vid.ended) {
        vid.play().catch(() => {});
      } else {
        vid.pause();
      }
    }

    vid.addEventListener('play', () => {
      if (dom.modalCtrlPlayBtn) dom.modalCtrlPlayBtn.innerHTML = '<i class="fa-solid fa-pause"></i>';
      if (dom.modalCenterPlay) dom.modalCenterPlay.style.display = 'none';
      resetModalIdleTimer();
    });

    vid.addEventListener('pause', () => {
      if (dom.modalCtrlPlayBtn) dom.modalCtrlPlayBtn.innerHTML = '<i class="fa-solid fa-play"></i>';
      if (dom.modalCenterPlay) dom.modalCenterPlay.style.display = 'flex';
      if (dom.modalControlsBar) dom.modalControlsBar.classList.remove('idle');
      clearTimeout(modalIdleTimeout);
    });

    if (dom.modalCenterPlay) dom.modalCenterPlay.addEventListener('click', toggleModalPlay);
    vid.addEventListener('click', toggleModalPlay);
    if (dom.modalCtrlPlayBtn) dom.modalCtrlPlayBtn.addEventListener('click', toggleModalPlay);

    if (dom.modalCtrlPrevVideoBtn) {
      dom.modalCtrlPrevVideoBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        playPrevVideo();
      });
    }
    if (dom.modalCtrlNextVideoBtn) {
      dom.modalCtrlNextVideoBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        playNextVideo();
      });
    }
    if (dom.modalNavPrevArrow) {
      dom.modalNavPrevArrow.addEventListener('click', (e) => {
        e.stopPropagation();
        playPrevVideo();
      });
    }
    if (dom.modalNavNextArrow) {
      dom.modalNavNextArrow.addEventListener('click', (e) => {
        e.stopPropagation();
        playNextVideo();
      });
    }

    if (dom.modalCtrlRewindBtn) {
      dom.modalCtrlRewindBtn.addEventListener('click', () => {
        vid.currentTime = Math.max(0, vid.currentTime - 10);
      });
    }
    if (dom.modalCtrlForwardBtn) {
      dom.modalCtrlForwardBtn.addEventListener('click', () => {
        vid.currentTime = Math.min(vid.duration || 0, vid.currentTime + 10);
      });
    }

    vid.addEventListener('timeupdate', () => {
      if (!isDraggingModalTimeline && vid.duration) {
        const percent = (vid.currentTime / vid.duration) * 100;
        if (dom.modalTimelineProgress) dom.modalTimelineProgress.style.width = `${percent}%`;
        if (dom.modalTimelineThumb) dom.modalTimelineThumb.style.left = `${percent}%`;
        if (dom.modalCtrlTimeDisplay) {
          dom.modalCtrlTimeDisplay.innerText = `${formatPlayerTime(vid.currentTime)} / ${formatPlayerTime(vid.duration)}`;
        }
      }
    });

    vid.addEventListener('progress', () => {
      if (vid.duration && vid.buffered.length > 0) {
        const bufferedEnd = vid.buffered.end(vid.buffered.length - 1);
        const bufferPercent = (bufferedEnd / vid.duration) * 100;
        if (dom.modalTimelineBuffer) dom.modalTimelineBuffer.style.width = `${bufferPercent}%`;
      }
    });

    // TIMELINE HOVER FRAME PREVIEW — compositor/GPU path, max 1 update per ekran frame.
    let updateTimelinePreview = () => {};
    let modalPreviewRaf = 0;
    let modalPreviewClientX = 0;

    if (dom.modalTimelineContainer) {
      const renderTimelinePreview = (clientX) => {
        const rect = dom.modalTimelineContainer.getBoundingClientRect();
        if (!rect.width) return;
        const rawX = clientX - rect.left;
        const pos = Math.max(0, Math.min(1, rawX / rect.width));
        const tooltipX = rect.width <= 168 ? rect.width / 2 : Math.max(84, Math.min(rect.width - 84, rawX));
        const totalDur = (Number.isFinite(vid.duration) && vid.duration > 0) ? vid.duration : (parseDurationToSeconds(state.currentVideoDetails?.duration) || 0);
        const targetTime = pos * totalDur;

        if (dom.modalTimelineTooltip) {
          dom.modalTimelineTooltip.style.setProperty('--timeline-preview-x', `${tooltipX}px`);
          dom.modalTimelineTooltip.style.display = 'flex';
        }
        if (dom.modalTimelineTimeText) {
          const text = formatPlayerTime(targetTime);
          if (dom.modalTimelineTimeText.innerText !== text) dom.modalTimelineTimeText.innerText = text;
        }

        // 1. Camwhores: 15 klatek ze storyboardu CDN (0ms)
        if (state.currentTimelinePrefix) {
          const count = state.currentTimelineCount || 15;
          const frameIdx = Math.min(count, Math.max(1, Math.round(pos * (count - 1)) + 1));
          if (dom.modalTimelinePreviewImg) {
            const src = `${state.currentTimelinePrefix}${frameIdx}.jpg`;
            if (dom.modalTimelinePreviewImg.src !== new URL(src, location.href).href) dom.modalTimelinePreviewImg.src = src;
            dom.modalTimelinePreviewImg.style.display = 'block';
          }
          if (dom.modalTimelinePreviewVideo) dom.modalTimelinePreviewVideo.style.display = 'none';
          if (dom.modalTimelinePreviewStatus) dom.modalTimelinePreviewStatus.style.display = 'none';
          if (dom.modalTimelineSprite && g.ArchivebateYouTubeStoryboard) g.ArchivebateYouTubeStoryboard.clearFrame(dom.modalTimelineSprite);
          return;
        }

        // 2. Archivebate gęsty segment: dokładna sekunda (błąd <= 1s, 0ms, 0 zapytań strumienia)
        const segment = g.ArchivebateYouTubeStoryboard?.getSegmentFromCache?.(state.currentVideoId, totalDur, targetTime);
        if (segment && dom.modalTimelineSprite && g.ArchivebateYouTubeStoryboard) {
          if (dom.modalTimelinePreviewImg) dom.modalTimelinePreviewImg.style.display = 'none';
          if (dom.modalTimelinePreviewVideo) dom.modalTimelinePreviewVideo.style.display = 'none';
          if (dom.modalTimelinePreviewStatus) dom.modalTimelinePreviewStatus.style.display = 'none';
          g.ArchivebateYouTubeStoryboard.applyFrame(dom.modalTimelineSprite, segment, targetTime, { targetTime, duration: totalDur });
          return;
        }

        // Zgłoszenie zapotrzebowania na gęsty segment w tle
        if (g.ArchivebateYouTubeStoryboard && state.currentVideoId && totalDur > 0) {
          g.ArchivebateYouTubeStoryboard.requestSegment({
            videoId: state.currentVideoId,
            duration: totalDur,
            targetTime,
            signal: state.timelineHoverController?.signal,
            onReady: () => {
              if (dom.modalTimelineTooltip?.style.display !== 'none') {
                renderTimelinePreview(modalPreviewClientX);
              }
            }
          });
        }

        // 3. Fallback: rzadki storyboard (QUICK / FULL) z informacją o przygotowywaniu dokładnego podglądu
        if (state.timelineSpriteBoard && dom.modalTimelineSprite && g.ArchivebateYouTubeStoryboard) {
          if (dom.modalTimelinePreviewImg) dom.modalTimelinePreviewImg.style.display = 'none';
          if (dom.modalTimelinePreviewVideo) dom.modalTimelinePreviewVideo.style.display = 'none';
          if (dom.modalTimelinePreviewStatus) {
            dom.modalTimelinePreviewStatus.innerText = 'Przygotowywanie dokładnego podglądu...';
            dom.modalTimelinePreviewStatus.style.display = 'block';
          }
          const res = g.ArchivebateYouTubeStoryboard.applyFrame(dom.modalTimelineSprite, state.timelineSpriteBoard, targetTime, { targetTime, duration: totalDur });
          if (dom.modalTimelineTimeText && res?.frameTime !== undefined) {
            dom.modalTimelineTimeText.innerText = `${formatPlayerTime(targetTime)} • klatka ≈ ${formatPlayerTime(res.frameTime)}`;
          }
          return;
        }

        // 4. Fallback podczas przygotowywania: plakat wideo + precyzyjny znacznik czasu
        if (dom.modalTimelineSprite && g.ArchivebateYouTubeStoryboard) g.ArchivebateYouTubeStoryboard.clearFrame(dom.modalTimelineSprite);
        if (dom.modalTimelinePreviewVideo) dom.modalTimelinePreviewVideo.style.display = 'none';
        if (dom.modalTimelinePreviewStatus) {
          dom.modalTimelinePreviewStatus.innerText = 'Przygotowywanie podglądu...';
          dom.modalTimelinePreviewStatus.style.display = 'block';
        }
        const posterSrc = (state.currentVideoDetails?.thumbnail || state.currentVideoDetails?.poster || '').replace('.mp4', '.jpg');
        if (posterSrc && dom.modalTimelinePreviewImg) {
          if (dom.modalTimelinePreviewImg.src !== new URL(posterSrc, location.href).href) dom.modalTimelinePreviewImg.src = posterSrc;
          dom.modalTimelinePreviewImg.style.display = 'block';
        }
      };

      updateTimelinePreview = (e) => {
        modalPreviewClientX = e.clientX;
        if (!state.timelineHoverController || state.timelineHoverController.signal.aborted) {
          state.timelineHoverController = new AbortController();
        }
        if (modalPreviewRaf) return;
        modalPreviewRaf = requestAnimationFrame(() => {
          modalPreviewRaf = 0;
          renderTimelinePreview(modalPreviewClientX);
        });
      };

      dom.modalTimelineContainer.addEventListener('pointerenter', updateTimelinePreview, { passive: true });
      dom.modalTimelineContainer.addEventListener('pointermove', (e) => {
        if (isDraggingModalTimeline) seekModalFromEvent(e);
        else updateTimelinePreview(e);
      }, { passive: true });
      dom.modalTimelineContainer.addEventListener('pointerleave', () => {
        state.timelineHoverController?.abort();
        state.timelineHoverController = null;
        if (dom.modalTimelineTooltip) dom.modalTimelineTooltip.style.display = 'none';
        if (dom.modalTimelineSprite && g.ArchivebateYouTubeStoryboard) {
          g.ArchivebateYouTubeStoryboard.clearFrame(dom.modalTimelineSprite);
        }
        if (dom.modalTimelinePreviewVideo) {
          dom.modalTimelinePreviewVideo.style.display = 'none';
        }
        if (dom.modalTimelinePreviewImg) {
          dom.modalTimelinePreviewImg.style.display = 'none';
        }
        if (dom.modalTimelinePreviewStatus) {
          dom.modalTimelinePreviewStatus.style.display = 'none';
        }
      });
      dom.modalTimelineContainer.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        isDraggingModalTimeline = true;
        try { dom.modalTimelineContainer.setPointerCapture(e.pointerId); } catch (_) {}
        seekModalFromEvent(e);
      });
    }

    window.addEventListener('pointerup', () => {
      if (isDraggingModalTimeline) isDraggingModalTimeline = false;
    });
    window.addEventListener('pointercancel', () => {
      isDraggingModalTimeline = false;
    });

    function seekModalFromEvent(e) {
      if (!dom.modalTimelineContainer) return;
      const rect = dom.modalTimelineContainer.getBoundingClientRect();
      const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      if (vid.duration) {
        vid.currentTime = pos * vid.duration;
        if (dom.modalTimelineProgress) dom.modalTimelineProgress.style.width = `${pos * 100}%`;
        if (dom.modalTimelineThumb) dom.modalTimelineThumb.style.left = `${pos * 100}%`;
      }
      updateTimelinePreview(e);
    }

    const PlayerCore = g.ArchivebatePlayerCore;

    // Głośność
    if (PlayerCore && typeof PlayerCore.setupVolumeControls === 'function') {
      PlayerCore.setupVolumeControls(vid, dom.modalCtrlVolumeSlider, dom.modalCtrlVolumeBtn);
    }

    // Prędkość
    if (PlayerCore && typeof PlayerCore.setupSpeedToggle === 'function') {
      PlayerCore.setupSpeedToggle(vid, dom.modalCtrlSpeedBtn);
    }

    // PiP
    if (dom.modalCtrlPipBtn) {
      dom.modalCtrlPipBtn.addEventListener('click', async () => {
        if (document.pictureInPictureElement) {
          await document.exitPictureInPicture();
        } else if (document.pictureInPictureEnabled) {
          await vid.requestPictureInPicture();
        }
      });
    }

    // Fullscreen
    if (PlayerCore && typeof PlayerCore.setupFullscreenToggle === 'function') {
      PlayerCore.setupFullscreenToggle(dom.modalPlayerWrapper, dom.modalCtrlFullscreenBtn);
    }

    // Auto-hide controls
    const resetModalIdleTimer = (PlayerCore && typeof PlayerCore.setupIdleTimer === 'function')
      ? PlayerCore.setupIdleTimer(dom.modalPlayerWrapper, dom.modalControlsBar, vid)
      : (() => {});

    // Skróty klawiszowe w modalu
    window.addEventListener('keydown', (e) => {
      if (!dom.videoModal || !dom.videoModal.classList.contains('active')) return;

      // Ignoruj tylko gdy użytkownik pisze w polach tekstowych
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
        if (document.activeElement.type === 'text' || document.activeElement.type === 'search') {
          return;
        }
      }

      const code = e.code;
      const key = e.key;

      if (code === 'Space' || key === ' ') {
        e.preventDefault();
        toggleModalPlay();
      } else if (code === 'ArrowUp' || key === 'ArrowUp' || key === 'Up') {
        e.preventDefault();
        playNextVideo();
      } else if (code === 'ArrowDown' || key === 'ArrowDown' || key === 'Down') {
        e.preventDefault();
        playPrevVideo();
      } else if (code === 'ArrowRight' || key === 'ArrowRight' || key === 'Right') {
        e.preventDefault();
        if (e.shiftKey || e.ctrlKey) {
          vid.currentTime = Math.min(vid.duration || 0, vid.currentTime + 10);
        } else {
          playNextVideo();
        }
      } else if (code === 'ArrowLeft' || key === 'ArrowLeft' || key === 'Left') {
        e.preventDefault();
        if (e.shiftKey || e.ctrlKey) {
          vid.currentTime = Math.max(0, vid.currentTime - 10);
        } else {
          playPrevVideo();
        }
      } else if (code === 'KeyF' || key === 'f' || key === 'F') {
        e.preventDefault();
        if (dom.modalCtrlFullscreenBtn) dom.modalCtrlFullscreenBtn.click();
      } else if (code === 'KeyM' || key === 'm' || key === 'M') {
        e.preventDefault();
        vid.muted = !vid.muted;
        if (PlayerCore && typeof PlayerCore.updateVolumeIcon === 'function') {
          PlayerCore.updateVolumeIcon(vid, dom.modalCtrlVolumeBtn);
        }
      }
    }, true);
  }

  const moduleExports = {
    init,
    initControls: initModalPlayerControls,
    initModalPlayerControls
  };

  g.ArchivebateModalPlayerControls = moduleExports;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = moduleExports;
  }
})(typeof window !== 'undefined' ? window : global);
