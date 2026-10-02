import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog_service import CatalogService


# Keep the fixture out of the live data directory even when this test is run
# directly from the source checkout.  The process-specific path also prevents
# two isolated runs from sharing a database.
_FIXTURE_ROOT = ROOT / "audit" / f"isolated_catalog_read_{os.getpid()}"
_FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)


class _FixedTemporaryDirectory:
    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.path = _FIXTURE_ROOT
        self.path.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        return False


tempfile.TemporaryDirectory = _FixedTemporaryDirectory


def sample(i):
    return {
        "id": str(i),
        "username": f"user{i % 7}",
        "source": "archivebate",
        "date": "1 day ago",
        "poster": f"https://example.invalid/{i}.jpg",
        "url": f"https://example.invalid/v/{i}",
    }


with tempfile.TemporaryDirectory() as td:
    service = CatalogService(Path(td) / "catalog.db")
    service.import_items([sample(i) for i in range(80)], revision=1, complete=True)

    before_diag = service.operational_diagnostics()
    assert before_diag["sqlite_version"]
    assert before_diag["file_backed"] is True
    assert before_diag["wal_bytes"] is None or before_diag["wal_bytes"] >= 0
    assert before_diag["active_readers"] == 0
    assert "last_passive_checkpoint" in before_diag
    with service._read_snapshot():
        assert service.operational_diagnostics()["active_readers"] == 1
    assert service.operational_diagnostics()["active_readers"] == 0

    entered = threading.Event()
    release = threading.Event()

    def hold_writer_lock():
        with service._lock:
            entered.set()
            release.wait(2.0)

    holder = threading.Thread(target=hold_writer_lock, daemon=True)
    holder.start()
    assert entered.wait(1.0), "writer-lock holder did not start"

    started = time.perf_counter()
    active = service.get_active_revision()
    result = service.query_page(page=1, page_size=20, revision=1)
    elapsed = time.perf_counter() - started

    release.set()
    holder.join(timeout=1.0)
    service.close()

    assert active == 1
    assert len(result["items"]) == 20
    assert elapsed < 1.0, f"catalog read waited behind writer lock: {elapsed:.3f}s"

print("PASS: file-backed catalog reads do not wait behind the indexer writer lock")
