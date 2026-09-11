from pathlib import Path

path = Path("catalog_service.py")
text = path.read_text(encoding="utf-8")

old = '''                conn.execute("UPDATE revisions SET is_active = 0")
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

new = '''                best_complete = conn.execute(
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

count = text.count(old)
if count != 1:
    raise SystemExit(f"active-normalization patch expected exactly one match, found {count}")

path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Adjusted startup active-revision normalization")
