from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

browser = (ROOT / "browser_server.py").read_text(encoding="utf-8")
desktop = (ROOT / "desktop_app.py").read_text(encoding="utf-8")
runtime = (ROOT / "runtime_app.py").read_text(encoding="utf-8")
launcher = (ROOT / "URUCHOM_W_PRZEGLADARCE.bat").read_text(encoding="utf-8")
quick = (ROOT / "fast_storyboard_quick.py").read_text(encoding="utf-8")
coordinator = (ROOT / "static" / "v43-timeline-fallback-v7.js").read_text(encoding="utf-8")

assert 'uvicorn.run("runtime_app:app"' in browser
assert 'uvicorn.run("runtime_app:app"' in desktop
assert 'from fast_grouped_feed_v2 import install' in runtime
assert 'import fast_storyboard_quick as _quick_storyboard' in runtime
assert '_quick_storyboard.QUICK_FRAME_COUNT = 8' in runtime
assert '_quick_storyboard.QUICK_PARALLELISM = 2' in runtime
assert '_quick_storyboard.QUICK_MIN_SUCCESS = 4' in runtime
assert '_quick_storyboard.install()' in runtime
assert 'parallel_quick_storyboard' in runtime  # compatibility marker
assert 'playback_safe_quick_storyboard' in runtime
assert 'timeline_coordinator_version' in runtime
assert 'media_seek_fallback' in runtime
assert 'RUNTIME_ID = "v4.3-fast2"' in runtime
assert '/api/runtime/v43/storyboard/quick' in runtime
assert '/api/runtime/v43/storyboard/stats' in runtime
assert 'X-Archivebate-Runtime' in runtime
assert 'v43-timeline-fallback-v7.js?v=7' in runtime

assert '_build_variant_playback_safe' in quick
assert 'wait_status' in quick
assert 'exact_preempts_quick' in quick
assert '_v43_playback_safe_quick_installed' in quick
assert 'kind="quick"' in quick
assert '_preempt_active_for_target =' not in quick, (
    'V4.3 must keep the baseline exact-target preemption function; QUICK may not be protected from it'
)

assert 'coordinatedRequestSegment' in coordinator
assert 'ensureInteractiveCoarse' in coordinator
assert 'COARSE_EXACT_DEFER_MS = 3500' in coordinator
assert 'source_video_id: latestVideoId' in coordinator

assert 'Get-NetTCPConnection -State Listen -LocalPort 8000' in launcher
assert '/api/runtime/v43' in launcher
assert 'v4.3-fast2' in launcher

print('PASS V4.3 RUNTIME WIRING + COARSE-FIRST V7 TIMELINE')