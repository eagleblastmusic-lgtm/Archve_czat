"""Regression Test Suite for Cross-Session Lifecycle, Multi-Tab Leasing,
Stale Request Immunity, and FFmpeg Media Security.

Covers:
1. Multi-Tab Consumer Lease Independence:
   - Multiple tabs acquire independent leases on the same video.
   - Closing one tab releases only its lease and does NOT cancel the video for other tabs.
   - Closing the final tab releases the last lease and triggers clean cancellation.
2. Stale Request Immunity (RED -> GREEN verification):
   - Passive GET manifest requests do NOT resurrect cancelled tasks or create leases.
   - Stale release from an old session does NOT remove the lease of a new session.
   - Stale release after new acquire is safely scoped.
3. Natural Lease Expiration (Tab Crash Simulation):
   - When a tab crashes without calling release, the lease naturally expires without worker lockup.
4. Concurrency, Atomicity, and Cross-Video Scoping:
   - Highly concurrent acquire/renew/release across multiple videos does not leak or deadlock.
   - Cancelling one video never impacts any other video key.
5. FFmpeg Media Security & SSRF Protection:
   - Direct remote URLs are rejected for FFmpeg input because libavformat follows redirects
     without peer IP validation.
   - Loopback stream endpoint /api/video/stream on authorized server port is permitted.
   - Private networks, loopback bypasses, CRLF injection, and file schemes are blocked.
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import player_qos_runtime as qos
import timeline_storyboard as ts
from video_identity import VideoKey


def test_multi_tab_consumer_lease_independence():
    """Verify that multiple tabs opening the same video maintain independent leases."""
    test_id = f"test_mt_{int(time.monotonic() * 1000)}"
    key = ts.resolve_video_key(test_id, source="archivebate")

    # 1. Tab A opens the video
    ok_a, epoch_a, count_a, cid_a = ts.acquire_lease(test_id, consumer_id="tab_A", duration=30.0)
    assert ok_a is True
    assert count_a == 1
    assert cid_a == "tab_A"
    assert ts.has_live_lease(test_id) is True
    assert not qos.is_hard_cancelled(test_id)

    # 2. Tab B opens the same video
    ok_b, epoch_b, count_b, cid_b = ts.acquire_lease(test_id, consumer_id="tab_B", duration=30.0)
    assert ok_b is True
    assert count_b == 2
    assert cid_b == "tab_B"
    assert ts.has_live_lease(test_id) is True

    # 3. Tab A closes and sends cancel for its consumer_id
    was_present, remaining = ts.release_lease(test_id, consumer_id="tab_A", reason="tab_a_closed")
    assert was_present is True
    assert remaining == 1, f"Expected 1 active consumer (Tab B), got {remaining}"

    # Critical Assertion: Video is NOT cancelled and lease is NOT removed for Tab B!
    assert not qos.is_hard_cancelled(test_id), "Tab A close must NOT hard cancel video while Tab B is active!"
    assert ts.has_live_lease(test_id) is True, "Tab B must retain its live lease!"

    # 4. Tab B renews its lease
    renew_ok, renew_count = ts.renew_lease(test_id, consumer_id="tab_B", duration=30.0)
    assert renew_ok is True
    assert renew_count == 1

    # 5. Tab B closes and sends cancel for its consumer_id
    was_present_b, remaining_b = ts.release_lease(test_id, consumer_id="tab_B", reason="tab_b_closed")
    assert was_present_b is True
    assert remaining_b == 0

    # With all consumers closed, video is now cleanly hard-cancelled and lease removed
    assert qos.is_hard_cancelled(test_id), "Closing last consumer must trigger hard cancel!"
    assert not ts.has_live_lease(test_id), "Closing last consumer must remove live lease!"

    print("PASS: Multi-tab consumer lease independence and final-consumer cancellation verified")


def test_stale_request_immunity():
    """Verify immunity against stale GET requests, stale releases, and out-of-order delivery."""
    test_id = f"test_stale_{int(time.monotonic() * 1000)}"

    # 1. Session A starts storyboard
    _, _, _, cid_a = ts.acquire_lease(test_id, consumer_id="session_A", duration=30.0)
    assert ts.has_live_lease(test_id) is True

    # 2. Session A is closed
    ts.release_lease(test_id, consumer_id="session_A", reason="close")
    assert qos.is_hard_cancelled(test_id) is True
    assert not ts.has_live_lease(test_id)

    # 3. Stale GET request arrives from Session A.
    # In the repaired contract, GET /api/storyboard/v2 does NOT call record_lease/acquire_lease.
    # We verify that manifest reading does NOT clear cancel state or establish a lease.
    manifest = ts.progressive_storyboard_service.get_manifest(test_id, 100.0)
    assert manifest is not None
    assert qos.is_hard_cancelled(test_id) is True, "GET manifest must NOT clear hard_cancel!"
    assert not ts.has_live_lease(test_id), "GET manifest must NOT establish or resurrect a lease!"

    # 4. New Session B opens and explicitly acquires a lease
    _, epoch_b, count_b, cid_b = ts.acquire_lease(test_id, consumer_id="session_B", duration=30.0)
    assert count_b == 1
    assert cid_b == "session_B"
    assert not qos.is_hard_cancelled(test_id), "Explicit acquire by Session B must clear cancel state"
    assert ts.has_live_lease(test_id) is True, "Session B must have active lease"

    # 5. Stale release from old Session A arrives AFTER Session B acquired lease
    was_present, remaining = ts.release_lease(test_id, consumer_id="session_A", reason="delayed_release")
    assert was_present is False, "Old session was already released"
    assert remaining == 1, "Session B's lease must NOT be affected by delayed release from Session A"
    assert ts.has_live_lease(test_id) is True, "Session B must still have live lease"
    assert not qos.is_hard_cancelled(test_id), "Delayed release must NOT cancel Session B"

    # 6. Session B finishes cleanly
    ts.release_lease(test_id, consumer_id="session_B", reason="done")
    assert qos.is_hard_cancelled(test_id) is True
    assert not ts.has_live_lease(test_id)

    print("PASS: Stale request immunity and out-of-order release scoping verified")


def test_natural_lease_expiration_on_tab_crash():
    """Verify that when a consumer tab crashes without sending release, the lease naturally expires."""
    test_id = f"test_crash_{int(time.monotonic() * 1000)}"

    # Short lease duration: 0.25 seconds
    ts.acquire_lease(test_id, consumer_id="crashing_tab", duration=0.25)
    assert ts.has_live_lease(test_id) is True

    # Simulate tab crash: no further renew calls are sent
    time.sleep(0.35)

    # After expiry, has_live_lease returns False naturally
    assert ts.has_live_lease(test_id) is False, "Lease must expire naturally when tab crashes"

    print("PASS: Natural lease expiration upon client crash verified")


def test_concurrency_and_cross_video_scoping():
    """Verify that concurrent lease operations on multiple videos never interfere or deadlock."""
    video_ids = [f"vid_conc_{i}_{int(time.monotonic() * 1000)}" for i in range(10)]

    def worker_task(vid_idx: int):
        vid = video_ids[vid_idx]
        for step in range(20):
            cid = f"worker_{threading.get_ident()}_{step}"
            ts.acquire_lease(vid, consumer_id=cid, duration=10.0)
            assert ts.has_live_lease(vid) is True
            ts.renew_lease(vid, consumer_id=cid, duration=15.0)
            ts.release_lease(vid, consumer_id=cid, reason="step_done")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker_task, i % len(video_ids)) for i in range(40)]
        for f in futures:
            f.result()

    # Cancel one specific video and verify others are untouched
    target_vid = video_ids[0]
    other_vid = video_ids[1]

    ts.acquire_lease(other_vid, consumer_id="other_client", duration=30.0)
    ts.acquire_lease(target_vid, consumer_id="target_client", duration=30.0)

    # Release target
    ts.release_lease(target_vid, consumer_id="target_client", reason="targeted_cancel")

    assert qos.is_hard_cancelled(target_vid) is True
    assert not qos.is_hard_cancelled(other_vid), "Cancelling target_vid must NOT affect other_vid!"
    assert ts.has_live_lease(other_vid) is True, "other_vid must retain its active lease"

    # Cleanup other
    ts.release_lease(other_vid, consumer_id="other_client", reason="cleanup")

    print("PASS: Concurrency safety and strict cross-video scoping verified")


def test_ffmpeg_input_security_and_ssrf_protection():
    """Verify that FFmpeg URL validation blocks redirect SSRF and private network attacks."""
    # 1. Allowed: Trusted loopback stream endpoint on authorized port 8000
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:8000/api/video/stream?id=safe_vid") is True
    assert ts.is_safe_ffmpeg_input_url("http://localhost:8000/api/video/stream?id=safe_vid") is True

    # 2. Blocked: Direct remote URLs (must be proxied through /api/video/stream to protect against redirect SSRF)
    assert ts.is_safe_ffmpeg_input_url("https://example.com/video.mp4") is False
    assert ts.is_safe_ffmpeg_input_url("http://attacker.com/302_redirect") is False

    # 3. Blocked: Private / link-local / cloud metadata IPs
    assert ts.is_safe_ffmpeg_input_url("http://192.168.1.1/video.mp4") is False
    assert ts.is_safe_ffmpeg_input_url("http://10.0.0.1/video.mp4") is False
    assert ts.is_safe_ffmpeg_input_url("http://169.254.169.254/latest/meta-data/") is False

    # 4. Blocked: Loopback bypasses and unauthorized endpoints
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:8000/api/admin") is False
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:8000/api/account/export") is False
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:22/api/video/stream") is False
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:9000/api/video/stream") is False

    # 5. Blocked: File schemes, protocol smuggling, CRLF injection
    assert ts.is_safe_ffmpeg_input_url("file:///etc/passwd") is False
    assert ts.is_safe_ffmpeg_input_url("file://C:/Windows/win.ini") is False
    assert ts.is_safe_ffmpeg_input_url("gopher://127.0.0.1:6379/") is False
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:8000/api/video/stream\r\nX-Injected: 1") is False
    assert ts.is_safe_ffmpeg_input_url("http://127.0.0.1:8000/api/video/stream\0") is False

    print("PASS: FFmpeg input security and SSRF/redirect prevention verified")


def run_all_tests():
    print("=" * 70)
    print("RUNNING CROSS-SESSION LIFECYCLE & SECURITY REGRESSION SUITE")
    print("=" * 70)
    test_multi_tab_consumer_lease_independence()
    test_stale_request_immunity()
    test_natural_lease_expiration_on_tab_crash()
    test_concurrency_and_cross_video_scoping()
    test_ffmpeg_input_security_and_ssrf_protection()
    print("=" * 70)
    print("ALL CROSS-SESSION LIFECYCLE TESTS PASSED (GREEN)!")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()
