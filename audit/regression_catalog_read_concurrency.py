import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog_service import CatalogService


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
