"""Regression: Archivebate isolated empty pages are gaps before the known page-1001 boundary."""
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
    calls = []

    def fetch(page):
        calls.append(page)
        if page == 1:
            return [{"id": "ab_1", "username": "first", "source": "archivebate"}]
        if page == 2:
            return []
        if page == 3:
            return [{"id": "ab_3", "username": "later", "source": "archivebate"}]
        if 4 <= page <= 8:
            return []
        raise AssertionError(f"unexpected Archivebate page {page}")

    rev = service.build_revision_background({"archivebate": fetch}, force=True)
    wait(service)
    assert calls == [1, 2, 3, 4, 5, 6, 7, 8], calls
    result = service.query_page(revision=rev, source="only-archivebate")
    assert result["catalog_complete"] is True, result
    assert result["video_count"] == 2, result
    assert result["catalog_limited"] is False, result
    with service._lock:
        run = service._get_conn().execute(
            "SELECT cursor, pages_scanned, complete, failed, end_reason FROM source_runs "
            "WHERE revision = ? AND source = 'archivebate'",
            (rev,),
        ).fetchone()
    assert int(run["cursor"]) == 9, dict(run)
    assert int(run["pages_scanned"]) == 8, dict(run)
    assert run["end_reason"] == "consecutive_empty_pages:5:8", dict(run)
    service.close()

# Cached Archivebate seeding follows the same sparse rule and leaves page 1001 to live probing.
with tempfile.TemporaryDirectory() as tmp:
    cache_dir = Path(tmp) / "cache"
    cache_dir.mkdir()
    old_cache_dir = catalog.FEED_CACHE_DIR
    catalog.FEED_CACHE_DIR = cache_dir
    try:
        now = time.time()
        def write_page(page, items, has_more=False):
            (cache_dir / f"raw_v1_archivebate_{page}.json").write_text(
                json.dumps({"fetched_at": now, "items": items, "has_more": has_more}),
                encoding="utf-8",
            )

        write_page(1, [{"id": "ab_a", "username": "a", "source": "archivebate"}])
        write_page(2, [])
        write_page(3, [{"id": "ab_b", "username": "b", "source": "archivebate"}])
        service = catalog.CatalogService(Path(tmp) / "cache-test.db")
        items, next_page, pages, ended = service._read_cached_source_pages("archivebate")
        assert len(items) == 2 and next_page == 4 and pages == 3 and ended is False, (len(items), next_page, pages, ended)
        service.close()
    finally:
        catalog.FEED_CACHE_DIR = old_cache_dir

print("PASS: Archivebate sparse pagination survives isolated empty pages")
