import ipaddress
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FEED_CACHE_DIR = DATA_DIR / "feed_cache"
DETAILS_CACHE_DIR = DATA_DIR / "details_cache"
STREAM_CACHE_DIR = DATA_DIR / "stream_cache"
THUMBS_CACHE_DIR = DATA_DIR / "thumbs_cache"
STORYBOARD_CACHE_DIR = DATA_DIR / "storyboard_cache"
for _d in (DATA_DIR, FEED_CACHE_DIR, DETAILS_CACHE_DIR, STREAM_CACHE_DIR, THUMBS_CACHE_DIR, STORYBOARD_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_json_lock = threading.RLock()
_host_cache_lock = threading.Lock()
_host_safety_cache = {}  # host -> (is_safe, checked_at)
_HOST_CACHE_TTL = 300


def atomic_write_json(path: os.PathLike, data: Any) -> None:
    """Crash-safe JSON write: temp -> fsync -> atomic replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with _json_lock:
        fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, target)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)
            except OSError:
                pass


def read_json_cache(path: os.PathLike) -> Tuple[Optional[Any], Optional[float]]:
    target = Path(path)
    try:
        stat = target.stat()
        with target.open("r", encoding="utf-8") as f:
            return json.load(f), stat.st_mtime
    except (OSError, ValueError, TypeError):
        return None, None


def cache_age_seconds(mtime: Optional[float]) -> float:
    return float("inf") if not mtime else max(0.0, time.time() - mtime)


def safe_cache_key(value: str) -> str:
    import hashlib
    return hashlib.sha256(str(value).encode("utf-8", "ignore")).hexdigest()


def is_safe_remote_url(url: str) -> bool:
    """SSRF guard: only public http/https targets, never localhost/private/link-local."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False

        now = time.time()
        with _host_cache_lock:
            cached = _host_safety_cache.get(host)
            if cached and now - cached[1] < _HOST_CACHE_TTL:
                return cached[0]

        # Literal IP albo jednorazowa walidacja DNS; wynik hosta cache'ujemy, żeby
        # zabezpieczenie SSRF nie dokładało osobnego DNS lookupu do każdej miniatury.
        try:
            ips = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                ips = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)]
            except (socket.gaierror, ValueError):
                with _host_cache_lock:
                    _host_safety_cache[host] = (False, now)
                return False
        safe = all(ip.is_global for ip in ips)
        with _host_cache_lock:
            _host_safety_cache[host] = (safe, now)
        return safe
    except Exception:
        return False


def trim_cache_directory(directory: os.PathLike, max_bytes: int, preserve_suffixes=(".meta",)) -> None:
    """Przybliżony LRU po mtime; usuwa najstarsze pliki po przekroczeniu limitu."""
    root = Path(directory)
    try:
        files = [p for p in root.iterdir() if p.is_file() and not p.name.endswith(".tmp")]
    except OSError:
        return
    total = 0
    entries = []
    for p in files:
        try:
            st = p.stat()
            total += st.st_size
            # Meta waży mało, ale usuwamy go razem z odpowiadającym .bin poniżej.
            if p.suffix not in preserve_suffixes:
                entries.append((st.st_mtime, st.st_size, p))
        except OSError:
            pass
    if total <= max_bytes:
        return
    entries.sort(key=lambda x: x[0])
    target = int(max_bytes * 0.90)
    for _, size, p in entries:
        if total <= target:
            break
        try:
            p.unlink(missing_ok=True)
            meta = p.with_suffix(".meta") if p.suffix == ".bin" else None
            if meta and meta.exists():
                try:
                    total -= meta.stat().st_size
                except OSError:
                    pass
                meta.unlink(missing_ok=True)
            total -= size
        except OSError:
            pass
