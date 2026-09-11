"""Regression: Archivebate page-1001 5xx is retried and exposed as a source limit."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


class FakeResponse:
    status_code = 500


class Fake500(Exception):
    def __init__(self, page=1001):
        super().__init__(
            f"500 Server Error: Internal Server Error for url: https://archivebate.com/?page={page}"
        )
        self.response = FakeResponse()


def wait(service):
    deadline = time.monotonic() + 10
    while service._indexing_thread and service._indexing_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not (service._indexing_thread and service._indexing_thread.is_alive()), "indexer did not finish"


# Upgrade path for the real state observed on Windows: a previous build persisted page 1001
# as a terminal HTTP-500 failure. The upgraded worker must resume that same revision, retry
# the boundary, and publish the already-indexed catalog with explicit limited-source metadata.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "ab-existing", "username": "legacy", "source": "archivebate"}],
        revision=23,
        complete=False,
        source="archivebate",
    )
    error_text = str(Fake500())
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "UPDATE revisions SET complete = 0, failed = 1, is_active = 0, error = ? WHERE revision = 23",
            (json.dumps({"archivebate": error_text}),),
        )
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(23, 'archivebate', 1001, 1000, 36000, 0, 1, ?, NULL, ?)",
            (error_text, time.time()),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)

    def persistent_boundary(page):
        calls.append(page)
        raise Fake500(page)

    rev = restarted.build_revision_background({"archivebate": persistent_boundary}, force=False)
    assert rev == 23, rev
    wait(restarted)
    assert calls == [1001, 1001, 1001, 1001], calls
    result = restarted.query_page(revision=23)
    assert result["catalog_complete"] is True, result
    assert result["catalog_limited"] is True, result
    assert result["limited_sources"]["archivebate"] == "source_http_limit:500:1001", result
    assert result["source_error"] == {}, result
    progress = restarted._indexing_progress["source_progress"]["archivebate"]
    assert progress["limited"] is True and progress["limit_page"] == 1000, progress
    restarted.close()


# A transient 500 must NOT be mistaken for a source limit. If a retry succeeds, indexing
# continues normally and can still finish at a real empty page.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "seed", "username": "seed", "source": "archivebate"}],
        revision=1,
        complete=False,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(1, 'archivebate', 1001, 1000, 1, 0, 0, NULL, NULL, ?)",
            (time.time(),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)
    page_1001_attempts = 0

    def transient(page):
        nonlocal_page = page
        calls.append(nonlocal_page)
        if page == 1001:
            count = sum(1 for p in calls if p == 1001)
            if count < 3:
                raise Fake500(page)
            return [{"id": "after-boundary", "username": "ok", "source": "archivebate"}]
        if page == 1002:
            return []
        raise AssertionError(page)

    rev = restarted.build_revision_background({"archivebate": transient}, force=False)
    assert rev == 1, rev
    wait(restarted)
    assert calls == [1001, 1001, 1001, 1002], calls
    result = restarted.query_page(revision=1)
    assert result["catalog_complete"] is True, result
    assert result["catalog_limited"] is False, result
    assert result["limited_sources"] == {}, result
    restarted.close()


home_stats = (Path(__file__).resolve().parents[1] / "static" / "home-stats.js").read_text(encoding="utf-8")
assert "Gotowe do limitu źródła" in home_stats
assert "data.catalog_limited" in home_stats


# The same upstream page-1001 boundary can answer HTTP 200 with no cards. It must be retried,
# then classified as limited rather than as a clean end of the service's history.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "seed-empty", "username": "seed", "source": "archivebate"}],
        revision=7,
        complete=False,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(7, 'archivebate', 1001, 1000, 36000, 0, 0, NULL, NULL, ?)",
            (time.time(),),
        )
    calls = []

    def empty_boundary(page):
        calls.append(page)
        assert page == 1001, page
        return []

    rev = service.build_revision_background({"archivebate": empty_boundary}, force=False)
    assert rev == 7, rev
    wait(service)
    assert calls == [1001, 1001, 1001, 1001], calls
    result = service.query_page(revision=7)
    assert result["catalog_complete"] is True, result
    assert result["catalog_limited"] is True, result
    assert result["limited_sources"]["archivebate"] == "source_page_limit:empty:1001", result
    progress = service._indexing_progress["source_progress"]["archivebate"]
    assert progress["limited"] is True and progress["limit_page"] == 1000, progress
    service.close()


# Existing databases that already stored the exact page-1001 empty-page shape are migrated
# in place, without re-indexing pages 1..1000.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "legacy-empty", "username": "legacy", "source": "archivebate"}],
        revision=21,
        complete=True,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(21, 'archivebate', 1001, 1000, 36000, 1, 0, NULL, 'empty_page', ?)",
            (time.time(),),
        )
    service.close()

    migrated = catalog.CatalogService(db)
    result = migrated.query_page(revision=21)
    assert result["catalog_complete"] is True, result
    assert result["catalog_limited"] is True, result
    assert result["limited_sources"]["archivebate"] == "source_page_limit:empty:1001", result
    migrated.close()

print("PASS: Archivebate page-1001 5xx/empty boundary is retried, classified as limited, and legacy empty-page state migrates in place")
