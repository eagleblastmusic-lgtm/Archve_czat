"""V2 Progressive Storyboard Service with van der Corput bisection and instant Layer 0.

Produces whole-video 10x10 sprite sheets (1600x900 px, 160x90 per frame) in
progressive bisection order (van der Corput sequence), published atomically with
increasing revision numbers.

Layer 0 provides 0ms instant hover:
- Camwhores: 15 native thumbnails via `timeline_prefix`
- Archivebate: fast 10-frame extraction from lightweight `preview_video.mp4` (~250 KB)
"""

from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional, Tuple

import imageio_ffmpeg
from PIL import Image

from cache_store import (
    STORYBOARD_CACHE_DIR,
    atomic_write_json,
    read_json_cache,
    safe_cache_key,
)
from urllib.parse import urlparse
from video_identity import VideoKey

# Architecture constants
STORYBOARD_V2_VERSION = 12
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
COLUMNS_PER_SHEET = 10
ROWS_PER_SHEET = 10
FRAMES_PER_SHEET = COLUMNS_PER_SHEET * ROWS_PER_SHEET  # 100
MAX_SLOTS_DEFAULT = 100
MAX_SHEETS = 2
MILESTONES = (1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 80, 100)

V2_CACHE_DIR = Path(STORYBOARD_CACHE_DIR) / "v2"
V2_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def is_safe_ffmpeg_input_url(url: Optional[str]) -> bool:
    """Validate that input URL for FFmpeg is safe against SSRF, internal network scanning,
    file:// access, loopback bypass, and header/protocol smuggling.

    Direct unproxied remote URLs are rejected because FFmpeg automatically follows HTTP
    redirects and resolves DNS without peer IP validation, which exposes private networks
    to SSRF and DNS rebinding. All remote streams must be routed through /api/video/stream.
    """
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if "\r" in clean or "\n" in clean or "\0" in clean:
        return False
    try:
        p = Path(clean)
        if p.is_file() and p.exists() and not clean.startswith("file:"):
            return True
    except Exception:
        pass
    try:
        parsed = urlparse(clean)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        if host in ("127.0.0.1", "localhost"):
            # Only trusted local video stream endpoint on local server port is permitted on loopback
            try:
                server_port = int(os.getenv("PORT", "8000"))
            except Exception:
                server_port = 8000
            url_port = parsed.port or (80 if parsed.scheme == "http" else 443)
            if url_port != server_port:
                return False
            clean_path = parsed.path.rstrip("/")
            return clean_path == "/api/video/stream"
        return False
    except Exception:
        return False

_lock = threading.RLock()
_state_condition = threading.Condition(_lock)
_active_builds: Dict[str, dict] = {}
_jobs_queue: Queue = Queue(maxsize=128)
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()
LEASE_DURATION_SECONDS = 120.0


class ConsumerLeaseManager:
    """Thread-safe, multi-consumer lease manager with per-consumer tracking,
    session epochs, natural expiration, and automatic pruning.
    """
    def __init__(self, default_duration: float = LEASE_DURATION_SECONDS, max_tracked_keys: int = 1000):
        self._lock = threading.RLock()
class AcquireResult(tuple):
    """Result of lease acquisition, backwards-compatible with 4-tuple while carrying session_id."""
    def __new__(cls, ok: bool, epoch: int, active_count: int, consumer_id: str, session_id: str = ""):
        inst = super().__new__(cls, (ok, epoch, active_count, consumer_id))
        inst.ok = ok
        inst.epoch = epoch
        inst.active_count = active_count
        inst.consumer_id = consumer_id
        inst.session_id = session_id
        return inst


class ConsumerLeaseManager:
    """Manages active consumers, heartbeats, session generations, and automatic pruning per VideoKey."""

    def __init__(self, default_duration: float = LEASE_DURATION_SECONDS, max_tracked_keys: int = 1000):
        self._lock = threading.Lock()
        # key_str -> {consumer_id: {"expires": float, "session_id": str, "epoch": int}}
        self._leases: Dict[str, Dict[str, dict]] = {}
        # key_str -> epoch counter
        self._epochs: Dict[str, int] = {}
        self.default_duration = max(5.0, float(default_duration))
        self.max_tracked_keys = max_tracked_keys
        self._last_prune = time.monotonic()

    def _prune_expired_locked(self, now: float) -> None:
        if now - self._last_prune < 5.0 and len(self._leases) < self.max_tracked_keys:
            return
        self._last_prune = now
        expired_keys = []
        for key_str, consumers in list(self._leases.items()):
            active = {cid: rec for cid, rec in consumers.items() if rec.get("expires", 0.0) > now}
            if active:
                self._leases[key_str] = active
            else:
                expired_keys.append(key_str)
        for k in expired_keys:
            self._leases.pop(k, None)
            self._epochs.pop(k, None)

        if len(self._leases) > self.max_tracked_keys:
            sorted_keys = sorted(
                self._leases.keys(),
                key=lambda k: max((rec.get("expires", 0.0) for rec in self._leases[k].values()), default=0.0)
            )
            for k in sorted_keys[: len(self._leases) - self.max_tracked_keys]:
                self._leases.pop(k, None)
                self._epochs.pop(k, None)

    def acquire(
        self,
        key: VideoKey,
        consumer_id: Optional[str] = None,
        duration: Optional[float] = None,
        session_id: Optional[str] = None,
    ) -> AcquireResult:
        now = time.monotonic()
        cid = str(consumer_id or "").strip()
        if not cid:
            import secrets
            cid = f"c_{secrets.token_hex(6)}_{int(now * 1000)}"

        sid = str(session_id or "").strip()
        if not sid:
            sid = f"{cid}_s1"

        dur = max(0.05, float(duration if duration is not None else self.default_duration))
        expires = now + dur
        key_str = key.as_string()

        with self._lock:
            self._prune_expired_locked(now)
            if key_str not in self._leases or not any(r.get("expires", 0.0) > now for r in self._leases[key_str].values()):
                if key_str not in self._leases:
                    self._leases[key_str] = {}
                self._epochs[key_str] = self._epochs.get(key_str, 0) + 1
            epoch = self._epochs[key_str]
            self._leases[key_str][cid] = {
                "expires": expires,
                "session_id": sid,
                "epoch": epoch,
            }
            active_count = sum(1 for rec in self._leases[key_str].values() if rec.get("expires", 0.0) > now)

        try:
            import player_qos_runtime as qos
            qos.clear_cancel_state(key.provider_id, source=key.source)
        except Exception:
            pass

        return AcquireResult(True, epoch, active_count, cid, sid)

    def renew(
        self,
        key: VideoKey,
        consumer_id: str,
        duration: Optional[float] = None,
        session_id: Optional[str] = None,
        epoch: Optional[int] = None,
    ) -> Tuple[bool, int]:
        now = time.monotonic()
        cid = str(consumer_id or "").strip()
        if not cid:
            return False, 0
        dur = max(0.05, float(duration if duration is not None else self.default_duration))
        key_str = key.as_string()

        with self._lock:
            consumers = self._leases.get(key_str)
            if not consumers:
                return False, 0
            rec = consumers.get(cid)
            if not rec:
                return False, 0

            # 1. Reject if already expired - do NOT resurrect expired leases!
            if rec.get("expires", 0.0) <= now:
                consumers.pop(cid, None)
                if not any(r.get("expires", 0.0) > now for r in consumers.values()):
                    self._leases.pop(key_str, None)
                return False, 0

            # 2. Reject if wrong session generation
            if session_id and rec.get("session_id") != str(session_id).strip():
                return False, 0

            # 3. Reject if stale epoch
            if epoch is not None and rec.get("epoch", 0) > int(epoch):
                return False, 0

            rec["expires"] = now + dur
            active_count = sum(1 for r in consumers.values() if r.get("expires", 0.0) > now)
            return True, active_count

    def release(
        self,
        key: VideoKey,
        consumer_id: Optional[str] = None,
        session_id: Optional[str] = None,
        epoch: Optional[int] = None,
        reason: str = "client_release",
    ) -> Tuple[bool, int]:
        now = time.monotonic()
        key_str = key.as_string()
        cid = str(consumer_id or "").strip() if consumer_id else None
        sid = str(session_id or "").strip() if session_id else None
        should_cancel = False

        with self._lock:
            consumers = self._leases.get(key_str)
            if consumers is None:
                return False, 0

            was_present = False
            if cid is None:
                was_present = bool(consumers)
                self._leases.pop(key_str, None)
                remaining = 0
                should_cancel = was_present
            else:
                rec = consumers.get(cid)
                if rec is not None:
                    # Verify session_id and epoch if supplied
                    if sid and rec.get("session_id") != sid:
                        was_present = False
                    elif epoch is not None and rec.get("epoch", 0) > int(epoch):
                        was_present = False
                    else:
                        consumers.pop(cid, None)
                        was_present = True

                active = {c: r for c, r in consumers.items() if r.get("expires", 0.0) > now}
                if active:
                    self._leases[key_str] = active
                    remaining = len(active)
                else:
                    self._leases.pop(key_str, None)
                    remaining = 0

                # Single authoritative cancellation:
                # Cancel ONLY when an actual registered consumer was removed AND remaining drops to 0!
                should_cancel = was_present and (remaining == 0)

        # Trigger hard_cancel outside self._lock to avoid lock inversion or deadlocks
        if should_cancel:
            try:
                import player_qos_runtime as qos
                qos.hard_cancel(key.provider_id, source=key.source, reason=reason, clear_lease=False)
            except Exception:
                pass

        return was_present, remaining

    def has_live_lease(self, key: VideoKey | str, source: str | None = None) -> bool:
        if isinstance(key, str):
            key = resolve_video_key(key, source=source)
        now = time.monotonic()
        key_str = key.as_string()
        with self._lock:
            consumers = self._leases.get(key_str)
            if not consumers:
                return False
            return any(rec.get("expires", 0.0) > now for rec in consumers.values())

    def get_active_consumers_count(self, key: VideoKey | str, source: str | None = None) -> int:
        if isinstance(key, str):
            key = resolve_video_key(key, source=source)
        now = time.monotonic()
        key_str = key.as_string()
        with self._lock:
            consumers = self._leases.get(key_str)
            if not consumers:
                return 0
            return sum(1 for rec in consumers.values() if rec.get("expires", 0.0) > now)

    def diagnostic_snapshot(self, key: Optional[VideoKey] = None) -> dict:
        now = time.monotonic()
        with self._lock:
            if key is not None:
                key_str = key.as_string()
                consumers = self._leases.get(key_str, {})
                active = {
                    cid: {
                        "ttl": round(max(0.0, rec.get("expires", 0.0) - now), 2),
                        "session_id": rec.get("session_id"),
                        "epoch": rec.get("epoch"),
                    }
                    for cid, rec in consumers.items()
                    if rec.get("expires", 0.0) > now
                }
                return {
                    "key": key_str,
                    "active_consumers_count": len(active),
                    "epoch": self._epochs.get(key_str, 0),
                    "consumers": list(active.keys()),
                    "details": active,
                }
            result = {}
            for k, consumers in list(self._leases.items()):
                active_count = sum(1 for rec in consumers.values() if rec.get("expires", 0.0) > now)
                if active_count > 0:
                    result[k] = {
                        "active_consumers": active_count,
                        "epoch": self._epochs.get(k, 0),
                    }
            return result


lease_manager = ConsumerLeaseManager()


def compute_bisection_order(num_slots: int) -> List[int]:
    """Return slot indices [0 .. num_slots-1] in van der Corput bisection order.
    
    Guarantees that any prefix of k items provides balanced spatial coverage
    across the entire duration.
    """
    if num_slots <= 0:
        return []
    if num_slots == 1:
        return [0]
    if num_slots == 2:
        return [0, 1]

    order = [0, num_slots - 1]
    seen = {0, num_slots - 1}
    queue = [(0, num_slots - 1)]

    while queue:
        left, right = queue.pop(0)
        if right - left <= 1:
            continue
        mid = (left + right) // 2
        if mid not in seen:
            order.append(mid)
            seen.add(mid)
        queue.append((left, mid))
        queue.append((mid, right))

    return order


def compute_progressive_schedule(duration: float, total_slots: int) -> List[int]:
    """Compute extraction slot sequence in van der Corput bisection order across entire duration."""
    if total_slots <= 0:
        return []
    return compute_bisection_order(total_slots)


def get_slot_pts_mapping(duration: float, total_slots: int) -> Dict[int, float]:
    """Return map of slot index -> exact target PTS seconds spanning 100% of duration."""
    mapping: Dict[int, float] = {}
    if total_slots <= 0:
        return mapping
    if total_slots == 1:
        mapping[0] = 0.0
        return mapping

    clean_dur = max(0.1, float(duration))
    # Margines bezpieczeństwa od końca pliku chroniący przed błędami dekodera EOF
    end_margin = min(1.0, clean_dur * 0.01) if clean_dur > 2.0 else 0.0
    end_pts = max(0.0, clean_dur - end_margin)

    step = end_pts / max(1, total_slots - 1)
    for s in range(total_slots):
        mapping[s] = round(min(end_pts, s * step), 3)

    return mapping


def resolve_video_key(video_id: Any, source: Optional[str] = None) -> VideoKey:
    """Resolve a clean, source-scoped canonical VideoKey idempotently."""
    if isinstance(video_id, VideoKey):
        return video_id
    raw = str(video_id or "").strip()
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) >= 3 and parts[1] == "id" and parts[0] in ("archivebate", "camwhores"):
            return VideoKey(parts[0], ":".join(parts[2:]))
    key = VideoKey.from_value(raw, source=source)
    if key is not None:
        return key
    if raw.lower().startswith("cw_"):
        return VideoKey("camwhores", raw)
    return VideoKey(source or "archivebate", raw or "unknown")


def get_cache_dir_for_key(key: VideoKey) -> Path:
    hashed = safe_cache_key(key.as_string())
    path = V2_CACHE_DIR / hashed
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_manifest_path(key: VideoKey) -> Path:
    return get_cache_dir_for_key(key) / "manifest.json"


def get_sheet_path(key: VideoKey, sheet_index: int, rev: Optional[int] = None) -> Path:
    if rev is not None and int(rev) > 0:
        return get_cache_dir_for_key(key) / f"sheet_{int(sheet_index)}_r{int(rev)}.jpg"
    return get_cache_dir_for_key(key) / f"sheet_{int(sheet_index)}.jpg"


def get_preview_frame_path(key: VideoKey, index: int) -> Path:
    return get_cache_dir_for_key(key) / "preview" / f"frame_{int(index)}.jpg"


def acquire_lease(
    video_id: str,
    consumer_id: Optional[str] = None,
    duration: float = LEASE_DURATION_SECONDS,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AcquireResult:
    key = resolve_video_key(video_id, source=source)
    return lease_manager.acquire(key, consumer_id=consumer_id, duration=duration, session_id=session_id)


def renew_lease(
    video_id: str,
    consumer_id: str,
    duration: float = LEASE_DURATION_SECONDS,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    epoch: Optional[int] = None,
) -> Tuple[bool, int]:
    key = resolve_video_key(video_id, source=source)
    return lease_manager.renew(key, consumer_id=consumer_id, duration=duration, session_id=session_id, epoch=epoch)


def release_lease(
    video_id: str,
    consumer_id: Optional[str] = None,
    source: Optional[str] = None,
    reason: str = "client_release",
    session_id: Optional[str] = None,
    epoch: Optional[int] = None,
) -> Tuple[bool, int]:
    key = resolve_video_key(video_id, source=source)
    return lease_manager.release(key, consumer_id=consumer_id, session_id=session_id, epoch=epoch, reason=reason)


def record_lease(
    video_id: str,
    duration: float = LEASE_DURATION_SECONDS,
    source: Optional[str] = None,
    clear_cancel: bool = True,
    consumer_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    key = resolve_video_key(video_id, source=source)
    try:
        import player_qos_runtime as qos
        if qos.is_hard_cancelled(key.provider_id, source=key.source):
            if not clear_cancel:
                return  # Do not resurrect or renew lease on passive/stale request for hard-cancelled video
    except Exception:
        pass
    lease_manager.acquire(key, consumer_id=consumer_id or "legacy_default", duration=duration, session_id=session_id)


def has_live_lease(video_id: str, source: Optional[str] = None) -> bool:
    key = resolve_video_key(video_id, source=source)
    return lease_manager.has_live_lease(key)


def remove_lease(video_id: str, source: Optional[str] = None) -> None:
    key = resolve_video_key(video_id, source=source)
    lease_manager.release(key, consumer_id=None, reason="remove_lease")


def _default_extract_frame(
    source_url: str,
    target_time: float,
    out_path: Path,
    *,
    timeout: float = 8.0,
    fast: bool = True,
    headers: Optional[Dict[str, str]] = None,
    process_key: Optional[str] = None,
    video_id: Optional[str] = None,
) -> bool:
    """Extract a single frame to out_path using imageio-ffmpeg with QoS process lifecycle."""
    if not is_safe_ffmpeg_input_url(source_url):
        return False

    try:
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return False

    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
    ]
    if headers:
        clean_headers = {
            str(k).strip(): str(v).strip()
            for k, v in headers.items()
            if "\r" not in str(k) and "\n" not in str(k) and "\r" not in str(v) and "\n" not in str(v)
        }
        if clean_headers:
            header_str = "".join(f"{k}: {v}\r\n" for k, v in clean_headers.items())
            cmd += ["-headers", header_str]

    cmd += ["-ss", f"{target_time:.3f}"]
    if fast:
        cmd += ["-noaccurate_seek", "-skip_frame", "nokey"]
    if source_url.startswith("http://") or source_url.startswith("https://"):
        cmd += [
            "-seekable", "1",
            "-probesize", "262144",
            "-analyzeduration", "1000000",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "2",
            "-protocol_whitelist", "http,https,tcp,tls",
        ]
    cmd += [
        "-i", source_url,
        "-map", "0:v:0",
        "-frames:v", "1",
        "-an", "-sn", "-dn",
        "-threads", "1",
        "-vf",
        f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_WIDTH}:{FRAME_HEIGHT}",
        "-q:v", "8",
        "-y", str(out_path),
    ]

    p_key = process_key or f"v2_{video_id or 'anon'}_{target_time}_{time.monotonic()}"
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            import player_qos_runtime as qos
            qos.register_v2_process(p_key, proc, video_id)
        except Exception:
            pass

        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=0.5)
                    except Exception:
                        pass
                except Exception:
                    pass
            return False

        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 350:
            if fast:
                try:
                    with Image.open(out_path) as im:
                        ext = im.getextrema()
                        if all(hi <= 16 for lo, hi in ext):
                            # Black decoder initialization frame, reject to trigger accurate seek
                            return False
                except Exception:
                    pass
            return True
        return False
    except Exception:
        return False
    finally:
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.5)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=0.5)
                    except Exception:
                        pass
            try:
                import player_qos_runtime as qos
                qos.unregister_v2_process(p_key)
            except Exception:
                pass


# Extractor hook replaceable in tests
_frame_extractor: Callable[..., bool] = _default_extract_frame


def set_frame_extractor_for_test(fn: Callable[..., bool]) -> None:
    global _frame_extractor
    _frame_extractor = fn


def reset_frame_extractor() -> None:
    global _frame_extractor
    _frame_extractor = _default_extract_frame


class ProgressiveStoryboardService:
    """Manages progressive van der Corput bisection storyboard generation."""

    def __init__(self) -> None:
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        global _worker_thread
        with _lock:
            if _worker_thread is None or not _worker_thread.is_alive():
                _worker_stop.clear()
                _worker_thread = threading.Thread(
                    target=self._worker_loop,
                    name="storyboard-v2-worker",
                    daemon=True,
                )
                _worker_thread.start()

    def get_manifest(
        self,
        video_id: str,
        duration: float,
        *,
        source: Optional[str] = None,
        native_prefix: Optional[str] = None,
        native_count: Optional[int] = None,
        preview_video: Optional[str] = None,
    ) -> dict:
        key = resolve_video_key(video_id, source=source)
        manifest_path = get_manifest_path(key)
        data, _ = read_json_cache(manifest_path)
        if isinstance(data, dict):
            if data.get("version") == STORYBOARD_V2_VERSION:
                return data
            elif data.get("version") == 11:
                clean_duration = max(1.0, float(duration or data.get("duration") or 0.0))
                is_safe_migration = (
                    clean_duration <= 6000.0
                    and len(data.get("frames", [])) >= 10
                    and data.get("complete") is True
                )
                if is_safe_migration:
                    data["version"] = STORYBOARD_V2_VERSION
                    atomic_write_json(manifest_path, data)
                    return data
                data["version"] = STORYBOARD_V2_VERSION
                data["complete"] = False
                atomic_write_json(manifest_path, data)
                return data

        # Generate provisional initial Layer 0 manifest
        clean_duration = max(1.0, float(duration or 0.0))
        native_info: Dict[str, Any] = {}
        if key.source == "camwhores" and (native_prefix or "180x135" in str(video_id)):
            prefix = native_prefix or ""
            count = int(native_count or 15)
            native_info = {
                "kind": "prefix",
                "prefix": prefix,
                "count": count,
                "frames": [
                    [round(i * (clean_duration / max(1, count)), 3), f"{prefix}{i + 1}.jpg"]
                    for i in range(count)
                ] if prefix else [],
            }
        elif preview_video:
            preview_dir = get_cache_dir_for_key(key) / "preview"
            preview_frames = []
            if preview_dir.exists():
                existing_p_files = sorted(preview_dir.glob("frame_*.jpg"))
                parsed_entries = []
                for p_file in existing_p_files:
                    try:
                        num = int(p_file.stem.split("_")[1])
                        parsed_entries.append((num, p_file))
                    except Exception:
                        continue
                parsed_entries.sort(key=lambda x: x[0])
                p_count = len(parsed_entries)
                for rank, (act_idx, p_file) in enumerate(parsed_entries):
                    preview_frames.append([
                        round(rank * (clean_duration / max(1, p_count)), 3),
                        f"/api/storyboard/v2/preview?id={key.provider_id}&idx={act_idx}&source={key.source}",
                    ])
            native_info = {
                "kind": "preview_video",
                "url": preview_video,
                "count": len(preview_frames),
                "frames": preview_frames,
            }

        provisional = {
            "version": STORYBOARD_V2_VERSION,
            "video_key": key.as_string(),
            "video_id": key.provider_id,
            "source": key.source,
            "duration": clean_duration,
            "rev": 1,
            "complete": False,
            "frame_w": FRAME_WIDTH,
            "frame_h": FRAME_HEIGHT,
            "columns": COLUMNS_PER_SHEET,
            "rows": ROWS_PER_SHEET,
            "sheets": [],
            "frames": [],
            "native": native_info,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        return provisional

    def wait_manifest(
        self,
        video_id: str,
        duration: float,
        *,
        since_rev: int = 0,
        timeout: float = 1.2,
        source: Optional[str] = None,
    ) -> dict:
        key = resolve_video_key(video_id, source=source)
        deadline = time.monotonic() + max(0.0, min(float(timeout), 5.0))

        with _state_condition:
            while True:
                manifest = self.get_manifest(video_id, duration, source=source)
                cur_rev = int(manifest.get("rev", 1))
                if cur_rev > since_rev or manifest.get("complete") is True:
                    return manifest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return manifest
                _state_condition.wait(remaining)

    def request_build(
        self,
        video_id: str,
        duration: float,
        source_url: str,
        *,
        source: Optional[str] = None,
        preview_url: Optional[str] = None,
        native_prefix: Optional[str] = None,
        native_count: Optional[int] = None,
        direct_url: Optional[str] = None,
        embed_url: Optional[str] = None,
        consumer_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        key = resolve_video_key(video_id, source=source)
        acquire_lease(key.as_string(), consumer_id=consumer_id, duration=LEASE_DURATION_SECONDS, source=source, session_id=session_id)
        manifest = self.get_manifest(
            video_id,
            duration,
            source=source,
            native_prefix=native_prefix,
            native_count=native_count,
            preview_video=preview_url,
        )

        if manifest.get("complete") is True:
            return manifest

        job = {
            "key": key,
            "duration": max(1.0, float(duration)),
            "source_url": source_url,
            "preview_url": preview_url,
            "native_prefix": native_prefix,
            "native_count": native_count,
            "direct_url": direct_url,
            "embed_url": embed_url,
        }

        with _lock:
            key_str = key.as_string()
            if key_str not in _active_builds:
                try:
                    _jobs_queue.put_nowait(job)
                    _active_builds[key_str] = job
                except Exception:
                    # Queue full: do NOT register in _active_builds to prevent permanent lockout
                    pass

        self._ensure_worker()
        return manifest

    def _worker_loop(self) -> None:
        while not _worker_stop.is_set():
            try:
                job = _jobs_queue.get(timeout=0.5)
            except Empty:
                continue

            key_str = job["key"].as_string()
            try:
                if not _worker_stop.is_set():
                    try:
                        import player_qos_runtime as qos
                        if qos.is_hard_cancelled(job["key"].provider_id):
                            continue
                    except Exception:
                        pass
                    if not has_live_lease(key_str, source=job["key"].source):
                        continue
                    self._execute_build_job(job)
            except Exception as exc:
                print(f"[Storyboard V2] Error executing job: {exc}")
            finally:
                with _lock:
                    _active_builds.pop(key_str, None)
                _jobs_queue.task_done()

    def _execute_build_job(self, job: dict) -> None:
        key: VideoKey = job["key"]
        duration: float = job["duration"]
        source_url: str = job["source_url"]
        preview_url: Optional[str] = job.get("preview_url")
        native_prefix: Optional[str] = job.get("native_prefix")
        native_count: Optional[int] = job.get("native_count")
        direct_url: Optional[str] = job.get("direct_url")
        embed_url: Optional[str] = job.get("embed_url")

        if preview_url and not is_safe_ffmpeg_input_url(preview_url):
            preview_url = None
        if direct_url and not is_safe_ffmpeg_input_url(direct_url):
            direct_url = None
        if source_url and not is_safe_ffmpeg_input_url(source_url):
            source_url = None

        if not direct_url and not source_url:
            return

        direct_headers = None
        if direct_url and (direct_url.startswith("http://") or direct_url.startswith("https://")):
            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            referer = "https://www.camwhores.tv/" if (key.source == "camwhores" or "cw_" in key.provider_id) else (embed_url or "https://mixdrop.ag/")
            direct_headers = {"Referer": referer, "User-Agent": ua}

        manifest = self.get_manifest(
            key.provider_id,
            duration,
            source=key.source,
            native_prefix=native_prefix,
            native_count=native_count,
            preview_video=preview_url,
        )
        if manifest.get("complete") is True:
            return

        cache_dir = get_cache_dir_for_key(key)
        manifest_path = get_manifest_path(key)
        sheet_path = get_sheet_path(key, 0)

        # Number of slots: up to 100 slots for sheet 0 spanning full duration
        total_slots = min(MAX_SLOTS_DEFAULT, max(10, int(duration // 2)))
        total_slots = min(total_slots, FRAMES_PER_SHEET)
        bisection_order = compute_progressive_schedule(duration, total_slots)
        slot_pts_map = get_slot_pts_mapping(duration, total_slots)

        # Initialize or load sheet image
        if sheet_path.exists():
            try:
                sheet_img = Image.open(sheet_path).convert("RGB")
            except Exception:
                sheet_img = Image.new(
                    "RGB",
                    (COLUMNS_PER_SHEET * FRAME_WIDTH, ROWS_PER_SHEET * FRAME_HEIGHT),
                    (12, 12, 12),
                )
        else:
            sheet_img = Image.new(
                "RGB",
                (COLUMNS_PER_SHEET * FRAME_WIDTH, ROWS_PER_SHEET * FRAME_HEIGHT),
                (12, 12, 12),
            )

        # Collect existing frames
        existing_frames_map = {}
        for entry in manifest.get("frames", []):
            if len(entry) >= 3:
                existing_frames_map[entry[2]] = entry[0]

        # Layer 0: Extract lightweight preview frames if preview_url is available
        preview_frames_exist = bool(manifest.get("native", {}).get("frames"))
        if preview_url and not preview_frames_exist and not _worker_stop.is_set():
            preview_dir = cache_dir / "preview"
            preview_dir.mkdir(parents=True, exist_ok=True)
            extracted_preview: List[Tuple[int, Path]] = []
            target_preview_count = 8
            for p_idx in range(target_preview_count):
                if _worker_stop.is_set() or not has_live_lease(key.as_string(), source=key.source):
                    break
                p_out = preview_dir / f"frame_{p_idx}.jpg"
                if p_out.exists() and p_out.stat().st_size > 350:
                    extracted_preview.append((p_idx, p_out))
                    continue

                # Acquire QoS budget for preview extraction
                budget = None
                try:
                    import player_qos_runtime as qos
                    def cancel_check():
                        return _worker_stop.is_set() or not has_live_lease(key.as_string(), source=key.source)
                    budget = qos.wait_for_ffmpeg_budget("v2_prev", 1, key.provider_id, cancel_check)
                except Exception:
                    budget = None

                if budget is None:
                    break

                try:
                    p_pts = round(0.3 + p_idx * 0.5, 2)
                    p_key = f"v2_prev_{key.provider_id}_{p_idx}"
                    try:
                        ok = _frame_extractor(
                            preview_url,
                            p_pts,
                            p_out,
                            timeout=5.0,
                            fast=True,
                            process_key=p_key,
                            video_id=key.provider_id,
                        )
                    except TypeError:
                        try:
                            ok = _frame_extractor(preview_url, p_pts, p_out, timeout=5.0, fast=True)
                        except TypeError:
                            ok = _frame_extractor(preview_url, p_pts, p_out)
                    if ok and p_out.exists() and p_out.stat().st_size > 350:
                        extracted_preview.append((p_idx, p_out))
                finally:
                    if budget is not None:
                        try:
                            import player_qos_runtime as qos
                            qos.release_ffmpeg_budget(budget)
                        except Exception:
                            pass

            if extracted_preview and not _worker_stop.is_set():
                extracted_preview.sort(key=lambda x: x[0])
                p_count = len(extracted_preview)
                native_frames = [
                    [
                        round(rank * (duration / max(1, p_count)), 3),
                        f"/api/storyboard/v2/preview?id={key.provider_id}&idx={act_idx}&source={key.source}",
                    ]
                    for rank, (act_idx, p_out) in enumerate(extracted_preview)
                ]
                manifest["native"] = {
                    "kind": "preview_video",
                    "url": preview_url,
                    "count": p_count,
                    "frames": native_frames,
                }
                current_rev = int(manifest.get("rev", 1)) + 1
                manifest["rev"] = current_rev
                atomic_write_json(manifest_path, manifest)
                with _state_condition:
                    _state_condition.notify_all()

        step = duration / max(1, total_slots)
        with tempfile.TemporaryDirectory(prefix="sb_v2_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            frames_count = len(existing_frames_map)
            current_rev = int(manifest.get("rev", 1))

            slots_to_do = [s for s in bisection_order if s not in existing_frames_map]

            def extract_slot(s_idx: int) -> Tuple[int, float, Optional[Path]]:
                if _worker_stop.is_set():
                    return s_idx, 0.0, None
                try:
                    import player_qos_runtime as qos
                    if qos.is_hard_cancelled(key.provider_id):
                        return s_idx, 0.0, None
                except Exception:
                    pass
                if not has_live_lease(key.as_string(), source=key.source):
                    return s_idx, 0.0, None

                pts = slot_pts_map.get(s_idx, round(s_idx * step, 3))
                f_out = tmp_dir / f"slot_{s_idx:03d}.jpg"
                ok = False
                p_key = f"v2_{key.provider_id}_{s_idx}_{time.monotonic()}"

                # QoS resource arbiter budget acquisition
                budget = None
                try:
                    import player_qos_runtime as qos
                    def cancel_check():
                        return _worker_stop.is_set() or not has_live_lease(key.as_string(), source=key.source)
                    budget = qos.wait_for_ffmpeg_budget("v2", 1, key.provider_id, cancel_check)
                except Exception:
                    budget = None

                if budget is None:
                    return s_idx, pts, None

                try:
                    if direct_url and direct_headers:
                        try:
                            ok = _frame_extractor(direct_url, pts, f_out, timeout=10.0, fast=True, headers=direct_headers, process_key=p_key, video_id=key.provider_id)
                        except TypeError:
                            try:
                                ok = _frame_extractor(direct_url, pts, f_out, timeout=10.0, fast=True, headers=direct_headers)
                            except TypeError:
                                ok = _frame_extractor(direct_url, pts, f_out, timeout=10.0, fast=True)
                        if not ok and not _worker_stop.is_set():
                            # Fallback to accurate seek to avoid dropped/black keyframe seek failures
                            try:
                                ok = _frame_extractor(direct_url, pts, f_out, timeout=12.0, fast=False, headers=direct_headers, process_key=p_key, video_id=key.provider_id)
                            except TypeError:
                                try:
                                    ok = _frame_extractor(direct_url, pts, f_out, timeout=12.0, fast=False, headers=direct_headers)
                                except TypeError:
                                    ok = _frame_extractor(direct_url, pts, f_out, timeout=12.0, fast=False)
                    elif source_url:
                        try:
                            ok = _frame_extractor(source_url, pts, f_out, timeout=8.0, fast=True, process_key=p_key, video_id=key.provider_id)
                        except TypeError:
                            ok = _frame_extractor(source_url, pts, f_out, timeout=8.0, fast=True)
                        if not ok and not _worker_stop.is_set():
                            try:
                                ok = _frame_extractor(source_url, pts, f_out, timeout=12.0, fast=False, process_key=p_key, video_id=key.provider_id)
                            except TypeError:
                                ok = _frame_extractor(source_url, pts, f_out, timeout=12.0, fast=False)
                finally:
                    if budget is not None:
                        try:
                            import player_qos_runtime as qos
                            qos.release_ffmpeg_budget(budget)
                        except Exception:
                            pass

                if ok and f_out.exists():
                    return s_idx, pts, f_out
                return s_idx, pts, None

            batch_size = 4
            max_parallel = 2  # Respect global FFMPEG_MAX_CONCURRENT
            for b_start in range(0, len(slots_to_do), batch_size):
                if _worker_stop.is_set():
                    break
                try:
                    import player_qos_runtime as qos
                    if qos.is_hard_cancelled(key.provider_id):
                        break
                except Exception:
                    pass
                if not has_live_lease(key.as_string(), source=key.source):
                    break

                self._wait_for_playback_qos(key.provider_id)

                batch = slots_to_do[b_start:b_start + batch_size]
                batch_added = 0
                with ThreadPoolExecutor(max_workers=min(max_parallel, len(batch)), thread_name_prefix="sb_v2_batch") as pool:
                    futures = [pool.submit(extract_slot, s) for s in batch]
                    for fut in as_completed(futures):
                        try:
                            s_idx, pts, f_out = fut.result()
                            if f_out and f_out.exists():
                                with Image.open(f_out) as frame_img:
                                    frame_rgb = frame_img.convert("RGB")
                                    if frame_rgb.size != (FRAME_WIDTH, FRAME_HEIGHT):
                                        frame_rgb = frame_rgb.resize(
                                            (FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.BILINEAR
                                        )
                                    col = s_idx % COLUMNS_PER_SHEET
                                    row = s_idx // COLUMNS_PER_SHEET
                                    sheet_img.paste(frame_rgb, (col * FRAME_WIDTH, row * FRAME_HEIGHT))
                                    existing_frames_map[s_idx] = pts
                                    frames_count += 1
                                    batch_added += 1
                        except Exception:
                            pass

                if batch_added > 0:
                    current_rev += 1
                    self._publish_manifest(
                        key=key,
                        manifest_path=manifest_path,
                        sheet_path=sheet_path,
                        sheet_img=sheet_img,
                        frames_map=existing_frames_map,
                        duration=duration,
                        rev=current_rev,
                        complete=(frames_count >= total_slots),
                        native_info=manifest.get("native", {}),
                    )

    def _publish_manifest(
        self,
        *,
        key: VideoKey,
        manifest_path: Path,
        sheet_path: Path,
        sheet_img: Image.Image,
        frames_map: Dict[int, float],
        duration: float,
        rev: int,
        complete: bool,
        native_info: dict,
    ) -> None:
        """Atomically persist revisioned sheet image and manifest JSON."""
        # 1. Save sheet to revisioned file sheet_0_r{rev}.jpg AND current sheet_0.jpg
        rev_sheet_path = get_sheet_path(key, 0, rev=rev)
        tmp_sheet = sheet_path.with_suffix(f".tmp.{rev}.jpg")
        try:
            sheet_img.save(tmp_sheet, format="JPEG", quality=72, optimize=True)
            # Atomically publish revisioned sheet
            shutil.copyfile(tmp_sheet, rev_sheet_path)
            # Atomically publish primary/unversioned sheet
            os.replace(tmp_sheet, sheet_path)
        finally:
            if tmp_sheet.exists():
                try:
                    tmp_sheet.unlink()
                except OSError:
                    pass

        # Cleanup older revisions (keep latest 2 revisions for active client transitions)
        try:
            for old_rev in range(1, max(1, rev - 2)):
                old_file = get_sheet_path(key, 0, rev=old_rev)
                if old_file.exists():
                    try:
                        old_file.unlink()
                    except OSError:
                        pass
        except Exception:
            pass

        # 2. Build sorted frames list: [[pts, sheet_idx, cell_idx], ...]
        sorted_frames = sorted(
            [[pts, 0, slot_idx] for slot_idx, pts in frames_map.items()],
            key=lambda x: x[0],
        )

        manifest = {
            "version": STORYBOARD_V2_VERSION,
            "video_key": key.as_string(),
            "video_id": key.provider_id,
            "source": key.source,
            "duration": round(float(duration), 3),
            "rev": int(rev),
            "complete": bool(complete),
            "frame_w": FRAME_WIDTH,
            "frame_h": FRAME_HEIGHT,
            "columns": COLUMNS_PER_SHEET,
            "rows": ROWS_PER_SHEET,
            "sheets": [
                {
                    "index": 0,
                    "url": f"/api/storyboard/v2/sheet?id={key.provider_id}&sheet=0&v={rev}&source={key.source}",
                    "rows": ROWS_PER_SHEET,
                    "columns": COLUMNS_PER_SHEET,
                }
            ],
            "frames": sorted_frames,
            "native": native_info or {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }

        atomic_write_json(manifest_path, manifest)

        with _state_condition:
            _state_condition.notify_all()

    def _wait_for_playback_qos(self, video_id: str) -> None:
        """Cooperate with player QoS: yield gently if playback is actively seeking, stalled or critical."""
        try:
            import player_qos_runtime as qos
            snap = qos._qos_snapshot()
            waited = 0.0
            while not _worker_stop.is_set() and snap.get("active") and not snap.get("paused") and (
                snap.get("seeking")
                or snap.get("ready_state", 4) < 2
                or snap.get("buffered_seconds", 10.0) < 2.0
            ):
                if waited >= 5.0 or qos.is_hard_cancelled(video_id):
                    break
                time.sleep(0.1)
                waited += 0.1
                snap = qos._qos_snapshot()
        except Exception:
            pass


progressive_storyboard_service = ProgressiveStoryboardService()
