from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "98efebdb4cab21d17f46fbeae4183f7841bfd8b6"


def p(rel: str) -> Path:
    return ROOT / rel


def read(rel: str) -> str:
    return p(rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    target = p(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    if old not in text:
        raise RuntimeError(f"Expected source fragment not found in {rel}: {old[:120]!r}")
    write(rel, text.replace(old, new, 1))


def regex_once(rel: str, pattern: str, replacement: str, flags: int = 0) -> None:
    text = read(rel)
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"Expected exactly one regex match in {rel}, got {count}: {pattern[:120]!r}")
    write(rel, new_text)


def append_once(rel: str, marker: str, addition: str) -> None:
    text = read(rel)
    if marker in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    write(rel, text + addition)


# ---------------------------------------------------------------------------
# P1: credentials fail closed. Never copy or repeat the old embedded values.
# ---------------------------------------------------------------------------
config = read("config.py")
config = config.replace(
    "4) bezpieczny domyślny fallback akiraaibabe@gmail.com.",
    "4) brak kompletnej jawnej konfiguracji: tryb anonimowy (puste dane).",
)
config, count = re.subn(
    r"\n    # Bezpieczny fallback do domyślnego konta użytkownika\n"
    r"    if not email:\n        email = \"[^\"]*\"\n"
    r"    if not password:\n        password = \"[^\"]*\"\n\n"
    r"    return email, password\n",
    "\n    # Brak kompletnej jawnej konfiguracji zawsze oznacza tryb anonimowy.\n"
    "    # Nie wybieramy żadnej domyślnej tożsamości i nie inicjujemy zdalnego loginu.\n"
    "    return \"\", \"\"\n",
    config,
    count=1,
)
if count != 1:
    raise RuntimeError("config.py: embedded credential fallback pattern not found")
write("config.py", config)


# ---------------------------------------------------------------------------
# P2: durable UserStorage contract: exception propagation + in-memory rollback.
# ---------------------------------------------------------------------------
replace_once("storage.py", "import os\n", "import os\nimport copy\n")
replace_once(
    "storage.py",
    '''def _locked_method(fn):
    """Serializuje operacje modyfikujące magazyn; RLock pozwala na zagnieżdżone wywołania."""
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped
''',
    '''def _locked_method(fn):
    """Serializuje mutację i przy błędzie przywraca ostatni stan in-memory.

    Durable write jest częścią kontraktu sukcesu. Jeżeli zapis na dysk zawiedzie,
    wyjątek wraca do warstwy API, a pamięć nie udaje stanu, którego nie da się
    odtworzyć po restarcie.
    """
    def wrapped(self, *args, **kwargs):
        with self._lock:
            snapshot = copy.deepcopy(self.data)
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                self.data = snapshot
                raise
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped
''',
)
replace_once(
    "storage.py",
    '''    def save(self):
        """Atomowy, odporny na przerwanie zapis JSON. RLock chroni równoległe mutacje."""
        try:
            with self._lock:
                atomic_write_json(STORE_FILE, self.data)
        except Exception as e:
            logger.error(f"Błąd zapisu magazynu danych: {e}")
''',
    '''    def save(self) -> bool:
        """Atomowo zapisuje stan; błąd durable commit jest błędem operacji."""
        with self._lock:
            atomic_write_json(STORE_FILE, self.data)
        return True
''',
)


# Minor durability sibling: model tag cache keeps dirty state until write succeeds.
replace_once("model_tags.py", "import requests\n", "import requests\nfrom cache_store import atomic_write_json\n")
replace_once(
    "model_tags.py",
    '''                        if self._dirty:
                            self._save()
                            self._dirty = False
''',
    '''                        if self._dirty and self._save():
                            self._dirty = False
''',
)
replace_once(
    "model_tags.py",
    '''    def _save(self):
        try:
            with open(MODEL_TAGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._db, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Błąd zapisu model_tags.json: {e}")
''',
    '''    def _save(self) -> bool:
        try:
            atomic_write_json(MODEL_TAGS_FILE, self._db)
            return True
        except Exception as e:
            logger.warning(f"Błąd zapisu model_tags.json: {e}")
            return False
''',
)
replace_once(
    "model_tags.py",
    '''                self._last_save = now
                self._save()
                self._dirty = False
''',
    '''                self._last_save = now
                if self._save():
                    self._dirty = False
''',
)


# ---------------------------------------------------------------------------
# P3: account synchronization returns typed outcomes; remote fetch can be strict.
# ---------------------------------------------------------------------------
regex_once(
    "scraper.py",
    r"    def get_account_section_videos\(self, endpoint: str, max_pages: int = 12\) -> List\[Dict\[str, Any\]\]:.*?\n    def toggle_remote_save",
    '''    def get_account_section_videos(self, endpoint: str, max_pages: int = 12, strict: bool = False) -> List[Dict[str, Any]]:
        """Pobiera listę sekcji konta; w trybie strict nie zamienia awarii na pustą listę."""
        if not self.session.is_logged_in:
            logged = self.session.login()
            if not logged:
                if strict:
                    raise RuntimeError("account_auth_failed")
                return []

        def fetch_page(p: int):
            url = f"https://archivebate.com/{endpoint}?page={p}" if p > 1 else f"https://archivebate.com/{endpoint}"
            try:
                r = self.session.session.get(url, timeout=12)
                if hasattr(r, "raise_for_status"):
                    r.raise_for_status()
                if "login" in str(getattr(r, "url", "")):
                    if not self.session.login():
                        raise RuntimeError("account_auth_failed")
                    r = self.session.session.get(url, timeout=12)
                    if hasattr(r, "raise_for_status"):
                        r.raise_for_status()
                    if "login" in str(getattr(r, "url", "")):
                        raise RuntimeError("account_auth_redirect")
                sections = re.findall(r'<section class="video_item">.*?</section>', r.text, re.DOTALL)
                result = []
                for section in sections:
                    parsed = self.parse_video_card(section)
                    if parsed:
                        result.append(parsed)
                return result
            except Exception as e:
                logger.error(f"Błąd pobierania {endpoint} strona {p}: {e}")
                if strict:
                    raise RuntimeError(f"account_fetch_failed:{endpoint}:{p}") from e
                return []

        with ThreadPoolExecutor(max_workers=10) as executor:
            page_results = list(executor.map(fetch_page, range(1, max_pages + 1)))

        all_videos = []
        seen_ids = set()
        for batch in page_results:
            for v in batch:
                if v and v.get("id") and v["id"] not in seen_ids:
                    seen_ids.add(v["id"])
                    all_videos.append(v)

        return sort_videos_newest_first(all_videos)

    def toggle_remote_save''',
    flags=re.S,
)

regex_once(
    "main.py",
    r"def sync_account_data\(\):.*?\n@asynccontextmanager",
    '''_account_sync_lock = threading.Lock()


def _account_counts() -> dict:
    return {
        "favorites_count": len(storage.get_favorites()),
        "history_count": len(storage.get_history()),
        "following_count": len(storage.get_following()),
        "last_synced": storage.data.get("last_synced"),
    }


def sync_account_data() -> dict:
    """Synchronizuje konto i zawsze zwraca strukturalny wynik operacji."""
    if not _account_sync_lock.acquire(blocking=False):
        return {"success": False, "status": "busy", **_account_counts()}
    try:
        print("[Archivebate Browser] Głęboka synchronizacja danych konta online...")
        if not session.email or not session.password:
            return {"success": False, "status": "not_configured", **_account_counts()}
        if not session.is_logged_in and not session.login():
            return {
                "success": False,
                "status": "auth_failed",
                "error": session.get_status().get("login_error") or "Nie udało się zalogować",
                **_account_counts(),
            }

        watchlater = scraper.get_account_section_videos("watchlater", max_pages=15, strict=True)
        history = scraper.get_account_section_videos("history", max_pages=15, strict=True)
        following = scraper.get_account_section_videos("following", max_pages=15, strict=True)
        storage.merge_remote_data(watchlater, history, following)
        print(f"[Archivebate Browser] Zsynchronizowano: {len(watchlater)} ulubionych, {len(history)} historii, {len(following)} obserwowanych.")
        return {
            "success": True,
            "status": "synced",
            "remote_counts": {
                "favorites": len(watchlater),
                "history": len(history),
                "following": len(following),
            },
            **_account_counts(),
        }
    except Exception as e:
        print(f"[Archivebate Browser] Błąd synchronizacji: {e}")
        return {"success": False, "status": "dependency_failed", "error": str(e), **_account_counts()}
    finally:
        _account_sync_lock.release()


@asynccontextmanager''',
    flags=re.S,
)

# Offload/coalesce automatic sync from every async GET path.
insert_marker = "# ENDPOINTY PANELU KONTA\n"
main_text = read("main.py")
if "async def _ensure_account_synced" not in main_text:
    main_text = main_text.replace(
        insert_marker,
        insert_marker
        + "async def _ensure_account_synced():\n"
        + "    if storage.data.get(\"last_synced\"):\n"
        + "        return None\n"
        + "    return await asyncio.to_thread(sync_account_data)\n\n",
        1,
    )
main_text = main_text.replace("        sync_account_data()\n", "        await _ensure_account_synced()\n", 4)
write("main.py", main_text)

regex_once(
    "main.py",
    r"@app.post\(\"/api/account/favorites/toggle\"\)\ndef toggle_favorite\(video: dict = Body\(\.\.\.\)\):.*?\n\n@app.get\(\"/api/account/history\"\)",
    '''@app.post("/api/account/favorites/toggle")
def toggle_favorite(video: dict = Body(...)):
    """Commituje lokalnie i jawnie raportuje wynik efektu zdalnego."""
    try:
        is_fav = storage.toggle_favorite(video)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Nie udało się trwale zapisać ulubionych: {exc}")

    v_id = str(video.get("id") or "")
    remote_required = bool(session.email and session.password and v_id)
    remote_synced = None
    if remote_required:
        remote_synced = bool(scraper.toggle_remote_save(v_id))
    sync_state = "local_only" if not remote_required else ("synced" if remote_synced else "remote_failed")
    invalidate_feed_cache("fav")
    return {
        "success": bool(not remote_required or remote_synced),
        "local_committed": True,
        "remote_synced": remote_synced,
        "sync_state": sync_state,
        "id": v_id,
        "is_favorite": is_fav,
        "total_favorites": len(storage.get_favorites()),
        "favorite_authors": storage.get_favorite_authors()
    }

@app.get("/api/account/history")''',
    flags=re.S,
)

regex_once(
    "main.py",
    r"@app.post\(\"/api/account/sync\"\)\ndef sync_account\(\):.*?\n\n@app.get\(\"/api/tags\"\)",
    '''@app.post("/api/account/sync")
def sync_account():
    """Wymusza synchronizację i zachowuje realny wynik auth/fetch/persistence."""
    return sync_account_data()

@app.get("/api/tags")''',
    flags=re.S,
)

# History writes also must not acknowledge a failed durable commit.
replace_once(
    "main.py",
    '''def record_history(video: dict = Body(...)):
    """Zapisuje obejrzenie filmu w historii."""
    storage.record_history(video)
    return {"success": True, "total_history": len(storage.get_history())}
''',
    '''def record_history(video: dict = Body(...)):
    """Zapisuje obejrzenie filmu w historii."""
    try:
        storage.record_history(video)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Nie udało się trwale zapisać historii: {exc}")
    return {"success": True, "total_history": len(storage.get_history())}
''',
)
replace_once(
    "main.py",
    '''def clear_history():
    """Czyści lokalną historię oglądania."""
    storage.clear_history()
    return {"success": True, "total_history": 0}
''',
    '''def clear_history():
    """Czyści lokalną historię oglądania."""
    try:
        storage.clear_history()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Nie udało się trwale wyczyścić historii: {exc}")
    return {"success": True, "total_history": 0}
''',
)


# ---------------------------------------------------------------------------
# P5: catalog correctness/recovery and stable identities.
# ---------------------------------------------------------------------------
replace_once("catalog_service.py", "import json\n", "import json\nimport hashlib\n")
replace_once(
    "catalog_service.py",
    '        return f"{source}:id:{raw_id}" if raw_id else f"{source}:hash:{hash(json.dumps(video, sort_keys=True))}"\n',
    '        if raw_id:\n            return f"{source}:id:{raw_id}"\n        canonical = json.dumps(video, sort_keys=True, ensure_ascii=False, separators=(",", ":"))\n        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()\n        return f"{source}:hash:{digest}"\n',
)
replace_once(
    "catalog_service.py",
    "DEFAULT_CATALOG_DB = DATA_DIR / \"catalog.db\"\n",
    "DEFAULT_CATALOG_DB = DATA_DIR / \"catalog.db\"\nMAX_PAGES_PER_SOURCE = {\"archivebate\": 1000, \"camwhores\": 500}\n",
)
replace_once(
    "catalog_service.py",
    '            row = conn.execute("SELECT revision FROM revisions WHERE is_active = 1 LIMIT 1").fetchone()\n',
    '            row = conn.execute("SELECT revision FROM revisions WHERE is_active = 1 AND failed = 0 LIMIT 1").fetchone()\n',
)
replace_once(
    "catalog_service.py",
    '            row_comp = conn.execute("SELECT revision FROM revisions WHERE complete = 1 ORDER BY updated_at DESC, revision DESC LIMIT 1").fetchone()\n',
    '            row_comp = conn.execute("SELECT revision FROM revisions WHERE complete = 1 AND failed = 0 ORDER BY updated_at DESC, revision DESC LIMIT 1").fetchone()\n',
)
replace_once(
    "catalog_service.py",
    '''    def is_revision_complete(self, revision: int) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT complete FROM revisions WHERE revision = ?", (revision,)).fetchone()
        return bool(row and row["complete"])
''',
    '''    def is_revision_complete(self, revision: int) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT complete, failed FROM revisions WHERE revision = ?", (revision,)).fetchone()
        return bool(row and row["complete"] and not row["failed"])
''',
)
# Extend revision stats with failure state.
replace_once(
    "catalog_service.py",
    '''                "updated_at": float(row["updated_at"]),
                "indexing_progress": self._indexing_progress,
            }
''',
    '''                "updated_at": float(row["updated_at"]),
                "failed": bool(row["failed"]),
                "error": row["error"],
                "state": "failed" if row["failed"] else ("complete" if row["complete"] else "building"),
                "indexing_progress": self._indexing_progress,
            }
''',
)
replace_once(
    "catalog_service.py",
    '''            conn.execute(
                "UPDATE revisions SET failed = 1, error = ?, updated_at = ? WHERE revision = ?",
                (error_msg, time.time(), revision),
            )
''',
    '''            conn.execute(
                "UPDATE revisions SET failed = 1, complete = 0, is_active = 0, error = ?, updated_at = ? WHERE revision = ?",
                (error_msg, time.time(), revision),
            )
''',
)
# Query projection must never turn a failed/partial revision into complete/no-error.
replace_once(
    "catalog_service.py",
    '''            is_complete = bool(rev_info["complete"]) if rev_info else False
            updated_at = float(rev_info["updated_at"]) if rev_info else 0.0
''',
    '''            is_failed = bool(rev_info["failed"]) if rev_info else False
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
''',
)
replace_once(
    "catalog_service.py",
    '''            "catalog_complete": is_complete,
            "updated_at": updated_at,
''',
    '''            "catalog_complete": is_complete,
            "catalog_state": "failed" if is_failed else ("complete" if is_complete else "partial"),
            "updated_at": updated_at,
''',
)
replace_once(
    "catalog_service.py",
    '''            "complete": is_complete or len(items) >= ps or not has_more,
''',
    '''            "complete": is_complete,
''',
)
replace_once(
    "catalog_service.py",
    '''            "source_error": {},
            "retryable": False,
''',
    '''            "source_error": source_error,
            "retryable": is_failed,
''',
)
# Hard ceilings are truncation/failure, never verified natural end.
replace_once(
    "catalog_service.py",
    '''        # Maximum pages safety bound per source
        max_pages = {"archivebate": 1000, "camwhores": 500}
''',
    '''        # Safety bound protects resources, but reaching it is truncation, not a verified source end.
        max_pages = MAX_PAGES_PER_SOURCE
''',
)
replace_once(
    "catalog_service.py",
    '''                if page > max_pages.get(source, 1000):
                    ended.add(source)
                    continue
''',
    '''                if page > max_pages.get(source, 1000):
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
''',
)

# Bootstrap waits in its own background wrapper for the actual inner worker result.
regex_once(
    "main.py",
    r"def _ensure_catalog_indexing\(service\) -> None:.*?\n\ndef _get_fav_authors_feed",
    '''def _ensure_catalog_indexing(service) -> None:
    """Schedule indexing and tie retry state to the actual inner worker outcome."""
    if _published_catalog_revision(service) is not None:
        return
    service_key = id(service)
    with _catalog_bootstrap_lock:
        now = time.monotonic()
        if service_key in _catalog_bootstrap_active:
            return
        if service_key in _catalog_bootstrap_started and now < _catalog_bootstrap_retry_at.get(service_key, float("inf")):
            return
        _catalog_bootstrap_started.add(service_key)
        _catalog_bootstrap_active.add(service_key)

    def worker():
        revision = None
        try:
            service.import_cached_raw_pages()
            if _published_catalog_revision(service) is None:
                revision = service.build_revision_background(_catalog_fetchers(), force=False)
                inner = getattr(service, "_indexing_thread", None)
                if inner and inner is not threading.current_thread():
                    inner.join()
            stats = service.get_revision_stats(revision) if revision else {}
            with _catalog_bootstrap_lock:
                if stats.get("complete") and not stats.get("failed"):
                    _catalog_bootstrap_retry_at[service_key] = float("inf")
                else:
                    _catalog_bootstrap_retry_at[service_key] = time.monotonic() + 30.0
        except Exception as exc:
            with _catalog_bootstrap_lock:
                _catalog_bootstrap_retry_at[service_key] = time.monotonic() + 30.0
            print(f"[Catalog] Błąd bootstrapu indeksu: {exc}")
        finally:
            with _catalog_bootstrap_lock:
                _catalog_bootstrap_active.discard(service_key)

    threading.Thread(target=worker, name="catalog-bootstrap", daemon=True).start()


def _get_fav_authors_feed''',
    flags=re.S,
)

# Force refresh gets an explicit operation/revision token instead of silently pinning old data.
main_text = read("main.py")
old_force = '''    if force_refresh:
        catalog_service.build_revision_background(_catalog_fetchers(), force=True)
'''
if main_text.count(old_force) != 2:
    raise RuntimeError(f"Expected 2 force-refresh sites, got {main_text.count(old_force)}")
main_text = main_text.replace(
    old_force,
    '''    refresh_revision = None
    if force_refresh:
        refresh_revision = catalog_service.build_revision_background(_catalog_fetchers(), force=True)
''',
)
# /api/videos response metadata.
main_text = main_text.replace(
    '''            "snapshot_id": res["snapshot_id"],
            "revision": res["revision"]
        }
''',
    '''            "snapshot_id": res["snapshot_id"],
            "revision": res["revision"],
            "refresh_revision": refresh_revision,
            "refresh_pending": bool(refresh_revision and refresh_revision != res["catalog_revision"])
        }
''',
    1,
)
# /api/feed result metadata.
old_return = '''        return catalog_service.query_page(
            page=page,
            page_size=HOME_PAGE_SIZE,
            source=source,
            author_filter=author_filter,
            group_authors=is_grouped,
            revision=rev_to_use,
            blocked_models=blocked_models,
            favorite_authors=fav_authors,
            favorite_ids=fav_ids,
            enrich_fn=lambda items: _enrich_videos(items, author_filter=author_filter, source=source, group_authors="0")
        )

    return _feed_snapshot(source, author_filter, group_authors, snapshot_id, force_refresh).read(page)
'''
new_return = '''        result = catalog_service.query_page(
            page=page,
            page_size=HOME_PAGE_SIZE,
            source=source,
            author_filter=author_filter,
            group_authors=is_grouped,
            revision=rev_to_use,
            blocked_models=blocked_models,
            favorite_authors=fav_authors,
            favorite_ids=fav_ids,
            enrich_fn=lambda items: _enrich_videos(items, author_filter=author_filter, source=source, group_authors="0")
        )
        result["refresh_revision"] = refresh_revision
        result["refresh_pending"] = bool(refresh_revision and refresh_revision != result.get("catalog_revision"))
        return result

    return _feed_snapshot(source, author_filter, group_authors, snapshot_id, force_refresh).read(page)
'''
if old_return not in main_text:
    raise RuntimeError("progressive_feed return block not found")
main_text = main_text.replace(old_return, new_return, 1)
# An explicit stream revision follows that revision, not whatever old active revision is published.
main_text = main_text.replace(
    '''                published = _published_catalog_revision(catalog_service)
                current_rev = published if published is not None else rev_to_check
''',
    '''                current_rev = rev_to_check
''',
    1,
)
write("main.py", main_text)


# Frontend follows refresh_revision while still rendering the previous revision until new batches arrive.
video_views = read("static/video-views.js")
video_views = video_views.replace(
    '''      state.feedSpecKey = specKey;
      state.feedSnapshotId = data.snapshot_id;
      state.catalogRevision = data.catalog_revision !== undefined ? data.catalog_revision : data.revision;
''',
    '''      state.feedSpecKey = specKey;
      state.feedSnapshotId = data.snapshot_id;
      state.catalogRevision = data.catalog_revision !== undefined ? data.catalog_revision : data.revision;
      const streamRevision = data.refresh_revision || state.catalogRevision;
      const streamSnapshotId = data.refresh_revision ? String(data.refresh_revision) : data.snapshot_id;
      if (data.refresh_pending) setFeedRefreshingIndicator(true);
''',
    1,
)
video_views = video_views.replace(
    '''      const catalogRevisionStream = data.catalog_complete === false && /^\\d+$/.test(String(data.snapshot_id || ''));
      if (catalogRevisionStream || !data.complete) {
        const stream = new EventSource(`/api/feed/stream?${params}&snapshot_id=${encodeURIComponent(data.snapshot_id)}`);
''',
    '''      const catalogRevisionStream = data.catalog_complete === false && /^\\d+$/.test(String(streamSnapshotId || ''));
      if (data.refresh_pending || catalogRevisionStream || !data.complete) {
        const streamRevisionParam = streamRevision ? `&revision=${encodeURIComponent(streamRevision)}` : '';
        const stream = new EventSource(`/api/feed/stream?${params}${streamRevisionParam}&snapshot_id=${encodeURIComponent(streamSnapshotId)}`);
''',
    1,
)
write("static/video-views.js", video_views)


# ---------------------------------------------------------------------------
# P1 safety: no process may be killed merely because it owns port 8000.
# ---------------------------------------------------------------------------
write(
    "desktop_app.py",
    '''import os
import socket
import threading
import time
import urllib.request

import uvicorn
import webview

os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--autoplay-policy=no-user-gesture-required"


def ensure_port_available(host="127.0.0.1", port=8000):
    """Fail safely on a bind conflict. Never terminate an unowned process."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} jest zajęty. Zamknij poprzednią instancję aplikacji lub proces używający portu; nic nie zostało automatycznie zakończone."
        ) from exc
    finally:
        probe.close()


def start_server():
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False, log_level="warning")


def wait_for_server(url="http://127.0.0.1:8000", timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


if __name__ == "__main__":
    print("=" * 60)
    print("   ARCHIVEBATE & CAMWHORES PRO (Aplikacja Pulpitowa)")
    print("   Uruchamianie natywnego okna bez przeglądarki...")
    print("=" * 60)
    try:
        ensure_port_available()
    except RuntimeError as exc:
        print(f"[START] {exc}")
        raise SystemExit(2)

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    if not wait_for_server():
        print("[START] Serwer nie zgłosił gotowości w wymaganym czasie.")
        raise SystemExit(3)

    webview.create_window(
        title="Archivebate & Camwhores Desktop",
        url="http://127.0.0.1:8000",
        width=1440,
        height=920,
        min_size=(960, 640),
        background_color="#0a0e17"
    )
    webview.start(private_mode=False)
''',
)
write(
    "run.py",
    '''import socket
import sys
import time
import webbrowser
import threading
import uvicorn


def ensure_port_available(host="127.0.0.1", port=8000):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform.startswith("win") and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"Port {port} jest zajęty; nie zakończono żadnego obcego procesu.") from exc
    finally:
        probe.close()


def open_browser():
    time.sleep(1.2)
    url = "http://127.0.0.1:8000"
    print(f"\\n[Archivebate Browser] Otwieranie aplikacji w przeglądarce: {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    print("=" * 60)
    print("   ARCHIVEBATE VIDEO BROWSER (GUI)")
    print("   Logowanie: konfiguracja z .env.local / zmiennych środowiskowych")
    print("=" * 60)
    try:
        ensure_port_available()
    except RuntimeError as exc:
        print(f"[START] {exc}")
        raise SystemExit(2)
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
''',
)
web_bat = '''@echo off
title Archivebate Video Browser
cd /d "%~dp0"

echo ============================================================
echo      ARCHIVEBATE VIDEO BROWSER
echo ============================================================
echo [1/2] Sprawdzanie bibliotek Pythona...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 exit /b 1

echo [2/2] Startowanie serwera i otwieranie przegladarki...
echo Port 8000 jest sprawdzany bezpiecznie przez run.py; obce procesy nie sa zabijane.
python run.py
pause
'''
write("start.bat", web_bat)
write("URUCHOM_PROGRAM.bat", web_bat)
write(
    "Uruchom_Desktop.bat",
    '''@echo off
title Archivebate Desktop Pro
cd /d "%~dp0"

echo ============================================================
echo      ARCHIVEBATE ^& CAMWHORES DESKTOP PRO
echo ============================================================
echo Port 8000 jest sprawdzany bezpiecznie przez desktop_app.py.
echo Obce procesy nie sa automatycznie zabijane.
python -m pip install -r requirements.txt --quiet
if errorlevel 1 exit /b 1
python desktop_app.py
pause
''',
)

# Resolve the packaging candidate: desktop dependency is explicit.
req = read("requirements.txt")
if "pywebview" not in req.lower():
    if not req.endswith("\n"):
        req += "\n"
    req += "pywebview>=5.0.0\n"
write("requirements.txt", req)


# ---------------------------------------------------------------------------
# P6: storyboard validation + decoded PTS timing; invalidate legacy cache.
# ---------------------------------------------------------------------------
replace_once("storyboard_service.py", "import os\n", "import os\nimport re\n")
replace_once("storyboard_service.py", "STORYBOARD_VERSION = 5\n", "STORYBOARD_VERSION = 6\n")
regex_once(
    "storyboard_service.py",
    r"def _extract_segment_frames\(.*?\n\ndef _nearest_success",
    '''def _cleanup_segment_frames(tmp_dir: Path) -> None:
    for frame in tmp_dir.glob("frame_*.jpg"):
        try:
            frame.unlink()
        except OSError:
            pass


def _valid_segment_frame(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 350:
            return False
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _run_segment_extract(
    ffmpeg: str,
    source_url: str,
    start_time: float,
    target_duration: float,
    tmp_dir: Path,
    timeout: int,
    seek_before_input: bool,
) -> Tuple[List[Path], List[float], bool]:
    _cleanup_segment_frames(tmp_dir)
    vf = (
        f"setpts=PTS-STARTPTS,fps={SEGMENT_FPS},showinfo,"
        f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_WIDTH}:{FRAME_HEIGHT}"
    )
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin"]
    if seek_before_input:
        cmd += ["-ss", f"{start_time:.3f}"]
    cmd += ["-i", source_url]
    if not seek_before_input:
        cmd += ["-ss", f"{start_time:.3f}"]
    cmd += [
        "-t", f"{target_duration:.3f}",
        "-vf", vf,
        "-an", "-sn", "-dn",
        "-threads", "1",
        "-q:v", "7",
        "-y", str(tmp_dir / "frame_%03d.jpg"),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            errors="replace",
        )
    except Exception:
        _cleanup_segment_frames(tmp_dir)
        return [], [], False

    frames = sorted(tmp_dir.glob("frame_*.jpg"))
    if result.returncode != 0 or not frames or any(not _valid_segment_frame(frame) for frame in frames):
        _cleanup_segment_frames(tmp_dir)
        return [], [], False

    stderr = result.stderr or ""
    pts = []
    for match in re.finditer(r"pts_time:([+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+))", stderr):
        try:
            pts.append(float(match.group(1)))
        except ValueError:
            pass
    exact = len(pts) >= len(frames)
    if exact:
        times = [min(float(start_time) + max(0.0, pts[i]), float(start_time) + float(target_duration)) for i in range(len(frames))]
    else:
        times = [float(start_time) + i * (1.0 / SEGMENT_FPS) for i in range(len(frames))]
    return frames, times, exact


def _extract_segment_frames_with_times(
    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25
) -> Tuple[List[Path], List[float], bool]:
    first = _run_segment_extract(ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout, True)
    if first[0]:
        return first
    return _run_segment_extract(ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout, False)


def _extract_segment_frames(
    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25
) -> List[Path]:
    """Compatibility wrapper; non-zero FFmpeg output is never accepted."""
    frames, _, _ = _extract_segment_frames_with_times(ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout)
    return frames


def _nearest_success''',
    flags=re.S,
)
replace_once(
    "storyboard_service.py",
    '''        frames = _extract_segment_frames(ffmpeg, source_url, start_time, seg_duration, tmp)
        if not frames:
            raise RuntimeError(f"FFmpeg nie wygenerował klatek dla segmentu {segment_index}")
''',
    '''        frames, decoded_times, precise_timing = _extract_segment_frames_with_times(
            ffmpeg, source_url, start_time, seg_duration, tmp
        )
        if not frames:
            raise RuntimeError(f"FFmpeg nie wygenerował poprawnych klatek dla segmentu {segment_index}")
''',
)
replace_once(
    "storyboard_service.py",
    '''    times = [round(min(duration, start_time + i * (1.0 / SEGMENT_FPS)), 3) for i in range(frame_count)]
    meta = {
''',
    '''    times = [round(min(float(duration), float(t)), 3) for t in decoded_times[:frame_count]]
    meta = {
''',
)
replace_once(
    "storyboard_service.py",
    '''        "approximate": False,
        "time_precision": "1s_dense_segment",
''',
    '''        "approximate": not precise_timing,
        "time_precision": "decoded_pts_1fps" if precise_timing else "nominal_1fps_fallback",
''',
)

# Package-D oracle now checks observed frames + decoded PTS and non-zero rejection.
package_d = read("audit/regression_package_d.py")
package_d = package_d.replace("from unittest.mock import patch, MagicMock\n", "from unittest.mock import patch, MagicMock\nfrom types import SimpleNamespace\n")
package_d = package_d.replace(
    '''        assert len(frames) == 10, f"Oczekiwano 10 klatek, otrzymano {len(frames)}"
        print("PASS 1: Jedno wywołanie FFmpeg wyodrębniło 10 klatek segmentu 1 fps")
''',
    '''        assert len(frames) == 10, f"Oczekiwano 10 klatek, otrzymano {len(frames)}"
        # Niezależny oracle: piksele po dekodowaniu muszą rosnąć zgodnie z numerem sekundy.
        reds = []
        for frame_path in frames:
            with Image.open(frame_path).convert("RGB") as observed:
                reds.append(sum(px[0] for px in observed.resize((1, 1)).getdata()))
        assert all(reds[i] <= reds[i + 1] + 8 for i in range(len(reds) - 1)), reds
        print("PASS 1: FFmpeg wyodrębnił 10 uporządkowanych klatek segmentu 1 fps")

    # Non-zero FFmpeg nie może zostać uznany za sukces nawet gdy zostawił poprawny plik JPG.
    with tempfile.TemporaryDirectory(dir=root) as failed_tmp:
        failed_dir = Path(failed_tmp)
        def fake_failed_run(cmd, **kwargs):
            output_pattern = next(str(part) for part in cmd if "frame_%03d.jpg" in str(part))
            Image.new("RGB", (160, 90), (20, 30, 40)).save(output_pattern.replace("%03d", "001"), "JPEG")
            return SimpleNamespace(returncode=1, stderr="forced failure")
        with patch.object(story.subprocess, "run", side_effect=fake_failed_run):
            rejected = story._extract_segment_frames(ffmpeg_exe, str(test_video), 0.0, 2.0, failed_dir)
        assert rejected == [], "Partial frame left by FFmpeg rc!=0 must be rejected"
        print("PASS 1B: Partial/non-zero FFmpeg output is rejected")
''',
    1,
)
package_d = package_d.replace('assert manifest["time_precision"] == "1s_dense_segment"', 'assert manifest["time_precision"] == "decoded_pts_1fps"')
package_d = package_d.replace(
    'print("PASS 2: Manifest segmentu zawiera precyzyjne znaczniki czasu times[] (błąd < 0.01s)")',
    'print("PASS 2: Manifest używa decoded PTS, a nie nominalnego generatora testu")',
)
write("audit/regression_package_d.py", package_d)


# ---------------------------------------------------------------------------
# P3 frontend: do not hide partial/failed remote outcomes.
# ---------------------------------------------------------------------------
favorites = read("static/favorites.js")
favorites = favorites.replace(
    '''      showToast(isFav ? 'Dodano do ulubionych ❤️' : 'Usunięto z ulubionych', isFav ? 'success' : 'info');
      return isFav;
''',
    '''      if (data.sync_state === 'remote_failed') {
        showToast('Zmiana zapisana lokalnie, ale synchronizacja z kontem zdalnym nie powiodła się.', 'warning');
      } else if (data.sync_state === 'local_only') {
        showToast((isFav ? 'Dodano' : 'Usunięto') + ' lokalnie (tryb anonimowy).', 'info');
      } else {
        showToast(isFav ? 'Dodano do ulubionych ❤️' : 'Usunięto z ulubionych', isFav ? 'success' : 'info');
      }
      return isFav;
''',
    1,
)
write("static/favorites.js", favorites)

account = read("static/account.js")
account = account.replace(
    '''      if (data.success) {
        showToast(`Pobrano: ${data.favorites_count} ulubionych, ${data.history_count} historii, ${data.following_count} obserwowanych!`, 'success');
        updateUserStatus(data);
        if (state.mode === 'favorites') loadFavorites(1);
        if (state.mode === 'history') loadHistory(1);
        if (state.mode === 'following') loadFollowing(1);
      }
''',
    '''      if (data.success) {
        showToast(`Pobrano: ${data.favorites_count} ulubionych, ${data.history_count} historii, ${data.following_count} obserwowanych!`, 'success');
        updateUserStatus(data);
        if (state.mode === 'favorites') loadFavorites(1);
        if (state.mode === 'history') loadHistory(1);
        if (state.mode === 'following') loadFollowing(1);
      } else {
        const reason = data.error || (data.status === 'not_configured' ? 'Konto nie jest skonfigurowane.' : `Synchronizacja nieukończona (${data.status || 'unknown'}).`);
        showToast(reason, 'error');
      }
''',
    1,
)
write("static/account.js", account)


# ---------------------------------------------------------------------------
# P7: historical diagnostics are explicitly segregated; CI becomes SHA-bound evidence.
# ---------------------------------------------------------------------------
historical = p("audit/historical")
historical.mkdir(parents=True, exist_ok=True)
for name in ("frontend_results.json", "performance_results.json"):
    src = p(f"audit/{name}")
    if src.exists():
        shutil.move(str(src), str(historical / name.replace(".json", ".legacy.json")))
write(
    "audit/historical/README.md",
    f'''# Historical diagnostic results\n\nFiles in this directory are retained only as historical observations copied from Archive `{SOURCE_SHA}`.\nThey are **not release evidence** and are not consumed by CI. Fresh qualification is produced by GitHub Actions and is therefore bound to the tested commit SHA and real command exit codes.\n''',
)

# New regression lane specifically covers audit findings that the old acceptance set missed.
write(
    "audit/regression_audit_fixes.py",
    r'''import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config

# 1. Anonymous startup: no embedded fallback.
with tempfile.TemporaryDirectory() as td, \
     patch.dict(os.environ, {}, clear=True), \
     patch.object(config, "ENV_FILE", Path(td) / ".env.local"), \
     patch.object(config, "LOCAL_CREDENTIALS_FILE", Path(td) / "credentials.local.json"):
    assert config.get_archivebate_credentials() == ("", "")
print("PASS audit-fix 1: no-config credentials fail closed")

# 2. UserStorage persistence failure rolls memory back and propagates the error.
import storage as storage_mod
with tempfile.TemporaryDirectory() as td, \
     patch.object(storage_mod, "STORE_FILE", str(Path(td) / "user_store.json")):
    test_store = storage_mod.UserStorage()
    with patch.object(storage_mod, "atomic_write_json", side_effect=OSError("disk full")):
        try:
            test_store.add_favorite({"id": "audit-1", "username": "fixture"})
            raise AssertionError("persistence failure was acknowledged")
        except OSError:
            pass
    assert test_store.get_favorites() == []
print("PASS audit-fix 2: persistence failure is not acknowledged and memory rolls back")

# 3. Canonical fallback identity is stable across Python hash seeds/processes.
from catalog_service import CatalogService, canonical_identity_key, MAX_PAGES_PER_SOURCE
fixture = {"source": "archivebate", "title": "no id/url fixture", "username": "fixture"}
key = canonical_identity_key(fixture)
assert key.startswith("archivebate:hash:") and len(key.rsplit(":", 1)[-1]) == 64
code = "from catalog_service import canonical_identity_key; print(canonical_identity_key(" + repr(fixture) + "))"
env1 = dict(os.environ, PYTHONHASHSEED="1")
env2 = dict(os.environ, PYTHONHASHSEED="2")
k1 = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env1, text=True).strip()
k2 = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env2, text=True).strip()
assert k1 == k2 == key
print("PASS audit-fix 3: fallback catalog identity is cross-process stable")

# 4. Failed revision is never projected as complete/no-error.
with tempfile.TemporaryDirectory() as td:
    svc = CatalogService(Path(td) / "catalog.db")
    svc.import_items([{"id": "1", "source": "archivebate", "title": "x"}], revision=1)
    svc.mark_revision_failed(1, '{"archivebate":"forced"}')
    data = svc.query_page(revision=1)
    assert data["catalog_complete"] is False
    assert data["complete"] is False
    assert data["catalog_state"] == "failed"
    assert data["retryable"] is True
    assert data["source_error"].get("archivebate") == "forced"
    svc.close()
print("PASS audit-fix 4: failed catalog revision is explicit")

# 5. Safety page cap is a failure/truncation state, not verified end.
with tempfile.TemporaryDirectory() as td:
    svc = CatalogService(Path(td) / "cap.db")
    import catalog_service as catalog_mod
    old = dict(catalog_mod.MAX_PAGES_PER_SOURCE)
    catalog_mod.MAX_PAGES_PER_SOURCE["archivebate"] = 2
    try:
        svc.build_revision_background({"archivebate": lambda page: [{"id": str(page), "source": "archivebate"}]}, force=True)
        svc._indexing_thread.join(10)
        stats = svc.get_revision_stats(1)
        assert stats["failed"] is True and stats["complete"] is False, stats
        page = svc.query_page(revision=1)
        assert page["catalog_state"] == "failed" and page["retryable"] is True
    finally:
        catalog_mod.MAX_PAGES_PER_SOURCE.clear(); catalog_mod.MAX_PAGES_PER_SOURCE.update(old)
        svc.close()
print("PASS audit-fix 5: catalog hard cap cannot publish complete")

# 6. Main account outcome is typed and async GETs use offload/coalescing helper.
import main
main.session.email = "configured@example.invalid"
main.session.password = "configured"
main.session.is_logged_in = False
with patch.object(main.session, "login", return_value=False):
    outcome = main.sync_account_data()
assert outcome["success"] is False and outcome["status"] == "auth_failed"
import inspect
assert "await _ensure_account_synced()" in inspect.getsource(main.get_account_summary)
assert "asyncio.to_thread(sync_account_data)" in inspect.getsource(main._ensure_account_synced)
print("PASS audit-fix 6: account sync exposes failure and async GET path offloads")

# 7. Remote favorite failure is not reported as fully successful.
with patch.object(main.storage, "toggle_favorite", return_value=True), \
     patch.object(main.scraper, "toggle_remote_save", return_value=False), \
     patch.object(main, "invalidate_feed_cache", return_value=None):
    main.session.email = "configured@example.invalid"; main.session.password = "configured"
    fav = main.toggle_favorite({"id": "42"})
assert fav["local_committed"] is True and fav["remote_synced"] is False
assert fav["success"] is False and fav["sync_state"] == "remote_failed"
print("PASS audit-fix 7: favorite remote failure is explicit")

# 8. No launcher contains port-authorized force termination.
for rel in ("desktop_app.py", "run.py", "start.bat", "URUCHOM_PROGRAM.bat", "Uruchom_Desktop.bat"):
    text = (ROOT / rel).read_text(encoding="utf-8").lower()
    assert "taskkill" not in text, rel
assert "pywebview" in (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
print("PASS audit-fix 8: launch paths are non-destructive and desktop dependency is declared")

# 9. Legacy checked-in result files cannot masquerade as current evidence.
assert not (ROOT / "audit/frontend_results.json").exists()
assert not (ROOT / "audit/performance_results.json").exists()
assert (ROOT / "audit/historical/frontend_results.legacy.json").exists()
assert (ROOT / "audit/historical/performance_results.legacy.json").exists()
print("PASS audit-fix 9: stale diagnostics segregated from release evidence")
''',
)

# Canonical release contract, previously missing from every audit lane.
write(
    "RELEASE_REQUIREMENTS.md",
    '''# Release requirements\n\nThis file is the source-native release contract for the repaired generation.\n\n1. **Anonymous startup:** without explicit credentials, the application performs zero remote authentication attempts. No credential literal may be committed.\n2. **Process authority:** occupying port 8000 never authorizes terminating a process. Launchers fail safely on conflicts.\n3. **Durability:** user-state success requires a successful durable commit; persistence failure propagates and in-memory state rolls back.\n4. **Remote effects:** local and remote account outcomes are explicit (`synced`, `local_only`, `remote_failed`, auth/fetch failure). No partial failure is reported as full success.\n5. **Async responsiveness:** async account GET handlers never execute full synchronous account synchronization on the event loop.\n6. **Catalog state:** only verified natural source completion may publish a complete revision. Hard limits are truncation/failure. Failed revisions expose errors and remain incomplete.\n7. **Refresh generation:** force refresh returns a revision token and the UI follows that requested revision rather than silently staying pinned to an older complete revision.\n8. **Stable identity:** fallback catalog identities are deterministic across processes.\n9. **Storyboard integrity:** FFmpeg non-zero/invalid output is rejected. Precision metadata is based on observed decoded PTS; fallback timing is explicitly approximate.\n10. **Evidence:** historical diagnostic JSON is not release evidence. CI commands and exit codes are bound to the commit by GitHub Actions.\n11. **Desktop packaging:** every imported runtime dependency, including pywebview, is declared in requirements.\n\nA release-ready decision requires all target regressions and the offline acceptance set to pass on the exact candidate commit.\n''',
)

# README: make the repaired contracts and acceptance lane explicit.
append_once(
    "README.md",
    "## Naprawy po audycie skonsolidowanym",
    '''\n## Naprawy po audycie skonsolidowanym\n\nTa generacja usuwa osadzone dane logowania, destrukcyjne `taskkill`, fałszywe potwierdzanie zapisu i synchronizacji, blokowanie event loop przez auto-sync, błędną semantykę kompletności/odświeżania katalogu oraz niezweryfikowane oznaczanie precyzji storyboardu.\n\nDodatkowa kwalifikacja:\n\n```text\npython audit/regression_audit_fixes.py\npython audit/regression_package_d.py\n```\n\nHistoryczne `frontend_results.json` i `performance_results.json` zostały przeniesione do `audit/historical/` i nie są release evidence. Bieżące testy są wykonywane przez GitHub Actions dla konkretnego SHA. Kontrakt wydania znajduje się w `RELEASE_REQUIREMENTS.md`.\n''',
)

# Final CI retained in the repaired repository. (Bootstrap workflow is removed below.)
write(
    ".github/workflows/ci.yml",
    '''name: CI\n\non:\n  push:\n    branches: [master]\n  pull_request:\n    branches: [master]\n\npermissions:\n  contents: read\n\njobs:\n  offline-regression:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n      - uses: actions/setup-node@v4\n        with:\n          node-version: "22"\n      - run: python -m pip install --upgrade pip\n      - run: pip install -r requirements.txt\n      - run: python -m compileall -q .\n      - run: python audit/regression_audit_fixes.py\n      - run: python audit/regression_checks.py\n      - run: node audit/regression_frontend.cjs\n      - run: node audit/regression_storyboard_client.cjs\n      - run: node audit/regression_storyboard_cross_tab.cjs\n      - run: node audit/regression_catalog_partial_frontend.cjs\n      - run: python audit/regression_feed.py\n      - run: python audit/regression_catalog_bootstrap.py\n      - run: python audit/regression_integration.py\n      - run: python audit/regression_package_d.py\n      - run: python -m unittest test_suite -q\n''',
)

write(
    "AUDIT_REPAIR_SUMMARY.md",
    f'''# Consolidated audit repair\n\nBase copied from `eagleblastmusic-lgtm/Archive` at `{SOURCE_SHA}`.\n\nImplemented repair families: credential fail-closed; non-destructive launch; transactional user storage; typed remote/account outcomes; async offload; catalog failure/truncation/retry/refresh semantics; deterministic catalog identity; storyboard FFmpeg validation and decoded-PTS timing; model-tag durability; explicit desktop dependency; source-native release requirements; and SHA-bound CI evidence.\n\nThe original repository is not modified by this workflow.\n''',
)

# Bootstrap-only files must not remain in the repaired product or retrigger themselves.
for rel in (".github/workflows/bootstrap_and_fix.yml", "tools/apply_audit_repairs.py"):
    target = p(rel)
    if target.exists():
        target.unlink()
try:
    p("tools").rmdir()
except OSError:
    pass

print("Consolidated audit repairs applied successfully")
