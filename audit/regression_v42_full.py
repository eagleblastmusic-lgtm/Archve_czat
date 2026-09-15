import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ARCHIVEBATE_USER_STORE", str(ROOT / "audit" / f"v42full_{os.getpid()}_store.json"))
os.environ.setdefault("ARCHIVEBATE_CATALOG_DB", str(ROOT / "audit" / f"v42full_{os.getpid()}_catalog.db"))

# Phase-1 security contract must stay green.
proc = subprocess.run([sys.executable, "audit/regression_security_v42.py"], cwd=ROOT)
assert proc.returncode == 0
print("PASS v4.2 full 1: phase-1 security contract")

import cache_store
assert cache_store.is_safe_remote_url("http://127.0.0.1:1", fresh=True) is False
assert cache_store.is_safe_remote_url("http://user:pass@example.com", fresh=True) is False
print("PASS v4.2 full 2: fresh outbound validation")

import main
from fastapi.testclient import TestClient
client = TestClient(main.app)
with patch.object(main, "sync_account_data", side_effect=AssertionError("GET triggered sync")):
    response = client.get("/api/account/summary", headers={"host": "testserver"})
    assert response.status_code == 200, response.text
print("PASS v4.2 full 3: account GET is read-only")

response = client.get("/api/status", headers={"host": "testserver"})
assert response.headers.get("x-content-type-options") == "nosniff"
assert response.headers.get("x-frame-options") == "DENY"
assert "frame-ancestors 'none'" in response.headers.get("content-security-policy", "")
print("PASS v4.2 full 4: security headers")

from client import ArchivebateSession
s = ArchivebateSession("a@example.invalid", "x")
clone = s.clone_for_background()
assert clone is not s and clone.session is not s.session
clone.session.cookies.set("clone-only", "1")
assert s.session.cookies.get("clone-only") is None
s.close(); clone.close()
print("PASS v4.2 full 5: background HTTP session isolation")

# Deep terminal/quarantine state is durable after the retry ceiling.
from deep_archivebate import DeepArchivebateService, PROFILE_MAX_RETRIES
from fetch_contract import FetchResult
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "deep.db"
    conn = sqlite3.connect(db)
    conn.executescript('''
        CREATE TABLE revisions(revision INTEGER PRIMARY KEY, complete INTEGER, failed INTEGER, is_active INTEGER, published_at REAL, updated_at REAL, created_at REAL, video_count INTEGER, error TEXT, published_hash TEXT);
        CREATE TABLE catalog_items(canonical_key TEXT, source TEXT, video_id TEXT, author TEXT, author_clean TEXT, published_at REAL, duration_seconds REAL, duration_str TEXT, poster TEXT, url TEXT, preview_video TEXT, title TEXT, platform TEXT, raw_json TEXT, revision INTEGER, PRIMARY KEY(revision, canonical_key));
        CREATE TABLE source_runs(revision INTEGER, source TEXT, cursor INTEGER, pages_scanned INTEGER, items_found INTEGER, complete INTEGER, failed INTEGER, error TEXT, end_reason TEXT, updated_at REAL, PRIMARY KEY(revision, source));
    ''')
    conn.close()
    svc = DeepArchivebateService(db, request_delay=0.01)
    now = __import__('time').time()
    c = svc._get_conn()
    c.execute("INSERT INTO archivebate_models(model_key,username,profile_url,discovered_from,priority,first_seen,updated_at,retry_count) VALUES('dead','dead','', 'test', 1, ?, ?, ?)", (now, now, PROFILE_MAX_RETRIES - 1))
    class Dead:
        def get_archivebate_model_videos_result(self, username, page=1):
            return FetchResult.error_result("permanent network failure", source="archivebate", page=page)
    assert svc.crawl_step(Dead()) is True
    row = c.execute("SELECT crawl_state,crawl_complete,retry_at FROM archivebate_models WHERE model_key='dead'").fetchone()
    assert row["crawl_state"] == "quarantined" and int(row["crawl_complete"]) == 1 and row["retry_at"] is None, dict(row)
    svc.close()
print("PASS v4.2 full 6: Deep retry ceiling quarantines terminal failures")

import storyboard_service
source = (ROOT / "storyboard_service.py").read_text(encoding="utf-8")
assert 'upgrade_status": "disabled"' in source
assert '_run_cancellable_process' in source and 'subprocess.Popen' in source
assert storyboard_service.runtime_stats()["auto_full_upgrade"] is False
print("PASS v4.2 full 7: segment-first storyboard and cancellable process contract")

import model_tags
# Source contract is asserted statically; no live provider request is made in CI.
model_source = (ROOT / "model_tags.py").read_text(encoding="utf-8")
assert '"gender": None' in model_source and 'model_tags.local.json' in model_source and 'model_tags.seed.json' in model_source
assert 'tags.add("Female")' not in (ROOT / "scraper.py").read_text(encoding="utf-8")
print("PASS v4.2 full 8: unknown gender is not silently classified Female")

for rel in ("start.bat", "URUCHOM_PROGRAM.bat", "Uruchom_Desktop.bat"):
    data = (ROOT / rel).read_text(encoding="utf-8").lower()
    assert "pip install" not in data, rel
assert (ROOT / "requirements.in").exists()
assert (ROOT / "requirements.lock.txt").exists()
print("PASS v4.2 full 9: runtime does not mutate dependencies and lockfile exists")

for rel in ("static/index.html", "static/watch.html"):
    html = (ROOT / rel).read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
print("PASS v4.2 full 10: Google Fonts removed from runtime")

ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
assert "data/model_tags.local.json" in ignored
assert (ROOT / "data/model_tags.seed.json").exists()
print("PASS v4.2 full 11: model-tag seed/runtime split")

ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
assert 'python-version: "3.14"' in ci and "requirements.lock.txt" in ci
print("PASS v4.2 full 12: Python 3.14 + locked dependency CI")

print("PASS ARCHIVEBITE V4.2 FULL HARDENING REGRESSION")
