import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ARCHIVEBATE_USER_STORE", str(ROOT / "audit" / f"v42_{os.getpid()}_store.json"))
os.environ.setdefault("ARCHIVEBATE_CATALOG_DB", str(ROOT / "audit" / f"v42_{os.getpid()}_catalog.db"))

from fastapi.testclient import TestClient
import cache_store
import main

assert cache_store.is_safe_remote_url("http://127.0.0.1:9999/internal") is False
assert cache_store.is_safe_remote_url("http://localhost:9999/internal") is False
assert cache_store.is_safe_remote_url("http://user:pass@example.com/file") is False
print("PASS v4.2 security 1: local/private and credential-bearing URLs are rejected")

client = TestClient(main.app)
foreign = client.get("/api/status", headers={"host": "attacker.invalid"})
assert foreign.status_code == 403, foreign.text
print("PASS v4.2 security 2: foreign Host is rejected on GET")
local = client.get("/api/status", headers={"host": "testserver"})
assert local.status_code == 200, local.text
print("PASS v4.2 security 3: trusted local/test Host still works")
stream_local = client.get("/api/video/stream", params={"url": "http://127.0.0.1:65534/internal"}, headers={"host": "testserver"})
assert stream_local.status_code == 400, stream_local.text
print("PASS v4.2 security 4: stream proxy cannot target localhost")
with patch.object(main, "_fetch_details_singleflight") as fetch_details:
    details_get = client.get("/api/video/details", params={"id": "fixture", "force_refresh": "true"}, headers={"host": "testserver"})
    assert details_get.status_code == 405, details_get.text
    fetch_details.assert_not_called()
print("PASS v4.2 security 5: force-refresh GET has no provider/cache side effect")
refresh_without_token = client.post("/api/video/details/refresh", params={"id": "fixture"}, json={}, headers={"host": "testserver"})
assert refresh_without_token.status_code == 403, refresh_without_token.text
print("PASS v4.2 security 6: refresh POST requires mutation token")
with patch.object(main, "_fetch_details_singleflight", return_value={"id": "fixture", "source": "archivebate"}):
    refresh_with_token = client.post("/api/video/details/refresh", params={"id": "fixture"}, json={}, headers={"host": "testserver", "x-archivebate-mutation-token": main.LOCAL_MUTATION_TOKEN})
    assert refresh_with_token.status_code == 200, refresh_with_token.text
print("PASS v4.2 security 7: token-protected refresh POST remains functional")
with patch.object(main, "_published_catalog_revision", return_value=10), patch.object(main, "_catalog_fetchers", return_value={"archivebate": lambda page: []}), patch.object(main, "_available_catalog_revision", return_value=10), patch("catalog_service.catalog_service.build_revision_background", return_value=11) as rebuild:
    catalog_get = client.get("/api/feed", params={"page": 1, "force_refresh": "true"}, headers={"host": "testserver"})
    assert catalog_get.status_code == 405, catalog_get.text
    rebuild.assert_not_called()
print("PASS v4.2 security 8: catalog refresh cannot be triggered by GET")
print("PASS V4.2 security regression")
