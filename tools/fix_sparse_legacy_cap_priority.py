from pathlib import Path

path = Path("catalog_service.py")
text = path.read_text(encoding="utf-8")

start_marker = '                # Repair sparse-page endings only on the absolute newest revision. Older completed\n'
end_marker = '                # Revision numbers are monotonic snapshots. Repair any stale is_active flag left\n'
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("generated sparse migration markers not found")

replacement = '''                # Repair sparse-page endings only on the absolute newest revision. Older completed
                # snapshots are fallback history and must never be reopened one-by-one on subsequent
                # application starts. A legacy Archivebate page-1000 false completion has priority:
                # reopen that source first and leave other sources unchanged for this run.
                latest_revision_row = conn.execute(
                    "SELECT revision, complete, failed FROM revisions ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if latest_revision_row and not bool(latest_revision_row["failed"]):
                    latest_revision = int(latest_revision_row["revision"])
                    legacy_archivebate_cap = conn.execute(
                        """
                        SELECT 1 FROM source_runs
                        WHERE revision = ?
                          AND source = 'archivebate'
                          AND complete = 1
                          AND failed = 0
                          AND cursor = 1001
                          AND pages_scanned = 1000
                          AND COALESCE(end_reason, '') = ''
                        LIMIT 1
                        """,
                        (latest_revision,),
                    ).fetchone()
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
                    legacy_archivebate_sparse = conn.execute(
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

                    if legacy_archivebate_cap:
                        now = time.time()
                        conn.execute(
                            "UPDATE revisions SET complete = 0, is_active = 0, failed = 0, error = NULL, updated_at = ? "
                            "WHERE revision = ?",
                            (now, latest_revision),
                        )
                        conn.execute(
                            "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "
                            "end_reason = 'legacy_cap_reopened', updated_at = ? "
                            "WHERE revision = ? AND source = 'archivebate'",
                            (now, latest_revision),
                        )
                    elif legacy_camwhores or legacy_archivebate_sparse:
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
                        if legacy_archivebate_sparse:
                            conn.execute(
                                "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "
                                "end_reason = 'legacy_sparse_recheck', updated_at = ? "
                                "WHERE revision = ? AND source = 'archivebate'",
                                (now, latest_revision),
                            )

'''

path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
print("Prioritized legacy Archivebate cap over sparse-source migration")
