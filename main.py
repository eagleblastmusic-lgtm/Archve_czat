import os
import re
import json
import math
import hashlib
import requests
import threading
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urljoin
from fastapi import FastAPI, Query, Request, HTTPException, Body, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from client import ArchivebateSession
from scraper import ArchivebateScraper, POPULAR_TAGS, extract_video_tags
from storage import storage
from camwhores import camwhores_scraper
from config import get_archivebate_credentials
from cache_store import (
    THUMBS_CACHE_DIR, FEED_CACHE_DIR, DETAILS_CACHE_DIR, STREAM_CACHE_DIR, STORYBOARD_CACHE_DIR,
    atomic_write_json, read_json_cache, cache_age_seconds, safe_cache_key,
    is_safe_remote_url, trim_cache_directory,
)
from storyboard_service import (
    start as start_storyboard,
    get_status as get_storyboard_status,
    sprite_path as get_storyboard_sprite_path,
    get_segment_status,
    start_segment,
    segment_sprite_path,
)

# Dane logowania nie są już zapisane w kodzie źródłowym.
_archivebate_email, _archivebate_password = get_archivebate_credentials()
session = ArchivebateSession(email=_archivebate_email, password=_archivebate_password)
scraper = ArchivebateScraper(session)

THUMBS_CACHE_DIR = str(THUMBS_CACHE_DIR)
FEED_CACHE_DIR = str(FEED_CACHE_DIR)
DETAILS_CACHE_DIR = str(DETAILS_CACHE_DIR)
STREAM_CACHE_DIR = str(STREAM_CACHE_DIR)
STORYBOARD_CACHE_DIR = str(STORYBOARD_CACHE_DIR)

_account_sync_lock = threading.Lock()


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Archivebate Browser] Błyskawiczny start serwera...")
    def background_startup():
        try:
            if session.email and session.password:
                session.login()
            # Czyszczenie cache poza ścieżką krytyczną startu.
            trim_cache_directory(THUMBS_CACHE_DIR, max_bytes=750 * 1024 * 1024)
            trim_cache_directory(DETAILS_CACHE_DIR, max_bytes=150 * 1024 * 1024)
            trim_cache_directory(STREAM_CACHE_DIR, max_bytes=50 * 1024 * 1024)
            trim_cache_directory(FEED_CACHE_DIR, max_bytes=60 * 1024 * 1024)
            trim_cache_directory(STORYBOARD_CACHE_DIR, max_bytes=500 * 1024 * 1024, preserve_suffixes=())
            # Zasil trwały indeks z lokalnych stron i wznowij brakujące strony
            # poza ścieżką krytyczną pierwszego renderu.
            try:
                from catalog_service import catalog_service
                _ensure_catalog_indexing(catalog_service)
            except Exception as exc:
                print(f"[Catalog] Błąd bootstrapu indeksu: {exc}")
        except Exception as e:
            print(f"[Archivebate Browser] Błąd inicjalizacji: {e}")
    threading.Thread(target=background_startup, daemon=True).start()
    yield
    print("[Archivebate Browser] Zamykanie aplikacji.")

app = FastAPI(title="Archivebate Video Browser", lifespan=lifespan)

# Duże feedy JSON/CSS/JS są kompresowane; dla lokalnego UI zmniejsza to koszt kopiowania
# i szczególnie pomaga, gdy aplikacja jest otwierana z innego urządzenia w LAN.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000", "http://localhost:8000",
        "http://127.0.0.1", "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Range", "Accept"],
)

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        from urllib.parse import parse_qs
        response = await super().get_response(path, scope)
        params=parse_qs(scope.get('query_string',b'').decode())
        digest=await asyncio.to_thread(_asset_digest, path)
        version=params.get('v',[''])[0]
        response.headers['Cache-Control']='public, max-age=31536000, immutable' if digest and version==digest else 'no-cache'
        return response


_ASSET_DIGEST_LOCK = threading.Lock()
_ASSET_DIGEST_CACHE = {}


def _asset_digest(path):
    try:
        target = os.path.join(STATIC_DIR, path)
        stat = os.stat(target)
        signature = (stat.st_mtime_ns, stat.st_size)
        with _ASSET_DIGEST_LOCK:
            cached = _ASSET_DIGEST_CACHE.get(path)
            if cached and cached[:2] == signature:
                return cached[2]
        with open(target, 'rb') as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:16]
        with _ASSET_DIGEST_LOCK:
            _ASSET_DIGEST_CACHE[path] = (*signature, digest)
        return digest
    except OSError:
        return None


def _versioned_html(path):
    from fastapi.responses import HTMLResponse
    with open(path,encoding='utf-8') as f: html=f.read()
    def version(match):
        name=match.group(1)
        return '/static/'+name+'?v='+(_asset_digest(name) or 'missing')
    html=re.sub(r'/static/([a-zA-Z0-9_.-]+\.(?:js|css))(?:\?v=[^"\s>]+)?',version,html)
    return HTMLResponse(html,headers={'Cache-Control':'no-cache'})


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

def _enrich_videos(videos: list, author_filter: str = "all", source: str = "all", group_authors: str = "0") -> list:
    """Dodaje flagę is_favorite, usuwa wszelkie duplikaty oraz filtruje zablokowane profile z całego programu."""
    videos = [dict(v) for v in videos if isinstance(v, dict)]
    from camwhores import deduplicate_videos
    videos = deduplicate_videos(videos)

    # 0. Filtr zablokowanych modeli - natychmiastowe usunięcie z każdego widoku programu
    blocked_models = storage.get_blocked_models()
    if blocked_models:
        blocked_set = set(re.sub(r'[^a-z0-9]', '', b.lower()) for b in blocked_models)
        videos = [
            v for v in videos
            if isinstance(v, dict) and re.sub(r'[^a-z0-9]', '', str(v.get("username", "")).lower()) not in blocked_set
        ]

    # Filtr źródła (Camwhores / Archivebate)
    if source == "only-camwhores":
        videos = [
            v for v in videos
            if isinstance(v, dict) and (v.get("source") == "camwhores" or str(v.get("id", "")).startswith("cw_") or "camwhores" in str(v.get("platform", "")).lower())
        ]
    elif source == "only-archivebate":
        videos = [
            v for v in videos
            if isinstance(v, dict) and not (v.get("source") == "camwhores" or str(v.get("id", "")).startswith("cw_") or "camwhores" in str(v.get("platform", "")).lower())
        ]

    fav_authors_raw = storage.get_favorite_authors()
    fav_authors_clean = set(re.sub(r'[^a-z0-9]', '', a.lower()) for a in fav_authors_raw)
    favorite_ids = {str(item.get("id")) for item in storage.data.get("favorites", []) if isinstance(item, dict)}

    if author_filter == "exclude_fav":
        videos = [
            v for v in videos
            if isinstance(v, dict) and re.sub(r'[^a-z0-9]', '', str(v.get("username", "")).lower()) not in fav_authors_clean and str(v.get("id")) not in favorite_ids
        ]
    elif author_filter == "only_fav":
        videos = [
            v for v in videos
            if isinstance(v, dict) and (re.sub(r'[^a-z0-9]', '', str(v.get("username", "")).lower()) in fav_authors_clean or str(v.get("id")) in favorite_ids)
        ]

    for v in videos:
        if isinstance(v, dict) and "id" in v:
            v["is_favorite"] = str(v["id"]) in favorite_ids
            u_clean = re.sub(r'[^a-z0-9]', '', str(v.get("username", "")).lower())
            v["has_favorite_video"] = u_clean in fav_authors_clean
            v["tags"] = v.get("tags") or extract_video_tags(v)
            
            poster = v.get("poster") or v.get("thumbnail") or ""
            if "logo" in poster.lower():
                poster = ""
            if poster:
                if poster.endswith(".mp4"):
                    poster = poster.replace(".mp4", ".jpg")
                v["poster"] = poster
                if "freefile.io" in poster or "camwhores" in poster:
                    v["poster_direct"] = poster
                v["poster_proxy"] = f"/api/thumb?url={requests.utils.quote(poster)}"
                v["thumbnail_proxy"] = f"/api/thumb?url={requests.utils.quote(poster)}"
            
            preview = v.get("preview_video") or ""
            if "logo" in preview.lower():
                preview = ""
            if v.get("source") != "camwhores" and not preview and poster and ".jpg" in poster and not poster.endswith("/logo.png"):
                preview = poster.replace(".jpg", ".mp4")
            if preview:
                v["preview_video"] = preview
                if "freefile.io" in preview:
                    v["preview_direct"] = preview
                    v["preview_proxy"] = preview
                else:
                    v["preview_proxy"] = f"/api/video/stream?url={requests.utils.quote(preview)}"

            if (v.get("source") == "camwhores" or str(v.get("id", "")).startswith("cw_")) and not v.get("preview_video"):
                raw_id = str(v.get("id", "")).replace("cw_", "").strip()
                if raw_id in camwhores_scraper._details_cache:
                    cached_det = camwhores_scraper._details_cache[raw_id]["data"]
                    if cached_det.get("direct_url"):
                        v["preview_video"] = cached_det["direct_url"]
                        v["preview_proxy"] = f"/api/video/stream?url={requests.utils.quote(cached_det['direct_url'])}"

    # Wzbogacanie tagów modeli z lokalnej bazy profili
    try:
        from model_tags import model_tag_manager
        for v in videos:
            if isinstance(v, dict):
                model_tag_manager.enrich_video(v)
    except Exception:
        pass

    # Grupowanie filmów według autora (jeśli włączone: 1 kafelek na modelkę + lista pozostałych filmów)
    if group_authors in (True, "1", "true", "True"):
        grouped_map = {}
        grouped_list = []
        for v in videos:
            if not isinstance(v, dict):
                continue
            raw_u = str(v.get("username", "")).strip()
            norm_u = re.sub(r'[^a-z0-9]', '', raw_u.lower())
            if not norm_u or norm_u == "model":
                v_copy = dict(v)
                v_copy["is_grouped"] = False
                v_copy["group_count"] = 1
                v_copy["grouped_videos"] = [dict(v)]
                grouped_list.append(v_copy)
                continue
            if norm_u not in grouped_map:
                leader = dict(v)
                leader["is_grouped"] = False
                leader["group_count"] = 1
                leader["grouped_videos"] = [dict(v)]
                grouped_map[norm_u] = leader
                grouped_list.append(leader)
            else:
                leader = grouped_map[norm_u]
                leader["is_grouped"] = True
                leader["group_count"] += 1
                leader["grouped_videos"].append(dict(v))
        videos = grouped_list

    return videos

@app.get("/")
def serve_index():
    return _versioned_html(os.path.join(STATIC_DIR,"index.html"))


@app.get("/watch/{video_id}")
def serve_watch_page(video_id: str):
    return _versioned_html(os.path.join(STATIC_DIR,"watch.html"))


import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Pamięć podręczna RAM (LRU) dla natychmiastowego serwowania (0.2 ms)
MEMORY_CACHE_LOCK = threading.Lock()
MEMORY_CACHE_MAX = 600
MEMORY_CACHE = OrderedDict()

# Dedykowana sesja z pulą połączeń do szybkiego pobierania miniatur równolegle
thumb_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=Retry(total=2, backoff_factor=0.1))
thumb_session.mount("http://", _adapter)
thumb_session.mount("https://", _adapter)
thumb_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://archivebate.com/"
})

# Pamięć podręczna rozwiązanych przekierowań (302) dla błyskawicznego streamingu wideo bez roundtripów
_REDIRECT_CACHE = {}
_REDIRECT_CACHE_LOCK = threading.Lock()
_REDIRECT_CACHE_TTL = 900  # 15 minut

def get_cached_redirect(url: str) -> Optional[str]:
    with _REDIRECT_CACHE_LOCK:
        entry = _REDIRECT_CACHE.get(url)
        if entry and (time.time() - entry["time"] < _REDIRECT_CACHE_TTL):
            return entry["target"]
    return None

def set_cached_redirect(url: str, target: str):
    with _REDIRECT_CACHE_LOCK:
        _REDIRECT_CACHE[url] = {"target": target, "time": time.time()}
        if len(_REDIRECT_CACHE) > 1000:
            try:
                _REDIRECT_CACHE.pop(next(iter(_REDIRECT_CACHE)))
            except Exception:
                pass

def invalidate_cached_redirect(url: str):
    if not url:
        return
    with _REDIRECT_CACHE_LOCK:
        _REDIRECT_CACHE.pop(url, None)
        keys_to_del = [k for k, v in _REDIRECT_CACHE.items() if isinstance(v, dict) and v.get("target") == url]
        for k in keys_to_del:
            _REDIRECT_CACHE.pop(k, None)

def _validated_session_get(http_session, url: str, *, headers=None, timeout=8, stream=False, max_redirects=3):
    """GET z walidacją każdego redirectu i pamięcią podręczną rozwiązanego URL dla błyskawicznego streamingu."""
    # 1. Błyskawiczny skrót: jeśli URL był już wcześniej rozwiązany, łączymy się bezpośrednio z docelowym CDN
    cached_target = get_cached_redirect(url)
    if cached_target and is_safe_remote_url(cached_target):
        try:
            res = http_session.get(cached_target, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)
            if res.status_code in (200, 206):
                return res
            res.close()
        except Exception:
            pass

    current = url
    for _ in range(max_redirects + 1):
        if not is_safe_remote_url(current):
            raise HTTPException(status_code=400, detail="Niedozwolony adres zdalny")
        res = http_session.get(current, headers=headers, timeout=timeout, stream=stream, allow_redirects=False)
        if res.status_code not in (301, 302, 303, 307, 308):
            if current != url:
                set_cached_redirect(url, current)
            return res
        location = res.headers.get("Location")
        res.close()
        if not location:
            return res
        current = urljoin(current, location)
    raise HTTPException(status_code=502, detail="Zbyt wiele przekierowań zdalnego zasobu")

_thumb_fetch_guard = threading.Lock()
_thumb_fetch_locks = {}
_thumb_disk_writes = 0
_last_thumb_trim_time = 0.0

def _thumb_fetch_lock(url_hash: str) -> threading.Lock:
    with _thumb_fetch_guard:
        lock = _thumb_fetch_locks.get(url_hash)
        if lock is None:
            if len(_thumb_fetch_locks) > 2000:
                _thumb_fetch_locks.clear()
            lock = threading.Lock()
            _thumb_fetch_locks[url_hash] = lock
        return lock

_thumb_failures = OrderedDict()
MEMORY_CACHE_BYTES = 64 * 1024 * 1024


def _remember_thumbnail(key, content, content_type):
    MEMORY_CACHE[key] = (content, content_type)
    MEMORY_CACHE.move_to_end(key)
    while len(MEMORY_CACHE)>MEMORY_CACHE_MAX or sum(len(v[0]) for v in MEMORY_CACHE.values())>MEMORY_CACHE_BYTES:
        MEMORY_CACHE.popitem(last=False)


@app.get("/api/thumb")
def get_thumbnail_proxy(url: str = Query(...)):
    import io
    import tempfile
    from PIL import Image
    if not url.startswith("http"):
        url = "https:"+url if url.startswith("//") else f"https://archivebate.com{url}"
    if url.endswith(".mp4"): url=url[:-4]+".jpg"
    if not is_safe_remote_url(url): raise HTTPException(400,"Niedozwolony adres miniatury")
    key=hashlib.sha256(url.encode()).hexdigest()
    headers={"Cache-Control":"public, max-age=86400"}
    with _thumb_fetch_lock(key):
        with MEMORY_CACHE_LOCK:
            if key in MEMORY_CACHE:
                content, kind=MEMORY_CACHE[key]
                MEMORY_CACHE.move_to_end(key)
                return Response(content,media_type=kind,headers={**headers,"X-Cache":"RAM"})
            if _thumb_failures.get(key,0)>time.monotonic():
                return Response(status_code=502,headers={"Cache-Control":"no-store"})
        path=os.path.join(THUMBS_CACHE_DIR,f"{key}.thumb")
        try:
            with open(path,"rb") as cache:
                kind=cache.readline(128).decode().strip()
                content=cache.read(4*1024*1024+1)
            if not kind.startswith('image/') or len(content)>4*1024*1024: raise ValueError('Invalid cached image')
            with MEMORY_CACHE_LOCK: _remember_thumbnail(key,content,kind)
            return Response(content,media_type=kind,headers={**headers,"X-Cache":"DISK"})
        except (OSError,ValueError): pass
        res=None
        try:
            res=_validated_session_get(thumb_session,url,timeout=6,stream=True)
            kind=res.headers.get("Content-Type","").split(';')[0].strip().lower()
            if res.status_code!=200 or not kind.startswith('image/'): raise ValueError('Not an image')
            body=bytearray()
            for chunk in res.iter_content(65536):
                body.extend(chunk)
                if len(body)>4*1024*1024: raise ValueError('Image too large')
            content=bytes(body)
            with Image.open(io.BytesIO(content)) as img: img.verify()
            try:
                fd,temporary=tempfile.mkstemp(dir=str(THUMBS_CACHE_DIR),suffix='.tmp')
                try:
                    with os.fdopen(fd,'wb') as cache:
                        cache.write(kind.encode()+b'\n'+content)
                        cache.flush()
                    os.replace(temporary,path)
                finally:
                    if os.path.exists(temporary): os.remove(temporary)
            except OSError: pass
            with MEMORY_CACHE_LOCK: _remember_thumbnail(key,content,kind)
            return Response(content,media_type=kind,headers={**headers,"X-Cache":"NET"})
        except Exception:
            with MEMORY_CACHE_LOCK:
                _thumb_failures[key]=time.monotonic()+20
                while len(_thumb_failures)>1000: _thumb_failures.popitem(last=False)
            return Response(status_code=502,headers={"Cache-Control":"no-store"})
        finally:
            if res is not None: res.close()

@app.get("/api/status")
async def get_status():
    """Zwraca status natychmiast z pamięci lokalnej.

    Logowanie odbywa się już w wątku startowym aplikacji. Nie wykonujemy tutaj
    pełnej synchronizacji konta ani dodatkowego logowania, bo endpoint jest
    odpytywany równolegle z pierwszym ekranem i wcześniej konkurował o sieć z
    miniaturami oraz listą filmów.
    """
    status = session.get_status()
    status["account_configured"] = bool(session.email and session.password)
    status["favorites_count"] = len(storage.data.get("favorites", []))
    status["history_count"] = len(storage.data.get("history", []))
    status["following_count"] = len(storage.data.get("following", []))
    status["last_synced"] = storage.data.get("last_synced")
    status["favorite_authors"] = storage.get_favorite_authors()
    return status

@app.post("/api/relogin")
async def relogin():
    """Ponawia logowanie; ponownie czyta .env.local, więc nie wymaga restartu aplikacji."""
    email, password = get_archivebate_credentials()
    session.email = email
    session.password = password
    success = await asyncio.to_thread(session.login)
    if success:
        threading.Thread(target=sync_account_data, daemon=True).start()
    return {"success": success, "status": await get_status()}

# ENDPOINTY PANELU KONTA
async def _ensure_account_synced():
    if storage.data.get("last_synced"):
        return None
    return await asyncio.to_thread(sync_account_data)

@app.get("/api/account/summary")
async def get_account_summary():
    """Zwraca podsumowanie panelu konta."""
    if not storage.data.get("last_synced"):
        await _ensure_account_synced()
    return {
        "email": session.email,
        "logged_in": session.is_logged_in,
        "favorites_count": len(storage.data.get("favorites", [])),
        "history_count": len(storage.data.get("history", [])),
        "following_count": len(storage.data.get("following", [])),
        "last_synced": storage.data.get("last_synced")
    }

@app.get("/api/account/favorites")
async def get_account_favorites(page: int = Query(1, ge=1), per_page: int = Query(280, ge=1, le=1000)):
    """Zwraca listę ulubionych wideo z obsługą stron."""
    favs = storage.get_favorites()
    if len(favs) == 0 and not storage.data.get("last_synced"):
        await _ensure_account_synced()
        favs = storage.get_favorites()

    total = len(favs)
    last_page = max(1, math.ceil(total / per_page))
    start_idx = (page - 1) * per_page
    sliced = favs[start_idx : start_idx + per_page]

    return {
        "total": total,
        "page": page,
        "last_page": last_page,
        "count": len(sliced),
        "videos": _enrich_videos(sliced)
    }

def invalidate_feed_cache(filter_pattern: Optional[str] = None):
    """Czyści pliki pamięci podręcznej feedów (np. po dodaniu do ulubionych lub zablokowaniu profilu)."""
    try:
        if hasattr(scraper, "_home_cache"):
            scraper._home_cache.clear()
        if os.path.exists(FEED_CACHE_DIR):
            for fname in os.listdir(FEED_CACHE_DIR):
                if fname.endswith(".json") and not fname.startswith(("raw_v1_", "snapshot_v1_")):
                    if filter_pattern is None or filter_pattern in fname:
                        try:
                            os.remove(os.path.join(FEED_CACHE_DIR, fname))
                        except OSError:
                            pass
    except Exception as e:
        print(f"[Cache] Błąd unieważniania feed cache: {e}")

@app.post("/api/account/favorites/toggle")
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

@app.get("/api/account/history")
async def get_account_history(page: int = Query(1, ge=1), per_page: int = Query(280, ge=1, le=1000)):
    """Zwraca historię oglądanych wideo z obsługą stron."""
    hist = storage.get_history()
    if len(hist) == 0 and not storage.data.get("last_synced"):
        await _ensure_account_synced()
        hist = storage.get_history()

    total = len(hist)
    last_page = max(1, math.ceil(total / per_page))
    start_idx = (page - 1) * per_page
    sliced = hist[start_idx : start_idx + per_page]

    return {
        "total": total,
        "page": page,
        "last_page": last_page,
        "count": len(sliced),
        "videos": _enrich_videos(sliced)
    }

@app.post("/api/account/history/record")
def record_history(video: dict = Body(...)):
    """Zapisuje obejrzenie filmu w historii."""
    try:
        storage.record_history(video)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Nie udało się trwale zapisać historii: {exc}")
    return {"success": True, "total_history": len(storage.get_history())}

@app.post("/api/account/history/clear")
def clear_history():
    """Czyści lokalną historię oglądania."""
    try:
        storage.clear_history()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Nie udało się trwale wyczyścić historii: {exc}")
    return {"success": True, "total_history": 0}

@app.get("/api/account/following")
async def get_account_following(page: int = Query(1, ge=1), per_page: int = Query(280, ge=1, le=1000)):
    """Zwraca wideo z obserwowanych kanałów z obsługą stron."""
    foll = storage.get_following()
    if len(foll) == 0 and not storage.data.get("last_synced"):
        await _ensure_account_synced()
        foll = storage.get_following()

    total = len(foll)
    last_page = max(1, math.ceil(total / per_page))
    start_idx = (page - 1) * per_page
    sliced = foll[start_idx : start_idx + per_page]

    return {
        "total": total,
        "page": page,
        "last_page": last_page,
        "count": len(sliced),
        "videos": _enrich_videos(sliced)
    }

@app.post("/api/account/sync")
def sync_account():
    """Wymusza synchronizację i zachowuje realny wynik auth/fetch/persistence."""
    return sync_account_data()

@app.get("/api/tags")
async def get_tags():
    """Zwraca listę przykładowych i popularnych tagów z serwisu."""
    return {"tags": POPULAR_TAGS}

_feed_refresh_lock = threading.Lock()
_feed_refreshing = set()
HOME_FEED_FRESH_SECONDS = 90
HOME_PAGE_SIZE = 280
_catalog_bootstrap_lock = threading.Lock()
_catalog_bootstrap_started = set()
_catalog_bootstrap_active = set()
_catalog_bootstrap_retry_at = {}


def _catalog_fetchers():
    """Fetcher map for a complete catalog revision.

    A revision always contains both sources; source filters are applied by the
    SQLite query. Building a revision from only the currently visible source
    would make the global count silently lose the other source.
    """
    return {
        "archivebate": lambda page: scraper._fetch_single_ab_home_page(page, strict=True),
        "camwhores": lambda page: camwhores_scraper.get_latest_videos(page, strict=True),
    }


def _published_catalog_revision(service):
    active = service.get_active_revision()
    if active is not None and service.is_revision_complete(active):
        return active
    return None


def _available_catalog_revision(service):
    """Return the best local revision, including a useful partial bootstrap."""
    return service.get_active_revision()


def _coerce_optional_revision(value):
    """Keep direct Python calls compatible with FastAPI's ``Query`` defaults."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    default = getattr(value, "default", None)
    if default is None or isinstance(default, bool):
        return None
    try:
        return int(default)
    except (TypeError, ValueError):
        return None


def _catalog_indexing_is_running(service) -> bool:
    """Check both the durable index worker and the short bootstrap wrapper."""
    with _catalog_bootstrap_lock:
        if id(service) in _catalog_bootstrap_active:
            return True
    progress = getattr(service, "_indexing_progress", {}) or {}
    return bool(progress.get("is_indexing"))


def _ensure_catalog_indexing(service) -> None:
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


def _get_fav_authors_feed(page: int = 1, source: str = "all", target_count: int = 280) -> list:
    """Pobiera feed składający się wyłącznie z ulubionych filmów oraz filmów od ulubionych autorów."""
    fav_videos = list(storage.get_favorites())
    if source == "only-camwhores":
        fav_videos = [v for v in fav_videos if v.get("source") == "camwhores" or "camwhores" in str(v.get("platform", "")).lower()]
    elif source == "only-archivebate":
        fav_videos = [v for v in fav_videos if v.get("source") != "camwhores" and "camwhores" not in str(v.get("platform", "")).lower()]

    fav_authors = storage.get_favorite_authors()

    needed = page * target_count
    from camwhores import deduplicate_videos
    combined = deduplicate_videos(fav_videos)

    if len(combined) < needed and fav_authors:
        authors_batch_size = 20 if source == "only-camwhores" else 10
        start_author_idx = ((page - 1) * authors_batch_size) % len(fav_authors)
        offset = 0
        author_videos = []

        while len(combined) < needed and offset < 60 and offset < len(fav_authors):
            batch = [fav_authors[(start_author_idx + offset + i) % len(fav_authors)] for i in range(min(authors_batch_size, len(fav_authors)))]
            offset += authors_batch_size

            with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                if source == "only-camwhores":
                    for res in executor.map(lambda a: camwhores_scraper.search_videos(a, 1), batch):
                        author_videos.extend(res.get("videos", []) if isinstance(res, dict) else (res or []))
                else:
                    for vlist in executor.map(lambda a: scraper.get_model_videos(a, 1), batch):
                        author_videos.extend(vlist or [])

            combined = deduplicate_videos(fav_videos + author_videos)

    from scraper import sort_videos_newest_first
    sorted_vids = sort_videos_newest_first(combined)
    start_idx = (page - 1) * target_count
    return sorted_vids[start_idx : start_idx + target_count]


def _home_feed_cache_path(page: int, source: str = "all", author_filter: str = "all", group_authors: str = "0") -> str:
    clean_src = re.sub(r'[^a-zA-Z0-9_-]', '', source or "all")
    clean_af = re.sub(r'[^a-zA-Z0-9_-]', '', author_filter or "all")
    grp_flag = "grp" if str(group_authors).lower() in ("1", "true") else "std"
    return os.path.join(FEED_CACHE_DIR, f"feed_{clean_src}_{clean_af}_{grp_flag}_{page}.json")


def _fetch_and_cache_home(page: int, source: str = "all", author_filter: str = "all", group_authors: str = "0") -> list:
    blocked_models = set(re.sub(r'[^a-z0-9]', '', b.lower()) for b in storage.get_blocked_models())
    fav_authors = set(re.sub(r'[^a-z0-9]', '', a.lower()) for a in storage.get_favorite_authors())

    if author_filter == "only_fav":
        videos = _get_fav_authors_feed(page=page, source=source, target_count=HOME_PAGE_SIZE)
    else:
        videos = scraper.get_home_videos(
            page=page,
            source=source,
            author_filter=author_filter,
            blocked_models=blocked_models,
            favorite_authors=fav_authors,
            target_count=HOME_PAGE_SIZE
        ) or []

    if videos:
        try:
            atomic_write_json(_home_feed_cache_path(page, source, author_filter, group_authors), videos)
        except Exception as e:
            print(f"[Cache] Nie udało się zapisać feedu {source}/{author_filter}/{page}: {e}")
    return videos


def _refresh_home_in_background(page: int, source: str = "all", author_filter: str = "all", group_authors: str = "0") -> None:
    key = (page, source, author_filter, group_authors)
    with _feed_refresh_lock:
        if key in _feed_refreshing:
            return
        _feed_refreshing.add(key)

    def worker():
        try:
            _fetch_and_cache_home(page, source, author_filter, group_authors)
        except Exception as e:
            print(f"[Cache] Odświeżenie feedu {source}/{author_filter}/{page} nie powiodło się: {e}")
        finally:
            with _feed_refresh_lock:
                _feed_refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def get_catalog_stats(source: str = "all", author_filter: str = "all", revision: Optional[int] = None) -> dict:
    """Dynamicznie przelicza całkowitą liczbę filmów i stron w katalogu na podstawie trwałego indeksu SQLite."""
    from catalog_service import catalog_service
    revision = _coerce_optional_revision(revision)
    if revision is None and _published_catalog_revision(catalog_service) is None:
        _ensure_catalog_indexing(catalog_service)
    revision_to_use = revision if revision is not None else _available_catalog_revision(catalog_service)
    blocked_models = storage.get_blocked_models()
    fav_authors = storage.get_favorite_authors()
    fav_ids = [str(item.get("id")) for item in storage.get_favorites()]

    res = catalog_service.query_page(
        page=1,
        page_size=HOME_PAGE_SIZE,
        source=source,
        author_filter=author_filter,
        group_authors=False,
        revision=revision_to_use,
        blocked_models=blocked_models,
        favorite_authors=fav_authors,
        favorite_ids=fav_ids
    )

    total_vids = res["video_count"]
    last_page = res["page_count"]

    base_res = catalog_service.query_page(
        page=1,
        page_size=HOME_PAGE_SIZE,
        source=source,
        author_filter="all",
        group_authors=False,
        revision=revision_to_use
    )
    base_videos = base_res["video_count"]
    deducted = max(0, base_videos - total_vids)

    return {
        "total_videos": total_vids,
        "last_page": last_page,
        "base_catalog_videos": base_videos,
        "blocked_videos": deducted,
        "catalog_complete": res["catalog_complete"],
        "updated_at": res["updated_at"]
    }


@app.get("/api/videos")
def get_videos(
    page: int = Query(1, ge=1),
    force_refresh: bool = Query(False),
    source: str = Query("all"),
    author_filter: str = Query("all"),
    group_authors: str = Query("0"),
    snapshot_id: Optional[str] = Query(None),
    revision: Optional[int] = Query(None)
):
    """Compatibility endpoint using the same response contract as ``/api/feed``.

    Older clients still receive ``videos`` and legacy aliases, while new clients
    can use one stable revision/snapshot/count contract during migration.
    """
    from catalog_service import catalog_service
    revision = _coerce_optional_revision(revision)
    active_rev = _published_catalog_revision(catalog_service)
    if active_rev is None:
        _ensure_catalog_indexing(catalog_service)
        active_rev = _published_catalog_revision(catalog_service)
    refresh_revision = None
    if force_refresh:
        refresh_revision = catalog_service.build_revision_background(_catalog_fetchers(), force=True)
    available_rev = _available_catalog_revision(catalog_service)
    if active_rev is not None or available_rev is not None:
        is_grouped = group_authors in (True, "1", "true", "True")
        rev_to_use = revision
        if rev_to_use is None and snapshot_id and str(snapshot_id).isdigit():
            rev_to_use = int(snapshot_id)
        if rev_to_use is None:
            rev_to_use = active_rev if active_rev is not None else available_rev
        res = catalog_service.query_page(
            page=page,
            page_size=HOME_PAGE_SIZE,
            source=source,
            author_filter=author_filter,
            group_authors=is_grouped,
            revision=rev_to_use,
            blocked_models=storage.get_blocked_models(),
            favorite_authors=storage.get_favorite_authors(),
            favorite_ids=[str(item.get("id")) for item in storage.get_favorites()],
            enrich_fn=lambda items: _enrich_videos(items, author_filter=author_filter, source=source, group_authors="0")
        )
        catalog_stats = get_catalog_stats(source=source, author_filter=author_filter, revision=rev_to_use)
        return {
            **res,
            "page": page,
            "last_page": res["page_count"],
            "total_videos": res["video_count"],
            "base_catalog_videos": catalog_stats.get("base_catalog_videos", res["video_count"]),
            "blocked_videos": catalog_stats.get("blocked_videos", 0),
            "count": len(res["items"]),
            "target_count": HOME_PAGE_SIZE,
            "source": source,
            "author_filter": author_filter,
            "group_authors": group_authors,
            "cache_state": "catalog_sqlite",
            "cache_age_seconds": max(0, int(time.time() - res["updated_at"])) if res["updated_at"] else 0,
            "items": res["items"],
            "catalog_revision": res["catalog_revision"],
            "catalog_complete": res["catalog_complete"],
            "group_count": res["group_count"],
            "page_count": res["page_count"],
            "snapshot_id": res["snapshot_id"],
            "revision": res["revision"],
            "refresh_revision": refresh_revision,
            "refresh_pending": bool(refresh_revision and refresh_revision != res["catalog_revision"])
        }

    catalog_stats = get_catalog_stats(source=source, author_filter=author_filter)
    max_page = catalog_stats["last_page"]
    if page > max_page:
        page = max_page

    cache_path = _home_feed_cache_path(page, source, author_filter, group_authors)
    cached, mtime = read_json_cache(cache_path)
    age = cache_age_seconds(mtime)

    # Sprawdzenie kompatybilności wstecznej ze starym formatem home_{page}.json
    if cached is None and source == "all" and author_filter == "all" and str(group_authors).lower() not in ("1", "true"):
        legacy_path = os.path.join(FEED_CACHE_DIR, f"home_{page}.json")
        cached, mtime = read_json_cache(legacy_path)
        age = cache_age_seconds(mtime)

    if isinstance(cached, list):
        if force_refresh or age > HOME_FEED_FRESH_SECONDS:
            _refresh_home_in_background(page, source, author_filter, group_authors)
        raw_videos = cached
        cache_state = "fresh" if age <= HOME_FEED_FRESH_SECONDS else "stale-refreshing"
    else:
        raw_videos = _fetch_and_cache_home(page, source, author_filter, group_authors)
        cache_state = "network"

    enriched = _enrich_videos([dict(v) for v in raw_videos], author_filter=author_filter, source=source, group_authors=group_authors)

    final_videos = enriched[:HOME_PAGE_SIZE]

    return {
        "catalog_revision": 0,
        "snapshot_id": None,
        "revision": 0,
        "page": page,
        "page_size": HOME_PAGE_SIZE,
        "last_page": catalog_stats["last_page"],
        "page_count": catalog_stats["last_page"],
        "total_videos": catalog_stats["total_videos"],
        "video_count": catalog_stats["total_videos"],
        "group_count": len(final_videos),
        "base_catalog_videos": catalog_stats["base_catalog_videos"],
        "blocked_videos": catalog_stats["blocked_videos"],
        "count": len(final_videos),
        "target_count": HOME_PAGE_SIZE,
        "source": source,
        "author_filter": author_filter,
        "group_authors": group_authors,
        "cache_state": cache_state,
        "cache_age_seconds": 0 if not math.isfinite(age) else int(age),
        "videos": final_videos,
        "items": final_videos,
        "catalog_complete": False,
        "updated_at": catalog_stats.get("updated_at", 0),
        "indexing_progress": {},
        "known_count": len(final_videos),
        "has_more": page < catalog_stats["last_page"],
        "complete": False,
        "target_count": HOME_PAGE_SIZE,
        "total_is_estimate": True,
        "source_error": {},
        "retryable": False
    }

def _feed_snapshot(source, author_filter, group_authors, snapshot_id=None, force=False):
    from feed_service import get_snapshot
    preferences = {"blocked": sorted(storage.get_blocked_models()), "favorites": sorted(str(v.get("id")) for v in storage.get_favorites())}
    spec = {"source": source, "author_filter": author_filter, "group_authors": group_authors, "preferences": preferences}
    fetchers = {}
    if source != "only-camwhores": fetchers["archivebate"] = lambda page: scraper._fetch_single_ab_home_page(page, strict=True)
    if source != "only-archivebate": fetchers["camwhores"] = lambda page: camwhores_scraper.get_latest_videos(page, strict=True)
    try:
        return get_snapshot(spec, fetchers, lambda items: _enrich_videos(items, author_filter=author_filter, source=source, group_authors=group_authors), snapshot_id, force)
    except KeyError:
        raise HTTPException(409, "Snapshot wygasł lub zmieniły się preferencje. Odśwież widok.")
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))


@app.get("/api/feed")
def progressive_feed(
    page: int = Query(1, ge=1),
    source: str = "all",
    author_filter: str = "all",
    group_authors: str = "0",
    snapshot_id: Optional[str] = None,
    force_refresh: bool = False,
    revision: Optional[int] = None
):
    from catalog_service import catalog_service
    revision = _coerce_optional_revision(revision)
    active_rev = _published_catalog_revision(catalog_service)
    if active_rev is None:
        _ensure_catalog_indexing(catalog_service)
        active_rev = _published_catalog_revision(catalog_service)
    refresh_revision = None
    if force_refresh:
        refresh_revision = catalog_service.build_revision_background(_catalog_fetchers(), force=True)
    available_rev = _available_catalog_revision(catalog_service)
    if active_rev is not None or available_rev is not None:
        rev_to_use = revision
        if rev_to_use is None and snapshot_id and str(snapshot_id).isdigit():
            rev_to_use = int(snapshot_id)
        if rev_to_use is None:
            rev_to_use = active_rev if active_rev is not None else available_rev

        is_grouped = group_authors in (True, "1", "true", "True")
        blocked_models = storage.get_blocked_models()
        fav_authors = storage.get_favorite_authors()
        fav_ids = [str(item.get("id")) for item in storage.get_favorites()]

        result = catalog_service.query_page(
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


@app.get("/api/feed/stream")
def progressive_feed_stream(
    snapshot_id: str,
    page: int = Query(1, ge=1),
    source: str = "all",
    author_filter: str = "all",
    group_authors: str = "0",
    revision: Optional[int] = None
):
    from catalog_service import catalog_service
    revision = _coerce_optional_revision(revision)
    rev_to_check = revision if revision is not None else (int(snapshot_id) if str(snapshot_id).isdigit() else catalog_service.get_active_revision())
    if rev_to_check is not None and catalog_service.is_revision_complete(rev_to_check):
        async def direct_events():
            is_grouped = group_authors in (True, "1", "true", "True")
            data = catalog_service.query_page(
                page=page,
                page_size=HOME_PAGE_SIZE,
                source=source,
                author_filter=author_filter,
                group_authors=is_grouped,
                revision=rev_to_check,
                blocked_models=storage.get_blocked_models(),
                favorite_authors=storage.get_favorite_authors(),
                favorite_ids=[str(item.get("id")) for item in storage.get_favorites()],
                enrich_fn=lambda items: _enrich_videos(items, author_filter=author_filter, source=source, group_authors="0")
            )
            data["type"] = "complete"
            yield f"data: {json.dumps(data)}\n\n"
        return StreamingResponse(direct_events(), media_type="text/event-stream", headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})

    # A partial local catalog is still useful on first start. Stream its
    # progress while the background worker imports/fetches the remaining
    # pages, then switch atomically to the newly published revision.
    if rev_to_check is not None and (revision is not None or str(snapshot_id).isdigit()):
        async def catalog_events():
            previous = None
            deadline = time.monotonic() + 120
            idle_since = None
            while time.monotonic() < deadline:
                current_rev = rev_to_check
                data = await asyncio.to_thread(
                    catalog_service.query_page,
                    page=page,
                    page_size=HOME_PAGE_SIZE,
                    source=source,
                    author_filter=author_filter,
                    group_authors=group_authors in (True, "1", "true", "True"),
                    revision=current_rev,
                    blocked_models=storage.get_blocked_models(),
                    favorite_authors=storage.get_favorite_authors(),
                    favorite_ids=[str(item.get("id")) for item in storage.get_favorites()],
                    enrich_fn=lambda items: _enrich_videos(items, author_filter=author_filter, source=source, group_authors="0"),
                )
                progress = data.get("indexing_progress") or {}
                signature = (
                    data.get("catalog_revision"),
                    data.get("updated_at"),
                    data.get("video_count"),
                    data.get("group_count"),
                    data.get("page_count"),
                    bool(data.get("catalog_complete")),
                    bool(progress.get("is_indexing")),
                    progress.get("error"),
                )
                if signature != previous:
                    previous = signature
                    data["type"] = "complete" if data.get("catalog_complete") else "batch"
                    yield f"data: {json.dumps(data)}\n\n"
                if data.get("catalog_complete"):
                    return
                if _catalog_indexing_is_running(catalog_service):
                    idle_since = None
                else:
                    # Allow the bootstrap thread to start between the initial
                    # feed response and this SSE connection, but do not leave
                    # the browser waiting forever after a failed run.
                    idle_since = idle_since or time.monotonic()
                    if time.monotonic() - idle_since >= 2.0:
                        data["type"] = "source_error"
                        data["stopped"] = True
                        yield f"data: {json.dumps(data)}\n\n"
                        return
                await asyncio.sleep(.1)
            data["type"] = "source_error"
            data["stopped"] = True
            yield f"data: {json.dumps(data)}\n\n"

        return StreamingResponse(catalog_events(), media_type="text/event-stream", headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})

    snapshot = _feed_snapshot(source, author_filter, group_authors, snapshot_id)
    async def events():
        snapshot.subscribe(page)
        previous = -1
        deadline = time.monotonic()+120
        try:
            while time.monotonic()<deadline:
                data = await asyncio.to_thread(snapshot.read, page)
                if data["revision"] != previous:
                    previous = data["revision"]
                    data["type"] = "source_error" if data["source_error"] else ("complete" if data["complete"] else "batch")
                    yield f"data: {json.dumps(data)}\n\n"
                if data["complete"]: return
                if not snapshot.building:
                    data["type"] = "source_error"
                    data["stopped"] = True
                    yield f"data: {json.dumps(data)}\n\n"
                    return
                await asyncio.sleep(.1)
        finally:
            snapshot.unsubscribe()
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})


@app.get("/api/model/{username}")
def get_model_videos(username: str, page: int = Query(1, ge=1)):
    """Pobiera filmy konkretnej modelki dla określonej strony."""
    videos = scraper.get_model_videos(username=username, page=page)
    return {
        "username": username,
        "page": page,
        "count": len(videos),
        "videos": _enrich_videos(videos)
    }

@app.get("/api/search")
def search_videos(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    source: str = Query("all"),
    author_filter: str = Query("all"),
    group_authors: str = Query("0")
):
    """Wyszukuje po tagu, nazwie modelki lub słowie kluczowym z obsługą stron oraz filtrów źródła i autorów."""
    results = scraper.search_query(q, page=page, source=source, author_filter=author_filter, group_authors=group_authors)
    results["videos"] = _enrich_videos(results.get("videos", []), author_filter=author_filter, source=source, group_authors=group_authors)
    return results

@app.get("/api/search/stream")
def search_videos_stream(
    q: str = Query(..., min_length=1),
    source: str = Query("all"),
    author_filter: str = Query("all"),
    group_authors: str = Query("0")
):
    """Strumieniowe wyszukiwanie Server-Sent Events (SSE): przesyła profile natychmiast, a wideo po kolei z uwzględnieniem filtrów."""
    def event_generator():
        for event in scraper.search_query_stream(q, source=source, author_filter=author_filter, group_authors=group_authors):
            if event.get("videos"):
                event["videos"] = _enrich_videos(event["videos"], author_filter=author_filter, source=source, group_authors=group_authors)
            if event.get("all_sorted_videos"):
                event["all_sorted_videos"] = _enrich_videos(event["all_sorted_videos"], author_filter=author_filter, source=source, group_authors=group_authors)
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

_details_refresh_lock = threading.Lock()
_details_refreshing = set()
_details_fetch_guard = threading.Lock()
_details_fetch_locks = {}

def _details_fetch_lock(video_id: str):
    key = safe_cache_key(video_id)
    with _details_fetch_guard:
        lock = _details_fetch_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _details_fetch_locks[key] = lock
        return lock


def _resolve_clean_video_id(video_id_or_url: str) -> str:
    if not video_id_or_url:
        return ""
    s = str(video_id_or_url).strip()
    if "/videos/" in s:
        m = re.search(r'/videos/(\d+)', s)
        if m:
            return f"cw_{m.group(1)}"
    elif "/watch/" in s:
        m = re.search(r'/watch/(\d+)', s)
        if m:
            return m.group(1)
    return s.rstrip("/").split("/")[-1].split("?")[0]


def _details_cache_path(video_id: str) -> str:
    clean = _resolve_clean_video_id(video_id)
    return os.path.join(DETAILS_CACHE_DIR, f"{safe_cache_key(clean)}.json")


def _stream_cache_path(video_id: str) -> str:
    clean = _resolve_clean_video_id(video_id)
    return os.path.join(STREAM_CACHE_DIR, f"{safe_cache_key(clean)}.json")


def _read_video_caches(video_id: str):
    """Read metadata and playback URL caches independently.

    Older installations kept both values in details_cache. Promote that shape
    lazily so an upgrade does not require a full resolver run or lose metadata.
    """
    clean_id = _resolve_clean_video_id(video_id)
    metadata, metadata_mtime = read_json_cache(_details_cache_path(clean_id))
    stream, stream_mtime = read_json_cache(_stream_cache_path(clean_id))
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    stream = dict(stream) if isinstance(stream, dict) else {}

    # Lazy migration of the pre-split cache format.
    legacy_direct = metadata.get("direct_url")
    if legacy_direct and not stream.get("direct_url"):
        stream = {
            "id": clean_id,
            "direct_url": legacy_direct,
            "embed_url": metadata.get("embed_url", ""),
            "direct_url_fetched_at": metadata.get("direct_url_fetched_at") or metadata_mtime or time.time(),
        }
        try:
            atomic_write_json(_stream_cache_path(clean_id), stream)
            metadata.pop("direct_url", None)
            metadata.pop("direct_url_fetched_at", None)
            # proxy_stream_url is derived and is never persisted in metadata.
            metadata.pop("proxy_stream_url", None)
            atomic_write_json(_details_cache_path(clean_id), metadata)
            stream_mtime = time.time()
        except Exception as exc:
            print(f"[Cache] Nie udało się rozdzielić cache detali {clean_id}: {exc}")

    combined = {**metadata, **stream}
    if stream.get("embed_url") and not combined.get("embed_url"):
        combined["embed_url"] = stream["embed_url"]
    return combined, metadata_mtime, stream_mtime


def _normalize_video_details(video_id: str, details: dict) -> dict:
    details = dict(details or {})
    if not details.get("preview_video") and details.get("thumbnail"):
        thumb = details["thumbnail"]
        details["preview_video"] = thumb.replace(".jpg", ".mp4") if ".jpg" in thumb else thumb
    vid_key = details.get("id") or _resolve_clean_video_id(video_id)
    details["id"] = vid_key
    # Proxy po ID pozostaje stabilny nawet gdy zewnętrzny direct_url wygaśnie.
    available = bool(details.get("direct_url")) and not details.get("is_private")
    details["availability"] = "private" if details.get("is_private") else ("available" if available else "unavailable")
    details["proxy_stream_url"] = f"/api/video/stream?id={vid_key}" if vid_key and available else ""
    details["is_favorite"] = storage.is_favorite(video_id)

    # Walidacja identyfikatora autora na podstawie linku profilu / znanego pola (Pakiet C, punkt 8)
    curr_u = (details.get("username") or "").strip()
    if not curr_u or curr_u.lower() == "model":
        prof_url = details.get("profile_url") or details.get("url") or ""
        recovered = None
        if prof_url:
            m = re.search(r'/(?:profile|models|search)/([a-zA-Z0-9_\-\.]+)', prof_url)
            if m and m.group(1).lower() not in ("model", "search", "videos"):
                recovered = m.group(1).strip()
        if recovered:
            details["username"] = recovered
        else:
            details["username"] = "Model"

    return details


STREAM_URL_FRESH_SECONDS = 1800  # 30 minut świeżości direct_url (Pakiet C, punkt 5)
DETAILS_CACHE_FRESH_SECONDS = 3600 * 6
DETAILS_CACHE_STALE_SECONDS = 3600 * 24

_RESOURCE_FAILURE_LOCK = threading.Lock()
_RESOURCE_FAILURES = {}  # host -> {"failed_at": float, "reason": str, "probing": bool}
RESOURCE_FAILURE_COOLDOWN_SECONDS = 15.0

_DETAILS_REFRESH_FAILURES_LOCK = threading.Lock()
_DETAILS_REFRESH_FAILURES = {}  # clean_id -> {"failed_at": float, "reason": str}
REFRESH_FAILURE_COOLDOWN_SECONDS = 15.0

_NO_STREAM_FAILURES_LOCK = threading.Lock()
_NO_STREAM_FAILURES = {}  # clean_id -> failed_at
NO_STREAM_FAILURE_COOLDOWN_SECONDS = 20.0

_playback_active_lock = threading.Lock()
_last_playback_active_time = 0.0

def is_playback_active(window: float = 5.0) -> bool:
    with _playback_active_lock:
        return (time.time() - _last_playback_active_time) < window

def _get_url_host(target_url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(target_url).netloc or target_url
    except Exception:
        return target_url

def _sanitize_log_url(target_url: str) -> str:
    try:
        from urllib.parse import urlparse
        p = urlparse(target_url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return "invalid-url"

def _is_host_failed(host: str) -> tuple:
    now = time.time()
    with _RESOURCE_FAILURE_LOCK:
        entry = _RESOURCE_FAILURES.get(host)
        if not entry:
            return False, ""
        if now - entry["failed_at"] < RESOURCE_FAILURE_COOLDOWN_SECONDS:
            if entry.get("probing"):
                return True, entry.get("reason", "Host jest w trakcie próby kontrolnej")
            entry["probing"] = True
            return False, ""
        _RESOURCE_FAILURES.pop(host, None)
        return False, ""

def _record_host_failure(host: str, reason: str):
    with _RESOURCE_FAILURE_LOCK:
        _RESOURCE_FAILURES[host] = {
            "failed_at": time.time(),
            "reason": reason,
            "probing": False
        }

def _clear_host_failure(host: str):
    with _RESOURCE_FAILURE_LOCK:
        _RESOURCE_FAILURES.pop(host, None)

def _has_recent_refresh_failure(clean_id: str) -> bool:
    now = time.time()
    with _DETAILS_REFRESH_FAILURES_LOCK:
        entry = _DETAILS_REFRESH_FAILURES.get(clean_id)
        if entry and (now - entry["failed_at"] < REFRESH_FAILURE_COOLDOWN_SECONDS):
            return True
        if clean_id in _DETAILS_REFRESH_FAILURES:
            _DETAILS_REFRESH_FAILURES.pop(clean_id, None)
        return False

def _record_refresh_failure(clean_id: str, reason: str = "Refresh failed"):
    with _DETAILS_REFRESH_FAILURES_LOCK:
        _DETAILS_REFRESH_FAILURES[clean_id] = {
            "failed_at": time.time(),
            "reason": reason
        }

def _clear_refresh_failure(clean_id: str):
    with _DETAILS_REFRESH_FAILURES_LOCK:
        _DETAILS_REFRESH_FAILURES.pop(clean_id, None)


def _has_recent_no_stream(clean_id: str) -> bool:
    now = time.time()
    with _NO_STREAM_FAILURES_LOCK:
        failed_at = _NO_STREAM_FAILURES.get(clean_id)
        if failed_at is not None and now - failed_at < NO_STREAM_FAILURE_COOLDOWN_SECONDS:
            return True
        _NO_STREAM_FAILURES.pop(clean_id, None)
        return False


def _record_no_stream(clean_id: str):
    with _NO_STREAM_FAILURES_LOCK:
        _NO_STREAM_FAILURES[clean_id] = time.time()
        if len(_NO_STREAM_FAILURES) > 1024:
            oldest = min(_NO_STREAM_FAILURES, key=_NO_STREAM_FAILURES.get)
            _NO_STREAM_FAILURES.pop(oldest, None)


def _clear_no_stream(clean_id: str):
    with _NO_STREAM_FAILURES_LOCK:
        _NO_STREAM_FAILURES.pop(clean_id, None)


def _fetch_and_cache_details(video_id: str) -> dict:
    details = dict(scraper.get_video_details(video_id) or {})
    if details:
        fetched_at = time.time()
        direct_url = details.get("direct_url")
        stream_payload = {
            "id": _resolve_clean_video_id(video_id),
            "direct_url": direct_url,
            "embed_url": details.get("embed_url", ""),
            "direct_url_fetched_at": fetched_at,
        }
        metadata = dict(details)
        metadata.pop("direct_url", None)
        metadata.pop("direct_url_fetched_at", None)
        metadata.pop("proxy_stream_url", None)
        try:
            atomic_write_json(_details_cache_path(video_id), metadata)
            if direct_url:
                atomic_write_json(_stream_cache_path(video_id), stream_payload)
                _clear_no_stream(_resolve_clean_video_id(video_id))
            else:
                # Resolver może zwrócić brak URL po tym, jak poprzedni adres
                # wygasł. Usuń stary dokument strumienia, żeby nie połączyć go
                # ponownie z aktualnymi metadanymi przy następnym odczycie.
                try:
                    os.remove(_stream_cache_path(video_id))
                except OSError:
                    pass
                _record_no_stream(_resolve_clean_video_id(video_id))
        except Exception as e:
            print(f"[Cache] Nie udało się zapisać detali {video_id}: {e}")
    return details


def _fetch_details_singleflight(video_id: str, force=False, rejected_url=None) -> dict:
    video_id = _resolve_clean_video_id(video_id)
    requested_at = time.time()
    with _details_fetch_lock(video_id):
        if _has_recent_refresh_failure(video_id) and rejected_url:
            cached, _, _ = _read_video_caches(video_id)
            return cached if isinstance(cached, dict) else {}
        cached, metadata_mtime, stream_mtime = _read_video_caches(video_id)
        if isinstance(cached, dict):
            metadata_fresh = cache_age_seconds(metadata_mtime) <= DETAILS_CACHE_STALE_SECONDS
            direct_fetched_at = cached.get("direct_url_fetched_at") or stream_mtime
            stream_fresh = bool(cached.get("direct_url")) and cache_age_seconds(direct_fetched_at) <= STREAM_URL_FRESH_SECONDS
            if rejected_url and cached.get("direct_url") != rejected_url:
                return cached
            if metadata_fresh and stream_fresh and not force:
                return cached
            # A metadata-only cache is still useful for a short period, but a
            # missing/stale stream URL must be resolved before playback.
            if metadata_fresh and not cached.get("direct_url") and not force and cache_age_seconds(metadata_mtime) <= DETAILS_CACHE_FRESH_SECONDS:
                return cached
        if force:
            if video_id.startswith("cw_"):
                camwhores_scraper._details_cache.pop(video_id[3:], None)
            elif hasattr(scraper, "_details_cache"):
                scraper._details_cache.pop(video_id, None)
            if rejected_url:
                invalidate_cached_redirect(rejected_url)
        fresh = _fetch_and_cache_details(video_id)
        if rejected_url and fresh and fresh.get("direct_url") == rejected_url:
            _record_refresh_failure(video_id, "Identical rejected URL")
            invalidate_cached_redirect(rejected_url)
        return fresh


def _refresh_details_in_background(video_id: str) -> None:
    with _details_refresh_lock:
        if video_id in _details_refreshing:
            return
        _details_refreshing.add(video_id)
    def worker():
        try:
            _fetch_details_singleflight(video_id, force=True)
        except Exception as e:
            print(f"[Cache] Odświeżenie detali {video_id} nie powiodło się: {e}")
        finally:
            with _details_refresh_lock:
                _details_refreshing.discard(video_id)
    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/video/details")
def get_video_details(id: str = Query(...), force_refresh: bool = Query(False)):
    """Detale z persistent cache i puli wątków; zapobiega blokowaniu pętli asyncio FastAPI."""
    clean_id = _resolve_clean_video_id(id)
    cached, metadata_mtime, stream_mtime = _read_video_caches(clean_id)
    metadata_age = cache_age_seconds(metadata_mtime)
    stream_age = cache_age_seconds(cached.get("direct_url_fetched_at") or stream_mtime) if isinstance(cached, dict) else float("inf")

    if force_refresh:
        _clear_no_stream(clean_id)
    if isinstance(cached, dict) and cached and not force_refresh and metadata_age <= DETAILS_CACHE_STALE_SECONDS:
        details = cached
        if metadata_age > DETAILS_CACHE_FRESH_SECONDS or (cached.get("direct_url") and stream_age > STREAM_URL_FRESH_SECONDS):
            _refresh_details_in_background(clean_id)
    elif force_refresh:
        details = _fetch_details_singleflight(clean_id, force=True)
    else:
        details = _fetch_details_singleflight(clean_id)

    return _normalize_video_details(clean_id, details)


# Dedykowana sesja ze ścisłym limitem retry dla ścieżki krytycznej wideo (Pakiet C, punkt 2)
stream_session = requests.Session()
_stream_adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=Retry(total=0, connect=0, read=0))
stream_session.mount("http://", _stream_adapter)
stream_session.mount("https://", _stream_adapter)
stream_session.headers.update({"Connection": "keep-alive"})

STREAM_CONNECT_TIMEOUT = 3.5
STREAM_READ_TIMEOUT = 12.0

@app.post("/api/playback/status")
async def update_playback_status(request: Request):
    """Informuje backend o aktywnym odtwarzaczu i stanie bufora (Pakiet C, punkt 6)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    is_busy = bool(body.get("is_busy"))
    buffered = float(body.get("buffered_seconds") or 0.0)
    with _playback_active_lock:
        global _last_playback_active_time
        if is_busy or buffered < 5.0:
            _last_playback_active_time = time.time()
        else:
            _last_playback_active_time = 0.0
    return {"ok": True, "playback_busy": is_playback_active()}

@app.get("/api/video/stream")
def stream_video_proxy(
    url: str = Query(None),
    id: str = Query(None),
    embed: str = Query(None),
    owner: str = Query("player"),
    priority: str = Query("high"),
    reason: str = Query("play"),
    request: Request = None
):
    """Proxy strumienia wideo z autoryzacją MixDrop/Camwhores, wspólnym budżetem prób,
    odświeżaniem na 403 i ConnectionError oraz ochroną przed lawinowymi zapytaniami (Pakiet C)."""
    clean_id = _resolve_clean_video_id(id) if id else None
    embed_url = embed

    # Aktywność odtwarzacza wstrzymuje ciężkie prace w tle (Pakiet C, punkt 6)
    if owner == "player":
        with _playback_active_lock:
            global _last_playback_active_time
            _last_playback_active_time = time.time()

    if not url and clean_id:
        # Najpierw sprawdzamy wiek direct_url z persistent cache
        cached_details, _, cached_stream_mtime = _read_video_caches(clean_id)
        direct_url = (cached_details or {}).get("direct_url") if isinstance(cached_details, dict) else None
        if direct_url:
            direct_age = time.time() - float((cached_details or {}).get("direct_url_fetched_at", cached_stream_mtime or 0))
            if direct_age <= STREAM_URL_FRESH_SECONDS:
                url = direct_url
                embed_url = embed_url or cached_details.get("embed_url")
        if not url:
            # Metadata may live for 24h, but an expired stream URL must bypass
            # that cache before connecting to the provider.
            # Brak direct_url jest osobnym, krótkim stanem cache; bez niego
            # każde żądanie playera mogłoby uruchomić kolejny resolver.
            details = cached_details if _has_recent_no_stream(clean_id) else _fetch_details_singleflight(clean_id, force=True)
            if details:
                url = details.get("direct_url")
                embed_url = embed_url or details.get("embed_url")
                if url:
                    _clear_no_stream(clean_id)
                else:
                    _record_no_stream(clean_id)
            else:
                _record_no_stream(clean_id)

    if not embed_url and clean_id and hasattr(scraper, "_details_cache") and clean_id in scraper._details_cache:
        embed_url = scraper._details_cache[clean_id]["data"].get("embed_url")

    if not url:
        raise HTTPException(status_code=400, detail="Brak URL lub ID wideo do odtworzenia")
    if not is_safe_remote_url(url) and not (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")):
        raise HTTPException(status_code=400, detail="Niedozwolony adres strumienia")

    referer = embed_url or "https://mixdrop.ag/"
    if "camwhores" in url or (clean_id and str(clean_id).startswith("cw_")):
        referer = "https://www.camwhores.tv/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": referer,
        "Accept-Encoding": "identity"
    }

    range_header = request.headers.get("range") if request else None
    if range_header:
        headers["Range"] = range_header

    active_session = stream_session

    def _execute_upstream_get(target_url, hdrs):
        target_host = _get_url_host(target_url)
        is_failed, fail_reason = _is_host_failed(target_host)
        if is_failed:
            raise HTTPException(
                status_code=502,
                detail=f"Zdalny serwer wideo {target_host} jest chwilowo niedostępny ({fail_reason})"
            )
        t_start = time.time()
        try:
            r = _validated_session_get(
                active_session,
                target_url,
                headers=hdrs,
                stream=True,
                timeout=(STREAM_CONNECT_TIMEOUT, STREAM_READ_TIMEOUT)
            )
            connect_ms = round((time.time() - t_start) * 1000, 1)
            _clear_host_failure(target_host)
            return r, target_host, connect_ms
        except Exception as exc:
            _record_host_failure(target_host, type(exc).__name__)
            raise

    attempted_refresh = False
    req = None
    host = _get_url_host(url)
    connect_dur_ms = 0.0

    try:
        try:
            req, host, connect_dur_ms = _execute_upstream_get(url, headers)
        except (requests.exceptions.RequestException, HTTPException) as conn_err:
            if isinstance(conn_err, HTTPException) and conn_err.status_code != 502:
                raise
            # Obsługa błędu połączenia / odmowy połączenia przed rozpoczęciem body (Pakiet C, punkt 3)
            if clean_id and not attempted_refresh and not _has_recent_refresh_failure(clean_id):
                attempted_refresh = True
                try:
                    fresh_details = _fetch_details_singleflight(clean_id, force=True, rejected_url=url)
                except Exception as ref_err:
                    _record_refresh_failure(clean_id, type(ref_err).__name__)
                    raise HTTPException(status_code=502, detail=f"Błąd odświeżania adresu wideo: {type(ref_err).__name__}")

                new_url = fresh_details.get("direct_url") if isinstance(fresh_details, dict) else None
                if not new_url or new_url == url:
                    _record_refresh_failure(clean_id, "Identical or missing URL")
                    raise HTTPException(status_code=502, detail="Zdalny serwer wideo odrzucił połączenie (nowy adres jest identyczny lub niedostępny)")

                # Nowy URL uzyskany: unieważniamy stary redirect
                invalidate_cached_redirect(url)
                _clear_refresh_failure(clean_id)
                url = new_url
                embed_url = fresh_details.get("embed_url") or embed_url
                headers["Referer"] = "https://www.camwhores.tv/" if ("camwhores" in url or "cw_" in str(clean_id)) else (embed_url or "https://mixdrop.ag/")
                try:
                    req, host, connect_dur_ms = _execute_upstream_get(url, headers)
                except Exception as retry_err:
                    _record_refresh_failure(clean_id, type(retry_err).__name__)
                    raise HTTPException(status_code=502, detail=f"Nowy adres wideo jest również niedostępny: {type(retry_err).__name__}")
            else:
                code = conn_err.status_code if isinstance(conn_err, HTTPException) else 502
                raise HTTPException(status_code=code, detail=f"Błąd połączenia ze zdalnym serwerem: {type(conn_err).__name__}")

        # Jeśli kod HTTP to 401, 403, 404, 410 (wygaśnięcie linku lub błąd autoryzacji)
        if req.status_code in (401, 403, 404, 410) and clean_id and not attempted_refresh and not _has_recent_refresh_failure(clean_id):
            req.close()
            attempted_refresh = True
            try:
                fresh_details = _fetch_details_singleflight(clean_id, force=True, rejected_url=url)
            except Exception as ref_err:
                _record_refresh_failure(clean_id, type(ref_err).__name__)
                raise HTTPException(status_code=502, detail="Odświeżenie adresu wideo nie powiodło się")

            new_url = fresh_details.get("direct_url") if isinstance(fresh_details, dict) else None
            if not new_url or new_url == url:
                _record_refresh_failure(clean_id, "Identical or missing URL")
                raise HTTPException(status_code=502, detail="Zdalny serwer wideo zwrócił błąd autoryzacji (nowy adres jest identyczny)")

            invalidate_cached_redirect(url)
            _clear_refresh_failure(clean_id)
            url = new_url
            embed_url = fresh_details.get("embed_url") or embed_url
            headers["Referer"] = "https://www.camwhores.tv/" if ("camwhores" in url or "cw_" in str(clean_id)) else (embed_url or "https://mixdrop.ag/")
            try:
                req, host, connect_dur_ms = _execute_upstream_get(url, headers)
            except Exception as retry_err:
                _record_refresh_failure(clean_id, type(retry_err).__name__)
                raise HTTPException(status_code=502, detail=f"Nowy adres wideo jest niedostępny: {type(retry_err).__name__}")

        if req.status_code not in (200, 206, 416):
            code = req.status_code
            req.close()
            raise HTTPException(status_code=502, detail=f"Zdalny serwer wideo zwrócił HTTP {code}")

        response_headers = {
            "Content-Type": req.headers.get("Content-Type", "video/mp4"),
            "Accept-Ranges": req.headers.get("Accept-Ranges", "none"),
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
            "X-Content-Type-Options": "nosniff",
            "Server-Timing": f"upstream_connect;dur={connect_dur_ms}"
        }
        if "Content-Range" in req.headers:
            response_headers["Content-Range"] = req.headers["Content-Range"]
        if "Content-Length" in req.headers:
            response_headers["Content-Length"] = req.headers["Content-Length"]

        def iterfile():
            try:
                for chunk in req.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        yield chunk
            except Exception as exc:
                print(f"[Stream] Upstream interrupted: {type(exc).__name__}")
                raise
            finally:
                req.close()

        return StreamingResponse(
            iterfile(),
            status_code=req.status_code,
            headers=response_headers
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd strumieniowania wideo: {type(e).__name__}")


@app.post("/api/storyboard/demand")
@app.delete("/api/storyboard/demand")
def storyboard_demand(request: Request, id: str, consumer: str):
    from storyboard_service import demand
    try:
        demand(_resolve_clean_video_id(id), consumer, request.method == "POST")
    except ValueError as exc:
        raise HTTPException(429, str(exc))
    return {"ok": True}


@app.get("/api/storyboard")
@app.post("/api/storyboard")
def storyboard_status_or_start(
    id: str = Query(..., min_length=1),
    duration: float = Query(..., gt=0, le=43200),
    force: bool = Query(False),
    request: Request = None,
):
    """YouTube-style storyboard: generuje jeden sprite JPG z klatkami filmu i cache'uje go na SSD."""
    clean_id = str(id).split("/")[-1].split("?")[0]
    cached_board = get_storyboard_status(clean_id, duration)
    if not force and cached_board.get("status") == "ready" and (request is None or request.method == "GET" or cached_board.get("quality") == "full"):
        cached_board["sprite_url"] = f"/api/storyboard/image?id={requests.utils.quote(clean_id)}&q={cached_board.get('quality', 'quick')}&v={cached_board.get('created_at', 0)}"
        return cached_board
    if request is not None and request.method == "GET":
        return cached_board
    # Rozwiąż direct_url RAZ przed uruchomieniem wielu seeków FFmpeg. W starej wersji
    # każdy równoległy proces potrafił wejść przez /stream?id=... i równocześnie
    # scrapować tę samą stronę detali, co dramatycznie spowalniało pierwszy storyboard.
    base = str(request.base_url if request else "http://127.0.0.1:8000/").rstrip("/")
    details = _fetch_details_singleflight(clean_id)
    direct = (details or {}).get("direct_url") or ""
    embed = (details or {}).get("embed_url") or ""
    if direct:
        source_url = (
            f"{base}/api/video/stream?id={requests.utils.quote(clean_id)}"
            f"&url={requests.utils.quote(direct, safe='')}"
            f"&embed={requests.utils.quote(embed, safe='')}"
        )
    else:
        source_url = f"{base}/api/video/stream?id={requests.utils.quote(clean_id)}"
    result = start_storyboard(clean_id, duration, source_url, force=force)
    if result.get("status") == "ready":
        result["sprite_url"] = f"/api/storyboard/image?id={requests.utils.quote(clean_id)}&q={result.get('quality', 'quick')}&v={result.get('created_at', 0)}"
    return result


@app.get("/api/storyboard/image")
def storyboard_image(id: str = Query(..., min_length=1), q: str = Query("best"), v: Optional[str] = Query(None)):
    clean_id = str(id).split("/")[-1].split("?")[0]
    quality = q if q in ("quick", "full") else "best"
    path = get_storyboard_sprite_path(clean_id, quality, revision=v)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Storyboard nie jest jeszcze gotowy")
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/storyboard/segment")
@app.post("/api/storyboard/segment")
def storyboard_segment_status_or_start(
    id: str = Query(..., min_length=1),
    duration: float = Query(..., gt=0, le=43200),
    segment: int = Query(0, ge=0),
    force: bool = Query(False),
    prefetch_next: bool = Query(False),
    request: Request = None,
):
    """Dense timeline segment: pobiera lub generuje sprite JPG dla wybranego segmentu osi czasu."""
    clean_id = _resolve_clean_video_id(id)
    cached_seg = get_segment_status(clean_id, duration, segment)
    if not force and cached_seg.get("status") == "ready" and (request is None or request.method == "GET"):
        cached_seg["sprite_url"] = f"/api/storyboard/segment/image?id={requests.utils.quote(clean_id)}&segment={segment}&v={cached_seg.get('created_at', 0)}"
        return cached_seg
    if request is not None and request.method == "GET":
        return cached_seg

    base = str(request.base_url if request else "http://127.0.0.1:8000/").rstrip("/")
    details = _fetch_details_singleflight(clean_id)
    direct = (details or {}).get("direct_url") or ""
    embed = (details or {}).get("embed_url") or ""
    if direct:
        source_url = (
            f"{base}/api/video/stream?id={requests.utils.quote(clean_id)}"
            f"&url={requests.utils.quote(direct, safe='')}"
            f"&embed={requests.utils.quote(embed, safe='')}"
        )
    else:
        source_url = f"{base}/api/video/stream?id={requests.utils.quote(clean_id)}"

    result = start_segment(clean_id, duration, segment, source_url, force=force, priority=0)
    if prefetch_next:
        next_seg = segment + 1
        if next_seg * 30.0 < duration:
            start_segment(clean_id, duration, next_seg, source_url, force=False, priority=1)

    if result.get("status") == "ready":
        result["sprite_url"] = f"/api/storyboard/segment/image?id={requests.utils.quote(clean_id)}&segment={segment}&v={result.get('created_at', 0)}"
    return result


@app.get("/api/storyboard/segment/image")
def storyboard_segment_image(
    id: str = Query(..., min_length=1),
    segment: int = Query(0, ge=0),
    v: Optional[str] = Query(None),
):
    clean_id = _resolve_clean_video_id(id)
    path = segment_sprite_path(clean_id, segment, revision=v)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Segment storyboardu nie jest jeszcze gotowy")
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )

@app.post("/api/scan/start")
async def start_quick_scan():
    """Uruchamia szybki skan profili w tle."""
    import threading
    from fast_scan import run_full_quick_scan
    threading.Thread(target=run_full_quick_scan, daemon=True).start()
    return {"status": "started", "message": "Skanowanie profili uruchomione w tle."}

@app.get("/api/scan/status")
async def get_scan_status():
    """Zwraca aktualną liczbę zaindeksowanych profili."""
    from model_tags import model_tag_manager
    count = len(model_tag_manager._db)
    return {"status": "ok", "indexed_models_count": count}

# ============================================================
# ZARZĄDZANIE CZARNĄ LISTĄ PROFILI (BLOKOWANIE / USUWANIE)
# ============================================================
def estimate_model_total_videos(username: str) -> int:
    """Zwraca rzeczywistą łączną liczbę filmów modelki z Archivebate i Camwhores (szybko i równolegle)."""
    clean_u = re.sub(r'[^a-z0-9_-]', '', username.strip().lower())
    if not clean_u:
        return 1

    def check_cw():
        try:
            cw_url = f"https://www.camwhores.tv/search/{clean_u}/"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            r_cw = requests.get(cw_url, headers=headers, timeout=2.5)
            if r_cw.status_code == 200:
                last_m = re.findall(r'from_videos\+from_albums:(\d+)', r_cw.text)
                if last_m:
                    cw_pages = max([int(x) for x in last_m])
                    return (cw_pages - 1) * 20 + 10
                items = re.findall(r'class="item\s', r_cw.text)
                return len(items)
        except Exception:
            pass
        return 0

    def check_ab():
        try:
            url = f"https://archivebate.com/profile/{clean_u}"
            r_ab = session.session.get(url, timeout=2.5)
            if r_ab.status_code == 200:
                html = r_ab.text
                scraper._sync_csrf(html, url)
                for m in re.finditer(r'wire:id="([^"]+)" wire:initial-data="([^"]+)"', html):
                    raw_data = m.group(2).replace('&quot;', '"')
                    data = json.loads(raw_data)
                    name = data.get('fingerprint', {}).get('name', '')
                    if 'model-videos' in name:
                        rendered = session.call_livewire(name, data['fingerprint'], data['serverMemo'], "load_profile_videos")
                        if rendered:
                            secs = re.findall(r'<section class="video_item">', rendered)
                            pages = re.findall(r'page=(\d+)', rendered)
                            if pages:
                                max_p = max([int(x) for x in pages])
                                return (max_p - 1) * 20 + len(secs)
                            return len(secs)
                        break
        except Exception:
            pass
        return 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        f_cw = executor.submit(check_cw)
        f_ab = executor.submit(check_ab)
        try:
            total = f_cw.result(timeout=3.0) + f_ab.result(timeout=3.0)
        except Exception:
            total = 0

    return max(total, 1)

@app.post("/api/model/{username}/block")
async def block_model_endpoint(username: str, count: Optional[int] = Query(None)):
    """Blokuje profil modelki, zlicza usunięte filmy i trwale usuwa ją z bazy, aktualizacji i wyszukiwarki."""
    loop = asyncio.get_running_loop()
    try:
        # Szybkie, równoległe oszacowanie liczby filmów (z limitem 2.5s)
        estimated = await asyncio.wait_for(
            loop.run_in_executor(None, estimate_model_total_videos, username),
            timeout=2.5
        )
    except Exception:
        estimated = count if (count and count > 0) else 1

    final_count = estimated if (estimated and estimated > 0) else (count if (count and count > 0) else 1)
    result = await asyncio.to_thread(storage.block_model, username, video_count=final_count)
    invalidate_feed_cache()
    stats = storage.get_blocked_stats()
    catalog_stats = get_catalog_stats()

    return {
        "success": result["success"],
        "username": username,
        "removed_videos": result["removed_videos"],
        "catalog_videos": catalog_stats["total_videos"],
        "catalog_pages": catalog_stats["last_page"],
        **stats
    }

@app.post("/api/model/{username}/unblock")
def unblock_model_endpoint(username: str):
    """Odblokowuje wcześniej zablokowany profil modelki."""
    success = storage.unblock_model(username)
    invalidate_feed_cache()
    stats = storage.get_blocked_stats()
    catalog_stats = get_catalog_stats()
    return {
        "success": success,
        "username": username,
        "catalog_videos": catalog_stats["total_videos"],
        "catalog_pages": catalog_stats["last_page"],
        **stats
    }

@app.get("/api/blocked_models")
async def get_blocked_models_endpoint():
    """Zwraca listę wszystkich zablokowanych profili wraz ze statystykami i liczbą filmów."""
    stats = storage.get_blocked_stats()
    catalog_stats = get_catalog_stats()
    return {
        "blocked_models": storage.get_blocked_models(),
        "blocked_model_video_counts": storage.data.get("blocked_model_video_counts", {}),
        "catalog_videos": catalog_stats["total_videos"],
        "catalog_pages": catalog_stats["last_page"],
        **stats
    }

@app.get("/api/stats")
async def get_system_stats():
    """Zwraca kompleksowe statystyki wideo i profili dla strony głównej."""
    from model_tags import model_tag_manager
    total_models = len(model_tag_manager._db)

    genders = {"Female": 0, "Trans": 0, "Couple": 0, "Male": 0}
    for m_info in model_tag_manager._db.values():
        g = m_info.get("gender")
        if g in genders:
            genders[g] += 1
        elif "Trans" in m_info.get("tags", []):
            genders["Trans"] += 1

    blocked_stats = storage.get_blocked_stats()
    fav_count = len(storage.get_favorites())
    hist_count = len(storage.data.get("history", []))
    foll_count = len(storage.data.get("following", []))
    catalog_stats = get_catalog_stats()

    return {
        "status": "ok",
        "total_models": total_models,
        "models_gender": genders,
        "blocked_authors_count": blocked_stats["blocked_authors_count"],
        "blocked_videos_total": blocked_stats["blocked_videos_total"],
        "catalog_videos": catalog_stats["total_videos"],
        "catalog_pages": catalog_stats["last_page"],
        "base_catalog_videos": catalog_stats["base_catalog_videos"],
        "catalog_complete": catalog_stats.get("catalog_complete", False),
        "updated_at": catalog_stats.get("updated_at", 0),
        "archivebate_pages": 1000,
        "estimated_archivebate_videos": 36000,
        "favorites_count": fav_count,
        "history_count": hist_count,
        "following_count": foll_count
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
