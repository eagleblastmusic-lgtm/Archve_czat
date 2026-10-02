"""Exercise the composed runtime through its real FastAPI routes.

Run in an isolated checkout: importing ``runtime_app`` initializes the local
catalog/store singletons under the checkout's data directory.
"""
from unittest.mock import patch
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
import runtime_app


client = TestClient(runtime_app.app)

marker = client.get("/api/runtime/v43")
assert marker.status_code == 200, marker.text
assert marker.headers.get("x-archivebate-runtime") == runtime_app.RUNTIME_ID
payload = marker.json()
assert payload["runtime"] == runtime_app.RUNTIME_ID
assert payload["grouped_fast_path_v2"] is True
assert payload["timeline_coordinator_version"] == 452
assert payload["next_video_prefetch"] is True

home = client.get("/")
assert home.status_code == 200
assert home.headers.get("x-archivebate-runtime") == runtime_app.RUNTIME_ID
for script in (
    runtime_app._V452_QOS_SCRIPT,
    runtime_app._V452_NEXT_PREFETCH_SCRIPT,
    runtime_app._LAZY_THUMB_RESILIENCE_SCRIPT,
    runtime_app._V43_TIMELINE_SCRIPT,
):
    assert script in home.text, f"runtime HTML injection missing {script}"

watch = client.get("/watch/runtime-smoke")
assert watch.status_code == 200
assert watch.headers.get("x-archivebate-runtime") == runtime_app.RUNTIME_ID
assert runtime_app._V43_TIMELINE_SCRIPT in watch.text
assert "previewVideo && streamUrl" not in watch.text, "secondary full-resolution watch stream must stay removed"

# The browser must be able to execute the final transformed watch script.
# Hashing the original static file would still block the runtime player.
import re
import hashlib
import base64
for response in (home, watch, runtime_app._original_versioned_html(str(ROOT / "static" / "watch.html"))):
    html = response.text if hasattr(response, "text") else response.body.decode("utf-8")
    csp = response.headers["Content-Security-Policy"]
    script_policy = next(part for part in csp.split(";") if part.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_policy and "'unsafe-eval'" not in script_policy
    for script in re.findall(r"<script\s*>(.*?)</script\s*>", html, re.S | re.I):
        if script.strip():
            expected = "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
            assert expected in script_policy, "served inline player script is blocked by CSP"
assert re.search(r"<script\s*>(.*?)</script", watch.text, re.S), "watch fixture must cover its inline player"

# Verify the API method and local mutation gate at the actual router boundary.
# Only the authorized POST is stubbed beyond the boundary; no provider is called.
forced_get = client.get("/api/video/details?id=runtime-smoke&force_refresh=true")
assert forced_get.status_code == 405, forced_get.text
blocked_post = client.post("/api/video/details/refresh?id=runtime-smoke", json={})
assert blocked_post.status_code == 403, blocked_post.text
with patch.object(main, "_fetch_details_singleflight", return_value={
    "direct_url": "https://fixture.invalid/runtime-smoke.mp4",
    "source": "archivebate",
}):
    refreshed = client.post(
        "/api/video/details/refresh?id=runtime-smoke",
        json={},
        headers={"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN},
    )
assert refreshed.status_code == 200, refreshed.text
assert refreshed.json()["availability"] == "available"

diagnostics = client.get("/api/diagnostics")
assert diagnostics.status_code == 200
assert diagnostics.json()["build"]["sqlite"]
catalog_sqlite = diagnostics.json()["catalog"]["sqlite"]
assert catalog_sqlite["sqlite_version"] == diagnostics.json()["build"]["sqlite"]
assert "active_readers" in catalog_sqlite and "wal_bytes" in catalog_sqlite
assert "last_passive_checkpoint" in catalog_sqlite

# Desktop diagnostics read the window's DOM through the existing WebView API.
# No native window is opened by this regression, including on Linux runners.
from unittest.mock import MagicMock
with patch.dict(sys.modules, {"webview": MagicMock()}):
    import desktop_app
window = MagicMock()
window.evaluate_js.return_value = {"video_id": "fixture", "frame_time": "460", "sprite_visible": True}
desktop_app.install_timeline_diagnostics(runtime_app.app, window)
desktop_snapshot = client.get('/api/runtime/desktop/timeline')
assert desktop_snapshot.status_code == 200 and desktop_snapshot.json()['frame_time'] == '460'
script = window.evaluate_js.call_args.args[0]
assert 'modalTimelineSprite' in script and 'ArchivebateYouTubeStoryboard?.stats()' in script
assert '.src' not in script and 'cookie' not in script and 'localStorage' not in script
calls_before = window.evaluate_js.call_count
assert client.get('/api/runtime/desktop/timeline', headers={'Host':'foreign.invalid'}).status_code == 403
assert window.evaluate_js.call_count == calls_before

client.close()
print("PASS: actual runtime marker, HTML injection, POST-only refresh gate, and SQLite diagnostics")
