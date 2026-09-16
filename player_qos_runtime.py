"""V4.5.2 primary-playback QoS arbiter.

This module is installed by runtime_app.py and deliberately keeps the playback
stabilisation layer outside the V4.2 core. It owns one small source of truth for
player health, wires it into storyboard_service, and exposes mutation endpoints
used by the browser coordinator.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Query, Request

import storyboard_service as _storyboard

STALE_AFTER_SECONDS = 5.0
CRITICAL_BUFFER_SECONDS = 2.0

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
_original_start_storyboard = None
_original_start_segment = None


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
    age = max(0.0, now - updated) if updated else float("inf")
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
        _state.update(
            {
                "video_id": video_id,
                "active": active,
                "paused": paused,
                "seeking": seeking,
                "ready_state": ready_state,
                "buffered_seconds": buffered,
                "is_busy": is_busy,
                "reason": reason,
                "updated_monotonic": time.monotonic(),
            }
        )

    critical = active and (
        seeking
        or ready_state < 2
        or buffered < CRITICAL_BUFFER_SECONDS
        or reason in {"waiting", "stalled", "seeking", "startup"}
    )
    preempted = _storyboard.protect_playback(reason=f"player:{reason}") if critical else 0
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
            video_id,
            duration,
            _tag_storyboard_source(source_url, "quick"),
            force=force,
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
    """Install routes + scheduler integration exactly once."""
    global _installed
    if _installed:
        return
    _installed = True

    _storyboard.configure_playback_qos(snapshot)
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
        count = _storyboard.protect_playback(reason=f"browser:{str(reason)[:64]}")
        return {"ok": True, "preempted": int(count), "qos": snapshot()}

    @app.post("/api/runtime/v452/storyboard/cancel")
    def v452_storyboard_cancel(
        id: Optional[str] = Query(None),
        reason: str = Query("lifecycle"),
    ):
        clean_id = _clean_video_id(main_module, id) if id else ""
        count = _storyboard.hard_cancel(clean_id or None, reason=f"browser:{str(reason)[:64]}")
        return {"ok": True, "cancelled": int(count), "video_id": clean_id}

    @app.get("/api/runtime/v452/qos")
    def v452_qos_snapshot():
        return {
            "installed": True,
            "qos": snapshot(),
            "storyboard": _storyboard.runtime_stats(),
        }


def runtime_marker() -> dict:
    return {
        "player_qos_stabilization": bool(_installed),
        "storyboard_stream_owner_tagging": bool(_installed),
        "ffmpeg_max_concurrent": int(_storyboard.FFMPEG_MAX_CONCURRENT),
        "playback_ffmpeg_max_concurrent": 1,
    }
