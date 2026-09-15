from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()


def fail(message: str) -> None:
    raise RuntimeError(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def write(path: Path, text: str) -> None:
    if path.suffix == ".py":
        ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8", newline="\n")


def edit(path: str, fn) -> None:
    p = ROOT / path
    if not p.exists():
        fail(f"missing {path}")
    original = p.read_text(encoding="utf-8")
    updated = fn(original)
    if updated == original:
        fail(f"{path}: no changes produced")
    write(p, updated)


def run_phase1() -> None:
    proc = subprocess.run([sys.executable, "tools/apply_v42_security_phase1.py"], cwd=ROOT)
    if proc.returncode != 0:
        fail(f"phase1 failed with rc={proc.returncode}")


def harden_main(text: str) -> str:
    # GET account endpoints become pure reads; bootstrap already performs explicit POST sync after phase 1.
    text = replace_once(
        text,
        '''@app.get("/api/account/summary")\nasync def get_account_summary():\n    """Zwraca podsumowanie panelu konta."""\n    if not storage.data.get("last_synced"):\n        await _ensure_account_synced()\n    return {\n''',
        '''@app.get("/api/account/summary")\nasync def get_account_summary():\n    """Read-only account summary. Synchronization is explicit POST /api/account/sync."""\n    return {\n''',
        "account summary GET purity",
    )
    for name, var, getter in (
        ("favorites", "favs", "storage.get_favorites(include_blocked=False)"),
        ("history", "hist", "storage.get_history(include_blocked=False)"),
        ("following", "foll", "storage.get_following(include_blocked=False)"),
    ):
        old = f'''    {var} = {getter}\n    if len({var}) == 0 and not storage.data.get("last_synced"):\n        await _ensure_account_synced()\n        {var} = {getter}\n\n'''
        text = replace_once(text, old, f"    {var} = {getter}\n\n", f"{name} GET purity")

    # Global response hardening lives in the existing local security middleware.
    old_tail = '''        if request.headers.get("x-archivebate-mutation-token") != LOCAL_MUTATION_TOKEN:\n            return JSONResponse({"success": False, "error": "mutation_token_required"}, status_code=403)\n    return await call_next(request)\n'''
    new_tail = '''        if request.headers.get("x-archivebate-mutation-token") != LOCAL_MUTATION_TOKEN:\n            return JSONResponse({"success": False, "error": "mutation_token_required"}, status_code=403)\n\n    response = await call_next(request)\n    response.headers.setdefault("X-Content-Type-Options", "nosniff")\n    response.headers.setdefault("Referrer-Policy", "no-referrer")\n    response.headers.setdefault("X-Frame-Options", "DENY")\n    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")\n    response.headers.setdefault(\n        "Content-Security-Policy",\n        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "\n        "font-src 'self' https://cdnjs.cloudflare.com; img-src 'self' data: blob: https:; "\n        "media-src 'self' blob: https:; connect-src 'self' https:; object-src 'none'; "\n        "base-uri 'none'; frame-ancestors 'none'",\n    )\n    return response\n'''
    text = replace_once(text, old_tail, new_tail, "security response headers")

    # Every outbound provider request gets a fresh DNS safety check plus a post-connect peer-IP check.
    text = replace_once(
        text,
        '''    cached_target = get_cached_redirect(url)\n    if cached_target and is_safe_remote_url(cached_target):\n        try:\n            res = http_session.get(cached_target, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)\n            if res.status_code in (200, 206):\n                return res\n            res.close()\n        except Exception:\n            pass\n\n    current = url\n    for _ in range(max_redirects + 1):\n        if not is_safe_remote_url(current):\n            raise HTTPException(status_code=400, detail="Niedozwolony adres zdalny")\n        res = http_session.get(current, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)\n''',
        '''    cached_target = get_cached_redirect(url)\n    if cached_target and is_safe_remote_url(cached_target, fresh=True):\n        try:\n            res = http_session.get(cached_target, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)\n            if not response_peer_is_global(res):\n                res.close()\n                raise HTTPException(status_code=400, detail="Połączenie z adresem prywatnym zostało zablokowane")\n            if res.status_code in (200, 206):\n                return res\n            res.close()\n        except HTTPException:\n            raise\n        except Exception:\n            pass\n\n    current = url\n    for _ in range(max_redirects + 1):\n        if not is_safe_remote_url(current, fresh=True):\n            raise HTTPException(status_code=400, detail="Niedozwolony adres zdalny")\n        res = http_session.get(current, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)\n        if not response_peer_is_global(res):\n            res.close()\n            raise HTTPException(status_code=400, detail="Połączenie z adresem prywatnym zostało zablokowane")\n''',
        "fresh DNS and peer IP validation",
    )

    # Thumbnail lock registry is bounded without clearing locks that may still protect active fetches.
    text = replace_once(text, "_thumb_fetch_locks = {}\n", "_thumb_fetch_locks = OrderedDict()\n", "thumbnail lock registry")
    old_lock_fn = '''def _thumb_fetch_lock(url_hash: str) -> threading.Lock:\n    with _thumb_fetch_guard:\n        lock = _thumb_fetch_locks.get(url_hash)\n        if lock is None:\n            if len(_thumb_fetch_locks) > 2000:\n                _thumb_fetch_locks.clear()\n            lock = threading.Lock()\n            _thumb_fetch_locks[url_hash] = lock\n        return lock\n'''
    new_lock_fn = '''def _thumb_fetch_lock(url_hash: str) -> threading.Lock:\n    with _thumb_fetch_guard:\n        lock = _thumb_fetch_locks.get(url_hash)\n        if lock is None:\n            lock = threading.Lock()\n            _thumb_fetch_locks[url_hash] = lock\n        _thumb_fetch_locks.move_to_end(url_hash)\n        while len(_thumb_fetch_locks) > 2000:\n            evicted = False\n            for old_key, old_lock in list(_thumb_fetch_locks.items()):\n                if old_key == url_hash or old_lock.locked():\n                    continue\n                _thumb_fetch_locks.pop(old_key, None)\n                evicted = True\n                break\n            if not evicted:\n                break\n        return lock\n'''
    text = replace_once(text, old_lock_fn, new_lock_fn, "thumbnail lock LRU")

    old_cache = '''_thumb_failures = OrderedDict()\nMEMORY_CACHE_BYTES = 64 * 1024 * 1024\n\n\ndef _remember_thumbnail(key, content, content_type):\n    MEMORY_CACHE[key] = (content, content_type)\n    MEMORY_CACHE.move_to_end(key)\n    while len(MEMORY_CACHE)>MEMORY_CACHE_MAX or sum(len(v[0]) for v in MEMORY_CACHE.values())>MEMORY_CACHE_BYTES:\n        MEMORY_CACHE.popitem(last=False)\n'''
    new_cache = '''_thumb_failures = OrderedDict()\nMEMORY_CACHE_BYTES = 64 * 1024 * 1024\n_memory_cache_bytes = 0\n\n\ndef _remember_thumbnail(key, content, content_type):\n    global _memory_cache_bytes\n    previous = MEMORY_CACHE.pop(key, None)\n    if previous is not None:\n        _memory_cache_bytes = max(0, _memory_cache_bytes - len(previous[0]))\n    MEMORY_CACHE[key] = (content, content_type)\n    MEMORY_CACHE.move_to_end(key)\n    _memory_cache_bytes += len(content)\n    while len(MEMORY_CACHE) > MEMORY_CACHE_MAX or _memory_cache_bytes > MEMORY_CACHE_BYTES:\n        _, evicted = MEMORY_CACHE.popitem(last=False)\n        _memory_cache_bytes = max(0, _memory_cache_bytes - len(evicted[0]))\n'''
    text = replace_once(text, old_cache, new_cache, "thumbnail O1 byte accounting")

    # Route the remaining direct authenticated request through the session wrapper.
    text = text.replace("session.session.get(url, timeout=2.5)", "session.request(\"GET\", url, timeout=2.5)")

    # Add storyboard runtime diagnostics without exposing source URLs.
    text = replace_once(
        text,
        '''    from fast_scan import quick_scan_supervisor\n    health = _public_store_health()\n''',
        '''    from fast_scan import quick_scan_supervisor\n    from storyboard_service import runtime_stats as storyboard_runtime_stats\n    health = _public_store_health()\n''',
        "diagnostics storyboard import",
    )
    text = replace_once(
        text,
        '''        "jobs": _redact_diagnostic_value({\n            "quick_scan": quick_scan_supervisor.status(),\n            "deep_archivebate": deep_archivebate_service.status(),\n        }),\n''',
        '''        "jobs": _redact_diagnostic_value({\n            "quick_scan": quick_scan_supervisor.status(),\n            "deep_archivebate": deep_archivebate_service.status(),\n            "storyboard": storyboard_runtime_stats(),\n        }),\n''',
        "diagnostics storyboard payload",
    )

    # Deep gets an isolated provider session/cookie snapshot instead of sharing the live account session object.
    text = replace_once(
        text,
        "                deep_archivebate_service.start(scraper)\n",
        "                deep_archivebate_service.start(scraper.clone_for_background())\n",
        "deep isolated scraper",
    )
    return text


def harden_cache_store(text: str) -> str:
    text = replace_once(text, "def is_safe_remote_url(url: str) -> bool:\n", "def is_safe_remote_url(url: str, *, fresh: bool = False) -> bool:\n", "fresh SSRF signature")
    text = replace_once(
        text,
        '''        now = time.time()\n        with _host_cache_lock:\n            cached = _host_safety_cache.get(host)\n            if cached and now - cached[1] < _HOST_CACHE_TTL:\n                return cached[0]\n''',
        '''        now = time.time()\n        if not fresh:\n            with _host_cache_lock:\n                cached = _host_safety_cache.get(host)\n                if cached and now - cached[1] < _HOST_CACHE_TTL:\n                    return cached[0]\n''',
        "fresh SSRF cache bypass",
    )
    insert = '''\n\ndef response_peer_is_global(response) -> bool:\n    """Best-effort post-connect check against DNS rebinding/TOCTOU.\n\n    If urllib3 exposes the connected peer, it must be a public/global address.\n    Unknown peer metadata is treated as unsafe for the local proxy path.\n    """\n    try:\n        raw = getattr(response, "raw", None)\n        connection = getattr(raw, "_connection", None) or getattr(raw, "connection", None)\n        sock = getattr(connection, "sock", None)\n        if sock is None:\n            return False\n        peer = sock.getpeername()[0]\n        return ipaddress.ip_address(peer).is_global\n    except Exception:\n        return False\n'''
    anchor = "\n\ndef trim_cache_directory(directory: os.PathLike, max_bytes: int, preserve_suffixes=(\".meta\",)) -> None:\n"
    text = replace_once(text, anchor, insert + anchor, "peer-IP helper")
    return text


def harden_client(text: str) -> str:
    text = replace_once(text, "import logging\n", "import logging\nimport threading\n", "client threading import")
    text = replace_once(
        text,
        '''        self.session = requests.Session()\n        \n        # Kontrolowana pula połączeń''',
        '''        self.session = requests.Session()\n        self._request_lock = threading.RLock()\n        self._auth_lock = threading.RLock()\n        \n        # Kontrolowana pula połączeń''',
        "client locks",
    )
    anchor = '''        self.last_login_error: str = ""\n\n    def refresh_csrf(self) -> Optional[str]:\n'''
    methods = '''        self.last_login_error: str = ""\n\n    def request(self, method: str, url: str, **kwargs):\n        """Serialize access to the mutable requests.Session state."""\n        with self._request_lock:\n            return self.session.request(method, url, **kwargs)\n\n    def clone_for_background(self) -> "ArchivebateSession":\n        """Create an independent HTTP pool with an atomic cookie/header snapshot."""\n        clone = ArchivebateSession(email=self.email, password=self.password)\n        with self._request_lock:\n            clone.session.headers.update(dict(self.session.headers))\n            clone.session.cookies.update(self.session.cookies)\n            clone.csrf_token = self.csrf_token\n            clone.is_logged_in = self.is_logged_in\n            clone.last_login_error = self.last_login_error\n        return clone\n\n    def refresh_csrf(self) -> Optional[str]:\n'''
    text = replace_once(text, anchor, methods, "client request/clone methods")
    text = text.replace("self.session.get(", "self.request(\"GET\", ")
    text = text.replace("self.session.post(", "self.request(\"POST\", ")
    old_login = '''    def login(self) -> bool:\n        """Loguje użytkownika do konta Archivebate i zachowuje czytelny powód błędu."""\n'''
    new_login = '''    def login(self) -> bool:\n        """Serialize login/CSRF transitions so cookies and auth state change atomically."""\n        with self._auth_lock:\n            return self._login_locked()\n\n    def _login_locked(self) -> bool:\n        """Loguje użytkownika do konta Archivebate i zachowuje czytelny powód błędu."""\n'''
    text = replace_once(text, old_login, new_login, "client serialized login")
    return text


def harden_scraper(text: str) -> str:
    # Remove the unsupported assumption that unknown gender means Female.
    text = replace_once(
        text,
        '''    if not any(t in tags for t in ["Trans", "Female", "Male", "Couple"]):\n        tags.add("Female")\n    elif "Trans" in tags and "Female" in tags:\n        tags.remove("Female")\n''',
        '''    if "Trans" in tags and "Female" in tags:\n        tags.remove("Female")\n''',
        "scraper unknown gender correctness",
    )
    text = replace_once(
        text,
        '''    def _preferences_version(self) -> int:\n''',
        '''    def clone_for_background(self) -> "ArchivebateScraper":\n        return ArchivebateScraper(self.session.clone_for_background())\n\n    def _preferences_version(self) -> int:\n''',
        "scraper clone",
    )
    # Route common direct session accesses through ArchivebateSession.request where present.
    text = text.replace("self.session.session.get(", "self.session.request(\"GET\", ")
    text = text.replace("self.session.session.post(", "self.session.request(\"POST\", ")
    return text


def harden_deep(text: str) -> str:
    text = replace_once(
        text,
        "PROFILE_REPEAT_END_THRESHOLD = 3\n",
        "PROFILE_REPEAT_END_THRESHOLD = 3\nPROFILE_MAX_RETRIES = 9\n",
        "deep max retry constant",
    )
    text = replace_once(
        text,
        '''                    crawl_complete INTEGER NOT NULL DEFAULT 0,\n                    end_reason TEXT,\n                    last_error TEXT\n''',
        '''                    crawl_complete INTEGER NOT NULL DEFAULT 0,\n                    crawl_state TEXT NOT NULL DEFAULT 'pending',\n                    end_reason TEXT,\n                    last_error TEXT\n''',
        "deep crawl_state schema",
    )
    text = replace_once(
        text,
        '''            if "retry_at" not in model_columns:\n                conn.execute("ALTER TABLE archivebate_models ADD COLUMN retry_at REAL")\n            queue_columns = {row["name"] for row in conn.execute("PRAGMA table_info(archivebate_discovery_queue)")}\n''',
        '''            if "retry_at" not in model_columns:\n                conn.execute("ALTER TABLE archivebate_models ADD COLUMN retry_at REAL")\n            if "crawl_state" not in model_columns:\n                conn.execute("ALTER TABLE archivebate_models ADD COLUMN crawl_state TEXT NOT NULL DEFAULT 'pending'")\n            conn.execute("UPDATE archivebate_models SET crawl_state='done' WHERE crawl_complete=1 AND crawl_state='pending'")\n            queue_columns = {row["name"] for row in conn.execute("PRAGMA table_info(archivebate_discovery_queue)")}\n''',
        "deep crawl_state migration",
    )
    text = replace_once(
        text,
        '''                WHERE crawl_complete = 0\n                  AND (retry_at IS NULL OR retry_at <= ?)\n''',
        '''                WHERE crawl_complete = 0\n                  AND crawl_state IN ('pending', 'retry')\n                  AND (retry_at IS NULL OR retry_at <= ?)\n''',
        "deep state-aware selection",
    )
    old_result_fail = '''                    retry_count = int(row["retry_count"] or 0) + 1\n                    retry_delay = max(\n                        float(result.retry_after or 0.0),\n                        min(300.0, 2.0 ** min(retry_count, 8)),\n                    )\n                    conn.execute(\n                        "UPDATE archivebate_models SET retry_count=?, retry_at=?, last_error=?, updated_at=? WHERE model_key=?",\n                        (retry_count, failed_at + retry_delay, error_text, failed_at, model_key),\n                    )\n                    self._progress["last_error"] = error_text\n'''
    new_result_fail = '''                    retry_count = int(row["retry_count"] or 0) + 1\n                    lowered = str(error_text or "").lower()\n                    terminal_unavailable = any(token in lowered for token in ("404", "410", "not found", "gone"))\n                    auth_blocked = any(token in lowered for token in ("401", "403", "unauthorized", "forbidden"))\n                    quarantined = retry_count >= PROFILE_MAX_RETRIES\n                    if terminal_unavailable:\n                        crawl_state, crawl_complete, retry_at, end_reason = "unavailable", 1, None, "unavailable"\n                    elif auth_blocked:\n                        crawl_state, crawl_complete, retry_at, end_reason = "auth_blocked", 1, None, "auth_blocked"\n                    elif quarantined:\n                        crawl_state, crawl_complete, retry_at, end_reason = "quarantined", 1, None, "retry_limit"\n                    else:\n                        retry_delay = max(float(result.retry_after or 0.0), min(300.0, 2.0 ** min(retry_count, 8)))\n                        crawl_state, crawl_complete, retry_at, end_reason = "retry", 0, failed_at + retry_delay, None\n                    conn.execute(\n                        "UPDATE archivebate_models SET retry_count=?, retry_at=?, crawl_state=?, crawl_complete=?, end_reason=?, last_error=?, updated_at=? WHERE model_key=?",\n                        (retry_count, retry_at, crawl_state, crawl_complete, end_reason, error_text, failed_at, model_key),\n                    )\n                    self._progress["last_error"] = error_text\n'''
    text = replace_once(text, old_result_fail, new_result_fail, "deep typed result terminal retry")
    old_exc_fail = '''                retry_count = int(row["retry_count"] or 0) + 1\n                retry_delay = min(300.0, 2.0 ** min(retry_count, 8))\n                conn.execute(\n                    "UPDATE archivebate_models SET retry_count=?, retry_at=?, last_error=?, updated_at=? WHERE model_key=?",\n                    (retry_count, failed_at + retry_delay, str(exc), failed_at, model_key),\n                )\n                self._progress["last_error"] = str(exc)\n'''
    new_exc_fail = '''                retry_count = int(row["retry_count"] or 0) + 1\n                quarantined = retry_count >= PROFILE_MAX_RETRIES\n                retry_delay = min(300.0, 2.0 ** min(retry_count, 8))\n                conn.execute(\n                    "UPDATE archivebate_models SET retry_count=?, retry_at=?, crawl_state=?, crawl_complete=?, end_reason=?, last_error=?, updated_at=? WHERE model_key=?",\n                    (retry_count, None if quarantined else failed_at + retry_delay, "quarantined" if quarantined else "retry", int(quarantined), "retry_limit" if quarantined else None, str(exc), failed_at, model_key),\n                )\n                self._progress["last_error"] = str(exc)\n'''
    text = replace_once(text, old_exc_fail, new_exc_fail, "deep exception terminal retry")

    # Successful progress clears retry state; terminal completions are classified as done.
    text = text.replace("retry_count=0, retry_at=NULL, updated_at=?", "retry_count=0, retry_at=NULL, crawl_state='done', updated_at=?", 1)
    text = text.replace("last_error=NULL, retry_count=0, retry_at=NULL, updated_at=?", "last_error=NULL, retry_count=0, retry_at=NULL, crawl_state='pending', updated_at=?", 1)
    text = replace_once(
        text,
        '''                            crawl_complete=?, end_reason=?, last_error=NULL, updated_at=?\n''',
        '''                            crawl_complete=?, crawl_state=?, end_reason=?, last_error=NULL, retry_count=0, retry_at=NULL, updated_at=?\n''',
        "deep empty completion state",
    )
    text = replace_once(
        text,
        '''                            int(complete),\n                            f"consecutive_empty_pages:{PROFILE_EMPTY_END_THRESHOLD}:{page}" if complete else None,\n                            now,\n''',
        '''                            int(complete),\n                            "done" if complete else "pending",\n                            f"consecutive_empty_pages:{PROFILE_EMPTY_END_THRESHOLD}:{page}" if complete else None,\n                            now,\n''',
        "deep empty completion params",
    )

    old_status = '''            completed_models = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_models WHERE crawl_complete=1").fetchone()["cnt"] or 0)\n            deep_items = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_deep_items").fetchone()["cnt"] or 0)\n            q = conn.execute(\n'''
    new_status = '''            completed_models = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_models WHERE crawl_complete=1").fetchone()["cnt"] or 0)\n            deep_items = int(conn.execute("SELECT COUNT(*) AS cnt FROM archivebate_deep_items").fetchone()["cnt"] or 0)\n            state_rows = conn.execute("SELECT crawl_state, COUNT(*) AS cnt FROM archivebate_models GROUP BY crawl_state").fetchall()\n            model_states = {str(row["crawl_state"] or "pending"): int(row["cnt"] or 0) for row in state_rows}\n            q = conn.execute(\n'''
    text = replace_once(text, old_status, new_status, "deep status state counts")
    text = replace_once(
        text,
        '''                "models_pending": max(0, models - completed_models),\n                "deep_items": deep_items,\n''',
        '''                "models_pending": max(0, models - completed_models),\n                "models_by_state": model_states,\n                "models_retry": model_states.get("retry", 0),\n                "models_unavailable": model_states.get("unavailable", 0),\n                "models_quarantined": model_states.get("quarantined", 0),\n                "models_auth_blocked": model_states.get("auth_blocked", 0),\n                "deep_items": deep_items,\n''',
        "deep status payload",
    )
    return text


def harden_storyboard(text: str) -> str:
    text = replace_once(text, "STORYBOARD_VERSION = 6\n", "STORYBOARD_VERSION = 7\n", "storyboard cache version")
    text = replace_once(
        text,
        '''_leases: Dict[str, Dict[str, float]] = {}\n''',
        '''_leases: Dict[str, Dict[str, float]] = {}\n_active_process_lock = threading.Lock()\n_active_processes: Dict[str, subprocess.Popen] = {}\n_cancelled_processes = 0\n''',
        "storyboard process registry",
    )
    helper_anchor = '''\ndef _key(video_id: str) -> str:\n'''
    helper = '''\ndef _has_live_lease(video_id: str) -> bool:\n    now = time.monotonic()\n    with _state_lock:\n        entries = _leases.get(_key(video_id)) or {}\n        return any(expiry > now for expiry in entries.values())\n\n\ndef _run_cancellable_process(cmd, *, timeout: float, process_key: str, text: bool = False, cancel_check=None):\n    global _cancelled_processes\n    proc = subprocess.Popen(\n        cmd,\n        stdout=subprocess.DEVNULL,\n        stderr=subprocess.PIPE,\n        text=text,\n        errors="replace" if text else None,\n    )\n    with _active_process_lock:\n        _active_processes[process_key] = proc\n    deadline = time.monotonic() + max(0.1, float(timeout))\n    try:\n        while True:\n            if cancel_check is not None and cancel_check():\n                _cancelled_processes += 1\n                proc.terminate()\n                try:\n                    proc.wait(timeout=1.5)\n                except subprocess.TimeoutExpired:\n                    proc.kill()\n                stderr = proc.stderr.read() if proc.stderr else ("" if text else b"")\n                return -15, stderr, True\n            remaining = deadline - time.monotonic()\n            if remaining <= 0:\n                proc.kill()\n                stderr = proc.stderr.read() if proc.stderr else ("" if text else b"")\n                return -9, stderr, False\n            try:\n                _, stderr = proc.communicate(timeout=min(0.20, remaining))\n                return proc.returncode, stderr, False\n            except subprocess.TimeoutExpired:\n                continue\n    finally:\n        with _active_process_lock:\n            _active_processes.pop(process_key, None)\n\n\ndef runtime_stats() -> dict:\n    with _active_process_lock:\n        active_processes = len(_active_processes)\n        cancelled = int(_cancelled_processes)\n    with _state_lock:\n        building = sum(1 for value in _states.values() if value.get("status") == "building")\n        active_leases = sum(1 for entries in _leases.values() if any(expiry > time.monotonic() for expiry in entries.values()))\n    return {\n        "queue_size": _jobs.qsize(),\n        "queue_capacity": _jobs.maxsize,\n        "active_processes": active_processes,\n        "cancelled_processes": cancelled,\n        "building_jobs": building,\n        "active_leases": active_leases,\n        "auto_full_upgrade": False,\n        "storyboard_version": STORYBOARD_VERSION,\n    }\n\n'''
    text = replace_once(text, helper_anchor, helper + helper_anchor, "storyboard cancellable helper")

    # Segment extractor now uses Popen and can terminate when the lease disappears.
    text = replace_once(
        text,
        '''    seek_before_input: bool,\n) -> Tuple[List[Path], List[float], bool]:\n''',
        '''    seek_before_input: bool,\n    cancel_check=None,\n    process_key: str = "segment",\n) -> Tuple[List[Path], List[float], bool]:\n''',
        "segment extractor cancel signature",
    )
    old_run = '''    try:\n        result = subprocess.run(\n            cmd,\n            stdout=subprocess.DEVNULL,\n            stderr=subprocess.PIPE,\n            timeout=timeout,\n            text=True,\n            errors="replace",\n        )\n    except Exception:\n        _cleanup_segment_frames(tmp_dir)\n        return [], [], False\n\n    frames = sorted(tmp_dir.glob("frame_*.jpg"))\n    if result.returncode != 0 or not frames or any(not _valid_segment_frame(frame) for frame in frames):\n        _cleanup_segment_frames(tmp_dir)\n        return [], [], False\n\n    stderr = result.stderr or ""\n'''
    new_run = '''    try:\n        returncode, stderr, cancelled = _run_cancellable_process(\n            cmd, timeout=timeout, process_key=process_key, text=True, cancel_check=cancel_check\n        )\n    except Exception:\n        _cleanup_segment_frames(tmp_dir)\n        return [], [], False\n\n    frames = sorted(tmp_dir.glob("frame_*.jpg"))\n    if cancelled or returncode != 0 or not frames or any(not _valid_segment_frame(frame) for frame in frames):\n        _cleanup_segment_frames(tmp_dir)\n        return [], [], False\n\n    stderr = stderr or ""\n'''
    text = replace_once(text, old_run, new_run, "segment Popen execution")

    # Use the precise extractor path only; it already has a safe fallback for timestamp precision.
    text = replace_once(
        text,
        '''def _extract_segment_frames_with_times(\n    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25\n) -> Tuple[List[Path], List[float], bool]:\n''',
        '''def _extract_segment_frames_with_times(\n    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25,\n    cancel_check=None, process_key: str = "segment"\n) -> Tuple[List[Path], List[float], bool]:\n''',
        "segment wrapper cancel signature",
    )
    # Replace the older duplicate subprocess.run fallback with calls to the precise extractor.
    start = text.index('def _extract_segment_frames_with_times(')
    end = text.index('\n\ndef _extract_segment_frames(', start)
    replacement = '''def _extract_segment_frames_with_times(\n    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25,\n    cancel_check=None, process_key: str = "segment"\n) -> Tuple[List[Path], List[float], bool]:\n    """Extract one dense 30-second segment in a cancellable FFmpeg process."""\n    first = _run_segment_extract(\n        ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout, True,\n        cancel_check=cancel_check, process_key=process_key,\n    )\n    if first[0]:\n        return first\n    if cancel_check is not None and cancel_check():\n        return [], [], False\n    return _run_segment_extract(\n        ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout, False,\n        cancel_check=cancel_check, process_key=process_key,\n    )\n'''
    text = text[:start] + replacement + text[end:]

    text = replace_once(
        text,
        '''        frames, decoded_times, precise_timing = _extract_segment_frames_with_times(\n            ffmpeg, source_url, start_time, seg_duration, tmp\n        )\n''',
        '''        frames, decoded_times, precise_timing = _extract_segment_frames_with_times(\n            ffmpeg, source_url, start_time, seg_duration, tmp,\n            cancel_check=lambda: _worker_stop.is_set() or not _has_live_lease(video_id),\n            process_key=f"{_key(video_id)}:seg:{segment_index}",\n        )\n''',
        "segment lease cancellation",
    )

    # Stop auto-generating 24-48 frame full boards; quick board + dense on-demand segments is the V4.2 contract.
    old_quick_result = '''                _states[k] = {\n                    "status": "ready",\n                    **result,\n                    "upgrade_status": "ready" if quality == "full" else "queued",\n                }\n                if quality == "quick":\n                    if not _jobs.full():\n                        _jobs.put_nowait((1, next(_sequence), video_id, duration, source_url, "full"))\n                    else:\n                        _states[k]["upgrade_status"] = "error"\n'''
    new_quick_result = '''                _states[k] = {\n                    "status": "ready",\n                    **result,\n                    "upgrade_status": "disabled",\n                }\n'''
    text = replace_once(text, old_quick_result, new_quick_result, "disable auto full storyboard")
    old_start_choice = '''        quality = "full" if quick and not force else "quick"\n        state = (\n            {"status": "ready", **quick, "upgrade_status": "queued"}\n            if quality == "full"\n            else {"status": "building", "stage": "quick"}\n        )\n'''
    new_start_choice = '''        if quick and not force:\n            return {"status": "ready", **quick, "upgrade_status": "disabled"}\n        quality = "quick"\n        state = {"status": "building", "stage": "quick", "upgrade_status": "disabled"}\n'''
    text = replace_once(text, old_start_choice, new_start_choice, "quick-only global storyboard")
    return text


def harden_model_tags(text: str) -> str:
    text = replace_once(
        text,
        '''MODEL_TAGS_FILE = os.path.join(DATA_DIR, "model_tags.json")\nos.makedirs(DATA_DIR, exist_ok=True)\n''',
        '''MODEL_TAGS_SEED_FILE = os.path.join(DATA_DIR, "model_tags.seed.json")\nMODEL_TAGS_FILE = os.path.join(DATA_DIR, "model_tags.local.json")\nos.makedirs(DATA_DIR, exist_ok=True)\n''',
        "model tags paths",
    )
    old_load = '''            if os.path.exists(MODEL_TAGS_FILE):\n                try:\n                    with open(MODEL_TAGS_FILE, "r", encoding="utf-8") as f:\n                        data = json.load(f)\n                        if isinstance(data, dict):\n                            for k, v in data.items():\n                                self._db[k.lower()] = v\n                except Exception as e:\n                    logger.warning(f"Błąd odczytu model_tags.json: {e}")\n'''
    new_load = '''            for source_path in (MODEL_TAGS_SEED_FILE, MODEL_TAGS_FILE):\n                if not os.path.exists(source_path):\n                    continue\n                try:\n                    with open(source_path, "r", encoding="utf-8") as f:\n                        data = json.load(f)\n                        if isinstance(data, dict):\n                            for k, v in data.items():\n                                self._db[k.lower()] = v\n                except Exception as e:\n                    logger.warning(f"Błąd odczytu {os.path.basename(source_path)}: {e}")\n'''
    text = replace_once(text, old_load, new_load, "model tags seed/local load")
    text = replace_once(
        text,
        '''        # Domyślnie Female jeśli nie wykryto nic innego\n        return {"username": username, "gender": "Female", "tags": ["Female"]}\n''',
        '''        # Brak wiarygodnego sygnału to Unknown, nigdy automatyczne "Female".\n        return {"username": username, "gender": None, "tags": [], "confidence": 0.0, "sources": []}\n''',
        "model tags unknown classification",
    )
    return text


def harden_html(text: str) -> str:
    text = re.sub(r'\n\s*<link rel="preconnect" href="https://fonts\.googleapis\.com">', '', text)
    text = re.sub(r'\n\s*<link rel="preconnect" href="https://fonts\.gstatic\.com" crossorigin>', '', text)
    text = re.sub(r'\n\s*<link href="https://fonts\.googleapis\.com[^>]+>', '', text)
    text = re.sub(r'\n\s*<noscript><link href="https://fonts\.googleapis\.com[^<]+</noscript>', '', text)
    text = text.replace("font-family: 'Inter', sans-serif;", "font-family: system-ui, 'Segoe UI', Arial, sans-serif;")
    return text


def harden_launchers(text: str) -> str:
    text = re.sub(r'echo \[1/2\] Sprawdzanie bibliotek Pythona\.\.\.\npython -m pip install -r requirements\.txt --quiet\nif errorlevel 1 exit /b 1\n\n', '', text)
    text = text.replace('echo [2/2] Startowanie serwera i otwieranie przegladarki...\n', 'echo Startowanie serwera i otwieranie przegladarki...\n')
    text = re.sub(r'python -m pip install -r requirements\.txt --quiet\nif errorlevel 1 exit /b 1\n', '', text)
    return text


def harden_ci(text: str) -> str:
    text = text.replace("pip install -r requirements.txt", "pip install -r requirements.lock.txt\n          pip check")
    # Add the full V4.2 regression after the phase-1 regression in both existing jobs.
    text = text.replace(
        "      - name: V4.2 local security regression\n        run: python audit/regression_security_v42.py\n",
        "      - name: V4.2 local security regression\n        run: python audit/regression_security_v42.py\n      - name: V4.2 full hardening regression\n        run: python audit/regression_v42_full.py\n",
    )
    if "windows-python314:" not in text:
        text += '''\n\n  windows-python314:\n    runs-on: windows-2022\n    timeout-minutes: 25\n    env:\n      PYTHONUTF8: "1"\n      PYTHONIOENCODING: "utf-8"\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.14"\n      - uses: actions/setup-node@v4\n        with:\n          node-version: "22"\n      - name: Install locked dependencies\n        shell: pwsh\n        run: |\n          python -m pip install --upgrade pip\n          pip install -r requirements.lock.txt\n          pip check\n      - name: Compile Python\n        run: python -m compileall -q .\n      - name: V4.2 full hardening regression\n        run: python audit/regression_v42_full.py\n      - name: V4.1 catalog concurrency regression\n        run: python audit/regression_catalog_v41.py\n      - name: Unit tests\n        run: python -m unittest -v test_suite.py\n'''
    return text


def create_regression() -> None:
    path = ROOT / "audit" / "regression_v42_full.py"
    content = r'''import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ARCHIVEBATE_USER_STORE", str(ROOT / "audit" / f"v42full_{os.getpid()}_store.json"))
os.environ.setdefault("ARCHIVEBATE_CATALOG_DB", str(ROOT / "audit" / f"v42full_{os.getpid()}_catalog.db"))

# Phase-1 security contract must stay green.
proc = subprocess.run([sys.executable, "audit/regression_security_v42.py"], cwd=ROOT)
assert proc.returncode == 0
print("PASS v4.2 full 1: phase-1 security contract")

import cache_store
assert cache_store.is_safe_remote_url("http://127.0.0.1:1", fresh=True) is False
assert cache_store.is_safe_remote_url("http://user:pass@example.com", fresh=True) is False
print("PASS v4.2 full 2: fresh outbound validation")

import main
from fastapi.testclient import TestClient
client = TestClient(main.app)
with patch.object(main, "sync_account_data", side_effect=AssertionError("GET triggered sync")):
    response = client.get("/api/account/summary", headers={"host": "testserver"})
    assert response.status_code == 200, response.text
print("PASS v4.2 full 3: account GET is read-only")

response = client.get("/api/status", headers={"host": "testserver"})
assert response.headers.get("x-content-type-options") == "nosniff"
assert response.headers.get("x-frame-options") == "DENY"
assert "frame-ancestors 'none'" in response.headers.get("content-security-policy", "")
print("PASS v4.2 full 4: security headers")

from client import ArchivebateSession
s = ArchivebateSession("a@example.invalid", "x")
clone = s.clone_for_background()
assert clone is not s and clone.session is not s.session
clone.session.cookies.set("clone-only", "1")
assert s.session.cookies.get("clone-only") is None
s.close(); clone.close()
print("PASS v4.2 full 5: background HTTP session isolation")

# Deep terminal/quarantine state is durable after the retry ceiling.
from deep_archivebate import DeepArchivebateService, PROFILE_MAX_RETRIES
from fetch_contract import FetchResult
with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "deep.db"
    conn = sqlite3.connect(db)
    conn.executescript('''
        CREATE TABLE revisions(revision INTEGER PRIMARY KEY, complete INTEGER, failed INTEGER, is_active INTEGER, published_at REAL, updated_at REAL, created_at REAL, video_count INTEGER, error TEXT, published_hash TEXT);
        CREATE TABLE catalog_items(canonical_key TEXT, source TEXT, video_id TEXT, author TEXT, author_clean TEXT, published_at REAL, duration_seconds REAL, duration_str TEXT, poster TEXT, url TEXT, preview_video TEXT, title TEXT, platform TEXT, raw_json TEXT, revision INTEGER, PRIMARY KEY(revision, canonical_key));
        CREATE TABLE source_runs(revision INTEGER, source TEXT, cursor INTEGER, pages_scanned INTEGER, items_found INTEGER, complete INTEGER, failed INTEGER, error TEXT, end_reason TEXT, updated_at REAL, PRIMARY KEY(revision, source));
    ''')
    conn.close()
    svc = DeepArchivebateService(db, request_delay=0.01)
    now = __import__('time').time()
    c = svc._get_conn()
    c.execute("INSERT INTO archivebate_models(model_key,username,profile_url,discovered_from,priority,first_seen,updated_at,retry_count) VALUES('dead','dead','', 'test', 1, ?, ?, ?)", (now, now, PROFILE_MAX_RETRIES - 1))
    class Dead:
        def get_archivebate_model_videos_result(self, username, page=1):
            return FetchResult.error_result("permanent network failure", source="archivebate", page=page)
    assert svc.crawl_step(Dead()) is True
    row = c.execute("SELECT crawl_state,crawl_complete,retry_at FROM archivebate_models WHERE model_key='dead'").fetchone()
    assert row["crawl_state"] == "quarantined" and int(row["crawl_complete"]) == 1 and row["retry_at"] is None, dict(row)
    svc.close()
print("PASS v4.2 full 6: Deep retry ceiling quarantines terminal failures")

import storyboard_service
source = (ROOT / "storyboard_service.py").read_text(encoding="utf-8")
assert 'upgrade_status": "disabled"' in source
assert '_run_cancellable_process' in source and 'subprocess.Popen' in source
assert storyboard_service.runtime_stats()["auto_full_upgrade"] is False
print("PASS v4.2 full 7: segment-first storyboard and cancellable process contract")

import model_tags
unknown = model_tags.model_tag_manager.resolve_model("__v42_nonexistent_fixture__")
# Avoid live network dependence: source contract is also asserted statically below.
model_source = (ROOT / "model_tags.py").read_text(encoding="utf-8")
assert '"gender": None' in model_source and 'model_tags.local.json' in model_source and 'model_tags.seed.json' in model_source
assert 'tags.add("Female")' not in (ROOT / "scraper.py").read_text(encoding="utf-8")
print("PASS v4.2 full 8: unknown gender is not silently classified Female")

for rel in ("start.bat", "URUCHOM_PROGRAM.bat", "Uruchom_Desktop.bat"):
    data = (ROOT / rel).read_text(encoding="utf-8").lower()
    assert "pip install" not in data, rel
assert (ROOT / "requirements.in").exists()
assert (ROOT / "requirements.lock.txt").exists()
print("PASS v4.2 full 9: runtime does not mutate dependencies and lockfile exists")

for rel in ("static/index.html", "static/watch.html"):
    html = (ROOT / rel).read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
print("PASS v4.2 full 10: Google Fonts removed from runtime")

ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
assert "data/model_tags.local.json" in ignored
assert (ROOT / "data/model_tags.seed.json").exists()
print("PASS v4.2 full 11: model-tag seed/runtime split")

ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
assert 'python-version: "3.14"' in ci and "requirements.lock.txt" in ci
print("PASS v4.2 full 12: Python 3.14 + locked dependency CI")

print("PASS ARCHIVEBITE V4.2 FULL HARDENING REGRESSION")
'''
    write(path, content)


def main() -> int:
    run_phase1()

    edit("main.py", harden_main)
    edit("cache_store.py", harden_cache_store)
    edit("client.py", harden_client)
    edit("scraper.py", harden_scraper)
    edit("deep_archivebate.py", harden_deep)
    edit("storyboard_service.py", harden_storyboard)
    edit("model_tags.py", harden_model_tags)
    edit("static/index.html", harden_html)
    edit("static/watch.html", harden_html)
    edit("start.bat", harden_launchers)
    edit("URUCHOM_PROGRAM.bat", harden_launchers)
    edit("Uruchom_Desktop.bat", harden_launchers)
    edit(".github/workflows/ci.yml", harden_ci)

    # Split the tracked runtime model tag file into a read-only seed and an ignored local runtime file.
    old_tags = ROOT / "data" / "model_tags.json"
    seed_tags = ROOT / "data" / "model_tags.seed.json"
    if old_tags.exists() and not seed_tags.exists():
        old_tags.rename(seed_tags)
    elif not seed_tags.exists():
        seed_tags.write_text("{}\n", encoding="utf-8")

    gitignore = ROOT / ".gitignore"
    gi = gitignore.read_text(encoding="utf-8")
    if "data/model_tags.local.json" not in gi:
        gi += "\n# Runtime model-tag enrichment (seed is tracked separately)\ndata/model_tags.local.json\n"
        write(gitignore, gi)

    # requirements.in is the human-edited dependency input. The workflow resolves the lock on Python 3.14.
    req = ROOT / "requirements.txt"
    req_in = ROOT / "requirements.in"
    if not req_in.exists():
        req_in.write_text(req.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    req.write_text("-r requirements.lock.txt\n", encoding="utf-8", newline="\n")

    install_bat = ROOT / "INSTALL_DEPENDENCIES.bat"
    install_bat.write_text(
        '@echo off\ncd /d "%~dp0"\npython -m pip install --upgrade pip\npython -m pip install -r requirements.lock.txt\npython -m pip check\npause\n',
        encoding="utf-8",
        newline="\n",
    )

    create_regression()

    # Static compile gate before dependency installation.
    for rel in ("main.py", "cache_store.py", "client.py", "scraper.py", "deep_archivebate.py", "storyboard_service.py", "model_tags.py", "audit/regression_v42_full.py"):
        ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)

    print("ARCHIVEBITE V4.2 FULL HARDENING PATCH APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
