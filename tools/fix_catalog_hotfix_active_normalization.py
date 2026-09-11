from pathlib import Path

path = Path("catalog_service.py")
text = path.read_text(encoding="utf-8")

old_active = '''                conn.execute("UPDATE revisions SET is_active = 0")
                best_complete = conn.execute(
                    "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 "
                    "ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if best_complete:
                    conn.execute(
                        "UPDATE revisions SET is_active = 1 WHERE revision = ?",
                        (int(best_complete["revision"]),),
                    )
'''

new_active = '''                best_complete = conn.execute(
                    "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 "
                    "ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if best_complete:
                    conn.execute("UPDATE revisions SET is_active = 0")
                    conn.execute(
                        "UPDATE revisions SET is_active = 1 WHERE revision = ?",
                        (int(best_complete["revision"]),),
                    )
'''

count = text.count(old_active)
if count != 1:
    raise SystemExit(f"active-normalization patch expected exactly one match, found {count}")
text = text.replace(old_active, new_active, 1)

old_camwhores = '''                        SELECT 1 FROM source_runs
                        WHERE revision = ?
                          AND source = 'camwhores'
                          AND complete = 1
                          AND failed = 0
                          AND COALESCE(end_reason, '') IN ('', 'empty_page', 'cached_end')
                        LIMIT 1
'''

new_camwhores = '''                        SELECT 1 FROM source_runs
                        WHERE revision = ?
                          AND source = 'camwhores'
                          AND complete = 1
                          AND failed = 0
                          AND COALESCE(end_reason, '') IN ('', 'empty_page', 'cached_end')
                          AND NOT EXISTS (
                              SELECT 1 FROM source_runs ab
                              WHERE ab.revision = source_runs.revision
                                AND ab.source = 'archivebate'
                                AND ab.complete = 1
                                AND ab.failed = 0
                                AND ab.cursor = 1001
                                AND ab.pages_scanned = 1000
                                AND COALESCE(ab.end_reason, '') = ''
                          )
                        LIMIT 1
'''

count = text.count(old_camwhores)
if count != 1:
    raise SystemExit(f"legacy-source sequencing patch expected exactly one match, found {count}")
text = text.replace(old_camwhores, new_camwhores, 1)

path.write_text(text, encoding="utf-8")
print("Adjusted startup active-revision normalization and legacy source sequencing")
