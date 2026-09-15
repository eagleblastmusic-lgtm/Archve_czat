"""Runtime application entry point for V4.3 launchers.

Keeps V4.3-only performance wiring outside main.py while both browser and desktop
launch the same FastAPI app.
"""

from fast_grouped_feed_v2 import install as install_grouped_feed_fast_path

install_grouped_feed_fast_path()

import main as _main  # noqa: E402  (patch must be installed before main imports the singleton)

app = _main.app
RUNTIME_ID = "v4.3-fast2"

# V4.3 timeline fallback is deliberately injected only by this runtime instead
# of changing the V4.2/master HTML. It loads after the ordinary application
# scripts and supplies a changing low-priority video frame only while an exact
# storyboard segment is still cold.
_V43_TIMELINE_SCRIPT = '<script src="/static/v43-timeline-fallback.js?v=2"></script>'
_original_versioned_html = _main._versioned_html


def _v43_versioned_html(path):
    response = _original_versioned_html(path)
    try:
        body = response.body.decode("utf-8")
        if _V43_TIMELINE_SCRIPT not in body and "</body>" in body:
            body = body.replace("</body>", f"  {_V43_TIMELINE_SCRIPT}\n</body>", 1)
            response.body = body.encode("utf-8")
            response.headers["content-length"] = str(len(response.body))
    except Exception:
        # HTML injection is a V4.3 enhancement; never make the app unbootable
        # if a future response type does not expose a mutable byte body.
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
    """Small local marker used by the launcher/live gate to reject stale port-8000 servers."""
    from catalog_service import CatalogService

    return {
        "runtime": RUNTIME_ID,
        "grouped_fast_path_v2": bool(getattr(CatalogService, "_v43_grouped_fast_v2_installed", False)),
        "dynamic_timeline_fallback": True,
    }


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
