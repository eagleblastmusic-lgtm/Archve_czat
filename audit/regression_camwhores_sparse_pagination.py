"""Regression: Camwhores isolated empty pages are gaps, not end-of-catalog."""
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


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "cw_1", "username": "first", "source": "camwhores"}],
        revision=5,
        complete=True,
    )
    with service._lock:
        service._get_conn().execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(5, 'camwhores', 2, 1, 1, 1, 0, NULL, 'empty_page', ?)",
            (time.time(),),
        )
    service.close()

    # Startup migration reopens the ambiguous legacy Camwhores EOF in place.
    service = catalog.CatalogService(db)
    assert service.get_resumable_revision() == 5

    calls = []
    def fetch(page):
        calls.append(page)
        if page == 2:
            return []
        if page == 3:
            return [{"id": "cw_3", "username": "later", "source": "camwhores"}]
        if 4 <= page <= 8:
            return []
        raise AssertionError(f"unexpected Camwhores page {page}")

    rev = service.build_revision_background({"camwhores": fetch}, force=False)
    assert rev == 5, rev
    wait(service)
    assert calls == [2, 3, 4, 5, 6, 7, 8], calls
    result = service.query_page(revision=5, source="only-camwhores")
    assert result["catalog_complete"] is True, result
    assert result["video_count"] == 2, result
    assert result["catalog_limited"] is False, result
    with service._lock:
        run = service._get_conn().execute(
            "SELECT cursor, pages_scanned, complete, failed, end_reason FROM source_runs "
            "WHERE revision = 5 AND source = 'camwhores'"
        ).fetchone()
    assert int(run["cursor"]) == 9, dict(run)
    assert int(run["pages_scanned"]) == 8, dict(run)
    assert bool(run["complete"]) and not bool(run["failed"]), dict(run)
    assert run["end_reason"] == "consecutive_empty_pages:5:8", dict(run)
    service.close()

# Cached seeding follows the same sparse-page rule and never trusts one has_more=false.
with tempfile.TemporaryDirectory() as tmp:
    cache_dir = Path(tmp) / "cache"
    cache_dir.mkdir()
    old_cache_dir = catalog.FEED_CACHE_DIR
    catalog.FEED_CACHE_DIR = cache_dir
    try:
        now = time.time()
        def write_page(page, items, has_more=False):
            (cache_dir / f"raw_v1_camwhores_{page}.json").write_text(
                json.dumps({"fetched_at": now, "items": items, "has_more": has_more}),
                encoding="utf-8",
            )

        write_page(1, [{"id": "cw_a", "username": "a", "source": "camwhores"}])
        write_page(2, [])
        write_page(3, [{"id": "cw_b", "username": "b", "source": "camwhores"}])

        service = catalog.CatalogService(Path(tmp) / "cache-test.db")
        items, next_page, pages, ended = service._read_cached_source_pages("camwhores")
        assert len(items) == 2 and next_page == 4 and pages == 3 and ended is False, (items, next_page, pages, ended)

        for page in range(4, 9):
            write_page(page, [])
        items, next_page, pages, ended = service._read_cached_source_pages("camwhores")
        assert len(items) == 2 and next_page == 9 and pages == 8 and ended is True, (len(items), next_page, pages, ended)
        service.close()
    finally:
        catalog.FEED_CACHE_DIR = old_cache_dir

print("PASS: Camwhores sparse pagination survives isolated empty pages in live and cached indexing")
