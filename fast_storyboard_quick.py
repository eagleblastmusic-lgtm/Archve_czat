"""V4.3 coarse storyboard accelerator.

The precise 1-fps/30s segment path remains untouched.  This module only replaces
QUICK storyboard generation with a low-resolution, persistent sprite built from
parallel fast keyframe seeks.  It is installed before ``main`` imports the
storyboard entry points, so browser and desktop use the same implementation.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Empty
import math
import os
import tempfile
import threading
import time

import imageio_ffmpeg
from PIL import Image

import storyboard_service as _sb

QUICK_PARALLELISM = 4
QUICK_FAST_TIMEOUT = 7
QUICK_ACCURATE_TIMEOUT = 9

_installed = False
_metrics_lock = threading.Lock()
_quick_build_ms = []
_quick_fast_hits = 0
_quick_retry_hits = 0

_original_build_variant = _sb._build_variant


def _remember_ms(value: float) -> None:
    with _metrics_lock:
        _quick_build_ms.append(max(0.0, float(value)))
        if len(_quick_build_ms) > 64:
            del _quick_build_ms[:-64]


def _percentile(values, p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(len(ordered) * p) - 1)))
    return round(float(ordered[idx]), 2)


def runtime_stats() -> dict:
    with _metrics_lock:
        samples = list(_quick_build_ms)
        fast_hits = int(_quick_fast_hits)
        retry_hits = int(_quick_retry_hits)
    return {
        "installed": bool(_installed),
        "parallelism": QUICK_PARALLELISM,
        "quick_build_p50_ms": _percentile(samples, 0.50),
        "quick_build_p95_ms": _percentile(samples, 0.95),
        "quick_build_samples": len(samples),
        "fast_keyframe_hits": fast_hits,
        "accurate_retry_hits": retry_hits,
    }


def _run_one(
    ffmpeg: str,
    source_url: str,
    target: float,
    output_path: Path,
    *,
    video_id: str,
    process_key: str,
    fast: bool,
) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    # QUICK is intentionally approximate.  Fast keyframe-only seeking avoids
    # decoding long GOPs simply to paint a 160x90 hover thumbnail.
    cmd += ["-ss", f"{target:.3f}"]
    if fast:
        cmd += ["-noaccurate_seek", "-skip_frame", "nokey"]
    cmd += [
        "-i", source_url,
        "-map", "0:v:0",
        "-frames:v", "1",
        "-an", "-sn", "-dn",
        "-threads", "1",
        "-vf",
        f"scale={_sb.FRAME_WIDTH}:{_sb.FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={_sb.FRAME_WIDTH}:{_sb.FRAME_HEIGHT}",
        "-q:v", "8",
        "-y", str(output_path),
    ]
    timeout = QUICK_FAST_TIMEOUT if fast else QUICK_ACCURATE_TIMEOUT
    try:
        returncode, _stderr, cancelled = _sb._run_cancellable_process(
            cmd,
            timeout=timeout,
            process_key=process_key,
            text=False,
            cancel_check=lambda: _sb._worker_stop.is_set() or not _sb._has_live_lease(video_id),
            video_id=video_id,
            kind="quick",
            segment_index=None,
            priority=3,
        )
    except Exception:
        return False
    return (
        not cancelled
        and returncode == 0
        and output_path.exists()
        and output_path.stat().st_size > 350
        and _sb._valid_segment_frame(output_path)
    )


def _extract_quick_frame(ffmpeg: str, source_url: str, video_id: str, revision: str, index: int, target: float, out: Path):
    global _quick_fast_hits, _quick_retry_hits
    fast_key = f"{_sb._key(video_id)}:quick-fast:{index}:{revision}"
    if _run_one(ffmpeg, source_url, target, out, video_id=video_id, process_key=fast_key, fast=True):
        with _metrics_lock:
            _quick_fast_hits += 1
        return index, out
    if not _sb._has_live_lease(video_id):
        return index, None
    retry_key = f"{_sb._key(video_id)}:quick-retry:{index}:{revision}"
    if _run_one(ffmpeg, source_url, target, out, video_id=video_id, process_key=retry_key, fast=False):
        with _metrics_lock:
            _quick_retry_hits += 1
        return index, out
    return index, None


def _build_variant_parallel(video_id: str, duration: float, source_url: str, quality: str) -> dict:
    if quality != "quick":
        return _original_build_variant(video_id, duration, source_url, quality)

    started = time.monotonic()
    sprite_base, meta_path = _sb._paths(video_id, quality)
    revision = str(time.time_ns())
    sprite_path_out = sprite_base.with_name(f"{sprite_base.stem}.{revision}.jpg")
    frame_count = int(_sb.QUICK_FRAMES)
    requested_times = [
        min(max(0.05, duration * ((i + 0.5) / frame_count)), max(0.05, duration - 0.12))
        for i in range(frame_count)
    ]
    successful = {}

    with tempfile.TemporaryDirectory(prefix="archivebate_storyboard_quick_parallel_") as tmp_dir:
        tmp = Path(tmp_dir)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        max_workers = max(1, min(QUICK_PARALLELISM, frame_count))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="storyboard-quick") as pool:
            futures = []
            for i, target in enumerate(requested_times):
                out = tmp / f"frame_{i:03d}.jpg"
                futures.append(pool.submit(
                    _extract_quick_frame,
                    ffmpeg, source_url, video_id, revision, i, target, out,
                ))
            for future in as_completed(futures):
                try:
                    index, path = future.result()
                except Exception:
                    continue
                if path is not None:
                    successful[index] = path

        if len(successful) < 4:
            raise RuntimeError(f"Parallel QUICK prepared only {len(successful)}/{frame_count} frames")

        columns = int(_sb.QUICK_COLUMNS)
        rows = int(math.ceil(frame_count / columns))
        sprite = Image.new("RGB", (columns * _sb.FRAME_WIDTH, rows * _sb.FRAME_HEIGHT), (12, 12, 12))
        selected_indices = [
            i if i in successful else min(successful, key=lambda key: abs(key - i))
            for i in range(frame_count)
        ]
        for i, source_idx in enumerate(selected_indices):
            with Image.open(successful[source_idx]) as frame:
                frame = frame.convert("RGB")
                if frame.size != (_sb.FRAME_WIDTH, _sb.FRAME_HEIGHT):
                    frame = frame.resize((_sb.FRAME_WIDTH, _sb.FRAME_HEIGHT), Image.Resampling.BILINEAR)
                sprite.paste(frame, ((i % columns) * _sb.FRAME_WIDTH, (i // columns) * _sb.FRAME_HEIGHT))

        tmp_sprite = sprite_path_out.with_suffix(".tmp.jpg")
        sprite.save(tmp_sprite, format="JPEG", quality=70, optimize=False, progressive=False, subsampling=2)
        os.replace(tmp_sprite, sprite_path_out)

    meta = {
        "version": _sb.STORYBOARD_VERSION,
        "quality": "quick",
        "video_id": str(video_id),
        "duration": float(duration),
        "frame_count": frame_count,
        "columns": int(_sb.QUICK_COLUMNS),
        "rows": int(math.ceil(frame_count / _sb.QUICK_COLUMNS)),
        "frame_width": _sb.FRAME_WIDTH,
        "frame_height": _sb.FRAME_HEIGHT,
        "times": [round(requested_times[i], 3) for i in selected_indices],
        "requested_times": [round(value, 3) for value in requested_times],
        "selected_indices": selected_indices,
        "approximate": True,
        "time_precision": "parallel_fast_keyframe_seek",
        "created_at": revision,
        "sprite_file": sprite_path_out.name,
    }
    _sb.atomic_write_json(meta_path, meta)
    try:
        _sb.trim_cache_directory(_sb.STORYBOARD_CACHE_DIR, max_bytes=500 * 1024 * 1024, preserve_suffixes=())
    except Exception:
        pass
    _remember_ms((time.monotonic() - started) * 1000.0)
    return meta


def _preempt_segments_only(video_id: str, segment_index: int) -> None:
    """Exact hover may preempt stale exact work, but not the one-time coarse sprite."""
    video_key = _sb._key(video_id)
    victims = []
    with _sb._active_process_lock:
        for process_key, info in list(_sb._active_processes.items()):
            if info.get("video_key") != video_key:
                continue
            if info.get("kind") == "quick":
                continue
            if info.get("kind") == "segment" and int(info.get("segment", -1)) == int(segment_index):
                continue
            proc = info.get("proc")
            if proc is not None and proc.poll() is None:
                _sb._preempted_processes.add(process_key)
                victims.append(proc)
        if victims:
            _sb._preempted_count += len(victims)
    for proc in victims:
        try:
            proc.terminate()
        except Exception:
            pass


def _run_jobs_v43():
    """Baseline scheduler with one change: QUICK is allowed to finish beside exact work."""
    while not _sb._worker_stop.is_set():
        try:
            job = _sb._jobs.get(timeout=0.20)
        except Empty:
            continue
        try:
            if _sb._worker_stop.is_set():
                continue
            priority = int(job[0])
            queued_at = float(job[2])
            _sb._remember_metric(_sb._recent_queue_wait_ms, (time.monotonic() - queued_at) * 1000.0)
            video_id = job[3]
            duration = float(job[4])
            source_url = job[5]
            kind = job[6]

            if kind == "segment":
                segment_index = int(job[7])
                generation = int(job[8])
                state_key = _sb._segment_state_key(video_id, segment_index)
                if not _sb._has_live_lease(video_id) or _sb._job_is_superseded(video_id, segment_index, generation, priority):
                    with _sb._state_changed:
                        if (_sb._states.get(state_key) or {}).get("status") == "building":
                            _sb._states.pop(state_key, None)
                        _sb._state_changed.notify_all()
                    continue
                try:
                    result = _sb._build_segment(video_id, duration, segment_index, source_url, priority=priority)
                    with _sb._state_changed:
                        _sb._states[state_key] = {"status": "ready", **result}
                        _sb._state_changed.notify_all()
                    if priority <= 0:
                        _sb._maybe_enqueue_directional_prefetch(video_id, duration, segment_index, source_url, generation)
                except Exception as exc:
                    superseded = _sb._job_is_superseded(video_id, segment_index, generation, priority)
                    if superseded or not _sb._has_live_lease(video_id):
                        with _sb._state_changed:
                            _sb._states.pop(state_key, None)
                            _sb._state_changed.notify_all()
                    else:
                        with _sb._state_changed:
                            _sb._states[state_key] = {"status": "error", "error": str(exc), "finished_at": time.time()}
                            _sb._state_changed.notify_all()
                continue

            quality = str(job[7])
            state_key = _sb._state_key(video_id, duration)
            if not _sb._has_live_lease(video_id):
                with _sb._state_changed:
                    _sb._states.pop(state_key, None)
                    _sb._state_changed.notify_all()
                continue
            try:
                result = _sb._build_variant(video_id, duration, source_url, quality)
                with _sb._state_changed:
                    _sb._states[state_key] = {"status": "ready", **result, "upgrade_status": "disabled"}
                    _sb._state_changed.notify_all()
            except Exception as exc:
                if not _sb._has_live_lease(video_id):
                    with _sb._state_changed:
                        _sb._states.pop(state_key, None)
                        _sb._state_changed.notify_all()
                else:
                    with _sb._state_changed:
                        _sb._states[state_key] = {
                            "status": "error",
                            "error": str(exc),
                            "finished_at": time.time(),
                            "upgrade_status": "disabled",
                        }
                        _sb._state_changed.notify_all()
        finally:
            _sb._jobs.task_done()


def install() -> None:
    global _installed
    if _installed:
        return
    _sb._build_variant = _build_variant_parallel
    _sb._preempt_active_for_target = _preempt_segments_only
    _sb._run_jobs = _run_jobs_v43
    _sb._v43_parallel_quick_installed = True
    _installed = True
