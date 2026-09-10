import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config

# 1. Anonymous startup: no embedded fallback.
with tempfile.TemporaryDirectory() as td, \
     patch.dict(os.environ, {}, clear=True), \
     patch.object(config, "ENV_FILE", Path(td) / ".env.local"), \
     patch.object(config, "LOCAL_CREDENTIALS_FILE", Path(td) / "credentials.local.json"):
    assert config.get_archivebate_credentials() == ("", "")
print("PASS audit-fix 1: no-config credentials fail closed")

# 2. UserStorage persistence failure rolls memory back and propagates the error.
import storage as storage_mod
with tempfile.TemporaryDirectory() as td, \
     patch.object(storage_mod, "STORE_FILE", str(Path(td) / "user_store.json")):
    test_store = storage_mod.UserStorage()
    with patch.object(storage_mod, "atomic_write_json", side_effect=OSError("disk full")):
        try:
            test_store.add_favorite({"id": "audit-1", "username": "fixture"})
            raise AssertionError("persistence failure was acknowledged")
        except OSError:
            pass
    assert test_store.get_favorites() == []
print("PASS audit-fix 2: persistence failure is not acknowledged and memory rolls back")

# 3. Canonical fallback identity is stable across Python hash seeds/processes.
from catalog_service import CatalogService, canonical_identity_key, MAX_PAGES_PER_SOURCE
fixture = {"source": "archivebate", "title": "no id/url fixture", "username": "fixture"}
key = canonical_identity_key(fixture)
assert key.startswith("archivebate:hash:") and len(key.rsplit(":", 1)[-1]) == 64
code = "from catalog_service import canonical_identity_key; print(canonical_identity_key(" + repr(fixture) + "))"
env1 = dict(os.environ, PYTHONHASHSEED="1")
env2 = dict(os.environ, PYTHONHASHSEED="2")
k1 = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env1, text=True).strip()
k2 = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env2, text=True).strip()
assert k1 == k2 == key
print("PASS audit-fix 3: fallback catalog identity is cross-process stable")

# 4. Failed revision is never projected as complete/no-error.
with tempfile.TemporaryDirectory() as td:
    svc = CatalogService(Path(td) / "catalog.db")
    svc.import_items([{"id": "1", "source": "archivebate", "title": "x"}], revision=1)
    svc.mark_revision_failed(1, '{"archivebate":"forced"}')
    data = svc.query_page(revision=1)
    assert data["catalog_complete"] is False
    assert data["complete"] is False
    assert data["catalog_state"] == "failed"
    assert data["retryable"] is True
    assert data["source_error"].get("archivebate") == "forced"
    svc.close()
print("PASS audit-fix 4: failed catalog revision is explicit")

# 5. Safety page cap is a failure/truncation state, not verified end.
with tempfile.TemporaryDirectory() as td:
    svc = CatalogService(Path(td) / "cap.db")
    import catalog_service as catalog_mod
    old = dict(catalog_mod.MAX_PAGES_PER_SOURCE)
    catalog_mod.MAX_PAGES_PER_SOURCE["archivebate"] = 2
    try:
        svc.build_revision_background({"archivebate": lambda page: [{"id": str(page), "source": "archivebate"}]}, force=True)
        svc._indexing_thread.join(10)
        stats = svc.get_revision_stats(1)
        assert stats["failed"] is True and stats["complete"] is False, stats
        page = svc.query_page(revision=1)
        assert page["catalog_state"] == "failed" and page["retryable"] is True
    finally:
        catalog_mod.MAX_PAGES_PER_SOURCE.clear(); catalog_mod.MAX_PAGES_PER_SOURCE.update(old)
        svc.close()
print("PASS audit-fix 5: catalog hard cap cannot publish complete")

# 6. Main account outcome is typed and async GETs use offload/coalescing helper.
import main
main.session.email = "configured@example.invalid"
main.session.password = "configured"
main.session.is_logged_in = False
with patch.object(main.session, "login", return_value=False):
    outcome = main.sync_account_data()
assert outcome["success"] is False and outcome["status"] == "auth_failed"
import inspect
assert "await _ensure_account_synced()" in inspect.getsource(main.get_account_summary)
assert "asyncio.to_thread(sync_account_data)" in inspect.getsource(main._ensure_account_synced)
print("PASS audit-fix 6: account sync exposes failure and async GET path offloads")

# 7. Remote favorite failure is not reported as fully successful.
with patch.object(main.storage, "toggle_favorite", return_value=True), \
     patch.object(main.scraper, "toggle_remote_save", return_value=False), \
     patch.object(main, "invalidate_feed_cache", return_value=None):
    main.session.email = "configured@example.invalid"; main.session.password = "configured"
    fav = main.toggle_favorite({"id": "42"})
assert fav["local_committed"] is True and fav["remote_synced"] is False
assert fav["success"] is False and fav["sync_state"] == "remote_failed"
print("PASS audit-fix 7: favorite remote failure is explicit")

# 8. No launcher contains port-authorized force termination.
for rel in ("desktop_app.py", "run.py", "start.bat", "URUCHOM_PROGRAM.bat", "Uruchom_Desktop.bat"):
    text = (ROOT / rel).read_text(encoding="utf-8").lower()
    assert "taskkill" not in text, rel
assert "pywebview" in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
print("PASS audit-fix 8: launch paths are non-destructive and desktop dependency is declared")

# 9. Legacy checked-in result files cannot masquerade as current evidence.
assert not (ROOT / "audit/frontend_results.json").exists()
assert not (ROOT / "audit/performance_results.json").exists()
assert (ROOT / "audit/historical/frontend_results.legacy.json").exists()
assert (ROOT / "audit/historical/performance_results.legacy.json").exists()
print("PASS audit-fix 9: stale diagnostics segregated from release evidence")
