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
from video_identity import VideoKey

# Architecture constants
STORYBOARD_V2_VERSION = 11
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

_lock = threading.RLock()
_state_condition = threading.Condition(_lock)
_active_builds: Dict[str, dict] = {}
_jobs_queue: Queue = Queue(maxsize=128)
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()
_live_leases: Dict[str, float] = {}  # key -> expires_monotonic
LEASE_DURATION_SECONDS = 120.0


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
    """Compute extraction slot sequence: Layer 0 (1 frame / 1 min) first, then densification."""
    if total_slots <= 0:
        return []
    if total_slots <= 2 or duration < 60.0:
        return compute_bisection_order(total_slots)

    # Layer 0: 1-minute milestones arranged in bisection order so initial batches
    # immediately span the whole duration (e.g., minute 0, last minute, middle minute...)
    num_minutes = min(total_slots, max(1, int(math.ceil(duration / 60.0))))
    bisect_indices = compute_bisection_order(num_minutes)
    layer_0_order = [bisect_indices[i] for i in range(len(bisect_indices))]

    # Layer 1 (Densification): remaining slots in bisection order
    remaining_sorted = [s for s in range(num_minutes, total_slots)]
    if remaining_sorted:
        rem_indices = compute_bisection_order(len(remaining_sorted))
        remaining_order = [remaining_sorted[i] for i in rem_indices]
    else:
        remaining_order = []

    return layer_0_order + remaining_order


def get_slot_pts_mapping(duration: float, total_slots: int) -> Dict[int, float]:
    """Return map of slot index -> exact target PTS seconds."""
    mapping: Dict[int, float] = {}
    if duration < 60.0:
        step = duration / max(1, total_slots)
        for s in range(total_slots):
            mapping[s] = round(s * step, 3)
        return mapping

    num_minutes = min(total_slots, max(1, int(math.ceil(duration / 60.0))))
    for m in range(num_minutes):
        pts = 0.0 if m == 0 else min(duration - 0.5, float(m * 60.0))
        mapping[m] = round(pts, 3)

    # Remaining slots (if any) used for densification:
    remaining_count = total_slots - num_minutes
    if remaining_count > 0:
        half_minutes = []
        for m in range(num_minutes):
            t = m * 60.0 + 30.0
            if t < duration - 10.0:
                half_minutes.append(round(t, 3))
        for i, s in enumerate(range(num_minutes, total_slots)):
            if i < len(half_minutes):
                mapping[s] = half_minutes[i]
            else:
                mapping[s] = round((s / max(1, total_slots)) * duration, 3)

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


def get_sheet_path(key: VideoKey, sheet_index: int) -> Path:
    return get_cache_dir_for_key(key) / f"sheet_{int(sheet_index)}.jpg"


def record_lease(video_id: str, duration: float = LEASE_DURATION_SECONDS, source: Optional[str] = None) -> None:
    key_str = resolve_video_key(video_id, source=source).as_string()
    with _lock:
        _live_leases[key_str] = time.monotonic() + max(1.0, float(duration))


def has_live_lease(video_id: str, source: Optional[str] = None) -> bool:
    key_str = resolve_video_key(video_id, source=source).as_string()
    with _lock:
        expiry = _live_leases.get(key_str, 0.0)
        return expiry > time.monotonic()


def remove_lease(video_id: str, source: Optional[str] = None) -> None:
    key_str = resolve_video_key(video_id, source=source).as_string()
    with _lock:
        _live_leases.pop(key_str, None)


def _default_extract_frame(
    source_url: str,
    target_time: float,
    out_path: Path,
    *,
    timeout: float = 8.0,
    fast: bool = True,
    headers: Optional[Dict[str, str]] = None,
) -> bool:
    """Extract a single frame to out_path using imageio-ffmpeg."""
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
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
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

    try:
        res = subprocess.run(cmd, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res.returncode == 0 and out_path.exists() and out_path.stat().st_size > 350:
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
        if isinstance(data, dict) and data.get("version") == STORYBOARD_V2_VERSION:
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
            native_info = {
                "kind": "preview_video",
                "url": preview_video,
                "frames": [],
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
    ) -> dict:
        key = resolve_video_key(video_id, source=source)
        record_lease(key.as_string())
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
                _active_builds[key_str] = job
                try:
                    _jobs_queue.put_nowait(job)
                except Exception:
                    pass

        self._ensure_worker()
        return manifest

    def _worker_loop(self) -> None:
        while not _worker_stop.is_set():
            try:
                job = _jobs_queue.get(timeout=0.5)
            except Empty:
                continue

            try:
                self._execute_build_job(job)
            except Exception as exc:
                print(f"[Storyboard V2] Error executing job: {exc}")
            finally:
                key_str = job["key"].as_string()
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

        # Number of slots: 100 slots for sheet 0
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

        step = duration / max(1, total_slots)
        with tempfile.TemporaryDirectory(prefix="sb_v2_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            frames_count = len(existing_frames_map)
            current_rev = int(manifest.get("rev", 1))

            slots_to_do = [s for s in bisection_order if s not in existing_frames_map]

            def extract_slot(s_idx: int) -> Tuple[int, float, Optional[Path]]:
                pts = slot_pts_map.get(s_idx, round(s_idx * step, 3))
                f_out = tmp_dir / f"slot_{s_idx:03d}.jpg"
                ok = False
                if direct_url and direct_headers:
                    try:
                        ok = _frame_extractor(direct_url, pts, f_out, timeout=10.0, fast=True, headers=direct_headers)
                    except TypeError:
                        ok = _frame_extractor(direct_url, pts, f_out, timeout=10.0, fast=True)
                    if not ok:
                        # Fallback to accurate seek to avoid dropped/black keyframe seek failures
                        try:
                            ok = _frame_extractor(direct_url, pts, f_out, timeout=12.0, fast=False, headers=direct_headers)
                        except TypeError:
                            ok = _frame_extractor(direct_url, pts, f_out, timeout=12.0, fast=False)
                elif source_url:
                    ok = _frame_extractor(source_url, pts, f_out, timeout=8.0, fast=True)
                    if not ok:
                        ok = _frame_extractor(source_url, pts, f_out, timeout=12.0, fast=False)
                if ok and f_out.exists():
                    return s_idx, pts, f_out
                return s_idx, pts, None

            batch_size = 5
            for b_start in range(0, len(slots_to_do), batch_size):
                if _worker_stop.is_set():
                    break

                self._wait_for_playback_qos(key.provider_id)

                batch = slots_to_do[b_start:b_start + batch_size]
                batch_added = 0
                with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="sb_v2_batch") as pool:
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
                    record_lease(key.as_string(), duration=LEASE_DURATION_SECONDS)
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
        """Atomically persist sheet image and manifest JSON."""
        # 1. Save sheet
        tmp_sheet = sheet_path.with_suffix(f".tmp.{rev}.jpg")
        try:
            sheet_img.save(tmp_sheet, format="JPEG", quality=72, optimize=True)
            os.replace(tmp_sheet, sheet_path)
        finally:
            if tmp_sheet.exists():
                try:
                    tmp_sheet.unlink()
                except OSError:
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
        """Cooperate with player QoS: yield gently if playback is actively seeking or stalled."""
        try:
            import player_qos_runtime as qos
            snap = qos._qos_snapshot()
            waited = 0.0
            while not _worker_stop.is_set() and snap.get("active") and not snap.get("paused") and (
                snap.get("seeking")
                or snap.get("ready_state", 4) < 2
            ):
                if waited >= 2.0:
                    break
                time.sleep(0.25)
                waited += 0.25
                snap = qos._qos_snapshot()
        except Exception:
            pass


progressive_storyboard_service = ProgressiveStoryboardService()
