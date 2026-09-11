"""Durable SQLite catalog service: canonical identities, atomic revisions, unified filtered counts & pagination."""
import json
import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cache_store import DATA_DIR, FEED_CACHE_DIR, atomic_write_json, read_json_cache
from camwhores import deduplicate_videos, video_identity
from scraper import parse_date_to_sort_seconds

PAGE_SIZE = 280
DEFAULT_CATALOG_DB = DATA_DIR / "catalog.db"
# Emergency safety ceilings only. Normal completion is determined by a verified empty page,
# not by reaching an arbitrary catalog-size limit. These values are intentionally far above
# the old 1000/500 caps so large source catalogs can be indexed to their real end.
MAX_PAGES_PER_SOURCE = {"archivebate": 1_000_000, "camwhores": 100_000}
CAMWHORES_EMPTY_END_THRESHOLD = 5
ARCHIVEBATE_EMPTY_END_THRESHOLD = 5
TRANSIENT_SOURCE_RETRY_DELAYS = (2.0, 8.0)


def canonical_identity_key(video: Dict[str, Any]) -> str:
    """Computes a stable, canonical string key from video identity."""
    ident = video_identity(video)
    if not ident:
        raw_id = str(video.get("id") or "").strip()
        source = video.get("source") or "archivebate"
        if raw_id:
            return f"{source}:id:{raw_id}"
        canonical = json.dumps(video, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{source}:hash:{digest}"
    source, kind, val = ident
    return f"{source}:{kind}:{val}"


def extract_item_metadata(video: Dict[str, Any], revision: int) -> Tuple[str, str, str, str, str, float, float, str, str, str, str, str, str, str, int]:
    """Extracts minimal normalized metadata tuple for SQLite storage."""
    canon_key = canonical_identity_key(video)
    ident = video_identity(video)
    source = ident[0] if ident else (video.get("source") or "archivebate")
    video_id = str(video.get("id") or "")
    if source == "camwhores" and video_id.startswith("cw_"):
        video_id = video_id[3:]

    author = str(video.get("username") or video.get("author") or "Model").strip()
    author_clean = re.sub(r"[^a-z0-9]", "", author.lower())

    # Publication timestamp calculation
    if "published_at" in video and isinstance(video["published_at"], (int, float)):
        published_at = float(video["published_at"])
    elif video.get("date"):
        age_seconds = parse_date_to_sort_seconds(str(video["date"]))
        published_at = time.time() - age_seconds
    else:
        published_at = 0.0

    # Duration
    dur_str = str(video.get("duration") or "")
    dur_secs = 0.0
    if "duration_seconds" in video and isinstance(video["duration_seconds"], (int, float)):
        dur_secs = float(video["duration_seconds"])
    elif dur_str and ":" in dur_str:
        parts = dur_str.split(":")
        try:
            if len(parts) == 2:
                dur_secs = float(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                dur_secs = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            pass

    poster = str(video.get("poster") or video.get("thumbnail") or "")
    url = str(video.get("url") or "")
    preview_video = str(video.get("preview_video") or "")
    title = str(video.get("title") or video.get("date") or "")
    platform = str(video.get("platform") or "")
    raw_json = json.dumps(video, ensure_ascii=False, separators=(",", ":"))

    return (
        canon_key,
        source,
        video_id,
        author,
        author_clean,
        published_at,
        dur_secs,
        dur_str,
        poster,
        url,
        preview_video,
        title,
        platform,
        raw_json,
        revision,
    )


class CatalogService:
    def __init__(self, db_path: Optional[Path] = None, page_size: int = PAGE_SIZE):
        self.db_path = Path(db_path or os.getenv("ARCHIVEBATE_CATALOG_DB") or DEFAULT_CATALOG_DB)
        self.page_size = page_size
        self._lock = threading.RLock()
        # Writer connection is serialized by _lock. File-backed catalog reads
        # use a separate thread-local SQLite connection so WAL can actually
        # provide concurrent readers while the background indexer is writing.
        self._read_local = threading.local()
        self._conn: Optional[sqlite3.Connection] = None
        self._indexing_thread: Optional[threading.Thread] = None
        self._indexing_stop = threading.Event()
        self._indexing_progress = {
            "is_indexing": False,
            "revision": 0,
            "source_progress": {},
            "error": None,
        }
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
                isolation_level=None,  # autocommit mode; manage transactions explicitly
            )
            conn.row_factory = sqlite3.Row
            if str(self.db_path) != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -64000")  # 64MB cache
            conn.execute("PRAGMA temp_store = MEMORY")
            self._conn = conn
        return self._conn

    def _get_read_conn(self) -> sqlite3.Connection:
        """Return a thread-local read connection for a file-backed WAL DB.

        The indexer deliberately owns the writer connection and _lock. Using
        that same lock for feed reads serialized the UI behind large import
        transactions, causing 12s client timeouts even though committed rows
        were already readable. A separate SQLite reader can observe the last
        committed WAL snapshot immediately.
        """
        if str(self.db_path) == ":memory:":
            return self._get_conn()
        conn = getattr(self._read_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=1.0,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA busy_timeout = 1000")
            conn.execute("PRAGMA cache_size = -32000")
            conn.execute("PRAGMA temp_store = MEMORY")
            self._read_local.conn = conn
        return conn

    def _read_guard(self):
        # In-memory SQLite databases cannot be shared by opening a second
        # connection, so tests/in-memory callers retain the original lock.
        return self._lock if str(self.db_path) == ":memory:" else nullcontext()

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS revisions (
                    revision INTEGER PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    complete INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 0,
                    failed INTEGER DEFAULT 0,
                    video_count INTEGER DEFAULT 0,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS source_runs (
                    revision INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    cursor INTEGER DEFAULT 1,
                    pages_scanned INTEGER DEFAULT 0,
                    items_found INTEGER DEFAULT 0,
                    complete INTEGER DEFAULT 0,
                    failed INTEGER DEFAULT 0,
                    error TEXT,
                    end_reason TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(revision, source)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS catalog_items (
                    canonical_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    author TEXT,
                    author_clean TEXT,
                    published_at REAL NOT NULL,
                    duration_seconds REAL DEFAULT 0,
                    duration_str TEXT,
                    poster TEXT,
                    url TEXT,
                    preview_video TEXT,
                    title TEXT,
                    platform TEXT,
                    raw_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY(revision, canonical_key)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_rev_order ON catalog_items(revision, published_at DESC, canonical_key ASC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_rev_src_order ON catalog_items(revision, source, published_at DESC, canonical_key ASC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_rev_author ON catalog_items(revision, author_clean, published_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_rev_id ON catalog_items(revision, video_id)")
            # CREATE TABLE IF NOT EXISTS does not upgrade an existing database.
            # Serialize schema inspection and migration across application processes.
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(revisions)")}
                if "is_active" not in columns:
                    conn.execute("ALTER TABLE revisions ADD COLUMN is_active INTEGER DEFAULT 0")
                    conn.execute("""
                        UPDATE revisions SET is_active = 1 WHERE revision = (
                            SELECT revision FROM revisions
                            WHERE complete = 1 AND failed = 0
                            ORDER BY updated_at DESC, revision DESC LIMIT 1
                        )
                    """)
                source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_runs)")}
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

                # Repair sparse-page endings only on the absolute newest revision. Older completed
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

                # Revision numbers are monotonic snapshots. Repair any stale is_active flag left
                # behind by the old resume bug: the newest completed successful revision is the
                # only published snapshot. An unfinished newer revision remains inactive while it
                # is resumed in the background.
                best_complete = conn.execute(
                    "SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 "
                    "ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if best_complete:
                    conn.execute("UPDATE revisions SET is_active = 0")
                    conn.execute(
                        "UPDATE revisions SET is_active = 1 WHERE revision = ?",
                        (int(best_complete["revision"]),),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def close(self):
        reader = getattr(self._read_local, "conn", None)
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
            self._read_local.conn = None
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def get_active_revision(self) -> Optional[int]:
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

    def import_items(self, items: List[Dict[str, Any]], revision: int, complete: bool = False, source: str = "manual"):
        """Imports a batch of video dicts into catalog_items for a specified revision."""
        if not items:
            return
        deduped = deduplicate_videos(items)
        records = [extract_item_metadata(v, revision) for v in deduped]
        with self._lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                conn.execute(
                    "INSERT OR IGNORE INTO revisions(revision, created_at, updated_at, complete, video_count) VALUES(?, ?, ?, 0, 0)",
                    (revision, now, now),
                )
                conn.executemany(
                    """
                    INSERT INTO catalog_items(
                        canonical_key, source, video_id, author, author_clean,
                        published_at, duration_seconds, duration_str, poster, url,
                        preview_video, title, platform, raw_json, revision
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(revision, canonical_key) DO UPDATE SET
                        author = excluded.author,
                        author_clean = excluded.author_clean,
                        published_at = excluded.published_at,
                        poster = excluded.poster,
                        url = excluded.url,
                        preview_video = excluded.preview_video,
                        raw_json = excluded.raw_json
                    """,
                    records,
                )
                cnt = conn.execute("SELECT COUNT(*) AS total FROM catalog_items WHERE revision = ?", (revision,)).fetchone()["total"]
                if complete:
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
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def publish_revision(self, revision: int) -> bool:
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

    def mark_revision_failed(self, revision: int, error_msg: str):
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE revisions SET failed = 1, complete = 0, is_active = 0, error = ?, updated_at = ? WHERE revision = ?",
                (error_msg, time.time(), revision),
            )

    def query_page(
        self,
        page: int = 1,
        page_size: Optional[int] = None,
        source: str = "all",
        author_filter: str = "all",
        group_authors: bool = False,
        revision: Optional[int] = None,
        blocked_models: Optional[List[str]] = None,
        favorite_authors: Optional[List[str]] = None,
        favorite_ids: Optional[List[str]] = None,
        enrich_fn: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """Unified filtered query for count and items."""
        ps = page_size or self.page_size
        rev = revision if revision is not None else self.get_active_revision()

        if rev is None:
            return {
                "catalog_revision": 0,
                "page": page,
                "page_size": ps,
                "video_count": 0,
                "group_count": 0,
                "page_count": 1,
                "items": [],
                "videos": [],
                "catalog_complete": False,
                "updated_at": 0.0,
                "indexing_progress": self._indexing_progress,
                "count": 0,
                "target_count": ps,
                "known_count": 0,
                "has_more": False,
                "complete": False,
                "last_page": 1,
                "total_videos": 0,
                "total_is_estimate": True,
                "snapshot_id": "0",
                "revision": 0,
                "source_error": {},
                "retryable": False,
                "catalog_limited": False,
                "limited_sources": {},
            }

        # File-backed reads must not queue behind the background writer.
        # WAL gives us a consistent committed snapshot on the dedicated reader.
        with self._read_guard():
            conn = self._get_conn() if str(self.db_path) == ":memory:" else self._get_read_conn()
            rev_info = conn.execute("SELECT * FROM revisions WHERE revision = ?", (rev,)).fetchone()
            is_failed = bool(rev_info["failed"]) if rev_info else False
            is_complete = bool(rev_info["complete"]) and not is_failed if rev_info else False
            revision_error = str(rev_info["error"] or "") if rev_info else ""
            updated_at = float(rev_info["updated_at"]) if rev_info else 0.0
            source_error = {}
            if revision_error:
                try:
                    decoded_error = json.loads(revision_error)
                    source_error = decoded_error if isinstance(decoded_error, dict) else {"catalog": revision_error}
                except (ValueError, TypeError):
                    source_error = {"catalog": revision_error}

            limited_rows = conn.execute(
                "SELECT source, end_reason FROM source_runs "
                "WHERE revision = ? AND (end_reason LIKE 'source_http_limit:%' "
                "OR end_reason LIKE 'source_page_limit:%')",
                (rev,),
            ).fetchall()
            limited_sources = {
                str(row["source"]): str(row["end_reason"] or "")
                for row in limited_rows
            }
            catalog_limited = bool(limited_sources)

            # Build dynamic WHERE clause
            where_clauses = ["revision = ?"]
            params: List[Any] = [rev]

            if source == "only-archivebate":
                where_clauses.append("source = 'archivebate'")
            elif source == "only-camwhores":
                where_clauses.append("source = 'camwhores'")

            if blocked_models:
                clean_b = [re.sub(r"[^a-z0-9]", "", b.lower()) for b in blocked_models if b]
                clean_b = [b for b in clean_b if b]
                if clean_b:
                    placeholders = ",".join("?" for _ in clean_b)
                    where_clauses.append(f"author_clean NOT IN ({placeholders})")
                    params.extend(clean_b)

            if author_filter == "exclude_fav":
                clean_fav = [re.sub(r"[^a-z0-9]", "", a.lower()) for a in (favorite_authors or []) if a]
                clean_fav = [a for a in clean_fav if a]
                fav_ids = [str(fid) for fid in (favorite_ids or []) if fid]
                conds = []
                if clean_fav:
                    placeholders = ",".join("?" for _ in clean_fav)
                    conds.append(f"author_clean NOT IN ({placeholders})")
                    params.extend(clean_fav)
                if fav_ids:
                    placeholders = ",".join("?" for _ in fav_ids)
                    conds.append(f"video_id NOT IN ({placeholders})")
                    params.extend(fav_ids)
                if conds:
                    where_clauses.append(" AND ".join(conds))

            elif author_filter == "only_fav":
                clean_fav = [re.sub(r"[^a-z0-9]", "", a.lower()) for a in (favorite_authors or []) if a]
                clean_fav = [a for a in clean_fav if a]
                fav_ids = [str(fid) for fid in (favorite_ids or []) if fid]
                conds = []
                if clean_fav:
                    placeholders = ",".join("?" for _ in clean_fav)
                    conds.append(f"author_clean IN ({placeholders})")
                    params.extend(clean_fav)
                if fav_ids:
                    placeholders = ",".join("?" for _ in fav_ids)
                    conds.append(f"video_id IN ({placeholders})")
                    params.extend(fav_ids)
                if conds:
                    where_clauses.append(f"({' OR '.join(conds)})")
                else:
                    where_clauses.append("1 = 0")

            where_sql = " AND ".join(where_clauses)

            # Execute unified count and page query
            if not group_authors:
                # Ungrouped mode
                cnt_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM catalog_items WHERE {where_sql}", params).fetchone()
                total_videos = int(cnt_row["cnt"])
                total_groups = total_videos
                page_count = max(1, math.ceil(total_videos / ps))

                offset = max(0, (page - 1) * ps)
                item_rows = conn.execute(
                    f"""
                    SELECT raw_json FROM catalog_items
                    WHERE {where_sql}
                    ORDER BY published_at DESC, canonical_key ASC
                    LIMIT ? OFFSET ?
                    """,
                    params + [ps, offset],
                ).fetchall()

                raw_items = [json.loads(r["raw_json"]) for r in item_rows]
                items = enrich_fn(raw_items) if enrich_fn else raw_items

            else:
                # Grouped mode (1 item per author, leader = newest video)
                cnt_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM catalog_items WHERE {where_sql}", params).fetchone()
                total_videos = int(cnt_row["cnt"])

                # Query group leaders
                grp_sql = f"""
                    WITH grp AS (
                        SELECT raw_json, canonical_key, author_clean, MAX(published_at) AS pub, COUNT(*) AS grp_cnt
                        FROM catalog_items
                        WHERE {where_sql} AND author_clean != '' AND author_clean != 'model'
                        GROUP BY author_clean
                        UNION ALL
                        SELECT raw_json, canonical_key, author_clean, published_at AS pub, 1 AS grp_cnt
                        FROM catalog_items
                        WHERE {where_sql} AND (author_clean = '' OR author_clean = 'model')
                    )
                """
                grp_count_row = conn.execute(grp_sql + " SELECT COUNT(*) AS cnt FROM grp", params + params).fetchone()
                total_groups = int(grp_count_row["cnt"])
                page_count = max(1, math.ceil(total_groups / ps))

                offset = max(0, (page - 1) * ps)
                leader_rows = conn.execute(
                    grp_sql + f"""
                        SELECT raw_json, canonical_key, author_clean, grp_cnt
                        FROM grp
                        ORDER BY pub DESC, canonical_key ASC
                        LIMIT ? OFFSET ?
                    """,
                    params + params + [ps, offset],
                ).fetchall()

                # Fetch grouped members for every leader on this page in one query.
                # The previous implementation executed one SELECT per author (up to 280
                # extra queries on every page), which made grouped first paint much slower
                # than the normal feed and could exceed the browser request timeout.
                grouped_authors = [
                    str(r["author_clean"] or "")
                    for r in leader_rows
                    if int(r["grp_cnt"]) > 1
                    and str(r["author_clean"] or "")
                    and str(r["author_clean"] or "") != "model"
                ]
                member_map: Dict[str, List[Dict[str, Any]]] = {}
                if grouped_authors:
                    member_placeholders = ",".join("?" for _ in grouped_authors)
                    member_rows = conn.execute(
                        f"""
                        SELECT author_clean, raw_json FROM catalog_items
                        WHERE {where_sql} AND author_clean IN ({member_placeholders})
                        ORDER BY author_clean ASC, published_at DESC, canonical_key ASC
                        """,
                        params + grouped_authors,
                    ).fetchall()
                    for mr in member_rows:
                        member_author = str(mr["author_clean"] or "")
                        member_map.setdefault(member_author, []).append(json.loads(mr["raw_json"]))

                items = []
                for r in leader_rows:
                    v = json.loads(r["raw_json"])
                    g_cnt = int(r["grp_cnt"])
                    author_clean = str(r["author_clean"] or "")
                    if g_cnt > 1 and author_clean and author_clean != "model":
                        v["is_grouped"] = True
                        v["group_count"] = g_cnt
                        members = member_map.get(author_clean, [dict(v)])
                        v["grouped_videos"] = enrich_fn(members) if enrich_fn else members
                    else:
                        v["is_grouped"] = False
                        v["group_count"] = 1
                        v["grouped_videos"] = [dict(v)]
                    items.append(v)

                if enrich_fn:
                    items = enrich_fn(items)

        has_more = page < page_count
        effective_known = total_groups if group_authors else total_videos

        return {
            "catalog_revision": rev,
            "page": page,
            "page_size": ps,
            "video_count": total_videos,
            "group_count": total_groups,
            "page_count": page_count,
            "items": items,
            "videos": items,
            "catalog_complete": is_complete,
            "catalog_state": "failed" if is_failed else ("complete" if is_complete else "partial"),
            "updated_at": updated_at,
            "indexing_progress": self._indexing_progress,
            "count": len(items),
            "target_count": ps,
            "known_count": effective_known,
            "has_more": has_more,
            "complete": is_complete,
            "last_page": page_count,
            "total_videos": total_videos,
            "total_is_estimate": not is_complete,
            "snapshot_id": str(rev),
            "revision": rev,
            "source_error": source_error,
            "retryable": bool(source_error) and not is_complete,
            "catalog_limited": catalog_limited,
            "limited_sources": limited_sources,
        }

    def import_cached_raw_pages(self, raw_cache_dir: Optional[Path] = None) -> int:
        """Imports existing raw_v1_*.json files into a partial initial revision if no complete revision exists."""
        with self._lock:
            active = self.get_active_revision()
            if active is not None and self.is_revision_complete(active):
                return 0

            target_rev = (active or 0) + 1 if active is None else active
            cache_dir = Path(raw_cache_dir or FEED_CACHE_DIR)
            if not cache_dir.exists():
                return 0

            imported_total = 0
            all_items = []
            for file in sorted(cache_dir.glob("raw_v1_*.json")):
                data, _ = read_json_cache(str(file))
                if isinstance(data, dict) and isinstance(data.get("items"), list):
                    all_items.extend(data["items"])

            if all_items:
                self.import_items(all_items, revision=target_rev, complete=False, source="cache_import")
                imported_total = len(all_items)
            return imported_total

    @staticmethod
    def _is_recoverable_page_limit_error(error_value: Any) -> bool:
        """Recognize legacy cap failures and the page-1001 5xx boundary probe as resumable.

        The page-1001 HTTP failure is retried by the new worker before it is classified as a
        source boundary. Older builds could already have persisted that one failure as terminal,
        so accept it once on upgrade and let the worker perform the qualified boundary probe.
        """
        if not error_value:
            return False
        decoded = error_value
        if isinstance(error_value, str):
            try:
                decoded = json.loads(error_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = error_value

        def recoverable(value: Any) -> bool:
            text = str(value or "")
            if text.startswith("page_limit_exceeded:"):
                return True
            return bool(re.search(r"\b5\d\d Server Error\b", text)) and "page=1001" in text

        if isinstance(decoded, dict) and decoded:
            return all(recoverable(value) for value in decoded.values())
        return recoverable(decoded)

    @classmethod
    def _is_recoverable_source_error(cls, error_value: Any) -> bool:
        """Return True for durable source failures that are safe to retry from the same cursor."""
        if cls._is_recoverable_page_limit_error(error_value):
            return True
        if not error_value:
            return False
        decoded = error_value
        if isinstance(error_value, str):
            try:
                decoded = json.loads(error_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = error_value

        def recoverable(value: Any) -> bool:
            text = str(value or "").lower()
            transient_fragments = (
                "source did not return a successful response",
                "read timed out",
                "readtimeout",
                "connecttimeout",
                "connectionerror",
                "max retries exceeded",
                "remotedisconnected",
                "connection reset",
                "temporarily unavailable",
                "too many requests",
            )
            if any(fragment in text for fragment in transient_fragments):
                return True
            if re.search(r"\b429\b", text):
                return True
            return bool(re.search(r"\b5\d\d\b", text)) and any(
                token in text for token in ("http", "server", "response", "status")
            )

        if isinstance(decoded, dict) and decoded:
            return all(recoverable(value) for value in decoded.values())
        return recoverable(decoded)

    def _reopen_legacy_archivebate_cap_revision(self) -> Optional[int]:
        """Reopen the active revision falsely completed by the old scraper page-1000 guard.

        Before the true-end fix, ``_fetch_single_ab_home_page(1001)`` returned ``[]`` locally
        without contacting Archivebate. A completed active run with exactly 1000 scanned pages,
        cursor 1001 and no persisted end reason is therefore known to be a false completion.
        New completions always persist ``end_reason`` and are never reopened here.
        """
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """
                SELECT r.revision
                FROM revisions r
                JOIN source_runs s ON s.revision = r.revision
                WHERE r.is_active = 1
                  AND r.complete = 1
                  AND r.failed = 0
                  AND s.source = 'archivebate'
                  AND s.complete = 1
                  AND s.failed = 0
                  AND s.cursor = 1001
                  AND s.pages_scanned = 1000
                  AND COALESCE(s.end_reason, '') = ''
                ORDER BY r.revision DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            revision = int(row["revision"])
            now = time.time()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE revisions SET complete = 0, failed = 0, error = NULL, updated_at = ? WHERE revision = ?",
                    (now, revision),
                )
                conn.execute(
                    "UPDATE source_runs SET complete = 0, failed = 0, error = NULL, "
                    "end_reason = 'legacy_cap_reopened', updated_at = ? "
                    "WHERE revision = ? AND source = 'archivebate'",
                    (now, revision),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return revision

    def _repair_reopened_legacy_archivebate_cursor(self, revision: int) -> bool:
        """Restore page 1001 if the legacy-cap migration was accidentally restarted at page 1.

        ``_reopen_legacy_archivebate_cap_revision`` is the only normal path that leaves an
        active revision in an incomplete state. Older builds did not persist an explicit marker,
        so ``is_active=1 + complete=0`` is also accepted as the legacy migration signature.
        Fresh partial revisions are not active and therefore are never fast-forwarded.
        """
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """
                SELECT r.is_active, r.complete AS revision_complete, r.failed AS revision_failed,
                       s.cursor, s.pages_scanned, s.items_found, s.complete AS source_complete,
                       s.failed AS source_failed, s.end_reason
                FROM revisions r
                JOIN source_runs s ON s.revision = r.revision
                WHERE r.revision = ? AND s.source = 'archivebate'
                """,
                (revision,),
            ).fetchone()
            if not row:
                return False

            cursor = int(row["cursor"] or 1)
            marker = str(row["end_reason"] or "") == "legacy_cap_reopened"
            legacy_active_partial = (
                bool(row["is_active"])
                and not bool(row["revision_complete"])
                and not bool(row["revision_failed"])
                and not bool(row["source_complete"])
                and not bool(row["source_failed"])
                and not str(row["end_reason"] or "")
            )
            if cursor >= 1001 or not (marker or legacy_active_partial):
                return False

            archivebate_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS cnt FROM catalog_items WHERE revision = ? AND source = 'archivebate'",
                    (revision,),
                ).fetchone()["cnt"]
                or 0
            )
            now = time.time()
            conn.execute(
                """
                UPDATE source_runs
                SET cursor = 1001,
                    pages_scanned = CASE WHEN pages_scanned < 1000 THEN 1000 ELSE pages_scanned END,
                    items_found = CASE WHEN items_found < ? THEN ? ELSE items_found END,
                    complete = 0, failed = 0, error = NULL,
                    end_reason = 'legacy_cap_reopened', updated_at = ?
                WHERE revision = ? AND source = 'archivebate'
                """,
                (archivebate_count, archivebate_count, now, revision),
            )
            return True

    def get_resumable_revision(self) -> Optional[int]:
        """Return the newest durable unfinished revision that can safely continue after restart.

        A revision created only by raw-cache import has no source_runs and is therefore not treated
        as resumable; the normal bootstrap path can seed a fresh revision from those cache files.
        Legacy revisions failed solely by the old 1000-page guard are explicitly recoverable.
        """
        with self._lock:
            conn = self._get_conn()
            latest_completed = conn.execute(
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
            for row in rows:
                if not bool(row["failed"]) or self._is_recoverable_source_error(row["error"]):
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

            if not force and "archivebate" in fetchers:
                self._reopen_legacy_archivebate_cap_revision()
            active = self.get_active_revision()
            resumable = None if force else self.get_resumable_revision()
            if resumable is None and not force and active is not None and self.is_revision_complete(active):
                return active

            now = time.time()
            conn = self._get_conn()
            resumed = resumable is not None
            legacy_cursor_repaired = False

            if resumed:
                next_rev = int(resumable)
                legacy_cursor_repaired = self._repair_reopened_legacy_archivebate_cursor(next_rev)
                rev_row = conn.execute(
                    "SELECT failed, error FROM revisions WHERE revision = ?",
                    (next_rev,),
                ).fetchone()
                if rev_row and bool(rev_row["failed"]):
                    if not self._is_recoverable_source_error(rev_row["error"]):
                        raise RuntimeError(f"Revision {next_rev} is failed and not resumable")
                    conn.execute(
                        "UPDATE revisions SET failed = 0, error = NULL, updated_at = ? WHERE revision = ?",
                        (now, next_rev),
                    )

                # Retry any source that was interrupted mid-page. Completed sources stay completed.
                conn.execute(
                    "UPDATE source_runs SET failed = 0, error = NULL, end_reason = NULL, updated_at = ? "
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
                            "UPDATE source_runs SET cursor = ?, pages_scanned = ?, items_found = ?, complete = ?, "
                            "end_reason = ?, updated_at = ? WHERE revision = ? AND source = ?",
                            (next_cursor, pages_scanned, len(seeded), int(source_complete),
                             "cached_end" if source_complete else None, time.time(), next_rev, src),
                        )

            self._indexing_stop.clear()
            self._indexing_progress = {
                "is_indexing": True,
                "revision": next_rev,
                "resumed": resumed,
                "legacy_cursor_repaired": legacy_cursor_repaired,
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

    def _run_indexing_worker(
        self,
        revision: int,
        fetchers: Dict[str, Callable[[int], List[Dict[str, Any]]]],
    ):
        """Worker loop executing low-concurrency incremental indexing."""
        sources = list(fetchers.keys())
        with self._lock:
            conn = self._get_conn()
            run_rows = {
                row["source"]: row
                for row in conn.execute("SELECT * FROM source_runs WHERE revision = ?", (revision,)).fetchall()
            }
        ended = {src for src in sources if run_rows.get(src) and run_rows[src]["complete"]}
        errors = {}
        cursors = {s: int(run_rows.get(s)["cursor"] or 1) if run_rows.get(s) else 1 for s in sources}
        source_counts = {s: int(run_rows.get(s)["items_found"] or 0) if run_rows.get(s) else 0 for s in sources}
        total_items_found = sum(int(row["items_found"] or 0) for row in run_rows.values())
        last_signatures: Dict[str, Optional[str]] = {s: None for s in sources}
        repeated_signatures: Dict[str, int] = {s: 0 for s in sources}
        consecutive_empty_pages: Dict[str, int] = {s: 0 for s in sources}
        transient_retry_counts: Dict[str, int] = {s: 0 for s in sources}

        # Safety bound protects resources, but reaching it is truncation, not a verified source end.
        max_pages = MAX_PAGES_PER_SOURCE

        while len(ended) + len(errors) < len(sources) and not self._indexing_stop.is_set():
            # Check playback and yield (Pakiet C / Pakiet B, point 8)
            try:
                import main
                if hasattr(main, "is_playback_active") and main.is_playback_active():
                    time.sleep(0.3)
                    if hasattr(main, "is_playback_active") and main.is_playback_active():
                        time.sleep(0.5)
                        continue
            except Exception:
                pass

            active_sources = [s for s in sources if s not in ended and s not in errors]
            if not active_sources:
                break

            for source in active_sources:
                page = cursors[source]
                if page > max_pages.get(source, 1_000_000):
                    error = f"page_safety_limit_exceeded:{max_pages.get(source, 1_000_000)}"
                    errors[source] = error
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET failed = 1, complete = 0, error = ?, updated_at = ? WHERE revision = ? AND source = ?",
                            (error, time.time(), revision, source),
                        )
                        self._indexing_progress["source_progress"][source] = {
                            "cursor": page,
                            "items": source_counts[source],
                            "complete": False,
                            "truncated": True,
                        }
                    continue

                # Cooperative sleep to maintain low background concurrency
                time.sleep(0.05)

                batch = None
                fetch_error = None
                # A single 5xx is not proof of a pagination boundary. Retry the exact page
                # several times before deciding whether this is transient or a stable source cap.
                for attempt in range(1, 5):
                    try:
                        batch = fetchers[source](page)
                        if not isinstance(batch, list):
                            raise RuntimeError(f"Source {source} returned non-list on page {page}")
                        # Archivebate's page-1001 boundary is inconsistent: it can be 5xx or a
                        # successful but empty document. Probe an empty post-1000 page repeatedly
                        # before classifying it, so one transient empty response cannot stop the crawl.
                        if source == "archivebate" and page == 1001 and not batch and attempt < 4:
                            time.sleep(0.35 * attempt)
                            continue
                        fetch_error = None
                        break
                    except Exception as exc:
                        fetch_error = exc
                        if attempt < 4:
                            time.sleep(0.35 * attempt)

                if fetch_error is not None:
                    response = getattr(fetch_error, "response", None)
                    status_code = getattr(response, "status_code", None)
                    # Archivebate's public home pagination currently accepts 1..1000 and returns
                    # a persistent server error beyond that boundary. Four consecutive 5xx probes
                    # after page 1000 are recorded as an upstream access limit, not as a clean EOF.
                    if (
                        source == "archivebate"
                        and page > 1000
                        and isinstance(status_code, int)
                        and 500 <= status_code < 600
                    ):
                        ended.add(source)
                        reason = f"source_http_limit:{status_code}:{page}"
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

                    error_text = str(fetch_error)
                    if self._is_recoverable_source_error(error_text):
                        retry_count = transient_retry_counts[source]
                        if retry_count < len(TRANSIENT_SOURCE_RETRY_DELAYS):
                            delay = float(TRANSIENT_SOURCE_RETRY_DELAYS[retry_count])
                            transient_retry_counts[source] = retry_count + 1
                            with self._lock:
                                conn = self._get_conn()
                                conn.execute(
                                    "UPDATE source_runs SET failed = 0, complete = 0, error = ?, "
                                    "end_reason = 'transient_retry_pending', updated_at = ? "
                                    "WHERE revision = ? AND source = ?",
                                    (error_text, time.time(), revision, source),
                                )
                                self._indexing_progress["source_progress"][source] = {
                                    "cursor": page,
                                    "items": source_counts[source],
                                    "complete": False,
                                    "retrying": True,
                                    "retry_count": transient_retry_counts[source],
                                    "retry_delay": delay,
                                    "error": error_text,
                                }
                            if self._indexing_stop.wait(delay):
                                break
                            continue

                    # Non-transient errors, or transient errors exhausted in this process, are
                    # persisted as failed. Recoverable transient failures can still resume from
                    # the same durable cursor on the next application start.
                    errors[source] = error_text
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET failed = 1, complete = 0, error = ?, updated_at = ? WHERE revision = ? AND source = ?",
                            (error_text, time.time(), revision, source),
                        )
                    continue

                transient_retry_counts[source] = 0

                if not batch:
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

                # Some sites clamp out-of-range page numbers and return the same last page forever.
                # Three identical non-empty pages in a row are treated as a verified pagination clamp.
                signature_keys = sorted(
                    canonical_identity_key(item) for item in batch if isinstance(item, dict)
                )
                signature = hashlib.sha256("\n".join(signature_keys).encode("utf-8")).hexdigest() if signature_keys else None
                if signature and signature == last_signatures[source]:
                    repeated_signatures[source] += 1
                else:
                    repeated_signatures[source] = 0
                    last_signatures[source] = signature

                if repeated_signatures[source] >= 2:
                    ended.add(source)
                    reason = f"repeated_page:{page}"
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET complete = 1, end_reason = ?, updated_at = ? "
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

                # Import batch into SQLite
                self.import_items(batch, revision=revision, complete=False, source=source)
                total_items_found += len(batch)
                source_counts[source] += len(batch)
                cursors[source] += 1

                with self._lock:
                    self._indexing_progress["source_progress"][source] = {
                        "cursor": cursors[source],
                        "items": source_counts[source],
                        "complete": False,
                    }
                    conn = self._get_conn()
                    conn.execute(
                        "UPDATE source_runs SET cursor = ?, pages_scanned = pages_scanned + 1, items_found = items_found + ?, "
                        "failed = 0, error = NULL, end_reason = NULL, updated_at = ? WHERE revision = ? AND source = ?",
                        (cursors[source], len(batch), time.time(), revision, source),
                    )

        with self._lock:
            self._indexing_progress["is_indexing"] = False
            if len(ended) == len(sources) and not errors:
                # All sources completed successfully. A stale worker is allowed to finish but is
                # never allowed to roll the live feed back over a newer completed revision.
                published = self.publish_revision(revision)
                if published:
                    self._indexing_progress["published_revision"] = revision
                else:
                    self._indexing_progress["superseded_revision"] = revision
                    self._indexing_progress["published_revision"] = self.get_active_revision()
            elif errors:
                self.mark_revision_failed(revision, json.dumps(errors))
                self._indexing_progress["error"] = errors

    def _read_cached_source_pages(self, source: str, max_age_seconds: float = 6 * 3600):
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


# Singleton instance
catalog_service = CatalogService()
