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
MAX_PAGES_PER_SOURCE = {"archivebate": 1000, "camwhores": 500}


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
                    conn.execute("UPDATE revisions SET is_active = 0")
                    conn.execute("UPDATE revisions SET complete = 1, is_active = 1, updated_at = ?, video_count = ? WHERE revision = ?", (now, cnt, revision))
                else:
                    conn.execute("UPDATE revisions SET updated_at = ?, video_count = ? WHERE revision = ?", (now, cnt, revision))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def publish_revision(self, revision: int):
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
            if is_failed and revision_error:
                try:
                    decoded_error = json.loads(revision_error)
                    source_error = decoded_error if isinstance(decoded_error, dict) else {"catalog": revision_error}
                except (ValueError, TypeError):
                    source_error = {"catalog": revision_error}

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

                items = []
                for r in leader_rows:
                    v = json.loads(r["raw_json"])
                    g_cnt = int(r["grp_cnt"])
                    author_clean = str(r["author_clean"] or "")
                    if g_cnt > 1 and author_clean and author_clean != "model":
                        v["is_grouped"] = True
                        v["group_count"] = g_cnt
                        # Fetch all videos for this author in this revision matching filters
                        member_rows = conn.execute(
                            f"""
                            SELECT raw_json FROM catalog_items
                            WHERE {where_sql} AND author_clean = ?
                            ORDER BY published_at DESC, canonical_key ASC
                            """,
                            params + [author_clean],
                        ).fetchall()
                        members = [json.loads(mr["raw_json"]) for mr in member_rows]
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
            "retryable": is_failed,
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

    def build_revision_background(
        self,
        fetchers: Dict[str, Callable[[int], List[Dict[str, Any]]]],
        force: bool = False,
        enrich_fn: Optional[Callable] = None,
    ) -> int:
        """Starts background indexing of a new revision, yielding to active playback."""
        with self._lock:
            if self._indexing_thread and self._indexing_thread.is_alive():
                return self._indexing_progress.get("revision", 0)

            active = self.get_active_revision()
            if not force and active is not None and self.is_revision_complete(active):
                return active

            next_rev = self.get_latest_revision_number() + 1
            now = time.time()
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO revisions(revision, created_at, updated_at, complete, failed, video_count, error) VALUES(?, ?, ?, 0, 0, 0, NULL)",
                (next_rev, now, now),
            )
            for src in fetchers:
                conn.execute(
                    "INSERT OR REPLACE INTO source_runs(revision, source, cursor, pages_scanned, items_found, complete, failed, error, updated_at) VALUES(?, ?, 1, 0, 0, 0, 0, NULL, ?)",
                    (next_rev, src, now),
                )

            # Przy pierwszym uruchomieniu wykorzystaj świeże, surowe strony,
            # które już są na dysku. Nowa rewizja pozostaje nieopublikowana,
            # więc stare dane są bezpieczne, a worker zaczyna od pierwszej
            # nieznanej strony zamiast pobierać cały katalog od zera.
            if not force:
                for src in fetchers:
                    seeded, next_cursor, pages_scanned, source_complete = self._read_cached_source_pages(src)
                    if seeded:
                        self.import_items(seeded, revision=next_rev, complete=False, source=src)
                    conn.execute(
                        "UPDATE source_runs SET cursor = ?, pages_scanned = ?, items_found = ?, complete = ?, updated_at = ? WHERE revision = ? AND source = ?",
                        (next_cursor, pages_scanned, len(seeded), int(source_complete), time.time(), next_rev, src),
                    )

            self._indexing_stop.clear()
            self._indexing_progress = {
                "is_indexing": True,
                "revision": next_rev,
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

            self._indexing_thread = threading.Thread(target=worker, name=f"catalog-indexer-rev{next_rev}", daemon=True)
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
                if page > max_pages.get(source, 1000):
                    error = f"page_limit_exceeded:{max_pages.get(source, 1000)}"
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

                try:
                    batch = fetchers[source](page)
                    if not isinstance(batch, list):
                        raise RuntimeError(f"Source {source} returned non-list on page {page}")
                except Exception as exc:
                    # An error on a page does NOT mean end of source; record error and stop this source run
                    errors[source] = str(exc)
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET failed = 1, error = ?, updated_at = ? WHERE revision = ? AND source = ?",
                            (str(exc), time.time(), revision, source),
                        )
                    continue

                if not batch:
                    # Verified clean end of pagination
                    ended.add(source)
                    with self._lock:
                        conn = self._get_conn()
                        conn.execute(
                            "UPDATE source_runs SET complete = 1, updated_at = ? WHERE revision = ? AND source = ?",
                            (time.time(), revision, source),
                        )
                        self._indexing_progress["source_progress"][source] = {
                            "cursor": page,
                            "items": source_counts[source],
                            "complete": True,
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
                        "UPDATE source_runs SET cursor = ?, pages_scanned = pages_scanned + 1, items_found = items_found + ?, updated_at = ? WHERE revision = ? AND source = ?",
                        (cursors[source], len(batch), time.time(), revision, source),
                    )

        with self._lock:
            self._indexing_progress["is_indexing"] = False
            if len(ended) == len(sources) and not errors:
                # All sources completed successfully: publish atomically!
                self.publish_revision(revision)
                self._indexing_progress["published_revision"] = revision
            elif errors:
                self.mark_revision_failed(revision, json.dumps(errors))
                self._indexing_progress["error"] = errors

    def _read_cached_source_pages(self, source: str, max_age_seconds: float = 6 * 3600):
        """Read contiguous raw source pages and return (items, next page, count, ended).

        A missing page or an old page stops seeding. We never infer the end of
        pagination from a non-empty page; only an explicitly empty page or
        ``has_more=false`` is treated as a verified source end.
        """
        cache_dir = Path(FEED_CACHE_DIR)
        if not cache_dir.exists():
            return [], 1, 0, False
        page = 1
        collected: List[Dict[str, Any]] = []
        pages_scanned = 0
        while page <= 1000:
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
            if not batch or has_more is False:
                return collected, page, pages_scanned, True
        return collected, page, pages_scanned, False


# Singleton instance
catalog_service = CatalogService()
