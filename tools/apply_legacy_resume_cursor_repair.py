from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"non-unique patch anchor: {label} ({text.count(old)})")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


catalog = Path("catalog_service.py")

replace_once(
    catalog,
    '''                conn.execute(\n                    "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, end_reason = NULL, updated_at = ? "\n                    "WHERE revision = ? AND source = 'archivebate'",\n                    (now, revision),\n                )\n''',
    '''                conn.execute(\n                    "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "\n                    "end_reason = 'legacy_cap_reopened', updated_at = ? "\n                    "WHERE revision = ? AND source = 'archivebate'",\n                    (now, revision),\n                )\n''',
    "persist legacy reopen marker",
)

anchor = '''            return revision\n\n    def get_resumable_revision(self) -> Optional[int]:\n'''
insert = '''            return revision\n\n    def _repair_reopened_legacy_archivebate_cursor(self, revision: int) -> bool:\n        """Restore page 1001 if the legacy-cap migration was accidentally restarted at page 1.\n\n        ``_reopen_legacy_archivebate_cap_revision`` is the only normal path that leaves an\n        active revision in an incomplete state. Older builds did not persist an explicit marker,\n        so ``is_active=1 + complete=0`` is also accepted as the legacy migration signature.\n        Fresh partial revisions are not active and therefore are never fast-forwarded.\n        """\n        with self._lock:\n            conn = self._get_conn()\n            row = conn.execute(\n                """\n                SELECT r.is_active, r.complete AS revision_complete, r.failed AS revision_failed,\n                       s.cursor, s.pages_scanned, s.items_found, s.complete AS source_complete,\n                       s.failed AS source_failed, s.end_reason\n                FROM revisions r\n                JOIN source_runs s ON s.revision = r.revision\n                WHERE r.revision = ? AND s.source = 'archivebate'\n                """,\n                (revision,),\n            ).fetchone()\n            if not row:\n                return False\n\n            cursor = int(row["cursor"] or 1)\n            marker = str(row["end_reason"] or "") == "legacy_cap_reopened"\n            legacy_active_partial = (\n                bool(row["is_active"])\n                and not bool(row["revision_complete"])\n                and not bool(row["revision_failed"])\n                and not bool(row["source_complete"])\n                and not bool(row["source_failed"])\n                and not str(row["end_reason"] or "")\n            )\n            if cursor >= 1001 or not (marker or legacy_active_partial):\n                return False\n\n            archivebate_count = int(\n                conn.execute(\n                    "SELECT COUNT(*) AS cnt FROM catalog_items WHERE revision = ? AND source = 'archivebate'",\n                    (revision,),\n                ).fetchone()["cnt"]\n                or 0\n            )\n            now = time.time()\n            conn.execute(\n                """\n                UPDATE source_runs\n                SET cursor = 1001,\n                    pages_scanned = CASE WHEN pages_scanned < 1000 THEN 1000 ELSE pages_scanned END,\n                    items_found = CASE WHEN items_found < ? THEN ? ELSE items_found END,\n                    complete = 0, failed = 0, error = NULL,\n                    end_reason = 'legacy_cap_reopened', updated_at = ?\n                WHERE revision = ? AND source = 'archivebate'\n                """,\n                (archivebate_count, archivebate_count, now, revision),\n            )\n            return True\n\n    def get_resumable_revision(self) -> Optional[int]:\n'''
replace_once(catalog, anchor, insert, "legacy cursor repair helper")

replace_once(
    catalog,
    '''            now = time.time()\n            conn = self._get_conn()\n            resumed = resumable is not None\n\n            if resumed:\n                next_rev = int(resumable)\n                rev_row = conn.execute(\n''',
    '''            now = time.time()\n            conn = self._get_conn()\n            resumed = resumable is not None\n            legacy_cursor_repaired = False\n\n            if resumed:\n                next_rev = int(resumable)\n                legacy_cursor_repaired = self._repair_reopened_legacy_archivebate_cursor(next_rev)\n                rev_row = conn.execute(\n''',
    "repair legacy cursor before resume",
)

replace_once(
    catalog,
    '''                "revision": next_rev,\n                "resumed": resumed,\n                "source_progress": {s: {"cursor": 1, "items": 0, "complete": False} for s in fetchers},\n''',
    '''                "revision": next_rev,\n                "resumed": resumed,\n                "legacy_cursor_repaired": legacy_cursor_repaired,\n                "source_progress": {s: {"cursor": 1, "items": 0, "complete": False} for s in fetchers},\n''',
    "expose cursor repair diagnostic",
)
