"""Regression: old unfinished revisions can never replace a newer completed catalog."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


def add_source_run(service, revision, source="archivebate"):
    with service._lock:
        service._get_conn().execute(
            "INSERT OR REPLACE INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(?, ?, 2, 1, 1, 0, 0, NULL, NULL, ?)",
            (revision, source, time.time()),
        )


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "r17", "username": "old"}], revision=17, complete=True)
    service.import_items([{"id": "r20", "username": "newer"}], revision=20, complete=True)
    service.import_items([{"id": "r23", "username": "newest"}], revision=23, complete=True)

    # Reproduce the broken on-disk shape: an older revision was activated later and therefore has
    # a newer updated_at. Reopening the service must still select revision 23 by revision order.
    with service._lock:
        conn = service._get_conn()
        conn.execute("UPDATE revisions SET is_active = 0")
        conn.execute("UPDATE revisions SET is_active = 1, updated_at = ? WHERE revision = 17", (time.time() + 1000,))
    service.close()

    service = catalog.CatalogService(db)
    assert service.get_active_revision() == 23, service.get_active_revision()
    with service._lock:
        active_rows = service._get_conn().execute(
            "SELECT revision FROM revisions WHERE is_active = 1 ORDER BY revision"
        ).fetchall()
    assert [int(r["revision"]) for r in active_rows] == [23], active_rows

    # A stale unfinished revision below the completed floor must never be resumed.
    service.import_items([{"id": "r18", "username": "stale"}], revision=18, complete=False)
    add_source_run(service, 18)
    assert service.get_resumable_revision() is None

    # Even if a stale worker somehow finishes, publication is refused and revision 23 stays live.
    assert service.publish_revision(18) is False
    assert service.get_active_revision() == 23

    # The same monotonic rule applies to import_items(..., complete=True).
    service.import_items([{"id": "r19", "username": "stale2"}], revision=19, complete=True)
    assert service.get_active_revision() == 23

    # A genuinely newer partial revision remains resumable.
    service.import_items([{"id": "r24", "username": "future"}], revision=24, complete=False)
    add_source_run(service, 24)
    assert service.get_resumable_revision() == 24
    service.close()

print("PASS: catalog publication and resume are monotonic by revision")
