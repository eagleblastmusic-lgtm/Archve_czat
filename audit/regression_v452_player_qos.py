"""Offline V4.5.2 contracts for player-first storyboard QoS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

runtime = (ROOT / "runtime_app.py").read_text(encoding="utf-8")
qos = (ROOT / "player_qos_runtime.py").read_text(encoding="utf-8")
story = (ROOT / "storyboard_service.py").read_text(encoding="utf-8")
quick = (ROOT / "fast_storyboard_quick.py").read_text(encoding="utf-8")
watch = (ROOT / "static" / "watch.html").read_text(encoding="utf-8")
client = (ROOT / "static" / "v452-player-qos.js").read_text(encoding="utf-8")
timeline = (ROOT / "static" / "youtube-storyboard.js").read_text(encoding="utf-8")
fallback = (ROOT / "static" / "v43-timeline-fallback-v7.js").read_text(encoding="utf-8")
live = (ROOT / "static" / "v43-live-report.js").read_text(encoding="utf-8")

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

# Storyboard traffic ownership and browser -> runtime QoS endpoints.
assert 'owner=storyboard' in qos or 'params["owner"] = "storyboard"' in qos
assert 'priority=low' in qos or 'params["priority"] = "low"' in qos
assert '/api/runtime/v452/playback/status' in qos
assert '/api/runtime/v452/storyboard/protect' in qos
assert '/api/runtime/v452/storyboard/cancel' in qos
assert '/api/runtime/v452/qos' in qos

# /watch must not retain the second full-resolution preview media path at runtime.
assert 'previewVideo.src = streamUrl' in watch
assert '_remove_watch_aux_stream' in runtime
assert 'const warmWatch = () =>' in runtime
assert 'watch_aux_media_seek_removed' in runtime
assert 'v452-player-qos.js?v=452' in runtime

# Client player telemetry.
assert 'droppedVideoFrames' in client
assert 'corruptedVideoFrames' in client
assert 'stall_duration_ms' in client
assert 'video_width' in client and 'display_width' in client

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
