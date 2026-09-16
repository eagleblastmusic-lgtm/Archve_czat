"""Offline V4.5.2 contracts for player-first storyboard QoS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

runtime = (ROOT / "runtime_app.py").read_text(encoding="utf-8")
qos = (ROOT / "player_qos_runtime.py").read_text(encoding="utf-8")
story = (ROOT / "storyboard_service.py").read_text(encoding="utf-8")
quick = (ROOT / "fast_storyboard_quick.py").read_text(encoding="utf-8")
watch = (ROOT / "static" / "watch.html").read_text(encoding="utf-8")
client = (ROOT / "static" / "v452-player-qos.js").read_text(encoding="utf-8")

assert 'FFMPEG_MAX_CONCURRENT = 2' in qos
assert '_playback_ffmpeg_slot = threading.BoundedSemaphore(1)' in qos
assert 'def configure_playback_qos' in qos
assert 'def protect_playback' in qos
assert 'def hard_cancel' in qos
assert 'def _qos_blocks_new_process' in qos
assert '_storyboard._run_cancellable_process = run_cancellable_process' in qos
assert 'quick_module._cancel_requested = quick_cancel_requested' in qos
assert 'quick_module._reserve_quick_slot = quick_reserve' in qos
assert 'data["exact_preempts_quick"] = False' in qos

assert 'owner=storyboard' in qos or 'params["owner"] = "storyboard"' in qos
assert 'priority=low' in qos or 'params["priority"] = "low"' in qos
assert '/api/runtime/v452/playback/status' in qos
assert '/api/runtime/v452/storyboard/protect' in qos
assert '/api/runtime/v452/storyboard/cancel' in qos

# The source file deliberately stays V4.2-compatible; runtime_app removes this
# legacy block before /watch HTML reaches the browser.
assert 'previewVideo.src = streamUrl' in watch
assert '_remove_watch_aux_stream' in runtime
assert 'const warmWatch = () =>' in runtime
assert 'v452-player-qos.js?v=452' in runtime
assert 'droppedVideoFrames' in client
assert 'stall_duration_ms' in client

print('PASS V4.5.2 PLAYER-FIRST QOS CONTRACTS')
