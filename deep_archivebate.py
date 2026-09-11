"""Durable Archivebate model discovery and deep-profile crawler.

The normal Archivebate home feed is intentionally treated as a fresh-content source.  This
service builds a second durable index by discovering model profiles through Archivebate's search
API and walking profile pagination.  Deep items are persisted independently and mirrored into the
currently published/latest catalog revisions so existing feed SQL, filters and pagination can use
them immediately without a second query path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cache_store import DATA_DIR

DEFAULT_DB = DATA_DIR / "catalog.db"
DISCOVERY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_- ."
DISCOVERY_PAGE_SPLIT_THRESHOLD = 30
DISCOVERY_MAX_PREFIX_DEPTH = 6
PROFILE_EMPTY_END_THRESHOLD = 3
PROFILE_REPEAT_END_THRESHOLD = 3


def _clean_author(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


class DeepArchivebateService:
    def __init__(self, db_path: Optional[Path] = None, request_delay: Optional[float] = None):
        self.db_path = Path(db_path or os.getenv("ARCHIVEBATE_CATALOG_DB") or DEFAULT_DB)
        self.request_delay = max(0.05, float(request_delay if request_delay is not None else os.getenv("DEEP_ARCHIVEBATE_DELAY", "0.75")))
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._scraper = None
        self._last_targets: Tuple[int, ...] = ()
        self._progress: Dict[str, Any] = {
            "running": False,
            "current_prefix": None,
            "current_model": None,
            "last_error": None,
            "last_activity": 0.0,
        }
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            if str(self.db_path) != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            self._conn = conn
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archivebate_models (
                    model_key TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    profile_url TEXT,
                    discovered_from TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    first_seen REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    next_page INTEGER NOT NULL DEFAULT 1,
                    pages_scanned INTEGER NOT NULL DEFAULT 0,
                    videos_found INTEGER NOT NULL DEFAULT 0,
                    empty_streak INTEGER NOT NULL DEFAULT 0,
                    last_signature TEXT,
                    repeated_signatures INTEGER NOT NULL DEFAULT 0,
                    crawl_complete INTEGER NOT NULL DEFAULT 0,
                    end_reason TEXT,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archivebate_discovery_queue (
                    prefix TEXT PRIMARY KEY,
                    depth INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    next_page INTEGER NOT NULL DEFAULT 1,
                    last_page INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archivebate_deep_items (
                    canonical_key TEXT PRIMARY KEY,
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
                    discovered_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_abdeep_order ON archivebate_deep_items(published_at DESC, canonical_key ASC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_abdeep_author ON archivebate_deep_items(author_clean, published_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_abmodels_crawl ON archivebate_models(crawl_complete, priority DESC, updated_at ASC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_abqueue_status ON archivebate_discovery_queue(status, depth, updated_at)")

    def close(self) -> None:
        self.stop(wait=True)
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def _target_revisions_locked(self) -> Tuple[int, ...]:
        conn = self._get_conn()
        targets = set()
        row = conn.execute(
            "SELECT MAX(revision) AS revision FROM revisions WHERE complete = 1 AND failed = 0"
        ).fetchone()
        if row and row["revision"]:
            targets.add(int(row["revision"]))
        row = conn.execute(
            "SELECT MAX(revision) AS revision FROM revisions WHERE failed = 0"
        ).fetchone()
        if row and row["revision"]:
            targets.add(int(row["revision"]))
        return tuple(sorted(targets))

    def _merge_all_into_targets_locked(self, targets: Sequence[int]) -> None:
        conn = self._get_conn()
        for revision in targets:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO catalog_items(
                        canonical_key, source, video_id, author, author_clean,
                        published_at, duration_seconds, duration_str, poster, url,
                        preview_video, title, platform, raw_json, revision
                    )
                    SELECT canonical_key, source, video_id, author, author_clean,
                           published_at, duration_seconds, duration_str, poster, url,
                           preview_video, title, platform, raw_json, ?
                    FROM archivebate_deep_items
                    """,
                    (revision,),
                )
                cnt = int(conn.execute("SELECT COUNT(*) AS cnt FROM catalog_items WHERE revision = ?", (revision,)).fetchone()["cnt"] or 0)
                conn.execute("UPDATE revisions SET video_count = ?, updated_at = ? WHERE revision = ?", (cnt, time.time(), revision))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _refresh_targets(self) -> None:
        with self._lock:
            targets = self._target_revisions_locked()
            if targets != self._last_targets:
                self._merge_all_into_targets_locked(targets)
                self._last_targets = targets

    def seed_catalog_models(self) -> int:
        """Seed the crawl registry from the newest useful catalog revision."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT MAX(revision) AS revision FROM revisions WHERE failed = 0").fetchone()
            revision = int(row["revision"] or 0) if row else 0
            if revision <= 0:
                return 0
            rows = conn.execute(
                """
                SELECT author_clean, MIN(author) AS username, MAX(published_at) AS last_seen
                FROM catalog_items
                WHERE revision = ? AND source = 'archivebate'
                  AND author_clean != '' AND author_clean != 'model'
                GROUP BY author_clean
                ORDER BY last_seen DESC
                """,
                (revision,),
            ).fetchall()
            now = time.time()
            inserted = 0
            for row in rows:
                key = str(row["author_clean"] or "")
                username = str(row["username"] or "").strip()
                if not key or not username:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO archivebate_models(
                        model_key, username, profile_url, discovered_from, priority,
                        first_seen, updated_at
                    ) VALUES(?, ?, ?, 'catalog', 100, ?, ?)
                    """,
                    (key, username, f"https://archivebate.com/profile/{username}", now, now),
                )
                inserted += max(0, int(cur.rowcount or 0))
            return inserted

    def seed_discovery_queue(self) -> int:
        with self._lock:
            conn = self._get_conn()
            now = time.time()
            inserted = 0
            for ch in DISCOVERY_ALPHABET:
                if ch == " ":
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO archivebate_discovery_queue(prefix, depth, status, updated_at) VALUES(?, 1, 'pending', ?)",
                    (ch, now),
                )
                inserted += max(0, int(cur.rowcount or 0))
            return inserted

    def _store_profiles_locked(self, profiles: Iterable[Dict[str, Any]], discovered_from: str) -> int:
        conn = self._get_conn()
        now = time.time()
        inserted = 0
        for profile in profiles:
            username = str((profile or {}).get("username") or "").strip()
            key = _clean_author(username)
            if not key or not username:
                continue
            profile_url = str((profile or {}).get("profile_url") or f"https://archivebate.com/profile/{username}")
            cur = conn.execute(
                """
                INSERT INTO archivebate_models(
                    model_key, username, profile_url, discovered_from, priority,
                    first_seen, updated_at
                ) VALUES(?, ?, ?, ?, 200, ?, ?)
                ON CONFLICT(model_key) DO UPDATE SET
                    username = excluded.username,
                    profile_url = CASE WHEN excluded.profile_url != '' THEN excluded.profile_url ELSE archivebate_models.profile_url END,
                    priority = MAX(archivebate_models.priority, excluded.priority),
                    updated_at = MIN(archivebate_models.updated_at, excluded.updated_at)
                """,
                (key, username, profile_url, discovered_from, now, now),
            )
            inserted += max(0, int(cur.rowcount or 0))
        return inserted

    def discovery_step(self, scraper=None) -> bool:
        scraper = scraper or self._scraper
        if scraper is None:
            return False
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """
                SELECT * FROM archivebate_discovery_queue
                WHERE status IN ('pending', 'paging', 'retry')
                ORDER BY CASE status WHEN 'paging' THEN 0 WHEN 'retry' THEN 1 ELSE 2 END,
                         depth ASC, updated_at ASC, prefix ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return False
            prefix = str(row["prefix"])
            status = str(row["status"])
            page = int(row["next_page"] or 1)
            self._progress["current_prefix"] = prefix

        try:
            profiles, total, last_page = scraper._fetch_search_profiles(prefix, page=page)
            total = int(total or 0)
            last_page = max(1, int(last_page or 1))
            now = time.time()
            with self._lock:
                conn = self._get_conn()
                self._store_profiles_locked(profiles or [], f"search:{prefix}")
                depth = int(row["depth"] or len(prefix))
                if status in ("pending", "retry") and page == 1 and last_page > DISCOVERY_PAGE_SPLIT_THRESHOLD and depth < DISCOVERY_MAX_PREFIX_DEPTH:
                    for ch in DISCOVERY_ALPHABET:
                        if ch == " ":
                            continue
                        child = prefix + ch
                        conn.execute(
                            "INSERT OR IGNORE INTO archivebate_discovery_queue(prefix, depth, status, updated_at) VALUES(?, ?, 'pending', ?)",
                            (child, depth + 1, now),
                        )
                    conn.execute(
                        "UPDATE archivebate_discovery_queue SET status='split', total=?, last_page=?, attempts=attempts+1, updated_at=?, last_error=NULL WHERE prefix=?",
                        (total, last_page, now, prefix),
                    )
                elif page >= last_page:
                    conn.execute(
                        "UPDATE archivebate_discovery_queue SET status='done', total=?, last_page=?, next_page=?, attempts=attempts+1, updated_at=?, last_error=NULL WHERE prefix=?",
                        (total, last_page, page + 1, now, prefix),
                    )
                else:
                    conn.execute(
                        "UPDATE archivebate_discovery_queue SET status='paging', total=?, last_page=?, next_page=?, attempts=attempts+1, updated_at=?, last_error=NULL WHERE prefix=?",
                        (total, last_page, page + 1, now, prefix),
                    )
                self._progress["last_activity"] = now
                self._progress["last_error"] = None
            return True
        except Exception as exc:
            with self._lock:
                conn = self._get_conn()
                attempts = int(row["attempts"] or 0) + 1
                terminal = attempts >= 8
                conn.execute(
                    "UPDATE archivebate_discovery_queue SET status=?, attempts=?, updated_at=?, last_error=? WHERE prefix=?",
                    ("error" if terminal else "retry", attempts, time.time(), str(exc), prefix),
                )
                self._progress["last_error"] = str(exc)
            return True
        finally:
            self._progress["current_prefix"] = None

    def _profile_batch(self, scraper, username: str, page: int) -> List[Dict[str, Any]]:
        cache_key = f"ab_model:{username}:{page}"
        last: List[Dict[str, Any]] = []
        for attempt in range(3):
            try:
                if hasattr(scraper, "_cache"):
                    scraper._cache.pop(cache_key, None)
                batch = scraper.get_archivebate_model_videos(username, page=page)
                if isinstance(batch, list) and batch:
                    return batch
                last = batch if isinstance(batch, list) else []
            except Exception:
                last = []
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
        return last

    @staticmethod
    def _batch_signature(videos: List[Dict[str, Any]]) -> str:
        ids = []
        for video in videos:
            ids.append(str(video.get("id") or video.get("url") or json.dumps(video, sort_keys=True, ensure_ascii=False)))
        return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()

    def _store_videos_locked(self, videos: List[Dict[str, Any]]) -> int:
        if not videos:
            return 0
        from catalog_service import extract_item_metadata

        conn = self._get_conn()
        now = time.time()
        new_records: List[Tuple[Any, ...]] = []
        for video in videos:
            record = extract_item_metadata(video, 0)
            source = str(record[1] or "archivebate")
            if source != "archivebate":
                continue
            deep_record = record[:-1] + (now,)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO archivebate_deep_items(
                    canonical_key, source, video_id, author, author_clean,
                    published_at, duration_seconds, duration_str, poster, url,
                    preview_video, title, platform, raw_json, discovered_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                deep_record,
            )
            if int(cur.rowcount or 0) > 0:
                new_records.append(record)

        if not new_records:
            return 0

        targets = self._target_revisions_locked()
        for revision in targets:
            revision_records = [rec[:-1] + (revision,) for rec in new_records]
            conn.executemany(
                """
                INSERT OR IGNORE INTO catalog_items(
                    canonical_key, source, video_id, author, author_clean,
                    published_at, duration_seconds, duration_str, poster, url,
                    preview_video, title, platform, raw_json, revision
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                revision_records,
            )
            cnt = int(conn.execute("SELECT COUNT(*) AS cnt FROM catalog_items WHERE revision = ?", (revision,)).fetchone()["cnt"] or 0)
            conn.execute("UPDATE revisions SET video_count = ?, updated_at = ? WHERE revision = ?", (cnt, now, revision))
        self._last_targets = targets
        return len(new_records)

    def crawl_step(self, scraper=None) -> bool:
        scraper = scraper or self._scraper
        if scraper is None:
            return False
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """
                SELECT * FROM archivebate_models
                WHERE crawl_complete = 0
                ORDER BY priority DESC, updated_at ASC, pages_scanned ASC, model_key ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return False
            model_key = str(row["model_key"])
            username = str(row["username"])
            page = max(1, int(row["next_page"] or 1))
            self._progress["current_model"] = username

        try:
            batch = self._profile_batch(scraper, username, page)
            now = time.time()
            with self._lock:
                conn = self._get_conn()
                if batch:
                    signature = self._batch_signature(batch)
                    last_signature = str(row["last_signature"] or "")
                    repeated = int(row["repeated_signatures"] or 0) + 1 if signature == last_signature else 0
                    new_count = self._store_videos_locked(batch)
                    if repeated >= PROFILE_REPEAT_END_THRESHOLD - 1:
                        conn.execute(
                            """
                            UPDATE archivebate_models
                            SET pages_scanned=pages_scanned+1, videos_found=videos_found+?,
                                last_signature=?, repeated_signatures=?, empty_streak=0,
                                crawl_complete=1, end_reason='repeated_page', last_error=NULL, updated_at=?
                            WHERE model_key=?
                            """,
                            (new_count, signature, repeated, now, model_key),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE archivebate_models
                            SET next_page=?, pages_scanned=pages_scanned+1, videos_found=videos_found+?,
                                last_signature=?, repeated_signatures=?, empty_streak=0,
                                last_error=NULL, updated_at=?
                            WHERE model_key=?
                            """,
                            (page + 1, new_count, signature, repeated, now, model_key),
                        )
                else:
                    empty_streak = int(row["empty_streak"] or 0) + 1
                    complete = empty_streak >= PROFILE_EMPTY_END_THRESHOLD
                    conn.execute(
                        """
                        UPDATE archivebate_models
                        SET next_page=?, pages_scanned=pages_scanned+1, empty_streak=?,
                            crawl_complete=?, end_reason=?, last_error=NULL, updated_at=?
                        WHERE model_key=?
                        """,
                        (
                            page + 1,
                            empty_streak,
                            int(complete),
                            f"consecutive_empty_pages:{PROFILE_EMPTY_END_THRESHOLD}:{page}" if complete else None,
                            now,
                            model_key,
                        ),
                    )
                self._progress["last_activity"] = now
                self._progress["last_error"] = None
            return True
        except Exception as exc:
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    "UPDATE archivebate_models SET last_error=?, updated_at=? WHERE model_key=?",
                    (str(exc), time.time(), model_key),
                )
                self._progress["last_error"] = str(exc)
            return True
        finally:
            self._progress["current_model"] = None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._get_conn()
            models = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_models").fetchone()["cnt"] or 0)
            completed_models = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_models WHERE crawl_complete=1").fetchone()["cnt"] or 0)
            deep_items = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_deep_items").fetchone()["cnt"] or 0)
            q = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM archivebate_discovery_queue GROUP BY status"
            ).fetchall()
            queue = {str(row["status"]): int(row["cnt"] or 0) for row in q}
            targets = self._target_revisions_locked()
            return {
                **self._progress,
                "models_discovered": models,
                "models_complete": completed_models,
                "models_pending": max(0, models - completed_models),
                "deep_items": deep_items,
                "discovery": queue,
                "target_revisions": list(targets),
            }

    def _worker(self) -> None:
        self._progress["running"] = True
        try:
            self.seed_catalog_models()
            self.seed_discovery_queue()
            self._refresh_targets()
            while not self._stop.is_set():
                worked = False
                worked = self.discovery_step(self._scraper) or worked
                if self._stop.wait(self.request_delay):
                    break
                worked = self.crawl_step(self._scraper) or worked
                self._refresh_targets()
                if self._stop.wait(self.request_delay if worked else max(2.0, self.request_delay)):
                    break
        except Exception as exc:
            self._progress["last_error"] = str(exc)
        finally:
            self._progress["running"] = False
            self._progress["current_prefix"] = None
            self._progress["current_model"] = None

    def start(self, scraper) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._scraper = scraper
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker, name="archivebate-deep-crawler", daemon=True)
            self._thread.start()
            return True

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        thread = self._thread
        if wait and thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)


deep_archivebate_service = DeepArchivebateService()
