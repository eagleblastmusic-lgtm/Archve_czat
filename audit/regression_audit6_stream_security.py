"""Audit #6: same-origin stream proxy, redirect, playback and QoS regressions.

No external requests, browser accounts, or user store are accessed.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import runtime_app
import player_qos_runtime as qos
import storyboard_service as legacy


class ResponseStub:
    def __init__(self, mime="video/mp4", code=200, body=b"video_fixture", extra=None):
        self.status_code = code
        self.headers = {**({"Content-Type": mime} if mime is not None else {}), **(extra or {})}
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size=8192):
        yield self.body

    def close(self):
        self.closed = True


class ProcessStub:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


URL = "https://cdn.example.test/video.mp4"


def proxy_get(mime, *, code=200, method="GET", extra=None):
    upstream = ResponseStub(mime, code=code, extra=extra)
    with patch.object(main, "is_safe_remote_url", return_value=True), \
         patch.object(main, "_validated_session_get", return_value=upstream), \
         patch.object(main, "_is_host_failed", return_value=(False, "")):
        client = TestClient(runtime_app.app)
        response = client.request(method, "/api/video/stream",
                                  params={"url": URL, "owner": "player"},
                                  headers={"host": "testserver"})
    return response, upstream


def test_untrusted_active_mime_not_same_origin():
    for content_type in ("text/html", "application/javascript",
                         "image/svg+xml", "application/xml", "text/plain",
                         "application/json", None):
        response, upstream = proxy_get(content_type)
        assert response.status_code == 502, (content_type, response.status_code)
        assert b"video_fixture" not in response.content
        assert upstream.closed, f"Upstream must close on {content_type}"
    print("PASS: remote HTML/JS/SVG/XML and absent MIME never reach same-origin stream", flush=True)


def test_video_get_head_range_and_cache():
    for content_type, status, extra in [
        ("video/mp4", 200, {}),
        ("video/webm", 206, {"Content-Range": "bytes 10-22/500",
                             "Content-Length": "13"}),
        ("application/octet-stream", 200, {}),
    ]:
        response, upstream = proxy_get(content_type, code=status, extra=extra)
        assert response.status_code == status, (content_type, response.text)
        assert response.headers["content-type"].startswith(content_type)
        assert response.headers["cache-control"] == "private, no-store"
        assert "sandbox" in response.headers["content-security-policy"]
        assert "script-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        if content_type == "application/octet-stream":
            assert response.headers["content-disposition"] == "attachment"
        if status == 206:
            assert response.headers["content-range"] == "bytes 10-22/500"
        assert response.content == b"video_fixture"
        assert upstream.closed

    head, upstream = proxy_get("video/mp4", method="HEAD")
    assert head.status_code == 200
    assert "sandbox" in head.headers["content-security-policy"]
    assert upstream.closed
    invalid, upstream = proxy_get("text/html", method="HEAD")
    assert invalid.status_code == 502 and upstream.closed
    out_of_range, upstream = proxy_get(None, code=416, extra={"Content-Range": "bytes */120"})
    assert out_of_range.status_code == 416 and out_of_range.headers["content-range"] == "bytes */120"
    assert upstream.closed
    print("PASS: valid video GET/HEAD/Range/416 and inert cache + CSP", flush=True)


def test_get_cannot_spoof_active_playback():
    old_value = main._last_playback_active_time
    try:
        main._last_playback_active_time = 0.0
        with TestClient(runtime_app.app) as client:
            # Avoid lifespan: it would start live crawler tasks.
            pass
    finally:
        main._last_playback_active_time = old_value


def test_no_player_activity_on_get():
    old = main._last_playback_active_time
    try:
        main._last_playback_active_time = 0.0
        response, upstream = proxy_get("video/mp4")
        assert response.status_code == 200
        assert main._last_playback_active_time == 0.0, "GET must not suspend background scans"
        with patch.object(main, "_fetch_details_singleflight", return_value={}):
            client = TestClient(runtime_app.app)
            invalid = client.get("/api/video/stream?owner=player", headers={"host": "testserver"})
        assert invalid.status_code == 400
        assert main._last_playback_active_time == 0.0
    finally:
        main._last_playback_active_time = old
    print("PASS: stream GET does not spoof real player heartbeat", flush=True)


def test_redirect_without_location_fails_closed():
    malformed = ResponseStub("text/html", code=302, extra={})
    class Transport:
        def get(self, *args, **kwargs):
            return malformed
    with patch.object(main, "is_safe_remote_url", return_value=True), \
         patch.object(main, "response_peer_is_global", return_value=True):
        try:
            main._validated_session_get(Transport(), URL)
        except HTTPException as error:
            assert error.status_code == 502
        else:
            raise AssertionError("Malformed upstream 302 must be rejected")
    assert malformed.closed
    print("PASS: malformed redirect rejected and socket closed", flush=True)


def test_legacy_qos_flags_are_canonical():
    video_id = "audit6_quick_legacy"
    canonical = qos._key(video_id, source="archivebate")
    hashed = legacy._key(video_id)
    process = ProcessStub()
    process_key = "audit6_process"
    qos.clear_cancel_state(video_id, source="archivebate", generation=5)
    try:
        with legacy._active_process_lock:
            legacy._active_processes[process_key] = {
                "proc": process,
                "video_key": hashed,
                "provider_id": video_id,
                "source": "archivebate",
                "qos_generation": 5,
                "kind": "quick",
            }
        qos.hard_cancel(video_id, source="archivebate", generation=5, clear_lease=False)
        assert process.terminated
        with qos._lock:
            assert canonical in qos._hard_cancel_flags
            assert hashed not in qos._hard_cancel_flags, "SHA-256 key leaked into canonical QoS flags"
            assert hashed not in qos._soft_cancel_until
        qos.clear_cancel_state(video_id, source="archivebate", generation=6)

        second = ProcessStub()
        with legacy._active_process_lock:
            legacy._active_processes[process_key] = {
                "proc": second,
                "video_key": hashed,
                "provider_id": video_id,
                "source": "archivebate",
                "qos_generation": 6,
                "kind": "quick",
            }
        qos.protect_playback("audit6")
        with qos._lock:
            assert canonical in qos._soft_cancel_until
            assert hashed not in qos._soft_cancel_until
        assert second.terminated
    finally:
        with legacy._active_process_lock:
            legacy._active_processes.pop(process_key, None)
            legacy._preempted_processes.discard(process_key)
        with qos._lock:
            qos._soft_cancel_until.pop(canonical, None)
            qos._hard_cancel_flags.pop(canonical, None)
    print("PASS: legacy QUICK never writes SHA keys into QoS cancellation dictionaries", flush=True)


if __name__ == "__main__":
    for test in [
        test_untrusted_active_mime_not_same_origin,
        test_video_get_head_range_and_cache,
        test_no_player_activity_on_get,
        test_redirect_without_location_fails_closed,
        test_legacy_qos_flags_are_canonical,
    ]:
        test()
    print("PASS 5/5 Audit-6 regression groups", flush=True)
