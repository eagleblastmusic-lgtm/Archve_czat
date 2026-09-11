from pathlib import Path

root = Path(__file__).resolve().parents[1]
cat_path = root / "catalog_service.py"
reg_path = root / "audit" / "regression_archivebate_source_limit.py"

text = cat_path.read_text(encoding="utf-8")

old = '''                source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_runs)")}
                if "end_reason" not in source_columns:
                    conn.execute("ALTER TABLE source_runs ADD COLUMN end_reason TEXT")
                conn.execute("COMMIT")
'''
new = '''                source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_runs)")}
                if "end_reason" not in source_columns:
                    conn.execute("ALTER TABLE source_runs ADD COLUMN end_reason TEXT")
                # Archivebate has an upstream pagination boundary at page 1001. Depending on the
                # request, that boundary has been observed as either HTTP 5xx or HTTP 200 with no
                # video cards. Older builds persisted the latter as a clean EOF. Reclassify only
                # the exact known 1000-page shape so normal empty-page endings are untouched.
                conn.execute(
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
assert old in text, "schema migration anchor missing"
text = text.replace(old, new, 1)

old = '''            limited_rows = conn.execute(
                "SELECT source, end_reason FROM source_runs "
                "WHERE revision = ? AND end_reason LIKE 'source_http_limit:%'",
                (rev,),
            ).fetchall()
'''
new = '''            limited_rows = conn.execute(
                "SELECT source, end_reason FROM source_runs "
                "WHERE revision = ? AND (end_reason LIKE 'source_http_limit:%' "
                "OR end_reason LIKE 'source_page_limit:%')",
                (rev,),
            ).fetchall()
'''
assert old in text, "limited_rows anchor missing"
text = text.replace(old, new, 1)

old = '''                for attempt in range(1, 5):
                    try:
                        batch = fetchers[source](page)
                        if not isinstance(batch, list):
                            raise RuntimeError(f"Source {source} returned non-list on page {page}")
                        fetch_error = None
                        break
                    except Exception as exc:
                        fetch_error = exc
                        if attempt < 4:
                            time.sleep(0.35 * attempt)
'''
new = '''                for attempt in range(1, 5):
                    try:
                        batch = fetchers[source](page)
                        if not isinstance(batch, list):
                            raise RuntimeError(f"Source {source} returned non-list on page {page}")
                        # Archivebate's page-1001 boundary is inconsistent: it can be 5xx or a
                        # successful but empty document. Probe an empty post-1000 page repeatedly
                        # before classifying it, so one transient empty response cannot stop the crawl.
                        if source == "archivebate" and page > 1000 and not batch and attempt < 4:
                            time.sleep(0.35 * attempt)
                            continue
                        fetch_error = None
                        break
                    except Exception as exc:
                        fetch_error = exc
                        if attempt < 4:
                            time.sleep(0.35 * attempt)
'''
assert old in text, "retry loop anchor missing"
text = text.replace(old, new, 1)

old = '''                if not batch:
                    # Verified clean end of pagination returned by the remote source.
                    ended.add(source)
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET complete = 1, end_reason = 'empty_page', updated_at = ? "
                            "WHERE revision = ? AND source = ?",
                            (time.time(), revision, source),
                        )
                        self._indexing_progress["source_progress"][source] = {
                            "cursor": page,
                            "items": source_counts[source],
                            "complete": True,
                            "end_reason": "empty_page",
                        }
                    continue
'''
new = '''                if not batch:
                    ended.add(source)
                    # Archivebate page 1001 is an upstream visibility boundary, not proof that the
                    # service has no older videos. Preserve that distinction even when the server
                    # answers HTTP 200 with an empty page instead of 5xx.
                    is_archivebate_limit = source == "archivebate" and page > 1000
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
'''
assert old in text, "empty-page anchor missing"
text = text.replace(old, new, 1)

cat_path.write_text(text, encoding="utf-8")

reg = reg_path.read_text(encoding="utf-8")
append = r'''

# The same upstream page-1001 boundary can answer HTTP 200 with no cards. It must be retried,
# then classified as limited rather than as a clean end of the service's history.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "seed-empty", "username": "seed", "source": "archivebate"}],
        revision=7,
        complete=False,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(7, 'archivebate', 1001, 1000, 36000, 0, 0, NULL, NULL, ?)",
            (time.time(),),
        )
    calls = []

    def empty_boundary(page):
        calls.append(page)
        assert page == 1001, page
        return []

    rev = service.build_revision_background({"archivebate": empty_boundary}, force=False)
    assert rev == 7, rev
    wait(service)
    assert calls == [1001, 1001, 1001, 1001], calls
    result = service.query_page(revision=7)
    assert result["catalog_complete"] is True, result
    assert result["catalog_limited"] is True, result
    assert result["limited_sources"]["archivebate"] == "source_page_limit:empty:1001", result
    progress = service._indexing_progress["source_progress"]["archivebate"]
    assert progress["limited"] is True and progress["limit_page"] == 1000, progress
    service.close()


# Existing databases that already stored the exact page-1001 empty-page shape are migrated
# in place, without re-indexing pages 1..1000.
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "catalog.db"
    service = catalog.CatalogService(db)
    service.import_items(
        [{"id": "legacy-empty", "username": "legacy", "source": "archivebate"}],
        revision=21,
        complete=True,
        source="archivebate",
    )
    with service._lock:
        conn = service._get_conn()
        conn.execute(
            "INSERT INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, end_reason, updated_at) "
            "VALUES(21, 'archivebate', 1001, 1000, 36000, 1, 0, NULL, 'empty_page', ?)",
            (time.time(),),
        )
    service.close()

    migrated = catalog.CatalogService(db)
    result = migrated.query_page(revision=21)
    assert result["catalog_complete"] is True, result
    assert result["catalog_limited"] is True, result
    assert result["limited_sources"]["archivebate"] == "source_page_limit:empty:1001", result
    migrated.close()
'''
marker = '\nprint("PASS: Archivebate page-1001 5xx is retried, qualified as a visible source limit, and transient 5xx still recovers")\n'
assert marker in reg, "regression print anchor missing"
reg = reg.replace(marker, append + '\nprint("PASS: Archivebate page-1001 5xx/empty boundary is retried, classified as limited, and legacy empty-page state migrates in place")\n', 1)
reg_path.write_text(reg, encoding="utf-8")
