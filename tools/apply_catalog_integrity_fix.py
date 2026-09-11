from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "catalog_service.py"
text = TARGET.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    'MAX_PAGES_PER_SOURCE = {"archivebate": 1_000_000, "camwhores": 100_000}\n',
    'MAX_PAGES_PER_SOURCE = {"archivebate": 1_000_000, "camwhores": 100_000}\n'
    'CAMWHORES_EMPTY_END_THRESHOLD = 5\n',
    "constant",
)

old_migration = '''                conn.execute(
                    """
                    UPDATE source_runs
                    SET end_reason = 'source_page_limit:empty:1001'
                    WHERE source = 'archivebate'
                      AND cursor = 1001
                      AND pages_scanned = 1000
                      AND complete = 1
                      AND failed = 0
                      AND end_reason = 'empty_page'
                    """
                )
                conn.execute("COMMIT")
'''
new_migration = '''                conn.execute(
                    """
                    UPDATE source_runs
                    SET end_reason = 'source_page_limit:empty:1001'
                    WHERE source = 'archivebate'
                      AND cursor = 1001
                      AND pages_scanned = 1000
                      AND complete = 1
                      AND failed = 0
                      AND end_reason = 'empty_page'
                    """
                )

                # Old builds treated the first empty Camwhores page (or a cached has_more=false)
                # as a definitive EOF. Real-world pagination contains holes (for example page 2
                # can be empty while page 3+ contains videos), so reopen only the newest completed
                # revision when it still carries one of those legacy ambiguous endings. The new
                # worker will verify the boundary with consecutive empty pages before republishing.
                newest_complete = conn.execute(
                    "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 "
                    "ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if newest_complete:
                    newest_revision = int(newest_complete["revision"])
                    legacy_camwhores = conn.execute(
                        """
                        SELECT 1 FROM source_runs
                        WHERE revision = ?
                          AND source = 'camwhores'
                          AND complete = 1
                          AND failed = 0
                          AND COALESCE(end_reason, '') IN ('', 'empty_page', 'cached_end')
                        LIMIT 1
                        """,
                        (newest_revision,),
                    ).fetchone()
                    if legacy_camwhores:
                        now = time.time()
                        conn.execute(
                            "UPDATE revisions SET complete = 0, is_active = 0, failed = 0, error = NULL, updated_at = ? "
                            "WHERE revision = ?",
                            (now, newest_revision),
                        )
                        conn.execute(
                            "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "
                            "end_reason = 'legacy_empty_recheck', updated_at = ? "
                            "WHERE revision = ? AND source = 'camwhores'",
                            (now, newest_revision),
                        )

                # Revision numbers are monotonic snapshots. Repair any stale is_active flag left
                # behind by the old resume bug: the newest completed successful revision is the
                # only published snapshot. An unfinished newer revision remains inactive while it
                # is resumed in the background.
                conn.execute("UPDATE revisions SET is_active = 0")
                best_complete = conn.execute(
                    "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 "
                    "ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if best_complete:
                    conn.execute(
                        "UPDATE revisions SET is_active = 1 WHERE revision = ?",
                        (int(best_complete["revision"]),),
                    )
                conn.execute("COMMIT")
'''
replace_once(old_migration, new_migration, "migration")

old_active = '''    def get_active_revision(self) -> Optional[int]:
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
'''
new_active = '''    def get_active_revision(self) -> Optional[int]:
        """Return the newest published snapshot, or the best partial revision if none exists."""
        with self._read_guard():
            conn = self._get_conn() if str(self.db_path) == ":memory:" else self._get_read_conn()
            # Revision numbers are the publication order. Never let a later updated_at timestamp
            # on an older resumed revision make it win over a newer completed snapshot.
            row_comp = conn.execute(
                "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 "
                "ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            if row_comp:
                return int(row_comp["revision"])
            # With no published snapshot, an explicitly active partial revision is still useful.
            row_active = conn.execute(
                "SELECT revision FROM revisions WHERE is_active = 1 AND failed = 0 "
                "ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            if row_active:
                return int(row_active["revision"])
            row_any = conn.execute(
                "SELECT revision FROM revisions WHERE failed = 0 "
                "ORDER BY video_count DESC, revision DESC LIMIT 1"
            ).fetchone()
            if not row_any:
                row_any = conn.execute("SELECT revision FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
            return int(row_any["revision"]) if row_any else None
'''
replace_once(old_active, new_active, "get_active_revision")

old_import_complete = '''                if complete:
                    conn.execute("UPDATE revisions SET is_active = 0")
                    conn.execute("UPDATE revisions SET complete = 1, is_active = 1, updated_at = ?, video_count = ? WHERE revision = ?", (now, cnt, revision))
                else:
                    conn.execute("UPDATE revisions SET updated_at = ?, video_count = ? WHERE revision = ?", (now, cnt, revision))
'''
new_import_complete = '''                if complete:
                    newer = conn.execute(
                        "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 AND revision > ? "
                        "ORDER BY revision DESC LIMIT 1",
                        (revision,),
                    ).fetchone()
                    if newer:
                        # Preserve the completed data, but an older snapshot can never become active.
                        conn.execute(
                            "UPDATE revisions SET complete = 1, is_active = 0, failed = 0, error = NULL, "
                            "updated_at = ?, video_count = ? WHERE revision = ?",
                            (now, cnt, revision),
                        )
                    else:
                        conn.execute("UPDATE revisions SET is_active = 0")
                        conn.execute(
                            "UPDATE revisions SET complete = 1, is_active = 1, failed = 0, error = NULL, "
                            "updated_at = ?, video_count = ? WHERE revision = ?",
                            (now, cnt, revision),
                        )
                else:
                    conn.execute("UPDATE revisions SET updated_at = ?, video_count = ? WHERE revision = ?", (now, cnt, revision))
'''
replace_once(old_import_complete, new_import_complete, "import complete guard")

old_publish = '''    def publish_revision(self, revision: int):
        """Atomically marks a revision as complete and active."""
        with self._lock:
            conn = self._get_conn()
            now = time.time()
            conn.execute("BEGIN IMMEDIATE")
            try:
                cnt = conn.execute("SELECT COUNT(*) AS total FROM catalog_items WHERE revision = ?", (revision,)).fetchone()["total"]
                conn.execute("UPDATE revisions SET is_active = 0")
                conn.execute(
                    "UPDATE revisions SET complete = 1, is_active = 1, failed = 0, updated_at = ?, video_count = ? WHERE revision = ?",
                    (now, cnt, revision),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
'''
new_publish = '''    def publish_revision(self, revision: int) -> bool:
        """Atomically publish a revision unless a newer successful snapshot already exists."""
        with self._lock:
            conn = self._get_conn()
            now = time.time()
            conn.execute("BEGIN IMMEDIATE")
            try:
                cnt = conn.execute("SELECT COUNT(*) AS total FROM catalog_items WHERE revision = ?", (revision,)).fetchone()["total"]
                newer = conn.execute(
                    "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 AND revision > ? "
                    "ORDER BY revision DESC LIMIT 1",
                    (revision,),
                ).fetchone()
                if newer:
                    # A stale worker may finish after a newer snapshot was already published.
                    # Keep its rows for diagnostics/history, but never roll the live feed backwards.
                    conn.execute(
                        "UPDATE revisions SET complete = 1, is_active = 0, failed = 0, error = NULL, "
                        "updated_at = ?, video_count = ? WHERE revision = ?",
                        (now, cnt, revision),
                    )
                    conn.execute("COMMIT")
                    return False
                conn.execute("UPDATE revisions SET is_active = 0")
                conn.execute(
                    "UPDATE revisions SET complete = 1, is_active = 1, failed = 0, error = NULL, updated_at = ?, video_count = ? WHERE revision = ?",
                    (now, cnt, revision),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise
'''
replace_once(old_publish, new_publish, "publish guard")

old_resume_query = '''            rows = conn.execute(
                """
                SELECT r.revision, r.failed, r.error
                FROM revisions r
                WHERE r.complete = 0
                  AND EXISTS (SELECT 1 FROM source_runs s WHERE s.revision = r.revision)
                ORDER BY r.revision DESC
                """
            ).fetchall()
'''
new_resume_query = '''            latest_completed = conn.execute(
                "SELECT MAX(revision) AS revision FROM revisions WHERE complete = 1 AND failed = 0"
            ).fetchone()
            completed_floor = int(latest_completed["revision"] or 0) if latest_completed else 0
            rows = conn.execute(
                """
                SELECT r.revision, r.failed, r.error
                FROM revisions r
                WHERE r.complete = 0
                  AND r.revision > ?
                  AND EXISTS (SELECT 1 FROM source_runs s WHERE s.revision = r.revision)
                ORDER BY r.revision DESC
                """,
                (completed_floor,),
            ).fetchall()
'''
replace_once(old_resume_query, new_resume_query, "resume floor")

old_reopen_call = '''            if not force:
                self._reopen_legacy_archivebate_cap_revision()
            active = self.get_active_revision()
'''
new_reopen_call = '''            if not force and "archivebate" in fetchers:
                self._reopen_legacy_archivebate_cap_revision()
            active = self.get_active_revision()
'''
replace_once(old_reopen_call, new_reopen_call, "source-aware legacy reopen")

old_worker_state = '''        last_signatures: Dict[str, Optional[str]] = {s: None for s in sources}
        repeated_signatures: Dict[str, int] = {s: 0 for s in sources}
'''
new_worker_state = '''        last_signatures: Dict[str, Optional[str]] = {s: None for s in sources}
        repeated_signatures: Dict[str, int] = {s: 0 for s in sources}
        consecutive_empty_pages: Dict[str, int] = {s: 0 for s in sources}
'''
replace_once(old_worker_state, new_worker_state, "empty streak state")

old_empty = '''                if not batch:
                    ended.add(source)
                    # Archivebate page 1001 is an upstream visibility boundary, not proof that the
                    # service has no older videos. Preserve that distinction even when the server
                    # answers HTTP 200 with an empty page instead of 5xx.
                    is_archivebate_limit = source == "archivebate" and page == 1001
                    reason = f"source_page_limit:empty:{page}" if is_archivebate_limit else "empty_page"
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET complete = 1, failed = 0, error = NULL, end_reason = ?, updated_at = ? "
                            "WHERE revision = ? AND source = ?",
                            (reason, time.time(), revision, source),
                        )
                        progress = {
                            "cursor": page,
                            "items": source_counts[source],
                            "complete": True,
                            "end_reason": reason,
                        }
                        if is_archivebate_limit:
                            progress.update({"limited": True, "limit_page": page - 1})
                        self._indexing_progress["source_progress"][source] = progress
                    continue

                # Some sites clamp out-of-range page numbers and return the same last page forever.
'''
new_empty = '''                if not batch:
                    if source == "camwhores":
                        # Camwhores can contain isolated empty HTTP-200 pages in the middle of the
                        # catalog. A single hole must not terminate indexing. Advance durably and
                        # require several consecutive empty pages before accepting a true end.
                        consecutive_empty_pages[source] += 1
                        last_signatures[source] = None
                        repeated_signatures[source] = 0
                        next_page = page + 1
                        cursors[source] = next_page
                        if consecutive_empty_pages[source] < CAMWHORES_EMPTY_END_THRESHOLD:
                            with self._lock:
                                conn = self._get_conn()
                                conn.execute(
                                    "UPDATE source_runs SET cursor = ?, pages_scanned = pages_scanned + 1, "
                                    "complete = 0, failed = 0, error = NULL, end_reason = NULL, updated_at = ? "
                                    "WHERE revision = ? AND source = ?",
                                    (next_page, time.time(), revision, source),
                                )
                                self._indexing_progress["source_progress"][source] = {
                                    "cursor": next_page,
                                    "items": source_counts[source],
                                    "complete": False,
                                    "empty_streak": consecutive_empty_pages[source],
                                }
                            continue

                        ended.add(source)
                        reason = f"consecutive_empty_pages:{CAMWHORES_EMPTY_END_THRESHOLD}:{page}"
                        with self._lock:
                            conn = self._get_conn()
                            conn.execute(
                                "UPDATE source_runs SET cursor = ?, pages_scanned = pages_scanned + 1, "
                                "complete = 1, failed = 0, error = NULL, end_reason = ?, updated_at = ? "
                                "WHERE revision = ? AND source = ?",
                                (next_page, reason, time.time(), revision, source),
                            )
                            self._indexing_progress["source_progress"][source] = {
                                "cursor": next_page,
                                "items": source_counts[source],
                                "complete": True,
                                "empty_streak": consecutive_empty_pages[source],
                                "end_reason": reason,
                            }
                        continue

                    ended.add(source)
                    # Archivebate page 1001 is an upstream visibility boundary, not proof that the
                    # service has no older videos. Preserve that distinction even when the server
                    # answers HTTP 200 with an empty page instead of 5xx.
                    is_archivebate_limit = source == "archivebate" and page == 1001
                    reason = f"source_page_limit:empty:{page}" if is_archivebate_limit else "empty_page"
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET complete = 1, failed = 0, error = NULL, end_reason = ?, updated_at = ? "
                            "WHERE revision = ? AND source = ?",
                            (reason, time.time(), revision, source),
                        )
                        progress = {
                            "cursor": page,
                            "items": source_counts[source],
                            "complete": True,
                            "end_reason": reason,
                        }
                        if is_archivebate_limit:
                            progress.update({"limited": True, "limit_page": page - 1})
                        self._indexing_progress["source_progress"][source] = progress
                    continue

                if source == "camwhores":
                    consecutive_empty_pages[source] = 0

                # Some sites clamp out-of-range page numbers and return the same last page forever.
'''
replace_once(old_empty, new_empty, "sparse Camwhores")

old_publish_worker = '''            if len(ended) == len(sources) and not errors:
                # All sources completed successfully: publish atomically!
                self.publish_revision(revision)
                self._indexing_progress["published_revision"] = revision
            elif errors:
'''
new_publish_worker = '''            if len(ended) == len(sources) and not errors:
                # All sources completed successfully. A stale worker is allowed to finish but is
                # never allowed to roll the live feed back over a newer completed revision.
                published = self.publish_revision(revision)
                if published:
                    self._indexing_progress["published_revision"] = revision
                else:
                    self._indexing_progress["superseded_revision"] = revision
                    self._indexing_progress["published_revision"] = self.get_active_revision()
            elif errors:
'''
replace_once(old_publish_worker, new_publish_worker, "worker publish result")

cache_pattern = re.compile(
    r'''    def _read_cached_source_pages\(self, source: str, max_age_seconds: float = 6 \* 3600\):\n.*?\n\n\n# Singleton instance''',
    re.DOTALL,
)
new_cache = '''    def _read_cached_source_pages(self, source: str, max_age_seconds: float = 6 * 3600):
        """Read contiguous raw source pages and return (items, next page, count, ended).

        Camwhores is sparse: isolated empty pages and has_more=false values are not reliable EOF
        signals. For that source we require five consecutive cached empty pages. A missing/old
        cache page always stops seeding without claiming completion so the live worker can resume.
        """
        cache_dir = Path(FEED_CACHE_DIR)
        if not cache_dir.exists():
            return [], 1, 0, False
        page = 1
        collected: List[Dict[str, Any]] = []
        pages_scanned = 0
        camwhores_empty_streak = 0
        while page <= MAX_PAGES_PER_SOURCE.get(source, 1_000_000):
            path = cache_dir / f"raw_v1_{source}_{page}.json"
            data, _ = read_json_cache(str(path))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                break
            try:
                fetched_at = float(data.get("fetched_at") or 0)
            except (TypeError, ValueError):
                fetched_at = 0.0
            if fetched_at and time.time() - fetched_at > max_age_seconds:
                break
            batch = data.get("items") or []
            collected.extend(item for item in batch if isinstance(item, dict))
            pages_scanned += 1
            has_more = data.get("has_more")
            page += 1

            if source == "camwhores":
                if batch:
                    camwhores_empty_streak = 0
                else:
                    camwhores_empty_streak += 1
                    if camwhores_empty_streak >= CAMWHORES_EMPTY_END_THRESHOLD:
                        return collected, page, pages_scanned, True
                # Do not trust one cached has_more=false for Camwhores; page 2 can be empty while
                # later pages contain videos. Continue only through actually present fresh files.
                continue

            if not batch or has_more is False:
                return collected, page, pages_scanned, True
        return collected, page, pages_scanned, False


# Singleton instance'''
text, cache_count = cache_pattern.subn(new_cache, text)
if cache_count != 1:
    raise SystemExit(f"cache reader: expected one match, found {cache_count}")

TARGET.write_text(text, encoding="utf-8")

revision_test = r'''"""Regression: old unfinished revisions can never replace a newer completed catalog."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


def add_source_run(service, revision, source="archivebate"):
    with service._lock:
        service._get_conn().execute(
            "INSERT OR REPLACE INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(?, ?, 2, 1, 1, 0, 0, NULL, NULL, ?)",
            (revision, source, time.time()),
        )


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items([{"id": "r17", "username": "old"}], revision=17, complete=True)
    service.import_items([{"id": "r20", "username": "newer"}], revision=20, complete=True)
    service.import_items([{"id": "r23", "username": "newest"}], revision=23, complete=True)

    # Reproduce the broken on-disk shape: an older revision was activated later and therefore has
    # a newer updated_at. Reopening the service must still select revision 23 by revision order.
    with service._lock:
        conn = service._get_conn()
        conn.execute("UPDATE revisions SET is_active = 0")
        conn.execute("UPDATE revisions SET is_active = 1, updated_at = ? WHERE revision = 17", (time.time() + 1000,))
    service.close()

    service = catalog.CatalogService(db)
    assert service.get_active_revision() == 23, service.get_active_revision()
    with service._lock:
        active_rows = service._get_conn().execute(
            "SELECT revision FROM revisions WHERE is_active = 1 ORDER BY revision"
        ).fetchall()
    assert [int(r["revision"]) for r in active_rows] == [23], active_rows

    # A stale unfinished revision below the completed floor must never be resumed.
    service.import_items([{"id": "r18", "username": "stale"}], revision=18, complete=False)
    add_source_run(service, 18)
    assert service.get_resumable_revision() is None

    # Even if a stale worker somehow finishes, publication is refused and revision 23 stays live.
    assert service.publish_revision(18) is False
    assert service.get_active_revision() == 23

    # The same monotonic rule applies to import_items(..., complete=True).
    service.import_items([{"id": "r19", "username": "stale2"}], revision=19, complete=True)
    assert service.get_active_revision() == 23

    # A genuinely newer partial revision remains resumable.
    service.import_items([{"id": "r24", "username": "future"}], revision=24, complete=False)
    add_source_run(service, 24)
    assert service.get_resumable_revision() == 24
    service.close()

print("PASS: catalog publication and resume are monotonic by revision")
'''
(ROOT / "audit" / "regression_catalog_revision_monotonicity.py").write_text(revision_test, encoding="utf-8")

camwhores_test = r'''"""Regression: Camwhores isolated empty pages are gaps, not end-of-catalog."""
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
'''
(ROOT / "audit" / "regression_camwhores_sparse_pagination.py").write_text(camwhores_test, encoding="utf-8")

print("Applied catalog integrity + sparse Camwhores fix")
