from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


path = Path("catalog_service.py")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    "import threading\nimport time\nfrom pathlib import Path",
    "import threading\nimport time\nfrom contextlib import nullcontext\nfrom pathlib import Path",
    "import nullcontext",
)

text = replace_once(
    text,
    "        self._lock = threading.RLock()\n        self._conn: Optional[sqlite3.Connection] = None",
    "        self._lock = threading.RLock()\n        # Writer connection is serialized by _lock. File-backed catalog reads\n        # use a separate thread-local SQLite connection so WAL can actually\n        # provide concurrent readers while the background indexer is writing.\n        self._read_local = threading.local()\n        self._conn: Optional[sqlite3.Connection] = None",
    "read-local init",
)

text = replace_once(
    text,
    "            self._conn = conn\n        return self._conn\n\n    def _init_db(self):",
    "            self._conn = conn\n        return self._conn\n\n    def _get_read_conn(self) -> sqlite3.Connection:\n        \"\"\"Return a thread-local read connection for a file-backed WAL DB.\n\n        The indexer deliberately owns the writer connection and _lock. Using\n        that same lock for feed reads serialized the UI behind large import\n        transactions, causing 12s client timeouts even though committed rows\n        were already readable. A separate SQLite reader can observe the last\n        committed WAL snapshot immediately.\n        \"\"\"\n        if str(self.db_path) == \":memory:\":\n            return self._get_conn()\n        conn = getattr(self._read_local, \"conn\", None)\n        if conn is None:\n            conn = sqlite3.connect(\n                str(self.db_path),\n                timeout=1.0,\n                check_same_thread=False,\n                isolation_level=None,\n            )\n            conn.row_factory = sqlite3.Row\n            conn.execute(\"PRAGMA query_only = ON\")\n            conn.execute(\"PRAGMA busy_timeout = 1000\")\n            conn.execute(\"PRAGMA cache_size = -32000\")\n            conn.execute(\"PRAGMA temp_store = MEMORY\")\n            self._read_local.conn = conn\n        return conn\n\n    def _read_guard(self):\n        # In-memory SQLite databases cannot be shared by opening a second\n        # connection, so tests/in-memory callers retain the original lock.\n        return self._lock if str(self.db_path) == \":memory:\" else nullcontext()\n\n    def _init_db(self):",
    "read connection helper",
)

old_methods_start = text.index("    def get_active_revision(self) -> Optional[int]:")
old_methods_end = text.index("    def import_items(", old_methods_start)
new_methods = '''    def get_active_revision(self) -> Optional[int]:
        """Returns the best readable revision without waiting for the writer lock."""
        with self._read_guard():
            conn = self._get_conn() if str(self.db_path) == ":memory:" else self._get_read_conn()
            row = conn.execute("SELECT revision FROM revisions WHERE is_active = 1 AND failed = 0 LIMIT 1").fetchone()
            if row:
                return int(row["revision"])
            row_comp = conn.execute("SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 ORDER BY updated_at DESC, revision DESC LIMIT 1").fetchone()
            if row_comp:
                return int(row_comp["revision"])
            # W czasie budowania kolejnej rewizji nie pokazuj pustej, nowej
            # rewizji kosztem częściowego katalogu, który już ma dane.
            row_any = conn.execute(
                "SELECT revision FROM revisions WHERE failed = 0 ORDER BY video_count DESC, updated_at DESC, revision DESC LIMIT 1"
            ).fetchone()
            if not row_any:
                row_any = conn.execute("SELECT revision FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
            return int(row_any["revision"]) if row_any else None

    def get_latest_revision_number(self) -> int:
        with self._read_guard():
            conn = self._get_conn() if str(self.db_path) == ":memory:" else self._get_read_conn()
            row = conn.execute("SELECT MAX(revision) AS max_rev FROM revisions").fetchone()
            return int(row["max_rev"] or 0)

    def is_revision_complete(self, revision: int) -> bool:
        with self._read_guard():
            conn = self._get_conn() if str(self.db_path) == ":memory:" else self._get_read_conn()
            row = conn.execute("SELECT complete, failed FROM revisions WHERE revision = ?", (revision,)).fetchone()
            return bool(row and row["complete"] and not row["failed"])

    def get_revision_stats(self, revision: Optional[int] = None) -> Dict[str, Any]:
        rev = revision if revision is not None else self.get_active_revision()
        if rev is None:
            return {
                "revision": 0,
                "complete": False,
                "video_count": 0,
                "updated_at": 0.0,
                "indexing_progress": self._indexing_progress,
            }
        with self._read_guard():
            conn = self._get_conn() if str(self.db_path) == ":memory:" else self._get_read_conn()
            row = conn.execute("SELECT * FROM revisions WHERE revision = ?", (rev,)).fetchone()
            if not row:
                return {
                    "revision": rev,
                    "complete": False,
                    "video_count": 0,
                    "updated_at": 0.0,
                    "indexing_progress": self._indexing_progress,
                }
            return {
                "revision": int(row["revision"]),
                "complete": bool(row["complete"]),
                "video_count": int(row["video_count"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
                "failed": bool(row["failed"]),
                "error": row["error"],
                "state": "failed" if row["failed"] else ("complete" if row["complete"] else "building"),
                "indexing_progress": self._indexing_progress,
            }

'''
text = text[:old_methods_start] + new_methods + text[old_methods_end:]

text = replace_once(
    text,
    "        with self._lock:\n            conn = self._get_conn()\n            rev_info = conn.execute(\"SELECT * FROM revisions WHERE revision = ?\", (rev,)).fetchone()",
    "        # File-backed reads must not queue behind the background writer.\n        # WAL gives us a consistent committed snapshot on the dedicated reader.\n        with self._read_guard():\n            conn = self._get_conn() if str(self.db_path) == \":memory:\" else self._get_read_conn()\n            rev_info = conn.execute(\"SELECT * FROM revisions WHERE revision = ?\", (rev,)).fetchone()",
    "query_page read guard",
)

# Close the reader associated with the current thread as well as the writer.
text = replace_once(
    text,
    "    def close(self):\n        with self._lock:\n            if self._conn:\n                try:\n                    self._conn.close()\n                except Exception:\n                    pass\n                self._conn = None\n",
    "    def close(self):\n        reader = getattr(self._read_local, \"conn\", None)\n        if reader is not None:\n            try:\n                reader.close()\n            except Exception:\n                pass\n            self._read_local.conn = None\n        with self._lock:\n            if self._conn:\n                try:\n                    self._conn.close()\n                except Exception:\n                    pass\n                self._conn = None\n",
    "close reader",
)

path.write_text(text, encoding="utf-8")

# Regression: a file-backed read must stay responsive while another thread owns
# the application's writer lock. Old code waits for this lock and takes ~2s.
reg = Path("audit/regression_catalog_read_concurrency.py")
reg.write_text(r'''import tempfile
import threading
import time
from pathlib import Path

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
''', encoding="utf-8")

# Keep the concurrency regression in the normal CI lane.
ci = Path(".github/workflows/ci.yml")
ci_text = ci.read_text(encoding="utf-8")
needle = "      - name: Catalog bootstrap regression\n        run: python audit/regression_catalog_bootstrap.py\n"
insert = needle + "      - name: Catalog read concurrency regression\n        run: python audit/regression_catalog_read_concurrency.py\n"
if ci_text.count(needle) != 1:
    raise SystemExit("CI insertion point not unique")
ci.write_text(ci_text.replace(needle, insert, 1), encoding="utf-8")
