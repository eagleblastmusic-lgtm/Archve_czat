"""Regression: Archivebate pagination is not cut at 1000 and legacy false completion resumes."""
import tempfile
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog_service as catalog
from scraper import ArchivebateScraper


class DummyResponse:
    status_code = 200
    text = "<html><body>empty fixture</body></html>"

    def raise_for_status(self):
        return None


class DummyHttp:
    def __init__(self):
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(url)
        return DummyResponse()


class DummyArchiveSession:
    def __init__(self):
        self.session = DummyHttp()
        self.csrf_token = None

    def call_livewire(self, *args, **kwargs):
        return ""


# The single-page fetcher must actually contact Archivebate for page 1001.
dummy = DummyArchiveSession()
scraper = ArchivebateScraper(dummy)
assert scraper._fetch_single_ab_home_page(1001, strict=True) == []
assert dummy.session.calls == ["https://archivebate.com?page=1001"], dummy.session.calls

# The fallback home loader must also be able to ask for pages above 1000.
fallback_calls = []

def fallback_fetch(page, strict=False):
    fallback_calls.append(page)
    return []

scraper._fetch_single_ab_home_page = fallback_fetch
scraper.get_home_videos(page=51, source="only-archivebate", target_count=1)
assert fallback_calls and min(fallback_calls) >= 1001, fallback_calls[:5]


def wait(service):
    deadline = time.monotonic() + 10
    while service._indexing_thread and service._indexing_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not (service._indexing_thread and service._indexing_thread.is_alive()), "indexer did not finish"


# Simulate the exact false-complete state produced by the old local `p > 1000 -> []` guard.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "legacy", "username": "legacy"}], revision=21, complete=True)
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(21, 'archivebate', 1001, 1000, 36000, 1, 0, NULL, NULL, ?)",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(21, 'camwhores', 3, 2, 28, 1, 0, NULL, 'empty_page', ?)",
            (time.time(),),
        )
    service.close()

    restarted = catalog.CatalogService(db)
    ab_calls = []
    cw_calls = []
    repeated_page = [{"id": "after_1000", "username": "resume", "source": "archivebate"}]

    def fetch_ab(page):
        ab_calls.append(page)
        assert page in (1001, 1002, 1003), page
        return repeated_page

    def fetch_cw(page):
        cw_calls.append(page)
        raise AssertionError("completed Camwhores source must not be fetched")

    revision = restarted.build_revision_background(
        {"archivebate": fetch_ab, "camwhores": fetch_cw},
        force=False,
    )
    assert revision == 21, revision
    assert restarted._indexing_progress.get("resumed") is True
    wait(restarted)

    assert ab_calls == [1001, 1002, 1003], ab_calls
    assert cw_calls == [], cw_calls
    stats = restarted.get_revision_stats(21)
    assert stats["complete"] is True and stats["failed"] is False, stats

    with restarted._lock:
        row = restarted._get_conn().execute(
            "SELECT cursor, pages_scanned, complete, end_reason FROM source_runs "
            "WHERE revision = 21 AND source = 'archivebate'"
        ).fetchone()
        assert row["complete"] == 1
        assert str(row["end_reason"]).startswith("repeated_page:"), dict(row)

    # A new startup/build must now trust the explicit new end reason and not reopen rev 21 again.
    ab_calls.clear()
    same = restarted.build_revision_background(
        {"archivebate": fetch_ab, "camwhores": fetch_cw},
        force=False,
    )
    assert same == 21
    assert ab_calls == []
    restarted.close()

print("PASS: Archivebate scans beyond page 1000, repairs legacy false completion, and stops repeated-page clamps")
