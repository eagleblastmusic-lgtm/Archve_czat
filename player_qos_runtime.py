"""V4.5.2 primary-playback QoS runtime layer.

The stable V4.3 scheduler remains the core implementation. This module installs
small, explicit runtime guards around its process launcher and cancellation
boundaries so primary playback always outranks storyboard generation.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Query, Request

import storyboard_service as _storyboard

STALE_AFTER_SECONDS = 5.0
FFMPEG_MAX_CONCURRENT = 2
PLAYBACK_CRITICAL_BUFFER_SECONDS = 2.0
PLAYBACK_QUICK_BUFFER_SECONDS = 5.0
PLAYBACK_EXACT_BUFFER_SECONDS = 3.0
PLAYBACK_BACKGROUND_BUFFER_SECONDS = 8.0
SOFT_CANCEL_SECONDS = 1.25

_lock = threading.RLock()
_state: Dict[str, Any] = {
    "video_id": "",
    "active": False,
    "paused": True,
    "seeking": False,
    "ready_state": 0,
    "buffered_seconds": 0.0,
    "is_busy": False,
    "reason": "startup",
    "updated_monotonic": 0.0,
}
_installed = False
_scheduler_installed = False
_original_start_storyboard = None
_original_start_segment = None
_original_run_process = None
_original_preempt_target = None
_original_cancel_all = None
_original_job_superseded = None
_original_demand = None
_original_runtime_stats = None
_original_quick_stats = None
_original_quick_reserve = None

_ffmpeg_slots = threading.BoundedSemaphore(FFMPEG_MAX_CONCURRENT)
_playback_ffmpeg_slot = threading.BoundedSemaphore(1)
_qos_metrics_lock = threading.Lock()
_qos_wait_count = 0
_qos_preemptions = 0
_hard_cancel_count = 0
_hard_cancel_flags: Dict[str, int] = {}
_active_generations: Dict[str, int] = {}
_soft_cancel_until: Dict[str, float] = {}
_v2_active_processes: Dict[str, dict] = {}
_v2_active_lock = threading.Lock()


def _key(video_id: Optional[str], source: Optional[str] = None) -> str:
    if not video_id:
        return ""
    try:
        from timeline_storyboard import resolve_video_key
        return resolve_video_key(video_id, source=source).as_string()
    except Exception:
        try:
            return str(_storyboard._key(video_id))
        except Exception:
            return str(video_id)


def _clean_video_id(main_module, value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(main_module._resolve_clean_video_id(value) or "")
    except Exception:
        return str(value).strip()


def snapshot() -> dict:
    """Return a scheduler-safe, freshness-aware playback snapshot."""
    now = time.monotonic()
    with _lock:
        data = dict(_state)
    updated = float(data.pop("updated_monotonic", 0.0) or 0.0)
    age = max(0.0, now - updated) if updated else 0.0
    if not updated or age > STALE_AFTER_SECONDS:
        return {
            "video_id": str(data.get("video_id") or ""),
            "active": False,
            "paused": True,
            "seeking": False,
            "ready_state": 0,
            "buffered_seconds": 0.0,
            "is_busy": False,
            "reason": "stale",
            "age_seconds": age,
        }
    data["age_seconds"] = age
    return data


def configure_playback_qos(provider) -> None:
    global _playback_qos_provider
    _playback_qos_provider = provider


_playback_qos_provider = snapshot


def _qos_snapshot() -> dict:
    provider = _playback_qos_provider
    try:
        value = dict(provider() or {})
    except Exception:
        value = {}
    return {
        "active": bool(value.get("active")),
        "paused": bool(value.get("paused", True)),
        "seeking": bool(value.get("seeking")),
        "ready_state": int(value.get("ready_state") or 0),
        "buffered_seconds": max(0.0, float(value.get("buffered_seconds") or 0.0)),
        "is_busy": bool(value.get("is_busy")),
        "age_seconds": max(0.0, float(value.get("age_seconds") or 0.0)),
    }


def _qos_blocks_new_process(kind: str, priority: int) -> bool:
    qos = _qos_snapshot()
    if not qos["active"] or qos["paused"]:
        return False
    buffered = qos["buffered_seconds"]
    if qos["seeking"] or qos["ready_state"] < 2 or buffered < PLAYBACK_CRITICAL_BUFFER_SECONDS:
        return True
    if str(kind) == "quick" and buffered < PLAYBACK_QUICK_BUFFER_SECONDS:
        return True
    if str(kind) == "segment" and int(priority) <= 0 and buffered < PLAYBACK_EXACT_BUFFER_SECONDS:
        return True
    if int(priority) > 0 and buffered < PLAYBACK_BACKGROUND_BUFFER_SECONDS:
        return True
    return False


def _hard_cancel_requested(video_id: Optional[str], source: Optional[str] = None, generation: Optional[int] = None) -> bool:
    key = _key(video_id, source=source)
    if not key:
        return False
    with _lock:
        if key not in _hard_cancel_flags:
            return False
        cancelled_gen = _hard_cancel_flags.get(key, 0)
        active_gen = _active_generations.get(key, 0)
        if active_gen > cancelled_gen:
            return False
        if generation is not None and int(generation) > cancelled_gen:
            return False
        return True


def _soft_cancel_requested(video_id: Optional[str], source: Optional[str] = None) -> bool:
    key = _key(video_id, source=source)
    if not key:
        return False
    now = time.monotonic()
    with _lock:
        until = float(_soft_cancel_until.get(key) or 0.0)
        if until and until <= now:
            _soft_cancel_until.pop(key, None)
            return False
        return until > now


def _clear_cancel_state(video_id: str, source: Optional[str] = None, generation: Optional[int] = None) -> None:
    key = _key(video_id, source=source)
    if not key:
        return
    with _lock:
        _hard_cancel_flags.pop(key, None)
        _soft_cancel_until.pop(key, None)
        if generation is not None:
            _active_generations[key] = max(_active_generations.get(key, 0), int(generation))
        else:
            _active_generations[key] = _active_generations.get(key, 0) + 1


def _mark_processes_preempted(
    *,
    video_id: Optional[str] = None,
    source: Optional[str] = None,
    hard: bool = False,
    generation: Optional[int] = None,
) -> int:
    """Mark + terminate matching storyboard subprocesses, including QUICK and V2."""
    global _qos_preemptions, _hard_cancel_count
    video_key = _key(video_id, source=source) if video_id else None
    victims = []
    keys = set()
    with _storyboard._active_process_lock:
        for process_key, info in list(_storyboard._active_processes.items()):
            current_key = str(info.get("video_key") or "")
            if video_key is not None and current_key != video_key:
                continue
            proc = info.get("proc")
            if proc is None or proc.poll() is not None:
                continue
            keys.add(current_key)
            _storyboard._preempted_processes.add(process_key)
            victims.append(proc)
        if victims and hasattr(_storyboard, "_preempted_count"):
            _storyboard._preempted_count += len(victims)
    with _v2_active_lock:
        for process_key, info in list(_v2_active_processes.items()):
            current_key = str(info.get("video_key") or "")
            if video_key is not None and current_key != video_key:
                continue
            proc_gen = info.get("generation")
            # Fencing check: protect processes from newer generations
            if generation is not None and proc_gen is not None and int(proc_gen) > int(generation):
                continue
            proc = info.get("proc")
            if proc is None or proc.poll() is not None:
                continue
            keys.add(current_key)
            victims.append(proc)
    now = time.monotonic()
    with _lock:
        for key in keys:
            if hard:
                _hard_cancel_flags[key] = int(generation) if generation is not None else _active_generations.get(key, 0)
            else:
                _soft_cancel_until[key] = now + SOFT_CANCEL_SECONDS
    for proc in victims:
        if proc is not None and getattr(proc, "poll", None) is not None and proc.poll() is None:
            try:
                if hasattr(proc, "terminate"):
                    proc.terminate()
                if hasattr(proc, "wait"):
                    try:
                        proc.wait(timeout=0.5)
                    except Exception:
                        if hasattr(proc, "kill"):
                            try:
                                proc.kill()
                                proc.wait(timeout=0.5)
                            except Exception:
                                pass
            except Exception:
                pass
    if victims:
        with _qos_metrics_lock:
            if hard:
                _hard_cancel_count += len(victims)
            else:
                _qos_preemptions += len(victims)
    return len(victims)


def clear_cancel_state(video_id: str, source: Optional[str] = None, generation: Optional[int] = None) -> None:
    """Clear hard/soft cancel state for video_id to allow clean regeneration upon revisit."""
    _clear_cancel_state(video_id, source=source, generation=generation)


def get_active_generation(video_id: Optional[str], source: Optional[str] = None) -> int:
    """Get authoritative active generation counter for video_id / source."""
    key = _key(video_id, source=source)
    if not key:
        return 0
    with _lock:
        return int(_active_generations.get(key, 0))


def evict_video_key(video_id: str, source: Optional[str] = None) -> None:
    """Safely remove video key state when evicted from capacity-bounded lease manager."""
    key = _key(video_id, source=source)
    if not key:
        return
    with _lock:
        _hard_cancel_flags.pop(key, None)
        _soft_cancel_until.pop(key, None)
        _active_generations.pop(key, None)


def register_v2_process(
    process_key: str,
    proc: Any,
    video_id: Optional[str],
    source: Optional[str] = None,
    generation: Optional[int] = None,
) -> None:
    """Register an active V2 FFmpeg subprocess so QoS can preempt it."""
    with _v2_active_lock:
        _v2_active_processes[str(process_key)] = {
            "proc": proc,
            "video_key": _key(video_id, source=source),
            "generation": int(generation) if generation is not None else None,
            "started_at": time.monotonic(),
        }


def unregister_v2_process(process_key: str) -> None:
    """Unregister a completed or terminated V2 FFmpeg subprocess."""
    with _v2_active_lock:
        _v2_active_processes.pop(str(process_key), None)


def wait_for_ffmpeg_budget(kind: str, priority: int, video_id: Optional[str], cancel_check=None, source: Optional[str] = None):
    """Acquire FFmpeg slot conforming to QoS limits (2 global, 1 active playback)."""
    return _wait_for_ffmpeg_budget(kind, priority, video_id, cancel_check, source=source)


def release_ffmpeg_budget(playback_serial) -> None:
    """Release acquired FFmpeg slot budget."""
    _release_ffmpeg_budget(playback_serial)


def is_hard_cancelled(video_id: Optional[str], source: Optional[str] = None, generation: Optional[int] = None) -> bool:
    return _hard_cancel_requested(video_id, source=source, generation=generation)


def is_soft_cancelled(video_id: Optional[str], source: Optional[str] = None) -> bool:
    return _soft_cancel_requested(video_id, source=source)


def protect_playback(reason: str = "playback_qos") -> int:
    """Soft emergency boundary: stop decoders but allow recovery shortly after."""
    return _mark_processes_preempted(hard=False)


def hard_cancel(
    video_id: Optional[str] = None,
    source: Optional[str] = None,
    reason: str = "client",
    clear_lease: bool = True,
    generation: Optional[int] = None,
) -> int:
    """Hard lifecycle boundary used for video switch, close and page teardown."""
    if video_id:
        key = _key(video_id, source=source)
        with _lock:
            current_active = _active_generations.get(key, 0)
            if generation is not None and current_active > int(generation):
                # Stale cancellation: newer generation has already been acquired!
                return 0
            _hard_cancel_flags[key] = int(generation) if generation is not None else current_active
        if clear_lease:
            try:
                import timeline_storyboard
                timeline_storyboard.remove_lease(video_id, source=source)
            except Exception:
                pass
    return _mark_processes_preempted(video_id=video_id, source=source, hard=True, generation=generation)


def _wait_for_ffmpeg_budget(kind: str, priority: int, video_id: Optional[str], cancel_check=None, source: Optional[str] = None):
    global _qos_wait_count
    waited = False
    while not getattr(_storyboard, "_worker_stop").is_set():
        if _hard_cancel_requested(video_id, source=source) or (cancel_check is not None and cancel_check()):
            return None
        if _qos_blocks_new_process(kind, priority):
            if not waited:
                waited = True
                with _qos_metrics_lock:
                    _qos_wait_count += 1
            time.sleep(0.05)
            continue
        if not _ffmpeg_slots.acquire(timeout=0.10):
            continue
        if _qos_blocks_new_process(kind, priority):
            _ffmpeg_slots.release()
            time.sleep(0.05)
            continue
        qos = _qos_snapshot()
        playback_serial = bool(qos.get("active") and not qos.get("paused"))
        if playback_serial and not _playback_ffmpeg_slot.acquire(timeout=0.10):
            _ffmpeg_slots.release()
            continue
        return playback_serial
    return None


def _release_ffmpeg_budget(playback_serial) -> None:
    if playback_serial is None:
        return
    if playback_serial:
        _playback_ffmpeg_slot.release()
    _ffmpeg_slots.release()


def _install_scheduler_qos(quick_module=None) -> None:
    """Install V4.5.2 guards over the stable V4.3 scheduler exactly once."""
    global _scheduler_installed
    global _original_run_process, _original_preempt_target, _original_cancel_all
    global _original_job_superseded, _original_demand, _original_runtime_stats
    global _original_quick_stats, _original_quick_reserve
    if _scheduler_installed:
        return
    if quick_module is None:
        import fast_storyboard_quick as quick_module
    _scheduler_installed = True

    _original_run_process = _storyboard._run_cancellable_process
    _original_preempt_target = _storyboard._preempt_active_for_target
    _original_cancel_all = _storyboard._cancel_all_for_video
    _original_job_superseded = _storyboard._job_is_superseded
    _original_demand = _storyboard.demand
    _original_runtime_stats = _storyboard.runtime_stats
    _original_quick_stats = quick_module.runtime_stats
    _original_quick_reserve = quick_module._reserve_quick_slot

    def run_cancellable_process(cmd, *, timeout, process_key, text=False, cancel_check=None,
                                video_id=None, kind="generic", segment_index=None, priority=0):
        def combined_cancel():
            return _hard_cancel_requested(video_id) or (cancel_check is not None and cancel_check())
        try:
            budget = _wait_for_ffmpeg_budget(kind, int(priority), video_id, combined_cancel)
        except Exception:
            budget = None
        if budget is None:
            empty = "" if text else b""
            return -15, empty, True
        try:
            return _original_run_process(
                cmd,
                timeout=timeout,
                process_key=process_key,
                text=text,
                cancel_check=combined_cancel,
                video_id=video_id,
                kind=kind,
                segment_index=segment_index,
                priority=priority,
            )
        finally:
            _release_ffmpeg_budget(budget)

    def preempt_active_for_target(video_id: str, segment_index: int) -> None:
        video_key = _storyboard._key(video_id)
        victims = []
        with _storyboard._active_process_lock:
            for process_key, info in list(_storyboard._active_processes.items()):
                if info.get("video_key") != video_key:
                    continue
                if info.get("kind") == "quick":
                    continue
                if info.get("kind") == "segment" and int(info.get("segment", -1)) == int(segment_index):
                    continue
                proc = info.get("proc")
                if proc is not None and proc.poll() is None:
                    _storyboard._preempted_processes.add(process_key)
                    victims.append(proc)
            if victims:
                _storyboard._preempted_count += len(victims)
        for proc in victims:
            try:
                proc.terminate()
            except Exception:
                pass

    def cancel_all_for_video(video_id: str) -> None:
        video_key = _storyboard._key(video_id)
        victims = []
        with _storyboard._active_process_lock:
            for process_key, info in list(_storyboard._active_processes.items()):
                if info.get("video_key") != video_key or info.get("kind") == "quick":
                    continue
                proc = info.get("proc")
                if proc is not None and proc.poll() is None:
                    _storyboard._preempted_processes.add(process_key)
                    victims.append(proc)
        for proc in victims:
            try:
                proc.terminate()
            except Exception:
                pass

    def job_is_superseded(video_id, segment_index, generation, priority):
        if _hard_cancel_requested(video_id) or _soft_cancel_requested(video_id):
            return True
        return _original_job_superseded(video_id, segment_index, generation, priority)

    def demand(video_id: str, consumer: str, active: bool = True):
        if active:
            _clear_cancel_state(video_id)
        return _original_demand(video_id, consumer, active)

    def runtime_stats():
        data = dict(_original_runtime_stats() or {})
        with _qos_metrics_lock:
            data.update({
                "ffmpeg_max_concurrent": FFMPEG_MAX_CONCURRENT,
                "playback_ffmpeg_max_concurrent": 1,
                "playback_qos": _qos_snapshot(),
                "qos_wait_count": int(_qos_wait_count),
                "qos_preemptions": int(_qos_preemptions),
                "hard_cancelled_processes": int(_hard_cancel_count),
            })
        return data

    def quick_cancel_requested(video_id: str) -> bool:
        return _storyboard._worker_stop.is_set() or _hard_cancel_requested(video_id)

    def quick_reserve(video_id: str, queued_at: float) -> bool:
        current = _storyboard._current_desired(video_id)
        if current and not quick_module._is_quick_reservation(current):
            updated = float(current.get("updated") or 0.0)
            if updated > float(queued_at) + 0.05:
                # A real/new exact hover must not destroy the persistent coarse
                # build. Preserve exact desired state and let QoS serialize FFmpeg.
                return True
        return _original_quick_reserve(video_id, queued_at)

    def quick_stats():
        data = dict(_original_quick_stats() or {})
        data["exact_preempts_quick"] = False
        data["player_qos_guard"] = True
        return data

    _storyboard._run_cancellable_process = run_cancellable_process
    _storyboard._preempt_active_for_target = preempt_active_for_target
    _storyboard._cancel_all_for_video = cancel_all_for_video
    _storyboard._job_is_superseded = job_is_superseded
    _storyboard.demand = demand
    _storyboard.runtime_stats = runtime_stats
    _storyboard.configure_playback_qos = configure_playback_qos
    _storyboard.protect_playback = protect_playback
    _storyboard.hard_cancel = hard_cancel
    _storyboard._hard_cancel_requested = _hard_cancel_requested
    _storyboard._qos_blocks_new_process = _qos_blocks_new_process
    _storyboard.FFMPEG_MAX_CONCURRENT = FFMPEG_MAX_CONCURRENT
    quick_module._cancel_requested = quick_cancel_requested
    quick_module._reserve_quick_slot = quick_reserve
    quick_module.runtime_stats = quick_stats


def update(body: dict, main_module) -> dict:
    """Update player health and soft-preempt storyboard work when critical."""
    raw_video_id = body.get("video_id") or body.get("id") or ""
    video_id = _clean_video_id(main_module, raw_video_id)
    paused = bool(body.get("paused"))
    seeking = bool(body.get("seeking"))
    try:
        ready_state = max(0, min(4, int(body.get("ready_state") or 0)))
    except Exception:
        ready_state = 0
    try:
        buffered = max(0.0, float(body.get("buffered_seconds") or 0.0))
    except Exception:
        buffered = 0.0
    is_busy = bool(body.get("is_busy"))
    reason = str(body.get("reason") or "status")[:64]
    active = bool(body.get("active", not paused)) and not paused

    with _lock:
        _state.update({
            "video_id": video_id,
            "active": active,
            "paused": paused,
            "seeking": seeking,
            "ready_state": ready_state,
            "buffered_seconds": buffered,
            "is_busy": is_busy,
            "reason": reason,
            "updated_monotonic": time.monotonic(),
        })

    critical = active and (
        seeking
        or ready_state < 2
        or buffered < PLAYBACK_CRITICAL_BUFFER_SECONDS
        or reason in {"waiting", "stalled", "seeking", "startup"}
    )
    preempted = protect_playback(reason=f"player:{reason}") if critical else 0
    result = snapshot()
    result["preempted"] = int(preempted)
    return result


def _tag_storyboard_source(source_url: str, reason: str) -> str:
    """Mark internal proxy traffic so it is never mistaken for primary playback."""
    if not source_url:
        return source_url
    try:
        parsed = urlsplit(source_url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params["owner"] = "storyboard"
        params["priority"] = "low"
        params["reason"] = reason
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment))
    except Exception:
        separator = "&" if "?" in source_url else "?"
        return f"{source_url}{separator}owner=storyboard&priority=low&reason={reason}"


def _install_source_tagging(main_module) -> None:
    """Wrap main module scheduler entrypoints without modifying main.py."""
    global _original_start_storyboard, _original_start_segment
    if getattr(main_module, "_v452_storyboard_source_tagging", False):
        return
    _original_start_storyboard = main_module.start_storyboard
    _original_start_segment = main_module.start_segment

    def start_storyboard(video_id, duration, source_url, force=False):
        return _original_start_storyboard(
            video_id, duration, _tag_storyboard_source(source_url, "quick"), force=force
        )

    def start_segment(video_id, duration, segment_index, source_url, force=False, priority=0):
        reason = "exact_hover" if int(priority) <= 0 else "exact_prewarm"
        return _original_start_segment(
            video_id,
            duration,
            segment_index,
            _tag_storyboard_source(source_url, reason),
            force=force,
            priority=priority,
        )

    main_module.start_storyboard = start_storyboard
    main_module.start_segment = start_segment
    main_module._v452_storyboard_source_tagging = True


def install(app, main_module) -> None:
    """Install scheduler QoS, source tagging and HTTP endpoints exactly once."""
    global _installed
    if _installed:
        return
    _installed = True
    import fast_storyboard_quick as quick_module
    _install_scheduler_qos(quick_module)
    configure_playback_qos(snapshot)
    _install_source_tagging(main_module)

    @app.post("/api/runtime/v452/playback/status")
    async def v452_playback_status(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Invalid playback status body")
        return {"ok": True, "qos": update(body, main_module)}

    @app.post("/api/runtime/v452/storyboard/protect")
    def v452_storyboard_protect(reason: str = Query("browser")):
        count = protect_playback(reason=f"browser:{str(reason)[:64]}")
        return {"ok": True, "preempted": int(count), "qos": snapshot()}

    @app.post("/api/runtime/v452/storyboard/cancel")
    def v452_storyboard_cancel(
        id: Optional[str] = Query(None),
        source: Optional[str] = Query(None),
        reason: str = Query("lifecycle"),
        consumer_id: Optional[str] = Query(None),
        session_id: Optional[str] = Query(None),
        epoch: Optional[int] = Query(None),
    ):
        clean_id = _clean_video_id(main_module, id) if id else ""
        if consumer_id and clean_id:
            try:
                import timeline_storyboard as ts
                was_present, remaining = ts.release_lease(
                    clean_id,
                    consumer_id=consumer_id,
                    session_id=session_id,
                    epoch=epoch,
                    source=source,
                    reason=f"browser:{str(reason)[:64]}",
                )
                return {
                    "ok": True,
                    "was_present": was_present,
                    "remaining_consumers": remaining,
                    "cancelled": 1 if (was_present and remaining == 0) else 0,
                    "video_id": clean_id,
                }
            except Exception:
                return {
                    "ok": False,
                    "was_present": False,
                    "remaining_consumers": 0,
                    "cancelled": 0,
                    "video_id": clean_id,
                }

        # If consumer_id was NOT provided:
        if clean_id:
            try:
                import timeline_storyboard as ts
                if ts.has_live_lease(clean_id, source=source):
                    # Active consumers exist! Do NOT perform global cancellation!
                    active_count = ts.get_active_consumers_count(clean_id, source=source)
                    return {
                        "ok": True,
                        "cancelled": 0,
                        "video_id": clean_id,
                        "remaining_consumers": active_count,
                    }
                ts.remove_lease(clean_id, source=source)
            except Exception:
                pass
        count = hard_cancel(clean_id or None, source=source, reason=f"browser:{str(reason)[:64]}", clear_lease=False)
        return {"ok": True, "cancelled": int(count), "video_id": clean_id, "remaining_consumers": 0}

    @app.get("/api/runtime/v452/qos")
    def v452_qos_snapshot():
        return {"installed": True, "qos": snapshot(), "storyboard": _storyboard.runtime_stats()}


def runtime_marker() -> dict:
    return {
        "player_qos_stabilization": bool(_installed),
        "storyboard_stream_owner_tagging": bool(_installed),
        "ffmpeg_max_concurrent": FFMPEG_MAX_CONCURRENT,
        "playback_ffmpeg_max_concurrent": 1,
    }
