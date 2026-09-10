"""Verifies that the durable catalog resumes from fresh raw pages."""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


raw = Path(__file__).with_name("catalog_fixture")
calls = []


def fetcher(page):
    calls.append(page)
    return []


with patch.object(catalog, "FEED_CACHE_DIR", raw):
    service = catalog.CatalogService(":memory:")
    service.import_cached_raw_pages()
    service.build_revision_background({"archivebate": fetcher}, force=False)
    deadline = time.monotonic() + 10
    while service._indexing_thread and service._indexing_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    result = service.query_page()
    assert result["catalog_complete"] is True
    assert result["video_count"] == 2
    assert calls == [2], calls
    service.close()

print("PASS: catalog bootstrap seeds fresh raw pages and resumes at the next source page")
