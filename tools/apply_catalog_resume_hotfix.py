from pathlib import Path
import re
import textwrap


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


catalog_path = Path("catalog_service.py")
catalog = catalog_path.read_text(encoding="utf-8")

catalog = replace_once(
    catalog,
    'MAX_PAGES_PER_SOURCE = {"archivebate": 1000, "camwhores": 500}',
    '# Emergency safety ceilings only. Normal completion is determined by a verified empty page,\n'
    '# not by reaching an arbitrary catalog-size limit. These values are intentionally far above\n'
    '# the old 1000/500 caps so large source catalogs can be indexed to their real end.\n'
    'MAX_PAGES_PER_SOURCE = {"archivebate": 1_000_000, "camwhores": 100_000}',
    "catalog safety limits",
)

build_pattern = re.compile(
    r"    def build_revision_background\(\n.*?(?=    def _run_indexing_worker\()",
    re.DOTALL,
)

new_build_block = '''    @staticmethod
    def _is_recoverable_page_limit_error(error_value: Any) -> bool:
        """Recognize revisions failed only because the pre-resume 1000-page cap was hit."""
        if not error_value:
            return False
        decoded = error_value
        if isinstance(error_value, str):
            try:
                decoded = json.loads(error_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return error_value.startswith("page_limit_exceeded:")
        if isinstance(decoded, dict) and decoded:
            return all(str(value).startswith("page_limit_exceeded:") for value in decoded.values())
        return False

    def get_resumable_revision(self) -> Optional[int]:
        """Return the newest durable unfinished revision that can safely continue after restart.

        A revision created only by raw-cache import has no source_runs and is therefore not treated
        as resumable; the normal bootstrap path can seed a fresh revision from those cache files.
        Legacy revisions failed solely by the old 1000-page guard are explicitly recoverable.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT r.revision, r.failed, r.error
                FROM revisions r
                WHERE r.complete = 0
                  AND EXISTS (SELECT 1 FROM source_runs s WHERE s.revision = r.revision)
                ORDER BY r.revision DESC
                """
            ).fetchall()
            for row in rows:
                if not bool(row["failed"]) or self._is_recoverable_page_limit_error(row["error"]):
                    return int(row["revision"])
        return None

    def build_revision_background(
        self,
        fetchers: Dict[str, Callable[[int], List[Dict[str, Any]]]],
        force: bool = False,
        enrich_fn: Optional[Callable] = None,
    ) -> int:
        """Start or resume durable background indexing, yielding to active playback.

        Non-forced startup first resumes the newest unfinished revision from source_runs. This makes
        cursor progress durable across process/PC restarts instead of depending on the six-hour raw
        cache window. A completed active revision is reused only when there is nothing to resume.
        """
        with self._lock:
            if self._indexing_thread and self._indexing_thread.is_alive():
                return self._indexing_progress.get("revision", 0)

            active = self.get_active_revision()
            resumable = None if force else self.get_resumable_revision()
            if resumable is None and not force and active is not None and self.is_revision_complete(active):
                return active

            now = time.time()
            conn = self._get_conn()
            resumed = resumable is not None

            if resumed:
                next_rev = int(resumable)
                rev_row = conn.execute(
                    "SELECT failed, error FROM revisions WHERE revision = ?",
                    (next_rev,),
                ).fetchone()
                if rev_row and bool(rev_row["failed"]):
                    if not self._is_recoverable_page_limit_error(rev_row["error"]):
                        raise RuntimeError(f"Revision {next_rev} is failed and not resumable")
                    conn.execute(
                        "UPDATE revisions SET failed = 0, error = NULL, updated_at = ? WHERE revision = ?",
                        (now, next_rev),
                    )

                # Retry any source that was interrupted mid-page. Completed sources stay completed.
                conn.execute(
                    "UPDATE source_runs SET failed = 0, error = NULL, updated_at = ? "
                    "WHERE revision = ? AND complete = 0",
                    (now, next_rev),
                )
                for src in fetchers:
                    conn.execute(
                        "INSERT OR IGNORE INTO source_runs("
                        "revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at"
                        ") VALUES(?, ?, 1, 0, 0, 0, 0, NULL, ?)",
                        (next_rev, src, now),
                    )
            else:
                next_rev = self.get_latest_revision_number() + 1
                conn.execute(
                    "INSERT OR REPLACE INTO revisions(revision, created_at, updated_at, complete, failed, video_count, error) "
                    "VALUES(?, ?, ?, 0, 0, 0, NULL)",
                    (next_rev, now, now),
                )
                for src in fetchers:
                    conn.execute(
                        "INSERT OR REPLACE INTO source_runs("
                        "revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at"
                        ") VALUES(?, ?, 1, 0, 0, 0, 0, NULL, ?)",
                        (next_rev, src, now),
                    )

                # On a genuinely new revision only, seed contiguous fresh raw pages so the initial
                # bootstrap can skip network work already persisted on disk.
                if not force:
                    for src in fetchers:
                        seeded, next_cursor, pages_scanned, source_complete = self._read_cached_source_pages(src)
                        if seeded:
                            self.import_items(seeded, revision=next_rev, complete=False, source=src)
                        conn.execute(
                            "UPDATE source_runs SET cursor = ?, pages_scanned = ?, items_found = ?, complete = ?, updated_at = ? "
                            "WHERE revision = ? AND source = ?",
                            (next_cursor, pages_scanned, len(seeded), int(source_complete), time.time(), next_rev, src),
                        )

            self._indexing_stop.clear()
            self._indexing_progress = {
                "is_indexing": True,
                "revision": next_rev,
                "resumed": resumed,
                "source_progress": {s: {"cursor": 1, "items": 0, "complete": False} for s in fetchers},
                "error": None,
            }
            for src in fetchers:
                row = conn.execute(
                    "SELECT cursor, items_found, complete FROM source_runs WHERE revision = ? AND source = ?",
                    (next_rev, src),
                ).fetchone()
                if row:
                    self._indexing_progress["source_progress"][src] = {
                        "cursor": int(row["cursor"] or 1),
                        "items": int(row["items_found"] or 0),
                        "complete": bool(row["complete"]),
                    }

            def worker():
                try:
                    self._run_indexing_worker(next_rev, fetchers)
                except Exception as exc:
                    with self._lock:
                        self.mark_revision_failed(next_rev, str(exc))
                        self._indexing_progress["is_indexing"] = False
                        self._indexing_progress["error"] = str(exc)

            self._indexing_thread = threading.Thread(
                target=worker,
                name=f"catalog-indexer-rev{next_rev}",
                daemon=True,
            )
            self._indexing_thread.start()
            return next_rev

'''

catalog, count = build_pattern.subn(new_build_block, catalog, count=1)
if count != 1:
    raise SystemExit(f"build_revision_background replacement: expected 1 match, got {count}")

catalog = replace_once(
    catalog,
    'error = f"page_limit_exceeded:{max_pages.get(source, 1000)}"',
    'error = f"page_safety_limit_exceeded:{max_pages.get(source, 1_000_000)}"',
    "runtime safety-limit error",
)
catalog = replace_once(
    catalog,
    'if page > max_pages.get(source, 1000):',
    'if page > max_pages.get(source, 1_000_000):',
    "runtime safety-limit fallback",
)
catalog = replace_once(
    catalog,
    'while page <= 1000:',
    'while page <= MAX_PAGES_PER_SOURCE.get(source, 1_000_000):',
    "raw-cache scan limit",
)

catalog_path.write_text(catalog, encoding="utf-8")

main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = replace_once(
    main,
    '''def _ensure_catalog_indexing(service) -> None:\n    """Schedule indexing and tie retry state to the actual inner worker outcome."""\n    if _published_catalog_revision(service) is not None:\n        return\n''',
    '''def _ensure_catalog_indexing(service) -> None:\n    """Schedule indexing and resume a durable unfinished revision when one exists."""\n    published = _published_catalog_revision(service)\n    resumable = service.get_resumable_revision() if hasattr(service, "get_resumable_revision") else None\n    if published is not None and resumable is None:\n        return\n''',
    "bootstrap early-return",
)
main = replace_once(
    main,
    '''            service.import_cached_raw_pages()\n            if _published_catalog_revision(service) is None:\n                revision = service.build_revision_background(_catalog_fetchers(), force=False)\n                inner = getattr(service, "_indexing_thread", None)\n                if inner and inner is not threading.current_thread():\n                    inner.join()\n''',
    '''            service.import_cached_raw_pages()\n            revision = service.build_revision_background(_catalog_fetchers(), force=False)\n            inner = getattr(service, "_indexing_thread", None)\n            if inner and inner is not threading.current_thread():\n                inner.join()\n''',
    "bootstrap resume invocation",
)
main_path.write_text(main, encoding="utf-8")

regression = r'''"""Regression: catalog indexing resumes durable cursors and is not truncated at page 1000."""
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


assert catalog.MAX_PAGES_PER_SOURCE["archivebate"] > 1000

# 1. A newer partial revision must resume from its persisted cursor even when an older complete
# revision exists. This models restarting the app/PC after already scanning >1000 pages.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "old", "username": "old"}], revision=6, complete=True)
    service.import_items([{"id": "partial", "username": "partial"}], revision=7, complete=False)
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at) "
            "VALUES(7, 'archivebate', 1001, 1000, 1, 0, 0, NULL, ?)",
            (time.time(),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)

    def fetch(page):
        calls.append(page)
        if page == 1001:
            return [{"id": "after_1000", "username": "resume"}]
        if page == 1002:
            return []
        raise AssertionError(f"unexpected page {page}")

    rev = restarted.build_revision_background({"archivebate": fetch}, force=False)
    assert rev == 7, rev
    assert restarted._indexing_progress.get("resumed") is True
    wait(restarted)
    result = restarted.query_page(revision=7)
    assert calls == [1001, 1002], calls
    assert result["catalog_complete"] is True, result
    assert result["video_count"] == 2, result
    # Once complete, a normal startup must reuse it rather than start another full scan.
    calls.clear()
    same = restarted.build_revision_background({"archivebate": fetch}, force=False)
    assert same == 7
    assert calls == []
    restarted.close()

# 2. A revision failed solely by the old page_limit_exceeded:1000 guard is recoverable and
# resumes at cursor 1001 instead of discarding already indexed work.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "legacy", "username": "legacy"}], revision=9, complete=False)
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at) "
            "VALUES(9, 'archivebate', 1001, 1000, 1, 0, 1, 'page_limit_exceeded:1000', ?)",
            (time.time(),),
        )
        conn.execute(
            "UPDATE revisions SET failed = 1, error = ?, is_active = 0 WHERE revision = 9",
            (json.dumps({"archivebate": "page_limit_exceeded:1000"}),),
        )
    service.close()

    calls = []
    restarted = catalog.CatalogService(db)
    assert restarted.get_resumable_revision() == 9

    def fetch_legacy(page):
        calls.append(page)
        assert page == 1001
        return []

    rev = restarted.build_revision_background({"archivebate": fetch_legacy}, force=False)
    assert rev == 9
    wait(restarted)
    stats = restarted.get_revision_stats(9)
    assert calls == [1001], calls
    assert stats["complete"] is True and stats["failed"] is False, stats
    restarted.close()

print("PASS: durable catalog resumes persisted cursors beyond page 1000 and recovers the legacy cap")
'''
Path("audit/regression_catalog_resume.py").write_text(regression, encoding="utf-8")

print("Applied durable catalog resume + real-end indexing hotfix")
