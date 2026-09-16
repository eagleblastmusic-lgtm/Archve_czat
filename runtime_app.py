"""Runtime application entry point for V4.3 launchers.

Keeps V4.3-only performance wiring outside main.py while both browser and desktop
launch the same FastAPI app.
"""

import re
from urllib.parse import quote

from fast_grouped_feed_v2 import install as install_grouped_feed_fast_path
import fast_storyboard_quick as _quick_storyboard

install_grouped_feed_fast_path()

# Cold-hover overview is only the first bridge until exact 1-fps segments take
# over. Four coarse anchors are prepared with bounded two-process parallelism.
# Exact segment frames remain authoritative and provide the fine-grained follow-up.
# V4.4 keeps this shared coarse fallback alive while exact hover work coexists.
_quick_storyboard.QUICK_FRAME_COUNT = 4
_quick_storyboard.QUICK_PARALLELISM = 2
_quick_storyboard.QUICK_MIN_SUCCESS = 3
_quick_storyboard.install()

import main as _main  # noqa: E402  (runtime accelerators must be installed before main binds entry points)
import player_qos_runtime as _player_qos  # noqa: E402

app = _main.app
_player_qos.install(app, _main)
RUNTIME_ID = "v4.3-fast2"

# V4.3 timeline layer is deliberately injected only by this runtime instead of
# changing the V4.2/master HTML. V7 coordinates the first cold hover so the
# persistent coarse sprite gets a bounded head start before exact 1-fps work.
# It never seeks a second full-resolution browser <video>.
_V452_QOS_SCRIPT = '<script src="/static/v452-player-qos.js?v=452"></script>'
_V43_TIMELINE_SCRIPT = '<script src="/static/v43-timeline-fallback-v7.js?v=452"></script>'
_original_versioned_html = _main._versioned_html


_WATCH_AUX_STREAM_RE = re.compile(
    r"\n            if \(previewVideo && streamUrl\) \{.*?"
    r"\n            \}\n          \}\n        \} else \{",
    re.DOTALL,
)


def _remove_watch_aux_stream(body: str) -> str:
    """Remove the historical second full-resolution timeline <video> path."""
    if "const warmWatch = () =>" not in body:
        return body
    return _WATCH_AUX_STREAM_RE.sub("\n          }\n        } else {", body, count=1)


def _v43_versioned_html(path):
    response = _original_versioned_html(path)
    try:
        body = response.body.decode("utf-8")
        if str(path).replace("\\", "/").endswith("/watch.html"):
            body = _remove_watch_aux_stream(body)
        scripts = []
        if _V452_QOS_SCRIPT not in body:
            scripts.append(_V452_QOS_SCRIPT)
        if _V43_TIMELINE_SCRIPT not in body:
            scripts.append(_V43_TIMELINE_SCRIPT)
        if scripts and "</body>" in body:
            body = body.replace("</body>", "  " + "\n  ".join(scripts) + "\n</body>", 1)
        response.body = body.encode("utf-8")
        response.headers["content-length"] = str(len(response.body))
    except Exception:
        # Runtime HTML enhancements must never make the application unbootable.
        pass
    return response


_main._versioned_html = _v43_versioned_html

# static/index.html still contains one historical Font Awesome media-switch
# handler: onload="this.media='all'". V4.2 CSP correctly blocks arbitrary inline
# script, which made Chromium log an error even though performance.js later repairs
# the stylesheet. Permit only that exact handler hash; arbitrary inline script
# remains blocked.
_FA_EVENT_HASH = "'sha256-MhtPZXr7+LpJUY5qtMutB+qWfQtMaPccfe7QXtCcEYc='"


@app.get("/api/runtime/v43")
def v43_runtime_marker():
    """Marker used by launchers/live gates to reject stale port-8000 servers."""
    from catalog_service import CatalogService
    import storyboard_service

    return {
        "runtime": RUNTIME_ID,
        "grouped_fast_path_v2": bool(getattr(CatalogService, "_v43_grouped_fast_v2_installed", False)),
        "dynamic_timeline_fallback": True,
        # Compatibility marker retained for older diagnostics.
        "parallel_quick_storyboard": bool(getattr(storyboard_service, "_v43_parallel_quick_installed", False)),
        "playback_safe_quick_storyboard": bool(getattr(storyboard_service, "_v43_playback_safe_quick_installed", False)),
        "quick_reservation_scheduler": bool(getattr(storyboard_service, "_v43_quick_reservation_installed", False)),
        "timeline_coordinator_version": 452,
        "timeline_mode": "local-first-player-qos-v452",
        "timeline_scheduler": "single-idle-exact-v452",
        "quick_scheduler_revision": 11,
        "quick_durable_build": True,
        "storyboard_priority_mode": "player-qos-arbiter-v452",
        "player_qos_stabilization": True,
        "storyboard_stream_owner_tagging": True,
        "watch_aux_media_seek_removed": True,
        **_player_qos.runtime_marker(),
        "quick_frame_count": int(_quick_storyboard.QUICK_FRAME_COUNT),
        "quick_parallelism": int(_quick_storyboard.QUICK_PARALLELISM),
        "quick_min_success": int(_quick_storyboard.QUICK_MIN_SUCCESS),
        "media_seek_fallback": False,
    }


@app.get("/api/runtime/v43/storyboard/quick")
def v43_quick_storyboard_status(id: str, duration: float, wait_ms: int = 1200):
    """Long-poll the tiny QUICK storyboard without browser-side busy polling.

    This is read-only. Building is still started through the protected
    POST /api/storyboard route, so the V4.2 mutation security contract remains
    unchanged.
    """
    clean_id = _main._resolve_clean_video_id(id)
    duration = float(duration or 0)
    if not clean_id or duration <= 0 or duration > 43200:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid ID or duration")
    bounded_wait = max(0, min(int(wait_ms or 0), 2000)) / 1000.0
    data = dict(_quick_storyboard.wait_status(clean_id, duration, timeout=bounded_wait) or {})
    if data.get("status") == "ready":
        data["sprite_url"] = (
            f"/api/storyboard/image?id={quote(clean_id, safe='')}"
            f"&q=quick&v={data.get('created_at', 0)}"
        )
    return data


@app.get("/api/runtime/v43/storyboard/stats")
def v43_quick_storyboard_stats():
    """Small live-gate view over V4.3 coarse-generation cost and concurrency."""
    return _quick_storyboard.runtime_stats()


@app.middleware("http")
async def v43_runtime_middleware(request, call_next):
    response = await call_next(request)
    response.headers["X-Archivebate-Runtime"] = RUNTIME_ID
    csp = response.headers.get("Content-Security-Policy")
    if csp and "script-src 'self'" in csp and _FA_EVENT_HASH not in csp:
        response.headers["Content-Security-Policy"] = csp.replace(
            "script-src 'self'",
            f"script-src 'self' 'unsafe-hashes' {_FA_EVENT_HASH}",
            1,
        )
    return response
