from pathlib import Path

path = Path("catalog_service.py")
text = path.read_text(encoding="utf-8")

old = 'CAMWHORES_EMPTY_END_THRESHOLD = 5\n'
new = 'CAMWHORES_EMPTY_END_THRESHOLD = 5\nARCHIVEBATE_EMPTY_END_THRESHOLD = 5\n'
if text.count(old) != 1:
    raise SystemExit(f"constant patch expected 1 match, found {text.count(old)}")
text = text.replace(old, new, 1)

start_marker = '                # Old builds treated the first empty Camwhores page (or a cached has_more=false)\n'
end_marker = '                # Revision numbers are monotonic snapshots. Repair any stale is_active flag left\n'
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("legacy sparse migration markers not found")

migration = '''                # Repair sparse-page endings only on the absolute newest revision. Older completed
                # snapshots are fallback history and must never be reopened one-by-one on subsequent
                # application starts. The latest revision may already be incomplete because one source
                # was reopened; in that case repair the other ambiguous source in the same revision.
                latest_revision_row = conn.execute(
                    "SELECT revision, complete, failed FROM revisions ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if latest_revision_row and not bool(latest_revision_row["failed"]):
                    latest_revision = int(latest_revision_row["revision"])
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
                        (latest_revision,),
                    ).fetchone()
                    legacy_archivebate = conn.execute(
                        """
                        SELECT 1 FROM source_runs
                        WHERE revision = ?
                          AND source = 'archivebate'
                          AND complete = 1
                          AND failed = 0
                          AND cursor < 1001
                          AND COALESCE(end_reason, '') IN ('', 'empty_page', 'cached_end')
                        LIMIT 1
                        """,
                        (latest_revision,),
                    ).fetchone()
                    if legacy_camwhores or legacy_archivebate:
                        now = time.time()
                        conn.execute(
                            "UPDATE revisions SET complete = 0, is_active = 0, failed = 0, error = NULL, updated_at = ? "
                            "WHERE revision = ?",
                            (now, latest_revision),
                        )
                        if legacy_camwhores:
                            conn.execute(
                                "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "
                                "end_reason = 'legacy_sparse_recheck', updated_at = ? "
                                "WHERE revision = ? AND source = 'camwhores'",
                                (now, latest_revision),
                            )
                        if legacy_archivebate:
                            conn.execute(
                                "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "
                                "end_reason = 'legacy_sparse_recheck', updated_at = ? "
                                "WHERE revision = ? AND source = 'archivebate'",
                                (now, latest_revision),
                            )

'''
text = text[:start] + migration + text[end:]

worker_anchor = text.find('    def _run_indexing_worker(')
start = text.find('                if not batch:\n', worker_anchor)
end = text.find('                # Some sites clamp out-of-range page numbers and return the same last page forever.\n', start)
if start < 0 or end < 0:
    raise SystemExit("worker empty-page block markers not found")

worker_block = '''                if not batch:
                    # Archivebate page 1001 is a known upstream visibility boundary. It is probed
                    # repeatedly above, then recorded as limited rather than a clean end-of-catalog.
                    if source == "archivebate" and page == 1001:
                        ended.add(source)
                        reason = f"source_page_limit:empty:{page}"
                        with self._lock:
                            conn = self._get_conn()
                            conn.execute(
                                "UPDATE source_runs SET complete = 1, failed = 0, error = NULL, end_reason = ?, updated_at = ? "
                                "WHERE revision = ? AND source = ?",
                                (reason, time.time(), revision, source),
                            )
                            self._indexing_progress["source_progress"][source] = {
                                "cursor": page,
                                "items": source_counts[source],
                                "complete": True,
                                "limited": True,
                                "limit_page": page - 1,
                                "end_reason": reason,
                            }
                        continue

                    # Both public catalogs have demonstrated sparse HTTP-200 pagination. Treat an
                    # isolated empty page as a hole and require a run of empty pages before EOF.
                    sparse_threshold = None
                    if source == "camwhores":
                        sparse_threshold = CAMWHORES_EMPTY_END_THRESHOLD
                    elif source == "archivebate":
                        sparse_threshold = ARCHIVEBATE_EMPTY_END_THRESHOLD

                    if sparse_threshold is not None:
                        consecutive_empty_pages[source] += 1
                        last_signatures[source] = None
                        repeated_signatures[source] = 0
                        next_page = page + 1
                        cursors[source] = next_page
                        if consecutive_empty_pages[source] < sparse_threshold:
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
                        reason = f"consecutive_empty_pages:{sparse_threshold}:{page}"
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
                    reason = "empty_page"
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET complete = 1, failed = 0, error = NULL, end_reason = ?, updated_at = ? "
                            "WHERE revision = ? AND source = ?",
                            (reason, time.time(), revision, source),
                        )
                        self._indexing_progress["source_progress"][source] = {
                            "cursor": page,
                            "items": source_counts[source],
                            "complete": True,
                            "end_reason": reason,
                        }
                    continue

                if source in ("camwhores", "archivebate"):
                    consecutive_empty_pages[source] = 0

'''
text = text[:start] + worker_block + text[end:]

cache_start = text.find('    def _read_cached_source_pages(')
cache_end = text.find('\n\n# Singleton instance', cache_start)
if cache_start < 0 or cache_end < 0:
    raise SystemExit("cached source function markers not found")

cache_func = '''    def _read_cached_source_pages(self, source: str, max_age_seconds: float = 6 * 3600):
        """Read contiguous raw source pages and return (items, next page, count, ended).

        Archivebate and Camwhores can contain isolated empty HTTP-200 pages. For both sources,
        cached seeding requires consecutive empty pages instead of trusting one empty page or one
        has_more=false value. Archivebate page 1001 is deliberately left for a live boundary probe.
        A missing/old cache page stops seeding without claiming completion.
        """
        cache_dir = Path(FEED_CACHE_DIR)
        if not cache_dir.exists():
            return [], 1, 0, False
        page = 1
        collected: List[Dict[str, Any]] = []
        pages_scanned = 0
        empty_streak = 0
        while page <= MAX_PAGES_PER_SOURCE.get(source, 1_000_000):
            current_page = page
            path = cache_dir / f"raw_v1_{source}_{current_page}.json"
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

            # Never consume a cached empty Archivebate page 1001 as EOF. Let the live worker probe
            # the known upstream boundary so catalog_limited remains truthful.
            if source == "archivebate" and current_page == 1001 and not batch:
                return collected, 1001, pages_scanned, False

            collected.extend(item for item in batch if isinstance(item, dict))
            pages_scanned += 1
            has_more = data.get("has_more")
            page = current_page + 1

            if source in ("camwhores", "archivebate"):
                threshold = (
                    CAMWHORES_EMPTY_END_THRESHOLD
                    if source == "camwhores"
                    else ARCHIVEBATE_EMPTY_END_THRESHOLD
                )
                if batch:
                    empty_streak = 0
                else:
                    empty_streak += 1
                    if empty_streak >= threshold:
                        return collected, page, pages_scanned, True
                # A single has_more=false is not a trustworthy boundary for sparse sources.
                continue

            if not batch or has_more is False:
                return collected, page, pages_scanned, True
        return collected, page, pages_scanned, False
'''
text = text[:cache_start] + cache_func + text[cache_end:]

path.write_text(text, encoding="utf-8")

Path("audit/regression_archivebate_sparse_pagination.py").write_text(r'''"""Regression: Archivebate isolated empty pages are gaps before the known page-1001 boundary."""
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
''', encoding="utf-8")

Path("audit/regression_latest_sparse_migration_scope.py").write_text(r'''"""Regression: sparse-page migration repairs only the absolute newest revision and never cascades."""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_service as catalog


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    now = time.time()
    with service._lock:
        conn = service._get_conn()
        for rev in (21, 22, 23):
            conn.execute(
                "INSERT INTO revisions(revision, created_at, updated_at, complete, is_active, failed, video_count, error) "
                "VALUES(?, ?, ?, ?, ?, 0, ?, NULL)",
                (rev, now, now, 1 if rev < 23 else 0, 1 if rev == 22 else 0, rev * 10),
            )
        # Older fallback revisions deliberately carry ambiguous legacy Camwhores endings.
        for rev in (21, 22):
            conn.execute(
                "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
                "VALUES(?, 'camwhores', 2, 1, 30, 1, 0, NULL, NULL, ?)",
                (rev, now),
            )
            conn.execute(
                "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
                "VALUES(?, 'archivebate', 1001, 1000, 36000, 1, 0, NULL, 'source_page_limit:empty:1001', ?)",
                (rev, now),
            )
        # Latest revision is already incomplete because Camwhores was reopened, but Archivebate
        # still has an old one-empty-page EOF that must be reopened in the same revision.
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(23, 'camwhores', 2, 1, 33, 0, 0, NULL, 'legacy_sparse_recheck', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(23, 'archivebate', 589, 588, 21168, 1, 0, NULL, 'empty_page', ?)",
            (now,),
        )
    service.close()

    service = catalog.CatalogService(db)
    with service._lock:
        conn = service._get_conn()
        revisions = {int(r['revision']): r for r in conn.execute(
            "SELECT revision, complete, is_active FROM revisions WHERE revision IN (21,22,23)"
        ).fetchall()}
        latest_ab = conn.execute(
            "SELECT complete, end_reason FROM source_runs WHERE revision=23 AND source='archivebate'"
        ).fetchone()
    assert bool(revisions[22]['complete']) is True, dict(revisions[22])
    assert bool(revisions[21]['complete']) is True, dict(revisions[21])
    assert bool(revisions[23]['complete']) is False, dict(revisions[23])
    assert bool(latest_ab['complete']) is False and latest_ab['end_reason'] == 'legacy_sparse_recheck', dict(latest_ab)
    assert service.get_active_revision() == 22
    assert service.get_resumable_revision() == 23
    service.close()

    # A second startup must be idempotent: it cannot peel back revision 22 or 21.
    service = catalog.CatalogService(db)
    assert service.get_active_revision() == 22
    assert service.get_resumable_revision() == 23
    with service._lock:
        rows = service._get_conn().execute(
            "SELECT revision, complete FROM revisions WHERE revision IN (21,22) ORDER BY revision"
        ).fetchall()
    assert all(bool(r['complete']) for r in rows), [dict(r) for r in rows]
    service.close()

print("PASS: sparse migration is latest-only, idempotent, and repairs both sources in the newest revision")
''', encoding="utf-8")

print("Applied latest-only sparse migration + Archivebate sparse pagination follow-up")
