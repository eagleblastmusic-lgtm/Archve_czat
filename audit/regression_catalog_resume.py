"""Regression: catalog indexing resumes durable cursors and is not truncated at page 1000."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


def wait(service):
    deadline = time.monotonic() + 10
    while service._indexing_thread and service._indexing_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not (service._indexing_thread and service._indexing_thread.is_alive()), "indexer did not finish"


assert catalog.MAX_PAGES_PER_SOURCE["archivebate"] > 1000

# 1. A newer partial revision must resume from its persisted cursor even when an older complete
# revision exists. This models restarting the app/PC after already scanning >1000 pages.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "old", "username": "old"}], revision=6, complete=True)
    service.import_items([{"id": "partial", "username": "partial"}], revision=7, complete=False)
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at) "
            "VALUES(7, 'archivebate', 1001, 1000, 1, 0, 0, NULL, ?)",
            (time.time(),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)

    def fetch(page):
        calls.append(page)
        if page == 1001:
            return [{"id": "after_1000", "username": "resume"}]
        if 1002 <= page <= 1006:
            return []
        raise AssertionError(f"unexpected page {page}")

    rev = restarted.build_revision_background({"archivebate": fetch}, force=False)
    assert rev == 7, rev
    assert restarted._indexing_progress.get("resumed") is True
    wait(restarted)
    result = restarted.query_page(revision=7)
    assert calls == [1001, 1002, 1003, 1004, 1005, 1006], calls
    assert result["catalog_complete"] is True, result
    assert result["video_count"] == 2, result
    # Once complete, a normal startup must reuse it rather than start another full scan.
    calls.clear()
    same = restarted.build_revision_background({"archivebate": fetch}, force=False)
    assert same == 7
    assert calls == []
    restarted.close()

# 2. A revision failed solely by the old page_limit_exceeded:1000 guard is recoverable and
# resumes at cursor 1001 instead of discarding already indexed work.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "legacy", "username": "legacy"}], revision=9, complete=False)
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at) "
            "VALUES(9, 'archivebate', 1001, 1000, 1, 0, 1, 'page_limit_exceeded:1000', ?)",
            (time.time(),),
        )
        conn.execute(
            "UPDATE revisions SET failed = 1, error = ?, is_active = 0 WHERE revision = 9",
            (json.dumps({"archivebate": "page_limit_exceeded:1000"}),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)
    assert restarted.get_resumable_revision() == 9

    def fetch_legacy(page):
        calls.append(page)
        assert page == 1001
        return []

    rev = restarted.build_revision_background({"archivebate": fetch_legacy}, force=False)
    assert rev == 9
    wait(restarted)
    stats = restarted.get_revision_stats(9)
    assert calls == [1001, 1001, 1001, 1001], calls
    assert stats["complete"] is True and stats["failed"] is False, stats
    page = restarted.query_page(revision=9)
    assert page["catalog_limited"] is True, page
    assert page["limited_sources"]["archivebate"] == "source_page_limit:empty:1001", page
    restarted.close()

print("PASS: durable catalog resumes persisted cursors beyond page 1000 and recovers the legacy cap")
