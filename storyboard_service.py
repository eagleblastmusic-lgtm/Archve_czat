import math
import os
import re
import subprocess
import tempfile
import threading
import time
from itertools import count
from pathlib import Path
from queue import Empty, PriorityQueue
from typing import Dict, List, Optional, Tuple

import imageio_ffmpeg
from PIL import Image

from cache_store import (
    STORYBOARD_CACHE_DIR,
    atomic_write_json,
    read_json_cache,
    safe_cache_key,
    trim_cache_directory,
)

# V4.3: exact-segment-first storyboard scheduler.
# - exact hover work is urgent and preemptible,
# - a second worker is reserved for useful neighbor/prewarm work,
# - stale generations are rejected before FFmpeg,
# - GET status calls briefly wait on state changes instead of busy-polling.
STORYBOARD_VERSION = 8
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
QUICK_FRAMES = 8
QUICK_COLUMNS = 4
SEGMENT_DURATION = 30.0
SEGMENT_FPS = 1
SEGMENT_COLUMNS = 6
WORKER_COUNT = 2
QUEUE_CAPACITY = 96
LEASE_SECONDS = 45.0
DESIRED_TARGET_TTL = 3.0
SEGMENT_STATUS_WAIT_SECONDS = 0.45

_state_lock = threading.RLock()
_state_changed = threading.Condition(_state_lock)
_states: Dict[str, Dict[str, object]] = {}
_leases: Dict[str, Dict[str, float]] = {}
_desired_segments: Dict[str, Dict[str, object]] = {}
_jobs = PriorityQueue(maxsize=QUEUE_CAPACITY)
_sequence = count()
_worker_started = False
_worker_threads: List[threading.Thread] = []
_worker_stop = threading.Event()

_active_process_lock = threading.RLock()
_active_processes: Dict[str, Dict[str, object]] = {}
_preempted_processes = set()
_cancelled_processes = 0
_preempted_count = 0
_recent_segment_ms: List[float] = []
_recent_queue_wait_ms: List[float] = []


def _key(video_id: str) -> str:
    return safe_cache_key(video_id)


def _state_key(video_id: str, duration: float) -> str:
    return f"{_key(video_id)}:{round(float(duration), 3)}"


def _segment_state_key(video_id: str, segment_index: int) -> str:
    return f"{_key(video_id)}:seg_{int(segment_index)}"


def _paths(video_id: str, quality: str) -> Tuple[Path, Path]:
    k = _key(video_id)
    suffix = "quick" if quality != "full" else "full"
    return (
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.{suffix}.jpg",
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.{suffix}.json",
    )


def _segment_paths(video_id: str, segment_index: int) -> Tuple[Path, Path]:
    k = _key(video_id)
    return (
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.seg_{int(segment_index)}.jpg",
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.seg_{int(segment_index)}.json",
    )


def _remember_metric(bucket: List[float], value: float) -> None:
    bucket.append(max(0.0, float(value)))
    if len(bucket) > 128:
        del bucket[:-128]


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(len(ordered) * percentile) - 1)))
    return round(float(ordered[idx]), 2)


def runtime_stats() -> dict:
    with _active_process_lock:
        active = list(_active_processes.values())
        cancelled = int(_cancelled_processes)
        preempted = int(_preempted_count)
    now = time.monotonic()
    with _state_lock:
        building = sum(1 for value in _states.values() if value.get("status") == "building")
        active_leases = sum(
            1 for entries in _leases.values()
            if any(float(expiry) > now for expiry in entries.values())
        )
        desired = {
            key: {
                "segment": int(value.get("segment", -1)),
                "direction": int(value.get("direction", 0)),
                "generation": int(value.get("generation", 0)),
            }
            for key, value in _desired_segments.items()
            if now - float(value.get("updated", 0.0)) <= DESIRED_TARGET_TTL
            or any(float(expiry) > now for expiry in (_leases.get(key) or {}).values())
        }
    return {
        "queue_size": _jobs.qsize(),
        "queue_capacity": _jobs.maxsize,
        "workers": WORKER_COUNT,
        "active_processes": len(active),
        "active_process_kinds": [str(item.get("kind") or "unknown") for item in active],
        "cancelled_processes": cancelled,
        "preempted_processes": preempted,
        "building_jobs": building,
        "active_leases": active_leases,
        "desired_segments": desired,
        "segment_ffmpeg_p50_ms": _percentile(_recent_segment_ms, 0.50),
        "segment_ffmpeg_p95_ms": _percentile(_recent_segment_ms, 0.95),
        "queue_wait_p50_ms": _percentile(_recent_queue_wait_ms, 0.50),
        "queue_wait_p95_ms": _percentile(_recent_queue_wait_ms, 0.95),
        "segment_status_wait_ms": int(SEGMENT_STATUS_WAIT_SECONDS * 1000),
        "auto_full_upgrade": False,
        "storyboard_version": STORYBOARD_VERSION,
    }


def _has_live_lease(video_id: str) -> bool:
    now = time.monotonic()
    with _state_lock:
        entries = _leases.get(_key(video_id)) or {}
        return any(float(expiry) > now for expiry in entries.values())


def _set_desired_segment_locked(video_id: str, segment_index: int) -> int:
    k = _key(video_id)
    now = time.monotonic()
    previous = _desired_segments.get(k) or {}
    previous_segment = previous.get("segment")
    direction = int(previous.get("direction") or 0)
    if previous_segment is not None and int(previous_segment) != int(segment_index):
        direction = 1 if int(segment_index) > int(previous_segment) else -1
    generation = int(previous.get("generation") or 0) + 1
    _desired_segments[k] = {
        "segment": int(segment_index),
        "direction": direction,
        "generation": generation,
        "updated": now,
    }
    return generation


def _set_desired_segment(video_id: str, segment_index: int) -> int:
    with _state_lock:
        return _set_desired_segment_locked(video_id, segment_index)


def _touch_desired_segment_locked(video_id: str, segment_index: int) -> Optional[int]:
    value = _desired_segments.get(_key(video_id))
    if not value or int(value.get("segment", -1)) != int(segment_index):
        return None
    value["updated"] = time.monotonic()
    return int(value.get("generation") or 0)


def _current_desired(video_id: str) -> Optional[dict]:
    now = time.monotonic()
    k = _key(video_id)
    with _state_lock:
        value = dict(_desired_segments.get(k) or {})
        live = any(float(expiry) > now for expiry in (_leases.get(k) or {}).values())
    if not value:
        return None
    if not live and now - float(value.get("updated") or 0.0) > DESIRED_TARGET_TTL:
        return None
    return value


def _job_is_superseded(video_id: str, segment_index: int, generation: int, priority: int) -> bool:
    desired = _current_desired(video_id)
    if not desired:
        return int(priority) > 0
    current_generation = int(desired.get("generation", -1))
    current_segment = int(desired.get("segment", -1))
    if int(priority) <= 0:
        return current_segment != int(segment_index) or current_generation != int(generation)
    if current_generation != int(generation):
        return True
    # Background work is allowed only for the current target itself or for the
    # single directional neighbor predicted by the latest real cursor movement.
    # This prevents an arbitrary same-generation background segment from
    # occupying the second FFmpeg worker.
    if int(segment_index) == current_segment:
        return False
    direction = int(desired.get("direction") or 0)
    if direction == 0:
        return True
    return int(segment_index) != current_segment + direction


def _preempt_active_for_target(video_id: str, segment_index: int) -> None:
    global _preempted_count
    video_key = _key(video_id)
    victims = []
    with _active_process_lock:
        for process_key, info in list(_active_processes.items()):
            if info.get("video_key") != video_key:
                continue
            if info.get("kind") == "segment" and int(info.get("segment", -1)) == int(segment_index):
                continue
            proc = info.get("proc")
            if proc is not None and proc.poll() is None:
                _preempted_processes.add(process_key)
                victims.append(proc)
        if victims:
            _preempted_count += len(victims)
    for proc in victims:
        try:
            proc.terminate()
        except Exception:
            pass


def _cancel_all_for_video(video_id: str) -> None:
    video_key = _key(video_id)
    victims = []
    with _active_process_lock:
        for process_key, info in list(_active_processes.items()):
            if info.get("video_key") != video_key:
                continue
            proc = info.get("proc")
            if proc is not None and proc.poll() is None:
                _preempted_processes.add(process_key)
                victims.append(proc)
    for proc in victims:
        try:
            proc.terminate()
        except Exception:
            pass


def _run_cancellable_process(
    cmd,
    *,
    timeout: float,
    process_key: str,
    text: bool = False,
    cancel_check=None,
    video_id: Optional[str] = None,
    kind: str = "generic",
    segment_index: Optional[int] = None,
    priority: int = 0,
):
    global _cancelled_processes
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=text,
        errors="replace" if text else None,
    )
    with _active_process_lock:
        _active_processes[process_key] = {
            "proc": proc,
            "video_key": _key(video_id or process_key),
            "kind": kind,
            "segment": segment_index,
            "priority": int(priority),
            "started": time.monotonic(),
        }
    deadline = time.monotonic() + max(0.1, float(timeout))
    try:
        while True:
            preempted = False
            with _active_process_lock:
                if process_key in _preempted_processes:
                    _preempted_processes.discard(process_key)
                    preempted = True
            if preempted or (cancel_check is not None and cancel_check()):
                _cancelled_processes += 1
                try:
                    proc.terminate()
                    proc.wait(timeout=0.75)
                except subprocess.TimeoutExpired:
                    proc.kill()
                except Exception:
                    pass
                stderr = proc.stderr.read() if proc.stderr else ("" if text else b"")
                return -15, stderr, True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    proc.kill()
                except Exception:
                    pass
                stderr = proc.stderr.read() if proc.stderr else ("" if text else b"")
                return -9, stderr, False
            try:
                _, stderr = proc.communicate(timeout=min(0.10, remaining))
                with _active_process_lock:
                    preempted_after_exit = process_key in _preempted_processes
                    _preempted_processes.discard(process_key)
                return proc.returncode, stderr, bool(preempted_after_exit)
            except subprocess.TimeoutExpired:
                continue
    finally:
        with _active_process_lock:
            _active_processes.pop(process_key, None)
            _preempted_processes.discard(process_key)


def _cached_variant(video_id: str, duration: float, quality: str) -> Optional[dict]:
    sprite_path, meta_path = _paths(video_id, quality)
    meta, _ = read_json_cache(meta_path)
    if not isinstance(meta, dict):
        return None
    filename = meta.get("sprite_file")
    if filename and Path(filename).name == filename:
        sprite_path = Path(STORYBOARD_CACHE_DIR) / filename
    if not sprite_path.exists():
        return None
    if meta.get("version") != STORYBOARD_VERSION or meta.get("quality") != quality:
        return None
    cached_duration = float(meta.get("duration") or 0)
    if duration > 0 and cached_duration > 0 and abs(cached_duration - duration) > max(2.0, duration * 0.01):
        return None
    return meta


def _cached_segment(video_id: str, duration: float, segment_index: int) -> Optional[dict]:
    sprite_path, meta_path = _segment_paths(video_id, segment_index)
    meta, _ = read_json_cache(meta_path)
    if not isinstance(meta, dict):
        return None
    filename = meta.get("sprite_file")
    if filename and Path(filename).name == filename:
        sprite_path = Path(STORYBOARD_CACHE_DIR) / filename
    if not sprite_path.exists():
        return None
    if meta.get("version") != STORYBOARD_VERSION or int(meta.get("segment_index", -1)) != int(segment_index):
        return None
    cached_total = float(meta.get("total_duration") or 0)
    if duration > 0 and cached_total > 0 and abs(cached_total - duration) > max(2.0, duration * 0.01):
        return None
    return meta


def get_status(video_id: str, duration: float) -> dict:
    quick = _cached_variant(video_id, duration, "quick")
    if quick:
        return {"status": "ready", **quick, "upgrade_status": "disabled"}
    with _state_lock:
        state = dict(_states.get(_state_key(video_id, duration)) or {})
    return state or {"status": "missing", "upgrade_status": "disabled"}


def get_segment_status(video_id: str, duration: float, segment_index: int) -> dict:
    cached = _cached_segment(video_id, duration, segment_index)
    if cached:
        return {"status": "ready", **cached}
    state_key = _segment_state_key(video_id, segment_index)
    with _state_changed:
        state = dict(_states.get(state_key) or {})
        if state.get("status") == "building":
            _state_changed.wait_for(
                lambda: (_states.get(state_key) or {}).get("status") != "building",
                timeout=SEGMENT_STATUS_WAIT_SECONDS,
            )
            state = dict(_states.get(state_key) or {})
    if state:
        return state
    cached = _cached_segment(video_id, duration, segment_index)
    if cached:
        return {"status": "ready", **cached}
    return {"status": "missing"}


def sprite_path(video_id: str, quality: str = "best", revision=None) -> Optional[Path]:
    qualities = ("quick",) if quality == "best" else (quality,)
    for variant in qualities:
        if variant not in {"quick", "full"}:
            continue
        base, meta_path = _paths(video_id, variant)
        if revision and str(revision).isdigit():
            candidate = base.with_name(f"{base.stem}.{revision}.jpg")
        else:
            meta, _ = read_json_cache(meta_path)
            if not isinstance(meta, dict) or meta.get("version") != STORYBOARD_VERSION:
                continue
            filename = str(meta.get("sprite_file") or "")
            if not filename or Path(filename).name != filename:
                continue
            candidate = Path(STORYBOARD_CACHE_DIR) / filename
        if candidate.exists():
            return candidate
    return None


def segment_sprite_path(video_id: str, segment_index: int, revision=None) -> Optional[Path]:
    base, meta_path = _segment_paths(video_id, segment_index)
    if revision and str(revision).isdigit():
        candidate = base.with_name(f"{base.stem}.{revision}.jpg")
    else:
        meta, _ = read_json_cache(meta_path)
        if not isinstance(meta, dict) or meta.get("version") != STORYBOARD_VERSION:
            return None
        filename = str(meta.get("sprite_file") or "")
        if not filename or Path(filename).name != filename:
            return None
        candidate = Path(STORYBOARD_CACHE_DIR) / filename
    return candidate if candidate.exists() else None


def _cleanup_segment_frames(tmp_dir: Path) -> None:
    for frame in tmp_dir.glob("frame_*.jpg"):
        try:
            frame.unlink()
        except OSError:
            pass


def _valid_segment_frame(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.size == (FRAME_WIDTH, FRAME_HEIGHT)
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
    cancel_check=None,
    process_key: str = "segment",
    video_id: Optional[str] = None,
    segment_index: Optional[int] = None,
    priority: int = 0,
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
        returncode, stderr, cancelled = _run_cancellable_process(
            cmd,
            timeout=timeout,
            process_key=process_key,
            text=True,
            cancel_check=cancel_check,
            video_id=video_id,
            kind="segment",
            segment_index=segment_index,
            priority=priority,
        )
    except Exception:
        _cleanup_segment_frames(tmp_dir)
        return [], [], False

    frames = sorted(tmp_dir.glob("frame_*.jpg"))
    if cancelled or returncode != 0 or not frames or any(not _valid_segment_frame(frame) for frame in frames):
        _cleanup_segment_frames(tmp_dir)
        return [], [], False

    stderr = stderr or ""
    pts = []
    for match in re.finditer(r"pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))", stderr):
        try:
            pts.append(float(match.group(1)))
        except ValueError:
            pass
    exact = len(pts) >= len(frames)
    if exact:
        times = [float(start_time) + max(0.0, pts[i]) for i in range(len(frames))]
    else:
        times = [float(start_time) + i * (1.0 / SEGMENT_FPS) for i in range(len(frames))]
    return frames, times, exact


def _extract_segment_frames_with_times(
    ffmpeg: str,
    source_url: str,
    start_time: float,
    target_duration: float,
    tmp_dir: Path,
    timeout: int = 25,
    cancel_check=None,
    process_key: str = "segment",
    video_id: Optional[str] = None,
    segment_index: Optional[int] = None,
    priority: int = 0,
) -> Tuple[List[Path], List[float], bool]:
    first = _run_segment_extract(
        ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout, True,
        cancel_check=cancel_check, process_key=process_key, video_id=video_id,
        segment_index=segment_index, priority=priority,
    )
    if first[0] or (cancel_check is not None and cancel_check()):
        return first
    return _run_segment_extract(
        ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout, False,
        cancel_check=cancel_check, process_key=process_key, video_id=video_id,
        segment_index=segment_index, priority=priority,
    )


def _extract_segment_frames(
    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25
) -> List[Path]:
    frames, _, _ = _extract_segment_frames_with_times(
        ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout
    )
    return frames


def _extract_one(ffmpeg: str, source_url: str, target: float, output_path: Path, timeout: int) -> bool:
    # Compatibility helper for the cache-only QUICK fallback. Production V4.3
    # does not start cold QUICK generation from card hover.
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{target:.3f}", "-i", source_url,
        "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn", "-threads", "1",
        "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,crop={FRAME_WIDTH}:{FRAME_HEIGHT}",
        "-q:v", "7", "-y", str(output_path),
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
        return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 350
    except Exception:
        return False


def _build_variant(video_id: str, duration: float, source_url: str, quality: str) -> dict:
    if quality != "quick":
        raise RuntimeError("FULL storyboard generation is disabled in V4.3")
    sprite_base, meta_path = _paths(video_id, quality)
    revision = str(time.time_ns())
    sprite_path_out = sprite_base.with_name(f"{sprite_base.stem}.{revision}.jpg")
    frame_count = QUICK_FRAMES
    requested_times = [
        min(max(0.05, duration * ((i + 0.5) / frame_count)), max(0.05, duration - 0.12))
        for i in range(frame_count)
    ]
    successful: Dict[int, Path] = {}
    with tempfile.TemporaryDirectory(prefix="archivebate_storyboard_quick_") as tmp_dir:
        tmp = Path(tmp_dir)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        for i, target in enumerate(requested_times):
            if not _has_live_lease(video_id):
                break
            out = tmp / f"frame_{i:03d}.jpg"
            if _extract_one(ffmpeg, source_url, target, out, 12):
                successful[i] = out
            if _current_desired(video_id):
                break
        if len(successful) < 4:
            raise RuntimeError(f"FFmpeg prepared only {len(successful)}/{frame_count} QUICK frames")
        columns = QUICK_COLUMNS
        rows = int(math.ceil(frame_count / columns))
        sprite = Image.new("RGB", (columns * FRAME_WIDTH, rows * FRAME_HEIGHT), (12, 12, 12))
        selected_indices = [
            i if i in successful else min(successful, key=lambda k: abs(k - i))
            for i in range(frame_count)
        ]
        for i, source_idx in enumerate(selected_indices):
            with Image.open(successful[source_idx]) as frame:
                frame = frame.convert("RGB")
                if frame.size != (FRAME_WIDTH, FRAME_HEIGHT):
                    frame = frame.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.BILINEAR)
                sprite.paste(frame, ((i % columns) * FRAME_WIDTH, (i // columns) * FRAME_HEIGHT))
        tmp_sprite = sprite_path_out.with_suffix(".tmp.jpg")
        sprite.save(tmp_sprite, format="JPEG", quality=72, optimize=False, progressive=False, subsampling=2)
        os.replace(tmp_sprite, sprite_path_out)
    meta = {
        "version": STORYBOARD_VERSION,
        "quality": "quick",
        "video_id": str(video_id),
        "duration": float(duration),
        "frame_count": frame_count,
        "columns": QUICK_COLUMNS,
        "rows": int(math.ceil(frame_count / QUICK_COLUMNS)),
        "frame_width": FRAME_WIDTH,
        "frame_height": FRAME_HEIGHT,
        "times": [round(requested_times[i], 3) for i in selected_indices],
        "requested_times": [round(value, 3) for value in requested_times],
        "selected_indices": selected_indices,
        "approximate": True,
        "time_precision": "seek_target_not_decoded_pts",
        "created_at": revision,
        "sprite_file": sprite_path_out.name,
    }
    atomic_write_json(meta_path, meta)
    return meta


def _build_segment(video_id: str, duration: float, segment_index: int, source_url: str, priority: int = 0) -> dict:
    started = time.monotonic()
    sprite_base, meta_path = _segment_paths(video_id, segment_index)
    revision = str(time.time_ns())
    sprite_path_out = sprite_base.with_name(f"{sprite_base.stem}.{revision}.jpg")
    start_time = float(segment_index) * SEGMENT_DURATION
    end_time = min(float(duration), (float(segment_index) + 1.0) * SEGMENT_DURATION)
    seg_duration = max(0.5, end_time - start_time)
    process_key = f"{_key(video_id)}:seg:{segment_index}:{revision}"
    with tempfile.TemporaryDirectory(prefix=f"archivebate_seg_{segment_index}_") as tmp_dir:
        tmp = Path(tmp_dir)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        frames, decoded_times, precise_timing = _extract_segment_frames_with_times(
            ffmpeg,
            source_url,
            start_time,
            seg_duration,
            tmp,
            cancel_check=lambda: _worker_stop.is_set() or not _has_live_lease(video_id),
            process_key=process_key,
            video_id=video_id,
            segment_index=segment_index,
            priority=priority,
        )
        if not frames:
            raise RuntimeError(f"FFmpeg did not produce valid frames for segment {segment_index}")
        frame_count = len(frames)
        columns = min(SEGMENT_COLUMNS, frame_count)
        rows = int(math.ceil(frame_count / columns))
        sprite = Image.new("RGB", (columns * FRAME_WIDTH, rows * FRAME_HEIGHT), (12, 12, 12))
        for i, frame_file in enumerate(frames):
            with Image.open(frame_file) as frame:
                frame = frame.convert("RGB")
                if frame.size != (FRAME_WIDTH, FRAME_HEIGHT):
                    frame = frame.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.BILINEAR)
                sprite.paste(frame, ((i % columns) * FRAME_WIDTH, (i // columns) * FRAME_HEIGHT))
        tmp_sprite = sprite_path_out.with_suffix(".tmp.jpg")
        sprite.save(tmp_sprite, format="JPEG", quality=74, optimize=False, progressive=False, subsampling=2)
        os.replace(tmp_sprite, sprite_path_out)
    times = [round(min(float(duration), float(t)), 3) for t in decoded_times[:frame_count]]
    meta = {
        "version": STORYBOARD_VERSION,
        "type": "segment",
        "video_id": str(video_id),
        "segment_index": int(segment_index),
        "start_time": round(start_time, 3),
        "end_time": round(end_time, 3),
        "duration": round(seg_duration, 3),
        "total_duration": float(duration),
        "frame_count": frame_count,
        "columns": columns,
        "rows": rows,
        "frame_width": FRAME_WIDTH,
        "frame_height": FRAME_HEIGHT,
        "times": times,
        "approximate": not precise_timing,
        "time_precision": "decoded_pts_1fps" if precise_timing else "nominal_1fps_fallback",
        "created_at": revision,
        "sprite_file": sprite_path_out.name,
    }
    atomic_write_json(meta_path, meta)
    _remember_metric(_recent_segment_ms, (time.monotonic() - started) * 1000.0)
    try:
        trim_cache_directory(STORYBOARD_CACHE_DIR, max_bytes=500 * 1024 * 1024, preserve_suffixes=())
    except Exception:
        pass
    return meta


def demand(video_id: str, consumer: str, active: bool = True):
    k = _key(video_id)
    should_cancel = False
    with _state_changed:
        now = time.monotonic()
        for old_key, old_entries in list(_leases.items()):
            for token, expiry in list(old_entries.items()):
                if float(expiry) <= now:
                    old_entries.pop(token, None)
            if not old_entries:
                _leases.pop(old_key, None)
        if k not in _leases and len(_leases) >= 256:
            raise ValueError("Too many storyboard consumers")
        entries = _leases.setdefault(k, {})
        if active:
            entries[str(consumer)] = now + LEASE_SECONDS
        else:
            entries.pop(str(consumer), None)
            if not entries:
                _leases.pop(k, None)
                _desired_segments.pop(k, None)
                should_cancel = True
        _state_changed.notify_all()
    if should_cancel:
        _cancel_all_for_video(video_id)


def _ensure_workers() -> None:
    global _worker_started, _worker_threads
    with _state_lock:
        living = [thread for thread in _worker_threads if thread.is_alive()]
        _worker_threads = living
        if len(living) >= WORKER_COUNT:
            _worker_started = True
            return
        if _worker_stop.is_set():
            _worker_stop.clear()
        for idx in range(len(living), WORKER_COUNT):
            thread = threading.Thread(target=_run_jobs, daemon=True, name=f"storyboard-worker-{idx + 1}")
            _worker_threads.append(thread)
            thread.start()
        _worker_started = True


def _maybe_enqueue_directional_prefetch(video_id: str, duration: float, segment_index: int, source_url: str, generation: int) -> None:
    desired = _current_desired(video_id)
    if not desired or int(desired.get("generation", -1)) != int(generation):
        return
    if int(desired.get("segment", -1)) != int(segment_index):
        return
    direction = int(desired.get("direction") or 0)
    if direction == 0 or not _has_live_lease(video_id):
        return
    neighbor = int(segment_index) + direction
    if neighbor < 0 or float(neighbor) * SEGMENT_DURATION >= float(duration):
        return
    start_segment(video_id, duration, neighbor, source_url, force=False, priority=1)


def _run_jobs():
    while not _worker_stop.is_set():
        try:
            job = _jobs.get(timeout=0.20)
        except Empty:
            continue
        try:
            if _worker_stop.is_set():
                continue
            priority = int(job[0])
            queued_at = float(job[2])
            _remember_metric(_recent_queue_wait_ms, (time.monotonic() - queued_at) * 1000.0)
            video_id = job[3]
            duration = float(job[4])
            source_url = job[5]
            kind = job[6]
            if kind == "segment":
                segment_index = int(job[7])
                generation = int(job[8])
                state_key = _segment_state_key(video_id, segment_index)
                if not _has_live_lease(video_id) or _job_is_superseded(video_id, segment_index, generation, priority):
                    with _state_changed:
                        if (_states.get(state_key) or {}).get("status") == "building":
                            _states.pop(state_key, None)
                        _state_changed.notify_all()
                    continue
                try:
                    result = _build_segment(video_id, duration, segment_index, source_url, priority=priority)
                    with _state_changed:
                        _states[state_key] = {"status": "ready", **result}
                        _state_changed.notify_all()
                    if priority <= 0:
                        _maybe_enqueue_directional_prefetch(video_id, duration, segment_index, source_url, generation)
                except Exception as exc:
                    superseded = _job_is_superseded(video_id, segment_index, generation, priority)
                    if superseded or not _has_live_lease(video_id):
                        with _state_changed:
                            _states.pop(state_key, None)
                            _state_changed.notify_all()
                    else:
                        with _state_changed:
                            _states[state_key] = {"status": "error", "error": str(exc), "finished_at": time.time()}
                            _state_changed.notify_all()
                continue

            quality = str(job[7])
            state_key = _state_key(video_id, duration)
            if not _has_live_lease(video_id):
                with _state_changed:
                    _states.pop(state_key, None)
                    _state_changed.notify_all()
                continue
            if _current_desired(video_id):
                with _state_changed:
                    _states.pop(state_key, None)
                    _state_changed.notify_all()
                continue
            try:
                result = _build_variant(video_id, duration, source_url, quality)
                with _state_changed:
                    _states[state_key] = {"status": "ready", **result, "upgrade_status": "disabled"}
                    _state_changed.notify_all()
            except Exception as exc:
                with _state_changed:
                    _states[state_key] = {
                        "status": "error", "error": str(exc), "finished_at": time.time(), "upgrade_status": "disabled"
                    }
                    _state_changed.notify_all()
        finally:
            _jobs.task_done()


def start(video_id: str, duration: float, source_url: str, force: bool = False) -> dict:
    duration = float(duration or 0)
    if not video_id or duration <= 0 or not math.isfinite(duration):
        return {"status": "error", "error": "Invalid ID or duration"}
    cached = _cached_variant(video_id, duration, "quick")
    if cached and not force:
        return {"status": "ready", **cached, "upgrade_status": "disabled"}
    k = _state_key(video_id, duration)
    with _state_changed:
        current = dict(_states.get(k) or {})
        if current.get("status") == "building":
            return current
        if current.get("status") == "error" and not force and time.time() - float(current.get("finished_at") or 0) < 30:
            return current
        if _jobs.full():
            return {"status": "error", "error": "Storyboard queue is full"}
        state = {"status": "building", "stage": "quick", "upgrade_status": "disabled"}
        _states[k] = state
        _jobs.put_nowait((3, next(_sequence), time.monotonic(), video_id, duration, source_url, "variant", "quick"))
        _state_changed.notify_all()
    _ensure_workers()
    return dict(state)


def start_segment(
    video_id: str,
    duration: float,
    segment_index: int,
    source_url: str,
    force: bool = False,
    priority: int = 0,
) -> dict:
    duration = float(duration or 0)
    segment_index = int(segment_index)
    priority = int(priority)
    if not video_id or duration <= 0 or not math.isfinite(duration) or segment_index < 0:
        return {"status": "error", "error": "Invalid ID, duration, or segment"}
    cached = _cached_segment(video_id, duration, segment_index)
    if cached and not force:
        return {"status": "ready", **cached}

    k_seg = _segment_state_key(video_id, segment_index)
    should_preempt = False
    with _state_changed:
        current = dict(_states.get(k_seg) or {})
        if current.get("status") == "building" and not force:
            if priority <= 0:
                _touch_desired_segment_locked(video_id, segment_index)
            return current
        if current.get("status") == "error" and not force and time.time() - float(current.get("finished_at") or 0) < 10:
            return current
        if _jobs.full():
            return {"status": "error", "error": "Storyboard queue is full"}

        if priority <= 0:
            generation = _set_desired_segment_locked(video_id, segment_index)
            should_preempt = True
        else:
            desired = dict(_desired_segments.get(_key(video_id)) or {})
            generation = int(desired.get("generation") or 0)

        state = {
            "status": "building",
            "type": "segment",
            "segment_index": segment_index,
            "priority": priority,
            "generation": generation,
        }
        _states[k_seg] = state
        _jobs.put_nowait((priority, next(_sequence), time.monotonic(), video_id, duration, source_url, "segment", segment_index, generation))
        _state_changed.notify_all()

    if should_preempt:
        _preempt_active_for_target(video_id, segment_index)
    _ensure_workers()
    return dict(state)


def shutdown(timeout: float = 10.0) -> None:
    global _worker_started, _worker_threads
    _worker_stop.set()
    with _active_process_lock:
        victims = [info.get("proc") for info in _active_processes.values() if info.get("proc") is not None]
    for proc in victims:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    deadline = time.monotonic() + max(0.0, float(timeout))
    for thread in list(_worker_threads):
        remaining = max(0.0, deadline - time.monotonic())
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=remaining)
    alive = [thread for thread in _worker_threads if thread.is_alive()]
    if alive:
        raise RuntimeError("storyboard workers did not stop before timeout")
    while True:
        try:
            _jobs.get_nowait()
        except Empty:
            break
        else:
            _jobs.task_done()
    with _state_changed:
        _states.clear()
        _desired_segments.clear()
        _leases.clear()
        _state_changed.notify_all()
    _worker_started = False
    _worker_threads = []


close = shutdown
