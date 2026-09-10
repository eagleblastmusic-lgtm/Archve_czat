import math
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import count
from pathlib import Path
from queue import PriorityQueue
from typing import Dict, List, Optional, Tuple

from PIL import Image
import imageio_ffmpeg

from cache_store import (
    STORYBOARD_CACHE_DIR,
    atomic_write_json,
    read_json_cache,
    safe_cache_key,
    trim_cache_directory,
)

# v5: progressive storyboard. Najpierw bardzo lekki QUICK, potem FULL w tle.
STORYBOARD_VERSION = 6
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
QUICK_FRAMES = 8
QUICK_COLUMNS = 4
FULL_MIN_FRAMES = 24
FULL_MAX_FRAMES = 48
FULL_TARGET_SECONDS_PER_FRAME = 20
QUICK_WORKERS = 2
FULL_WORKERS = 2

# Pakiet D: gęsty cache segmentów osi czasu (segmenty 30s, 1 klatka/s, manifest rzeczywistych znaczników czasu)
SEGMENT_DURATION = 30.0
SEGMENT_FPS = 1
SEGMENT_COLUMNS = 6

_state_lock = threading.Lock()
_states: Dict[str, Dict[str, object]] = {}
_jobs = PriorityQueue(maxsize=64)
_sequence = count()
_worker_started = False
_leases: Dict[str, Dict[str, float]] = {}


def _key(video_id: str) -> str:
    return safe_cache_key(video_id)


def _paths(video_id: str, quality: str) -> Tuple[Path, Path]:
    k = _key(video_id)
    suffix = "full" if quality == "full" else "quick"
    return (
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.{suffix}.jpg",
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.{suffix}.json",
    )


def _segment_paths(video_id: str, segment_index: int) -> Tuple[Path, Path]:
    k = _key(video_id)
    return (
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.seg_{segment_index}.jpg",
        Path(STORYBOARD_CACHE_DIR) / f"{k}.v{STORYBOARD_VERSION}.seg_{segment_index}.json",
    )


def _frame_count(duration: float, quality: str) -> int:
    if quality == "quick":
        return QUICK_FRAMES
    desired = int(math.ceil(max(1.0, duration) / FULL_TARGET_SECONDS_PER_FRAME))
    return max(FULL_MIN_FRAMES, min(FULL_MAX_FRAMES, desired))


def _columns(frame_count: int, quality: str) -> int:
    if quality == "quick":
        return QUICK_COLUMNS
    # Arkusz bliski kwadratowi dekoduje się i mieści w GPU lepiej niż jeden długi pasek.
    return max(6, min(12, int(math.ceil(math.sqrt(frame_count * 16 / 9)))))


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
    if int(meta.get("frame_width") or 0) != FRAME_WIDTH or int(meta.get("frame_height") or 0) != FRAME_HEIGHT:
        return None
    try:
        os.utime(sprite_path, None)
        os.utime(meta_path, None)
    except OSError:
        pass
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
    if meta.get("version") != STORYBOARD_VERSION or meta.get("segment_index") != segment_index:
        return None
    cached_total = float(meta.get("total_duration") or 0)
    if duration > 0 and cached_total > 0 and abs(cached_total - duration) > max(2.0, duration * 0.01):
        return None
    try:
        os.utime(sprite_path, None)
        os.utime(meta_path, None)
    except OSError:
        pass
    return meta


def get_status(video_id: str, duration: float) -> dict:
    full = _cached_variant(video_id, duration, "full")
    if full:
        return {"status": "ready", **full}
    quick = _cached_variant(video_id, duration, "quick")
    k = f"{_key(video_id)}:{round(duration,3)}"
    with _state_lock:
        state = dict(_states.get(k) or {})
    if quick:
        return {"status": "ready", **quick, "upgrade_status": state.get("upgrade_status", "idle")}
    if state:
        return state
    return {"status": "missing"}


def get_segment_status(video_id: str, duration: float, segment_index: int) -> dict:
    cached = _cached_segment(video_id, duration, segment_index)
    if cached:
        return {"status": "ready", **cached}
    k_seg = f"{_key(video_id)}:seg_{segment_index}"
    with _state_lock:
        state = dict(_states.get(k_seg) or {})
    if state:
        return state
    return {"status": "missing"}


def sprite_path(video_id: str, quality: str = "best", revision=None) -> Optional[Path]:
    qualities = ("full", "quick") if quality == "best" else (quality,)
    for variant in qualities:
        base, meta_path = _paths(video_id, variant)
        if revision and str(revision).isdigit():
            candidate = base.with_name(f"{base.stem}.{revision}.jpg")
        else:
            meta, _ = read_json_cache(meta_path)
            if not isinstance(meta, dict) or meta.get("version") != STORYBOARD_VERSION:
                continue
            filename = meta.get("sprite_file", "")
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
        filename = meta.get("sprite_file", "")
        if not filename or Path(filename).name != filename:
            return None
        candidate = Path(STORYBOARD_CACHE_DIR) / filename
    if candidate.exists():
        return candidate
    return None


def _extract_one(ffmpeg: str, source_url: str, target: float, output_path: Path, timeout: int) -> bool:
    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{target:.3f}",
        "-i", source_url,
        "-map", "0:v:0",
        "-frames:v", "1",
        "-an", "-sn", "-dn",
        "-threads", "1",
        "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,crop={FRAME_WIDTH}:{FRAME_HEIGHT}",
        "-q:v", "7",
        "-y", str(output_path),
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
        return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 350
    except Exception:
        return False


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
    for match in re.finditer(r"pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))", stderr):
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
    """Extract a dense segment safely using the source-proven FFmpeg command shape.

    A segment is accepted only when FFmpeg reports success and every produced
    JPEG decodes at the expected dimensions. Timing is nominal 1 fps, so the
    manifest must mark it as approximate instead of claiming decoded precision.
    """
    vf = (
        f"fps={SEGMENT_FPS},"
        f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={FRAME_WIDTH}:{FRAME_HEIGHT}"
    )

    def run(seek_before_input: bool) -> Tuple[List[Path], List[float], bool]:
        _cleanup_segment_frames(tmp_dir)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if seek_before_input:
            cmd += ["-ss", f"{start_time:.3f}", "-t", f"{target_duration:.3f}", "-i", source_url]
        else:
            cmd += ["-i", source_url, "-ss", f"{start_time:.3f}", "-t", f"{target_duration:.3f}"]
        cmd += [
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
            )
        except Exception:
            _cleanup_segment_frames(tmp_dir)
            return [], [], False

        frames = sorted(tmp_dir.glob("frame_*.jpg"))
        if result.returncode != 0 or not frames or any(not _valid_segment_frame(frame) for frame in frames):
            _cleanup_segment_frames(tmp_dir)
            return [], [], False

        times = [float(start_time) + i * (1.0 / SEGMENT_FPS) for i in range(len(frames))]
        return frames, times, False

    first = run(True)
    if first[0]:
        return first
    return run(False)


def _extract_segment_frames(
    ffmpeg: str, source_url: str, start_time: float, target_duration: float, tmp_dir: Path, timeout: int = 25
) -> List[Path]:
    """Compatibility wrapper; non-zero FFmpeg output is never accepted."""
    frames, _, _ = _extract_segment_frames_with_times(ffmpeg, source_url, start_time, target_duration, tmp_dir, timeout)
    return frames


def _nearest_success(index: int, successful: Dict[int, Path]) -> Optional[Path]:
    if not successful:
        return None
    nearest_idx = min(successful.keys(), key=lambda k: abs(k - index))
    return successful[nearest_idx]


def _build_variant(video_id: str, duration: float, source_url: str, quality: str) -> dict:
    sprite_path_out, meta_path = _paths(video_id, quality)
    revision = str(time.time_ns())
    sprite_path_out = sprite_path_out.with_name(f"{sprite_path_out.stem}.{revision}.jpg")
    frame_count = _frame_count(duration, quality)
    columns = _columns(frame_count, quality)
    rows = int(math.ceil(frame_count / columns))
    times = [
        min(max(0.05, duration * ((i + 0.5) / frame_count)), max(0.05, duration - 0.12))
        for i in range(frame_count)
    ]

    workers = QUICK_WORKERS if quality == "quick" else FULL_WORKERS
    timeout = 14 if quality == "quick" else 20

    with tempfile.TemporaryDirectory(prefix=f"archivebate_storyboard_{quality}_") as tmp_dir:
        tmp = Path(tmp_dir)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        successful: Dict[int, Path] = {}

        with ThreadPoolExecutor(max_workers=min(workers, frame_count)) as pool:
            futures = {}
            for i, target in enumerate(times):
                out = tmp / f"frame_{i:03d}.jpg"
                futures[pool.submit(_extract_one, ffmpeg, source_url, target, out, timeout)] = (i, out)
            for future in as_completed(futures):
                i, out = futures[future]
                try:
                    if future.result():
                        successful[i] = out
                except Exception:
                    pass

        minimum = max(4, int(frame_count * (0.42 if quality == "quick" else 0.55)))
        if len(successful) < minimum:
            raise RuntimeError(f"FFmpeg przygotował tylko {len(successful)}/{frame_count} klatek ({quality})")

        sprite = Image.new("RGB", (columns * FRAME_WIDTH, rows * FRAME_HEIGHT), (12, 12, 12))
        selected_indices = [i if i in successful else min(successful, key=lambda k: abs(k-i)) for i in range(frame_count)]
        for i in range(frame_count):
            source = successful[selected_indices[i]]
            if not source:
                continue
            with Image.open(source) as frame:
                frame = frame.convert("RGB")
                if frame.size != (FRAME_WIDTH, FRAME_HEIGHT):
                    frame = frame.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.BILINEAR)
                x = (i % columns) * FRAME_WIDTH
                y = (i // columns) * FRAME_HEIGHT
                sprite.paste(frame, (x, y))

        tmp_sprite = sprite_path_out.with_suffix(".tmp.jpg")
        sprite.save(tmp_sprite, format="JPEG", quality=74, optimize=False, progressive=False, subsampling=2)
        os.replace(tmp_sprite, sprite_path_out)

    meta = {
        "version": STORYBOARD_VERSION,
        "quality": quality,
        "video_id": str(video_id),
        "duration": float(duration),
        "frame_count": frame_count,
        "columns": columns,
        "rows": rows,
        "frame_width": FRAME_WIDTH,
        "frame_height": FRAME_HEIGHT,
        "times": [round(times[i], 3) for i in selected_indices],
        "requested_times": [round(t, 3) for t in times],
        "selected_indices": selected_indices,
        "approximate": True,
        "time_precision": "seek_target_not_decoded_pts",
        "source": "camwhores" if str(video_id).startswith("cw_") else "archivebate",
        "created_at": revision,
        "sprite_file": sprite_path_out.name,
    }
    atomic_write_json(meta_path, meta)
    return meta


def _build_segment(video_id: str, duration: float, segment_index: int, source_url: str) -> dict:
    sprite_path_out, meta_path = _segment_paths(video_id, segment_index)
    revision = str(time.time_ns())
    sprite_path_out = sprite_path_out.with_name(f"{sprite_path_out.stem}.{revision}.jpg")

    start_time = float(segment_index) * SEGMENT_DURATION
    end_time = min(float(duration), (float(segment_index) + 1.0) * SEGMENT_DURATION)
    seg_duration = max(0.5, end_time - start_time)

    with tempfile.TemporaryDirectory(prefix=f"archivebate_seg_{segment_index}_") as tmp_dir:
        tmp = Path(tmp_dir)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        frames, decoded_times, precise_timing = _extract_segment_frames_with_times(
            ffmpeg, source_url, start_time, seg_duration, tmp
        )
        if not frames:
            raise RuntimeError(f"FFmpeg nie wygenerował poprawnych klatek dla segmentu {segment_index}")

        frame_count = len(frames)
        columns = min(SEGMENT_COLUMNS, frame_count)
        rows = int(math.ceil(frame_count / columns))

        sprite = Image.new("RGB", (columns * FRAME_WIDTH, rows * FRAME_HEIGHT), (12, 12, 12))
        for i, frame_file in enumerate(frames):
            with Image.open(frame_file) as frame:
                frame = frame.convert("RGB")
                if frame.size != (FRAME_WIDTH, FRAME_HEIGHT):
                    frame = frame.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.BILINEAR)
                x = (i % columns) * FRAME_WIDTH
                y = (i // columns) * FRAME_HEIGHT
                sprite.paste(frame, (x, y))

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
    try:
        trim_cache_directory(STORYBOARD_CACHE_DIR, max_bytes=500 * 1024 * 1024, preserve_suffixes=())
    except Exception:
        pass
    return meta


def demand(video_id: str, consumer: str, active: bool = True):
    with _state_lock:
        now = time.monotonic()
        for old_key, old_entries in list(_leases.items()):
            alive = any(expiry > now for expiry in old_entries.values())
            pending = any(
                key.startswith(old_key + ":")
                and (value.get("status") == "building" or value.get("upgrade_status") == "queued")
                for key, value in _states.items()
            )
            if not alive and not pending:
                _leases.pop(old_key, None)
        if _key(video_id) not in _leases and len(_leases) >= 256:
            raise ValueError("Too many storyboard consumers")
        entries = _leases.setdefault(_key(video_id), {})
        for token, expiry in list(entries.items()):
            if expiry < now:
                entries.pop(token, None)
        if active:
            entries[consumer] = now + 45
        else:
            entries.pop(consumer, None)


def _run_jobs():
    while True:
        job = _jobs.get()
        if len(job) >= 7 and job[5] == "segment":
            _, _, video_id, duration, source_url, _, segment_index = job
            k_seg = f"{_key(video_id)}:seg_{segment_index}"
            try:
                with _state_lock:
                    leases = _leases.get(_key(video_id))
                    if not leases or not any(expiry > time.monotonic() for expiry in leases.values()):
                        _states.pop(k_seg, None)
                        continue
                result = _build_segment(video_id, duration, segment_index, source_url)
                with _state_lock:
                    _states[k_seg] = {"status": "ready", **result}
            except Exception as exc:
                with _state_lock:
                    _states[k_seg] = {"status": "error", "error": str(exc), "finished_at": time.time()}
            finally:
                _jobs.task_done()
            continue

        _, _, video_id, duration, source_url, quality = job
        k = f"{_key(video_id)}:{round(duration,3)}"
        try:
            with _state_lock:
                leases = _leases.get(_key(video_id))
                if not leases or not any(expiry > time.monotonic() for expiry in leases.values()):
                    if quality == "full":
                        _states[k]["upgrade_status"] = "idle"
                    else:
                        _states.pop(k, None)
                    continue
            result = _build_variant(video_id, duration, source_url, quality)
            with _state_lock:
                _states[k] = {
                    "status": "ready",
                    **result,
                    "upgrade_status": "ready" if quality == "full" else "queued",
                }
                if quality == "quick":
                    if not _jobs.full():
                        _jobs.put_nowait((1, next(_sequence), video_id, duration, source_url, "full"))
                    else:
                        _states[k]["upgrade_status"] = "error"
        except Exception as exc:
            with _state_lock:
                if quality == "full":
                    _states[k].update(upgrade_status="error", upgrade_error=str(exc))
                else:
                    _states[k] = {"status": "error", "error": str(exc), "finished_at": time.time()}
        finally:
            _jobs.task_done()


def start(video_id: str, duration: float, source_url: str, force: bool = False) -> dict:
    global _worker_started
    duration = float(duration or 0)
    if not video_id or duration <= 0 or not math.isfinite(duration):
        return {"status": "error", "error": "Invalid ID or duration"}
    k = f"{_key(video_id)}:{round(duration,3)}"
    with _state_lock:
        current = _states.get(k, {})
        if current.get("status") == "building" or current.get("upgrade_status") == "queued":
            return dict(current)
        full = _cached_variant(video_id, duration, "full")
        if full and not force:
            return {"status": "ready", **full, "upgrade_status": "ready"}
        quick = _cached_variant(video_id, duration, "quick")
        if current.get("status") == "error" and not force and time.time() - current.get("finished_at", 0) < 30:
            return dict(current)
        if _jobs.full():
            return {"status": "error", "error": "Storyboard queue is full"}
        quality = "full" if quick and not force else "quick"
        state = (
            {"status": "ready", **quick, "upgrade_status": "queued"}
            if quality == "full"
            else {"status": "building", "stage": "quick"}
        )
        for old_key in list(_states):
            old = _states[old_key]
            if len(_states) < 256:
                break
            if old.get("status") != "building" and old.get("upgrade_status") != "queued":
                del _states[old_key]
        _states[k] = state
        _jobs.put_nowait((0 if quality == "quick" else 1, next(_sequence), video_id, duration, source_url, quality))
        if not _worker_started:
            _worker_started = True
            threading.Thread(target=_run_jobs, daemon=True, name="storyboard-worker").start()
        return dict(state)


def start_segment(
    video_id: str, duration: float, segment_index: int, source_url: str, force: bool = False, priority: int = 0
) -> dict:
    global _worker_started
    duration = float(duration or 0)
    segment_index = int(segment_index)
    if not video_id or duration <= 0 or not math.isfinite(duration) or segment_index < 0:
        return {"status": "error", "error": "Invalid ID, duration, or segment"}
    k_seg = f"{_key(video_id)}:seg_{segment_index}"
    with _state_lock:
        current = _states.get(k_seg, {})
        if current.get("status") == "building":
            return dict(current)
        cached = _cached_segment(video_id, duration, segment_index)
        if cached and not force:
            return {"status": "ready", **cached}
        if current.get("status") == "error" and not force and time.time() - current.get("finished_at", 0) < 30:
            return dict(current)
        if _jobs.full():
            return {"status": "error", "error": "Storyboard queue is full"}
        state = {"status": "building", "type": "segment", "segment_index": segment_index}
        _states[k_seg] = state
        _jobs.put_nowait((priority, next(_sequence), video_id, duration, source_url, "segment", segment_index))
        if not _worker_started:
            _worker_started = True
            threading.Thread(target=_run_jobs, daemon=True, name="storyboard-worker").start()
        return dict(state)
