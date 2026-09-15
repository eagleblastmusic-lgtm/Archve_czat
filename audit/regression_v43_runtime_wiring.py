from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

browser = (ROOT / "browser_server.py").read_text(encoding="utf-8")
desktop = (ROOT / "desktop_app.py").read_text(encoding="utf-8")
runtime = (ROOT / "runtime_app.py").read_text(encoding="utf-8")
launcher = (ROOT / "URUCHOM_W_PRZEGLADARCE.bat").read_text(encoding="utf-8")

assert 'uvicorn.run("runtime_app:app"' in browser
assert 'uvicorn.run("runtime_app:app"' in desktop
assert 'from fast_grouped_feed_v2 import install' in runtime
assert 'RUNTIME_ID = "v4.3-fast2"' in runtime
assert '/api/runtime/v43' in runtime
assert 'X-Archivebate-Runtime' in runtime
assert 'Get-NetTCPConnection -State Listen -LocalPort 8000' in launcher
assert '/api/runtime/v43' in launcher
assert 'v4.3-fast2' in launcher

print('PASS V4.3 RUNTIME WIRING')
