"""Regression: sparse-page migration repairs only the absolute newest revision and never cascades."""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    now = time.time()
    with service._lock:
        conn = service._get_conn()
        for rev in (21, 22, 23):
            conn.execute(
                "INSERT INTO revisions(revision, created_at, updated_at, complete, is_active, failed, video_count, error) "
                "VALUES(?, ?, ?, ?, ?, 0, ?, NULL)",
                (rev, now, now, 1 if rev < 23 else 0, 1 if rev == 22 else 0, rev * 10),
            )
        # Older fallback revisions deliberately carry ambiguous legacy Camwhores endings.
        for rev in (21, 22):
            conn.execute(
                "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
                "VALUES(?, 'camwhores', 2, 1, 30, 1, 0, NULL, NULL, ?)",
                (rev, now),
            )
            conn.execute(
                "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
                "VALUES(?, 'archivebate', 1001, 1000, 36000, 1, 0, NULL, 'source_page_limit:empty:1001', ?)",
                (rev, now),
            )
        # Latest revision is already incomplete because Camwhores was reopened, but Archivebate
        # still has an old one-empty-page EOF that must be reopened in the same revision.
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(23, 'camwhores', 2, 1, 33, 0, 0, NULL, 'legacy_sparse_recheck', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(23, 'archivebate', 589, 588, 21168, 1, 0, NULL, 'empty_page', ?)",
            (now,),
        )
    service.close()

    service = catalog.CatalogService(db)
    with service._lock:
        conn = service._get_conn()
        revisions = {int(r['revision']): r for r in conn.execute(
            "SELECT revision, complete, is_active FROM revisions WHERE revision IN (21,22,23)"
        ).fetchall()}
        latest_ab = conn.execute(
            "SELECT complete, end_reason FROM source_runs WHERE revision=23 AND source='archivebate'"
        ).fetchone()
    assert bool(revisions[22]['complete']) is True, dict(revisions[22])
    assert bool(revisions[21]['complete']) is True, dict(revisions[21])
    assert bool(revisions[23]['complete']) is False, dict(revisions[23])
    assert bool(latest_ab['complete']) is False and latest_ab['end_reason'] == 'legacy_sparse_recheck', dict(latest_ab)
    assert service.get_active_revision() == 22
    assert service.get_resumable_revision() == 23
    service.close()

    # A second startup must be idempotent: it cannot peel back revision 22 or 21.
    service = catalog.CatalogService(db)
    assert service.get_active_revision() == 22
    assert service.get_resumable_revision() == 23
    with service._lock:
        rows = service._get_conn().execute(
            "SELECT revision, complete FROM revisions WHERE revision IN (21,22) ORDER BY revision"
        ).fetchall()
    assert all(bool(r['complete']) for r in rows), [dict(r) for r in rows]
    service.close()

print("PASS: sparse migration is latest-only, idempotent, and repairs both sources in the newest revision")
