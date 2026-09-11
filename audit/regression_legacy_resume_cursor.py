"""Regression: a reopened legacy Archivebate revision must resume at page 1001, not page 1."""
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


# A legacy-cap revision is active because it used to be published. If an older migration path
# accidentally reset its source cursor to a small value, the resume path must repair it to 1001.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "legacy-item", "username": "legacy", "source": "archivebate"}],
        revision=21,
        complete=False,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute("UPDATE revisions SET is_active = 1, complete = 0, failed = 0 WHERE revision = 21")
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(21, 'archivebate', 37, 36, 1296, 0, 0, NULL, NULL, ?)",
            (time.time(),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)

    def fetch(page):
        calls.append(page)
        assert page == 1001, page
        return []

    rev = restarted.build_revision_background({"archivebate": fetch}, force=False)
    assert rev == 21, rev
    assert restarted._indexing_progress.get("resumed") is True
    assert restarted._indexing_progress.get("legacy_cursor_repaired") is True
    wait(restarted)
    assert calls == [1001], calls
    stats = restarted.get_revision_stats(21)
    assert stats["complete"] is True and stats["failed"] is False, stats
    restarted.close()


# A normal partial revision is not active and must keep its real durable cursor.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "partial-item", "username": "partial", "source": "archivebate"}],
        revision=22,
        complete=False,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(22, 'archivebate', 37, 36, 1296, 0, 0, NULL, NULL, ?)",
            (time.time(),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)

    def fetch_partial(page):
        calls.append(page)
        if page == 37:
            return [{"id": "page37", "username": "partial", "source": "archivebate"}]
        if page == 38:
            return []
        raise AssertionError(page)

    rev = restarted.build_revision_background({"archivebate": fetch_partial}, force=False)
    assert rev == 22, rev
    assert restarted._indexing_progress.get("legacy_cursor_repaired") is False
    wait(restarted)
    assert calls == [37, 38], calls
    restarted.close()

print("PASS: reopened legacy Archivebate cursor is repaired to 1001 without touching normal partial resumes")
