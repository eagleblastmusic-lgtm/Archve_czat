"""V4.3 playback-safe coarse storyboard accelerator.

The precise 1-fps/30s segment path remains the authoritative hover source. This
module builds a tiny persistent coarse sprite after primary playback is healthy.
V8 coordination gives an interactive QUICK build a short reservation so stale
background exact-prewarm work cannot clear it before it starts. A real exact
hover can still replace that reservation and preempt QUICK immediately.
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

# Runtime may override these before install(). Keep the conservative defaults
# useful when this module is imported directly by tests.
QUICK_FRAME_COUNT = 4
QUICK_PARALLELISM = 2
QUICK_MIN_SUCCESS = 3
QUICK_FAST_TIMEOUT = 5
QUICK_ACCURATE_TIMEOUT = 7

_installed = False
_metrics_lock = threading.Lock()
_quick_build_ms = []
_quick_fast_hits = 0
_quick_retry_hits = 0
_quick_builds = 0
_quick_cancelled = 0
_quick_failures = 0
_quick_reservation_preemptions = 0
_quick_missing_waits = 0

_original_build_variant = _sb._build_variant


class QuickCancelled(RuntimeError):
    """Expected cancellation when playback/real exact hover takes priority."""


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


def _is_quick_reservation(value) -> bool:
    return bool(value and value.get("quick_reservation"))


def _cancel_requested(video_id: str) -> bool:
    desired = _sb._current_desired(video_id)
    # A QUICK reservation is not a competing exact target. It exists solely to
    # invalidate stale/queued background exact-prewarm work while the first
    # coarse overview gets a bounded head start. A subsequent real exact target
    # overwrites the reservation and immediately makes this return True.
    exact_target_active = bool(desired) and not _is_quick_reservation(desired)
    return (
        _sb._worker_stop.is_set()
        or not _sb._has_live_lease(video_id)
        or exact_target_active
    )


def runtime_stats() -> dict:
    with _metrics_lock:
        samples = list(_quick_build_ms)
        fast_hits = int(_quick_fast_hits)
        retry_hits = int(_quick_retry_hits)
        builds = int(_quick_builds)
        cancelled = int(_quick_cancelled)
        failures = int(_quick_failures)
        reservation_preemptions = int(_quick_reservation_preemptions)
        missing_waits = int(_quick_missing_waits)
    with _sb._active_process_lock:
        active_quick = sum(1 for info in _sb._active_processes.values() if info.get("kind") == "quick")
    with _sb._state_lock:
        reservations = sum(1 for value in _sb._desired_segments.values() if _is_quick_reservation(value))
    return {
        "installed": bool(_installed),
        "frame_count": QUICK_FRAME_COUNT,
        "parallelism": QUICK_PARALLELISM,
        "quick_build_p50_ms": _percentile(samples, 0.50),
        "quick_build_p95_ms": _percentile(samples, 0.95),
        "quick_build_samples": len(samples),
        "quick_builds": builds,
        "quick_cancelled": cancelled,
        "quick_failures": failures,
        "active_quick_processes": active_quick,
        "fast_keyframe_hits": fast_hits,
        "accurate_retry_hits": retry_hits,
        "exact_preempts_quick": True,
        "quick_reservations": reservations,
        "stale_exact_preemptions": reservation_preemptions,
        "missing_status_waits": missing_waits,
    }


def wait_status(video_id: str, duration: float, timeout: float = 1.2) -> dict:
    """Long-poll QUICK state without a tight loop when state temporarily vanishes.

    V7 live telemetry exposed 63 status requests during one failed build. The
    previous long-poll only waited while state was exactly `building`; if stale
    exact-prewarm work cleared that state, every request returned `missing`
    immediately and the browser spun. Missing now also waits for one state-change
    window before returning.
    """
    global _quick_missing_waits
    duration = float(duration or 0)
    cached = _sb._cached_variant(video_id, duration, "quick")
    if cached:
        return {"status": "ready", **cached, "upgrade_status": "disabled"}

    bounded = max(0.0, min(float(timeout), 2.0))
    key = _sb._state_key(video_id, duration)
    with _sb._state_changed:
        state = dict(_sb._states.get(key) or {})
        if state.get("status") == "building" and bounded > 0:
            _sb._state_changed.wait_for(
                lambda: (_sb._states.get(key) or {}).get("status") != "building",
                timeout=bounded,
            )
            state = dict(_sb._states.get(key) or {})
        elif not state and bounded > 0:
            with _metrics_lock:
                _quick_missing_waits += 1
            _sb._state_changed.wait(timeout=bounded)
            state = dict(_sb._states.get(key) or {})

    cached = _sb._cached_variant(video_id, duration, "quick")
    if cached:
        return {"status": "ready", **cached, "upgrade_status": "disabled"}
    return state or {"status": "missing", "upgrade_status": "disabled"}


def _reserve_quick_slot(video_id: str, queued_at: float) -> bool:
    """Reserve one cold-coarse slot and retire only stale background exact work.

    The V7 frontend defers a *real* exact hover before this QUICK job is queued.
    Therefore an older desired target is background playback prewarm. A desired
    target created after this QUICK job was queued is treated as a real/new exact
    request and QUICK yields instead of overwriting it.
    """
    global _quick_reservation_preemptions
    video_key = _sb._key(video_id)
    now = time.monotonic()
    with _sb._state_changed:
        current = dict(_sb._desired_segments.get(video_key) or {})
        if current and not _is_quick_reservation(current):
            updated = float(current.get("updated") or 0.0)
            if updated > float(queued_at) + 0.05:
                return False
        generation = int(current.get("generation") or 0) + 1
        _sb._desired_segments[video_key] = {
            "segment": -1,
            "direction": 0,
            "generation": generation,
            "updated": now,
            "quick_reservation": True,
        }
        _sb._state_changed.notify_all()

    victims = []
    with _sb._active_process_lock:
        for process_key, info in list(_sb._active_processes.items()):
            if info.get("video_key") != video_key or info.get("kind") != "segment":
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
    if victims:
        with _metrics_lock:
            _quick_reservation_preemptions += len(victims)
    return True


def _release_quick_slot(video_id: str) -> None:
    video_key = _sb._key(video_id)
    with _sb._state_changed:
        current = _sb._desired_segments.get(video_key)
        if _is_quick_reservation(current):
            _sb._desired_segments.pop(video_key, None)
            _sb._state_changed.notify_all()


def _run_one(
    ffmpeg: str,
    source_url: str,
    target: float,
    output_path: Path,
    *,
    video_id: str,
    process_key: str,
    fast: bool,
) -> str:
    if _cancel_requested(video_id):
        return "cancelled"

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    cmd += ["-ss", f"{target:.3f}"]
    if fast:
        # QUICK is intentionally approximate. Keyframe-only input seeking keeps
        # each request small and avoids decoding long GOPs for a 160x90 image.
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
            cancel_check=lambda: _cancel_requested(video_id),
            video_id=video_id,
            kind="quick",
            segment_index=None,
            priority=3,
        )
    except Exception:
        return "failed"

    if cancelled or _cancel_requested(video_id):
        return "cancelled"
    if (
        returncode == 0
        and output_path.exists()
        and output_path.stat().st_size > 350
        and _sb._valid_segment_frame(output_path)
    ):
        return "ok"
    return "failed"


def _fast_frame(ffmpeg, source_url, video_id, revision, index, target, out):
    global _quick_fast_hits
    status = _run_one(
        ffmpeg,
        source_url,
        target,
        out,
        video_id=video_id,
        process_key=f"{_sb._key(video_id)}:quick-fast:{index}:{revision}",
        fast=True,
    )
    if status == "ok":
        with _metrics_lock:
            _quick_fast_hits += 1
    return index, out if status == "ok" else None, status


def _accurate_retry(ffmpeg, source_url, video_id, revision, index, target, out):
    global _quick_retry_hits
    status = _run_one(
        ffmpeg,
        source_url,
        target,
        out,
        video_id=video_id,
        process_key=f"{_sb._key(video_id)}:quick-retry:{index}:{revision}",
        fast=False,
    )
    if status == "ok":
        with _metrics_lock:
            _quick_retry_hits += 1
    return index, out if status == "ok" else None, status


def _build_variant_playback_safe(video_id: str, duration: float, source_url: str, quality: str) -> dict:
    global _quick_builds
    if quality != "quick":
        return _original_build_variant(video_id, duration, source_url, quality)
    if _cancel_requested(video_id):
        raise QuickCancelled("QUICK skipped because exact/playback work has priority")

    with _metrics_lock:
        _quick_builds += 1
    started = time.monotonic()
    sprite_base, meta_path = _sb._paths(video_id, quality)
    revision = str(time.time_ns())
    sprite_path_out = sprite_base.with_name(f"{sprite_base.stem}.{revision}.jpg")
    frame_count = QUICK_FRAME_COUNT
    requested_times = [
        min(max(0.05, duration * ((i + 0.5) / frame_count)), max(0.05, duration - 0.12))
        for i in range(frame_count)
    ]
    successful = {}

    with tempfile.TemporaryDirectory(prefix="archivebate_storyboard_quick_safe_") as tmp_dir:
        tmp = Path(tmp_dir)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with ThreadPoolExecutor(
            max_workers=min(QUICK_PARALLELISM, frame_count),
            thread_name_prefix="storyboard-quick",
        ) as pool:
            futures = []
            for i, target in enumerate(requested_times):
                out = tmp / f"frame_{i:03d}.jpg"
                futures.append(pool.submit(
                    _fast_frame,
                    ffmpeg, source_url, video_id, revision, i, target, out,
                ))
            cancelled = False
            for future in as_completed(futures):
                try:
                    index, path, status = future.result()
                except Exception:
                    continue
                if status == "cancelled":
                    cancelled = True
                if path is not None:
                    successful[index] = path
            if cancelled or _cancel_requested(video_id):
                for future in futures:
                    future.cancel()
                raise QuickCancelled("QUICK preempted by real exact hover")

        # Accurate retry is serial and stops as soon as there are enough unique
        # frames. This avoids multiplying upstream traffic during playback.
        if len(successful) < QUICK_MIN_SUCCESS:
            for i, target in enumerate(requested_times):
                if i in successful:
                    continue
                if _cancel_requested(video_id):
                    raise QuickCancelled("QUICK retry preempted by real exact hover")
                out = tmp / f"frame_{i:03d}.jpg"
                index, path, status = _accurate_retry(
                    ffmpeg, source_url, video_id, revision, i, target, out,
                )
                if status == "cancelled":
                    raise QuickCancelled("QUICK accurate retry cancelled")
                if path is not None:
                    successful[index] = path
                if len(successful) >= QUICK_MIN_SUCCESS:
                    break

        if len(successful) < QUICK_MIN_SUCCESS:
            raise RuntimeError(
                f"Playback-safe QUICK prepared only {len(successful)}/{frame_count} frames"
            )

        columns = frame_count
        rows = 1
        sprite = Image.new(
            "RGB",
            (columns * _sb.FRAME_WIDTH, rows * _sb.FRAME_HEIGHT),
            (12, 12, 12),
        )
        selected_indices = [
            i if i in successful else min(successful, key=lambda key: abs(key - i))
            for i in range(frame_count)
        ]
        for i, source_idx in enumerate(selected_indices):
            with Image.open(successful[source_idx]) as frame:
                frame = frame.convert("RGB")
                if frame.size != (_sb.FRAME_WIDTH, _sb.FRAME_HEIGHT):
                    frame = frame.resize(
                        (_sb.FRAME_WIDTH, _sb.FRAME_HEIGHT),
                        Image.Resampling.BILINEAR,
                    )
                sprite.paste(frame, (i * _sb.FRAME_WIDTH, 0))

        tmp_sprite = sprite_path_out.with_suffix(".tmp.jpg")
        sprite.save(
            tmp_sprite,
            format="JPEG",
            quality=68,
            optimize=False,
            progressive=False,
            subsampling=2,
        )
        os.replace(tmp_sprite, sprite_path_out)

    meta = {
        "version": _sb.STORYBOARD_VERSION,
        "quality": "quick",
        "video_id": str(video_id),
        "duration": float(duration),
        "frame_count": frame_count,
        "columns": frame_count,
        "rows": 1,
        "frame_width": _sb.FRAME_WIDTH,
        "frame_height": _sb.FRAME_HEIGHT,
        "times": [round(requested_times[i], 3) for i in selected_indices],
        "requested_times": [round(value, 3) for value in requested_times],
        "selected_indices": selected_indices,
        "approximate": True,
        "time_precision": "playback_safe_keyframe_seek",
        "created_at": revision,
        "sprite_file": sprite_path_out.name,
    }
    _sb.atomic_write_json(meta_path, meta)
    try:
        _sb.trim_cache_directory(
            _sb.STORYBOARD_CACHE_DIR,
            max_bytes=500 * 1024 * 1024,
            preserve_suffixes=(),
        )
    except Exception:
        pass
    _remember_ms((time.monotonic() - started) * 1000.0)
    return meta


def _clear_state(state_key: str) -> None:
    with _sb._state_changed:
        _sb._states.pop(state_key, None)
        _sb._state_changed.notify_all()


def _run_jobs_v43():
    """Baseline scheduler plus a bounded QUICK reservation.

    A stale playback-prewarm exact target may exist before the first real hover.
    When the interactive QUICK job was queued first, reserve the video briefly,
    invalidate those stale exact jobs, and build the low-resolution overview.
    Any *new* exact target overwrites the reservation and preempts QUICK.
    """
    global _quick_cancelled, _quick_failures
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
            _sb._remember_metric(
                _sb._recent_queue_wait_ms,
                (time.monotonic() - queued_at) * 1000.0,
            )
            video_id = job[3]
            duration = float(job[4])
            source_url = job[5]
            kind = job[6]

            if kind == "segment":
                segment_index = int(job[7])
                generation = int(job[8])
                state_key = _sb._segment_state_key(video_id, segment_index)
                if (
                    not _sb._has_live_lease(video_id)
                    or _sb._job_is_superseded(video_id, segment_index, generation, priority)
                ):
                    with _sb._state_changed:
                        if (_sb._states.get(state_key) or {}).get("status") == "building":
                            _sb._states.pop(state_key, None)
                        _sb._state_changed.notify_all()
                    continue
                try:
                    result = _sb._build_segment(
                        video_id,
                        duration,
                        segment_index,
                        source_url,
                        priority=priority,
                    )
                    with _sb._state_changed:
                        _sb._states[state_key] = {"status": "ready", **result}
                        _sb._state_changed.notify_all()
                    if priority <= 0:
                        _sb._maybe_enqueue_directional_prefetch(
                            video_id,
                            duration,
                            segment_index,
                            source_url,
                            generation,
                        )
                except Exception as exc:
                    superseded = _sb._job_is_superseded(
                        video_id, segment_index, generation, priority
                    )
                    if superseded or not _sb._has_live_lease(video_id):
                        _clear_state(state_key)
                    else:
                        with _sb._state_changed:
                            _sb._states[state_key] = {
                                "status": "error",
                                "error": str(exc),
                                "finished_at": time.time(),
                            }
                            _sb._state_changed.notify_all()
                continue

            quality = str(job[7])
            state_key = _sb._state_key(video_id, duration)
            if not _sb._has_live_lease(video_id):
                _clear_state(state_key)
                continue

            reserved = _reserve_quick_slot(video_id, queued_at)
            if not reserved:
                _clear_state(state_key)
                continue
            try:
                result = _sb._build_variant(video_id, duration, source_url, quality)
                with _sb._state_changed:
                    _sb._states[state_key] = {
                        "status": "ready",
                        **result,
                        "upgrade_status": "disabled",
                    }
                    _sb._state_changed.notify_all()
            except QuickCancelled:
                with _metrics_lock:
                    _quick_cancelled += 1
                _clear_state(state_key)
            except Exception as exc:
                with _metrics_lock:
                    _quick_failures += 1
                desired = _sb._current_desired(video_id)
                if not _sb._has_live_lease(video_id) or (desired and not _is_quick_reservation(desired)):
                    _clear_state(state_key)
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
                _release_quick_slot(video_id)
        finally:
            _sb._jobs.task_done()


def install() -> None:
    global _installed
    if _installed:
        return
    _sb._build_variant = _build_variant_playback_safe
    _sb._run_jobs = _run_jobs_v43
    _sb._v43_parallel_quick_installed = True
    _sb._v43_playback_safe_quick_installed = True
    _sb._v43_quick_reservation_installed = True
    _installed = True
