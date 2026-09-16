"""Offline V4.5.2 contracts for player-first storyboard QoS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

runtime = (ROOT / "runtime_app.py").read_text(encoding="utf-8")
qos = (ROOT / "player_qos_runtime.py").read_text(encoding="utf-8")
story = (ROOT / "storyboard_service.py").read_text(encoding="utf-8")
quick = (ROOT / "fast_storyboard_quick.py").read_text(encoding="utf-8")
watch = (ROOT / "static" / "watch.html").read_text(encoding="utf-8")
client = (ROOT / "static" / "v452-player-qos.js").read_text(encoding="utf-8")
player_core = (ROOT / "static" / "player-core.js").read_text(encoding="utf-8")
timeline = (ROOT / "static" / "youtube-storyboard.js").read_text(encoding="utf-8")
fallback = (ROOT / "static" / "v43-timeline-fallback-v7.js").read_text(encoding="utf-8")
live = (ROOT / "static" / "v43-live-report.js").read_text(encoding="utf-8")
next_prefetch = (ROOT / "static" / "v452-next-video-prefetch.js").read_text(encoding="utf-8")
launcher = (ROOT / "run.py").read_text(encoding="utf-8")

# Backend arbitration and cancellation.
assert 'FFMPEG_MAX_CONCURRENT = 2' in qos
assert '_playback_ffmpeg_slot = threading.BoundedSemaphore(1)' in qos
assert 'PLAYBACK_CRITICAL_BUFFER_SECONDS = 2.0' in qos
assert 'PLAYBACK_QUICK_BUFFER_SECONDS = 5.0' in qos
assert 'PLAYBACK_EXACT_BUFFER_SECONDS = 3.0' in qos
assert 'PLAYBACK_BACKGROUND_BUFFER_SECONDS = 8.0' in qos
assert 'def configure_playback_qos' in qos
assert 'def protect_playback' in qos
assert 'def hard_cancel' in qos
assert 'def _qos_blocks_new_process' in qos
assert '_storyboard._run_cancellable_process = run_cancellable_process' in qos
assert 'quick_module._cancel_requested = quick_cancel_requested' in qos
assert 'quick_module._reserve_quick_slot = quick_reserve' in qos
assert 'data["exact_preempts_quick"] = False' in qos

# Stale/startup QoS snapshots must remain JSON serializable. Returning inf here
# makes FastAPI/Starlette fail the protect/status endpoint with HTTP 500.
assert 'age = max(0.0, now - updated) if updated else 0.0' in qos
assert 'float("inf")' not in qos

# Storyboard traffic ownership and browser -> runtime QoS endpoints.
assert 'owner=storyboard' in qos or 'params["owner"] = "storyboard"' in qos
assert 'priority=low' in qos or 'params["priority"] = "low"' in qos
assert '/api/runtime/v452/playback/status' in qos
assert '/api/runtime/v452/storyboard/protect' in qos
assert '/api/runtime/v452/storyboard/cancel' in qos
assert '/api/runtime/v452/qos' in qos

# The default end-user launcher must never bypass the release runtime wiring.
assert 'uvicorn.run("runtime_app:app"' in launcher
assert 'uvicorn.run("main:app"' not in launcher

# /watch must not retain the second full-resolution preview media path at runtime.
assert 'previewVideo.src = streamUrl' in watch
assert '_remove_watch_aux_stream' in runtime
assert 'const warmWatch = () =>' in runtime
assert 'watch_aux_media_seek_removed' in runtime
assert 'v452-player-qos.js?v=452' in runtime

# Next-video startup warm-up must be injected by the release runtime. It resolves
# exactly one likely candidate after playback starts and must not inherit the
# current player generation AbortSignal, otherwise A -> B cancels B's warm-up.
assert 'v452-next-video-prefetch.js?v=452' in runtime
assert '"next_video_prefetch": True' in runtime
assert "event?.target?.id !== 'modalVideo'" in next_prefetch
assert 'prefetch.prefetchVideoDetails(candidateId)' in next_prefetch
assert 'signal:' not in next_prefetch
assert 'let activeWarm = null' in next_prefetch
assert 'active_candidate_id' in next_prefetch
# Real Chromium sessions may miss the document-level non-bubbling `playing`
# capture path, so a tiny watchdog must deterministically observe actual playback.
assert 'function maybeWarmFromPlayer' in next_prefetch
assert "setInterval?.(() =>" in next_prefetch
assert '}, 250);' in next_prefetch
assert 'lastObservedPlayingId' in next_prefetch
assert 'watchdog_active' in next_prefetch

# Client player telemetry.
assert 'droppedVideoFrames' in client
assert 'corruptedVideoFrames' in client
assert 'stall_duration_ms' in client
assert 'video_width' in client and 'display_width' in client

# Stationary interaction with the controls/timeline must keep the controls visible.
# Cold EXACT can legitimately take longer than the default 2.5 s idle timeout.
assert 'let controlsHovered = false' in player_core
assert "controls?.addEventListener?.('pointerenter', onControlsEnter" in player_core
assert "controls?.addEventListener?.('pointerleave', onControlsLeave" in player_core
assert '!video?.paused && !video?.ended && !controlsHovered' in player_core
assert "controls?.classList?.remove?.('idle')" in player_core

# Final V4.5.2 exact-timeline client: cached frames are free, uncached work is
# held behind an idle gate plus a second playback-health check before demand/POST.
assert 'const EXACT_BUFFER_SECONDS = 3.0' in timeline
assert 'const BACKGROUND_BUFFER_SECONDS = 8.0' in timeline
assert 'const QOS_RECHECK_MS = 180' in timeline
assert 'function playbackAllowsStoryboard' in timeline
assert 'async function waitForPlaybackBudget' in timeline
assert 'await waitForPlaybackBudget(videoId, EXACT_BUFFER_SECONDS' in timeline
assert 'if (!playbackAllowsStoryboard(videoId, EXACT_BUFFER_SECONDS))' in timeline
assert 'ensureTargetLease(videoId, signal);' in timeline
assert 'qos_blocked_starts' in timeline
assert 'qos_prewarm_skips' in timeline
assert 'player_qos_guard: true' in timeline
assert 'BACKGROUND_BUFFER_SECONDS' in timeline

# Hard lifecycle cancellation must release the session-level hover lease too.
# Otherwise the backend keeps a live demand lease until its 45 s TTL even after
# modal close/video switch, while all FFmpeg processes have already stopped.
assert 'function releaseTargetLease(videoId' in timeline
assert 'releaseTargetLease(videoId);' in timeline
assert "holder.signal?.removeEventListener?.('abort', holder.release)" in timeline
assert 'if (holder.url) releaseLease(holder.url);' in timeline
assert 'if (targetLeases.get(videoId) === holder) targetLeases.delete(videoId);' in timeline

# The legacy coarse coordinator may delegate to requestSegment; the final
# storyboard client now owns the authoritative second QoS gate, so that delegate
# cannot start uncached FFmpeg during critical playback.
assert 'originalRequestSegment' in fallback
assert 'media_seek_enabled: false' in fallback

# Live A/B diagnostics must expose both server arbitration and media quality.
assert "archivebate-v452-live-report/1" in live
assert '/api/runtime/v452/qos' in live
assert 'dropped_video_frames' in live
assert 'corrupted_video_frames' in live
assert 'stall_duration_ms' in live
assert 'video_width' in live and 'display_width' in live
assert 'ArchivebateV452Diagnostics' in live

print('PASS V4.5.2 PLAYER-FIRST QOS CONTRACTS')
