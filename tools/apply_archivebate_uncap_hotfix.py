from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"non-unique patch anchor: {label} ({text.count(old)})")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# scraper.py: remove the hidden legacy 1000-page ceiling.
# ---------------------------------------------------------------------------
scraper = Path("scraper.py")
replace_once(
    scraper,
    '''    def _fetch_single_ab_home_page(self, p: int, strict: bool = False) -> List[Dict[str, Any]]:\n        """Pobiera pojedynczą stronę z Archivebate."""\n        if p > 1000:\n            return []\n        url = f"https://archivebate.com?page={p}" if p > 1 else "https://archivebate.com"\n''',
    '''    def _fetch_single_ab_home_page(self, p: int, strict: bool = False) -> List[Dict[str, Any]]:\n        """Pobiera pojedynczą stronę z Archivebate bez sztucznego limitu numeru strony."""\n        if p < 1:\n            return []\n        url = f"https://archivebate.com?page={p}" if p > 1 else "https://archivebate.com"\n''',
    "single Archivebate page cap",
)
replace_once(
    scraper,
    '''            ab_pages = [p for p in range(ab_start, ab_start + 20) if p <= 1000]\n''',
    '''            ab_pages = list(range(ab_start, ab_start + 20))\n''',
    "only-archivebate initial batch cap",
)
replace_once(
    scraper,
    '''            while len(merged) < target_count and extra_p <= ab_start + 40 and extra_p <= 1000:\n                batch = [p for p in range(extra_p, extra_p + 5) if p <= 1000]\n''',
    '''            while len(merged) < target_count and extra_p <= ab_start + 40:\n                batch = list(range(extra_p, extra_p + 5))\n''',
    "only-archivebate refill cap",
)
replace_once(
    scraper,
    '''            ab_pages = [p for p in range(ab_start, ab_start + 10) if p <= 1000]\n''',
    '''            ab_pages = list(range(ab_start, ab_start + 10))\n''',
    "mixed source initial batch cap",
)
replace_once(
    scraper,
    '''                extra_ab = [ab_start + 10 + extra_offset * 3 + i for i in range(3) if ab_start + 10 + extra_offset * 3 + i <= 1000]\n''',
    '''                extra_ab = [ab_start + 10 + extra_offset * 3 + i for i in range(3)]\n''',
    "mixed source refill cap",
)

# ---------------------------------------------------------------------------
# catalog_service.py: mark how a source ended, reopen legacy false-complete
# page-1000 revisions once, and stop safely if a remote source clamps/repeats
# the same non-empty page forever.
# ---------------------------------------------------------------------------
catalog = Path("catalog_service.py")
replace_once(
    catalog,
    '''                    failed INTEGER DEFAULT 0,\n                    error TEXT,\n                    updated_at REAL NOT NULL,\n''',
    '''                    failed INTEGER DEFAULT 0,\n                    error TEXT,\n                    end_reason TEXT,\n                    updated_at REAL NOT NULL,\n''',
    "source_runs end_reason schema",
)
replace_once(
    catalog,
    '''                if "is_active" not in columns:\n                    conn.execute("ALTER TABLE revisions ADD COLUMN is_active INTEGER DEFAULT 0")\n                    conn.execute("""\n                        UPDATE revisions SET is_active = 1 WHERE revision = (\n                            SELECT revision FROM revisions\n                            WHERE complete = 1 AND failed = 0\n                            ORDER BY updated_at DESC, revision DESC LIMIT 1\n                        )\n                    """)\n                conn.execute("COMMIT")\n''',
    '''                if "is_active" not in columns:\n                    conn.execute("ALTER TABLE revisions ADD COLUMN is_active INTEGER DEFAULT 0")\n                    conn.execute("""\n                        UPDATE revisions SET is_active = 1 WHERE revision = (\n                            SELECT revision FROM revisions\n                            WHERE complete = 1 AND failed = 0\n                            ORDER BY updated_at DESC, revision DESC LIMIT 1\n                        )\n                    """)\n                source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_runs)")}\n                if "end_reason" not in source_columns:\n                    conn.execute("ALTER TABLE source_runs ADD COLUMN end_reason TEXT")\n                conn.execute("COMMIT")\n''',
    "source_runs end_reason migration",
)

anchor = '''    def get_resumable_revision(self) -> Optional[int]:\n'''
legacy_helper = '''    def _reopen_legacy_archivebate_cap_revision(self) -> Optional[int]:\n        """Reopen the active revision falsely completed by the old scraper page-1000 guard.\n\n        Before the true-end fix, ``_fetch_single_ab_home_page(1001)`` returned ``[]`` locally\n        without contacting Archivebate. A completed active run with exactly 1000 scanned pages,\n        cursor 1001 and no persisted end reason is therefore known to be a false completion.\n        New completions always persist ``end_reason`` and are never reopened here.\n        """\n        with self._lock:\n            conn = self._get_conn()\n            row = conn.execute(\n                """\n                SELECT r.revision\n                FROM revisions r\n                JOIN source_runs s ON s.revision = r.revision\n                WHERE r.is_active = 1\n                  AND r.complete = 1\n                  AND r.failed = 0\n                  AND s.source = 'archivebate'\n                  AND s.complete = 1\n                  AND s.failed = 0\n                  AND s.cursor = 1001\n                  AND s.pages_scanned = 1000\n                  AND COALESCE(s.end_reason, '') = ''\n                ORDER BY r.revision DESC\n                LIMIT 1\n                """\n            ).fetchone()\n            if not row:\n                return None\n            revision = int(row["revision"])\n            now = time.time()\n            conn.execute("BEGIN IMMEDIATE")\n            try:\n                conn.execute(\n                    "UPDATE revisions SET complete = 0, failed = 0, error = NULL, updated_at = ? WHERE revision = ?",\n                    (now, revision),\n                )\n                conn.execute(\n                    "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, end_reason = NULL, updated_at = ? "\n                    "WHERE revision = ? AND source = 'archivebate'",\n                    (now, revision),\n                )\n                conn.execute("COMMIT")\n            except Exception:\n                conn.execute("ROLLBACK")\n                raise\n            return revision\n\n'''
text = catalog.read_text(encoding="utf-8")
if anchor not in text:
    raise SystemExit("missing patch anchor: get_resumable_revision")
if legacy_helper not in text:
    text = text.replace(anchor, legacy_helper + anchor, 1)
    catalog.write_text(text, encoding="utf-8")

replace_once(
    catalog,
    '''            active = self.get_active_revision()\n            resumable = None if force else self.get_resumable_revision()\n''',
    '''            if not force:\n                self._reopen_legacy_archivebate_cap_revision()\n            active = self.get_active_revision()\n            resumable = None if force else self.get_resumable_revision()\n''',
    "reopen legacy false-complete before selecting active revision",
)
replace_once(
    catalog,
    '''                conn.execute(\n                    "UPDATE source_runs SET failed = 0, error = NULL, updated_at = ? "\n                    "WHERE revision = ? AND complete = 0",\n                    (now, next_rev),\n                )\n''',
    '''                conn.execute(\n                    "UPDATE source_runs SET failed = 0, error = NULL, end_reason = NULL, updated_at = ? "\n                    "WHERE revision = ? AND complete = 0",\n                    (now, next_rev),\n                )\n''',
    "clear end_reason on resume",
)
replace_once(
    catalog,
    '''                            "UPDATE source_runs SET cursor = ?, pages_scanned = ?, items_found = ?, complete = ?, updated_at = ? "\n                            "WHERE revision = ? AND source = ?",\n                            (next_cursor, pages_scanned, len(seeded), int(source_complete), time.time(), next_rev, src),\n''',
    '''                            "UPDATE source_runs SET cursor = ?, pages_scanned = ?, items_found = ?, complete = ?, "\n                            "end_reason = ?, updated_at = ? WHERE revision = ? AND source = ?",\n                            (next_cursor, pages_scanned, len(seeded), int(source_complete),\n                             "cached_end" if source_complete else None, time.time(), next_rev, src),\n''',
    "persist cached end reason",
)
replace_once(
    catalog,
    '''        total_items_found = sum(int(row["items_found"] or 0) for row in run_rows.values())\n\n        # Safety bound protects resources, but reaching it is truncation, not a verified source end.\n''',
    '''        total_items_found = sum(int(row["items_found"] or 0) for row in run_rows.values())\n        last_signatures: Dict[str, Optional[str]] = {s: None for s in sources}\n        repeated_signatures: Dict[str, int] = {s: 0 for s in sources}\n\n        # Safety bound protects resources, but reaching it is truncation, not a verified source end.\n''',
    "repeat-page detector state",
)
replace_once(
    catalog,
    '''                if not batch:\n                    # Verified clean end of pagination\n                    ended.add(source)\n                    with self._lock:\n                        conn = self._get_conn()\n                        conn.execute(\n                            "UPDATE source_runs SET complete = 1, updated_at = ? WHERE revision = ? AND source = ?",\n                            (time.time(), revision, source),\n                        )\n                        self._indexing_progress["source_progress"][source] = {\n                            "cursor": page,\n                            "items": source_counts[source],\n                            "complete": True,\n                        }\n                    continue\n\n                # Import batch into SQLite\n                self.import_items(batch, revision=revision, complete=False, source=source)\n''',
    '''                if not batch:\n                    # Verified clean end of pagination returned by the remote source.\n                    ended.add(source)\n                    with self._lock:\n                        conn = self._get_conn()\n                        conn.execute(\n                            "UPDATE source_runs SET complete = 1, end_reason = 'empty_page', updated_at = ? "\n                            "WHERE revision = ? AND source = ?",\n                            (time.time(), revision, source),\n                        )\n                        self._indexing_progress["source_progress"][source] = {\n                            "cursor": page,\n                            "items": source_counts[source],\n                            "complete": True,\n                            "end_reason": "empty_page",\n                        }\n                    continue\n\n                # Some sites clamp out-of-range page numbers and return the same last page forever.\n                # Three identical non-empty pages in a row are treated as a verified pagination clamp.\n                signature_keys = sorted(\n                    canonical_identity_key(item) for item in batch if isinstance(item, dict)\n                )\n                signature = hashlib.sha256("\\n".join(signature_keys).encode("utf-8")).hexdigest() if signature_keys else None\n                if signature and signature == last_signatures[source]:\n                    repeated_signatures[source] += 1\n                else:\n                    repeated_signatures[source] = 0\n                    last_signatures[source] = signature\n\n                if repeated_signatures[source] >= 2:\n                    ended.add(source)\n                    reason = f"repeated_page:{page}"\n                    with self._lock:\n                        conn = self._get_conn()\n                        conn.execute(\n                            "UPDATE source_runs SET complete = 1, end_reason = ?, updated_at = ? "\n                            "WHERE revision = ? AND source = ?",\n                            (reason, time.time(), revision, source),\n                        )\n                        self._indexing_progress["source_progress"][source] = {\n                            "cursor": page,\n                            "items": source_counts[source],\n                            "complete": True,\n                            "end_reason": reason,\n                        }\n                    continue\n\n                # Import batch into SQLite\n                self.import_items(batch, revision=revision, complete=False, source=source)\n''',
    "empty end reason and repeated-page clamp detection",
)

print("Archivebate true-end indexing hotfix applied")
