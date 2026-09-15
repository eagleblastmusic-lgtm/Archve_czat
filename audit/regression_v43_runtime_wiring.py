from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

browser = (ROOT / "browser_server.py").read_text(encoding="utf-8")
desktop = (ROOT / "desktop_app.py").read_text(encoding="utf-8")
runtime = (ROOT / "runtime_app.py").read_text(encoding="utf-8")
launcher = (ROOT / "URUCHOM_W_PRZEGLADARCE.bat").read_text(encoding="utf-8")
quick = (ROOT / "fast_storyboard_quick.py").read_text(encoding="utf-8")

assert 'uvicorn.run("runtime_app:app"' in browser
assert 'uvicorn.run("runtime_app:app"' in desktop
assert 'from fast_grouped_feed_v2 import install' in runtime
assert 'import fast_storyboard_quick as _quick_storyboard' in runtime
assert '_quick_storyboard.install()' in runtime
assert 'parallel_quick_storyboard' in runtime  # compatibility marker
assert 'playback_safe_quick_storyboard' in runtime
assert 'media_seek_fallback' in runtime
assert 'RUNTIME_ID = "v4.3-fast2"' in runtime
assert '/api/runtime/v43/storyboard/quick' in runtime
assert '/api/runtime/v43/storyboard/stats' in runtime
assert 'X-Archivebate-Runtime' in runtime

assert 'QUICK_FRAME_COUNT = 4' in quick
assert 'QUICK_PARALLELISM = 2' in quick
assert 'QUICK_MIN_SUCCESS = 3' in quick
assert '_build_variant_playback_safe' in quick
assert 'wait_status' in quick
assert 'exact_preempts_quick' in quick
assert '_v43_playback_safe_quick_installed' in quick
assert 'kind="quick"' in quick
assert '_preempt_active_for_target =' not in quick, (
    'V4.3 must keep the baseline exact-target preemption function; QUICK may not be protected from it'
)

assert 'Get-NetTCPConnection -State Listen -LocalPort 8000' in launcher
assert '/api/runtime/v43' in launcher
assert 'v4.3-fast2' in launcher

print('PASS V4.3 RUNTIME WIRING + PLAYBACK-SAFE QUICK STORYBOARD')
