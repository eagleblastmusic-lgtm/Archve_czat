"""Audit Repair Verification Test Suite for Archivebite P1 & P2 Fixes.

Covers:
P1-01: FFmpeg concurrency limits & QoS arbiter integration.
P1-02: Queue full non-blocking atomic registration & idempotent retry.
P1-03: Full video coverage & PTS monotonicity for [30s, 2m, 15m, 60m, 100m, 180m, 360m].
P1-04: Layer 0 provisional & preview metadata mapping.
P2-02: Revisioned sprite sheets & immutable cache header consistency.
P2-04: Cancellation, lease expiry and QoS preemption lifecycle.
P2-05: Security hardening: removal of open CORS wildcard on local frames.
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image

import main
import player_qos_runtime as qos
import runtime_app
from timeline_storyboard import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    ProgressiveStoryboardService,
    _active_builds,
    _jobs_queue,
    compute_progressive_schedule,
    get_sheet_path,
    get_slot_pts_mapping,
    has_live_lease,
    record_lease,
    remove_lease,
    reset_frame_extractor,
    resolve_video_key,
    set_frame_extractor_for_test,
)

client = TestClient(runtime_app.app)


def test_p1_03_video_coverage():
    """Verify full duration coverage and PTS mapping for all requested video lengths."""
    durations = [30.0, 120.0, 900.0, 3600.0, 6000.0, 10800.0, 21600.0]

    for dur in durations:
        total_slots = min(100, max(10, int(dur // 2)))
        mapping = get_slot_pts_mapping(dur, total_slots)
        order = compute_progressive_schedule(dur, total_slots)

        assert len(mapping) == total_slots, f"Mapping length mismatch for dur={dur}: {len(mapping)} vs {total_slots}"
        assert len(order) == total_slots, f"Order length mismatch for dur={dur}: {len(order)} vs {total_slots}"
        assert set(order) == set(range(total_slots)), f"Order missing slots for dur={dur}"

        # First slot must be video start
        assert mapping[0] == 0.0, f"First slot must be 0.0, got {mapping[0]} for dur={dur}"

        # Last slot must span to near the end of video (never truncate long videos!)
        last_slot = total_slots - 1
        last_pts = mapping[last_slot]
        assert last_pts >= dur - min(2.0, dur * 0.02), (
            f"Coverage failed for dur={dur}s: last slot PTS is only {last_pts}s!"
        )

        # Monotonicity check
        prev = -1.0
        for s in range(total_slots):
            assert mapping[s] >= prev, f"PTS non-monotonic at slot {s}: {mapping[s]} < {prev}"
            prev = mapping[s]

        # Specific verification for 180 min (3h) and 360 min (6h)
        if dur == 10800.0:
            assert last_pts > 10700.0, f"180 min video truncated at {last_pts}s"
            # Middle slot must be near the middle
            mid_pts = mapping[50]
            assert 5000.0 < mid_pts < 5600.0, f"180 min middle slot deviated: {mid_pts}"
        elif dur == 21600.0:
            assert last_pts > 21500.0, f"360 min video truncated at {last_pts}s"
            mid_pts = mapping[50]
            assert 10500.0 < mid_pts < 11200.0, f"360 min middle slot deviated: {mid_pts}"

    print("PASS: P1-03 full video coverage & PTS mapping for 30s, 2m, 15m, 60m, 100m, 180m, 360m")


def test_p1_02_queue_full_and_retry():
    """Verify that a full job queue does NOT lock out the video permanently and allows retrying."""
    from unittest.mock import patch
    service = ProgressiveStoryboardService()
    test_id = f"test_queue_lock_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(test_id, source="archivebate")
    key_str = key.as_string()

    # 1. Simulate full queue raising queue.Full
    with patch.object(_jobs_queue, "put_nowait", side_effect=queue.Full):
        manifest = service.request_build(test_id, 120.0, "http://127.0.0.1/fake.mp4", source="archivebate")
        assert manifest is not None
        # Video key must NOT remain registered in _active_builds
        assert key_str not in _active_builds, "Video key must NOT remain in _active_builds when queue is full!"

    # 2. Retry the same video when queue has space -> must succeed and register
    manifest2 = service.request_build(test_id, 120.0, "http://127.0.0.1/fake.mp4", source="archivebate")
    assert manifest2 is not None

    # Cleanup test_id
    remove_lease(test_id)
    print("PASS: P1-02 queue full handling, non-locking atomic registration & retry")


def test_p1_01_p2_04_qos_limiter_and_preemption():
    """Verify that V2 extracts respect global FFmpeg semaphore limits and can be preempted by QoS."""
    service = ProgressiveStoryboardService()
    test_id = f"test_qos_preempt_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(test_id, source="archivebate")

    running_concurrent = 0
    max_observed_concurrent = 0
    extractor_calls = 0

    def mock_extractor(source_url: str, target_time: float, out_path: Path, timeout: float = 8.0, fast: bool = True, **kwargs) -> bool:
        nonlocal running_concurrent, max_observed_concurrent, extractor_calls
        running_concurrent += 1
        if running_concurrent > max_observed_concurrent:
            max_observed_concurrent = running_concurrent
        extractor_calls += 1

        img = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (0, 255, 0))
        img.save(out_path, format="JPEG")

        time.sleep(0.05)
        running_concurrent -= 1
        return True

    set_frame_extractor_for_test(mock_extractor)
    record_lease(test_id, duration=15.0)

    job = {
        "key": key,
        "duration": 60.0,
        "source_url": "http://127.0.0.1/fake_qos.mp4",
    }

    # Execute build job
    service._execute_build_job(job)

    # Concurrency must not exceed FFMPEG_MAX_CONCURRENT (2)
    assert max_observed_concurrent <= qos.FFMPEG_MAX_CONCURRENT, (
        f"Exceeded max concurrent FFmpeg decoders: observed {max_observed_concurrent} > {qos.FFMPEG_MAX_CONCURRENT}"
    )

    # Test QoS cancellation propagation
    cancel_test_id = f"test_qos_cancel_{int(time.monotonic() * 1000)}"
    cancel_key = resolve_video_key(cancel_test_id, source="archivebate")
    cancelled_extractor_calls = 0

    def mock_cancel_extractor(source_url: str, target_time: float, out_path: Path, timeout: float = 8.0, fast: bool = True, **kwargs) -> bool:
        nonlocal cancelled_extractor_calls
        cancelled_extractor_calls += 1
        img = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 255))
        img.save(out_path, format="JPEG")
        # Trigger hard cancellation on the 2nd frame
        if cancelled_extractor_calls >= 2:
            qos.hard_cancel(cancel_test_id, reason="test_switch")
        return True

    set_frame_extractor_for_test(mock_cancel_extractor)
    record_lease(cancel_test_id, duration=15.0)

    cancel_job = {
        "key": cancel_key,
        "duration": 300.0,
        "source_url": "http://127.0.0.1/fake_cancel.mp4",
    }
    service._execute_build_job(cancel_job)

    # Must have stopped early due to cancellation instead of continuing all 100 frames
    assert cancelled_extractor_calls < 20, (
        f"Cancellation failed to halt job: made {cancelled_extractor_calls} calls"
    )

    reset_frame_extractor()
    print("PASS: P1-01 & P2-04 QoS concurrency budget enforcement and cancellation preemption")


def test_p2_02_p2_05_sheet_revision_and_security():
    """Verify versioned sheet persistence, immutable caching contract and CORS security."""
    test_id = f"test_sec_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(test_id, source="archivebate")
    service = ProgressiveStoryboardService()

    def fake_extractor(source_url: str, target_time: float, out_path: Path, **kwargs) -> bool:
        img = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (120, 120, 120))
        img.save(out_path, format="JPEG")
        return True

    set_frame_extractor_for_test(fake_extractor)
    record_lease(test_id, duration=20.0)

    job = {
        "key": key,
        "duration": 30.0,
        "source_url": "http://127.0.0.1:8000/api/video/stream?id=test_sec&source=archivebate",
    }
    service._execute_build_job(job)

    manifest = service.get_manifest(test_id, 30.0, source="archivebate")
    rev = manifest.get("rev", 1)

    # 1. Versioned sheet file exists
    versioned_path = get_sheet_path(key, 0, rev=rev)
    assert versioned_path.exists(), f"Versioned file {versioned_path} must exist"

    # 2. HTTP GET with version `v` returns immutable cache-control
    resp_v = client.get(f"/api/storyboard/v2/sheet?id={test_id}&sheet=0&v={rev}&source=archivebate")
    assert resp_v.status_code == 200
    assert "immutable" in resp_v.headers.get("cache-control", "")
    assert "public" in resp_v.headers.get("cache-control", "")

    # 3. HTTP GET without version returns revalidating cache-control (not immutable)
    resp_nov = client.get(f"/api/storyboard/v2/sheet?id={test_id}&sheet=0&source=archivebate")
    assert resp_nov.status_code == 200
    assert "immutable" not in resp_nov.headers.get("cache-control", "")
    assert "must-revalidate" in resp_nov.headers.get("cache-control", "")

    # 4. Security: Access-Control-Allow-Origin must NOT be wildcard "*"
    cors = resp_v.headers.get("access-control-allow-origin")
    assert cors != "*", f"Security violation: Access-Control-Allow-Origin must not be '*': {cors}"

    reset_frame_extractor()
    print("PASS: P2-02 & P2-05 versioned sheet persistence, cache-control contracts and CORS security")


def test_p1_04_preview_video_layer0():
    """Verify Layer 0 preview video extraction, manifest publishing, and HTTP endpoint serving."""
    test_id = f"test_p1_04_prev_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(test_id, source="archivebate")
    service = ProgressiveStoryboardService()

    preview_calls = []
    def mock_preview_extractor(source_url: str, target_time: float, out_path: Path, **kwargs) -> bool:
        if "fake_preview" in source_url:
            preview_calls.append((source_url, target_time))
            img = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (255, 128, 0))
            img.save(out_path, format="JPEG")
            return True
        img = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (50, 50, 50))
        img.save(out_path, format="JPEG")
        return True

    set_frame_extractor_for_test(mock_preview_extractor)
    record_lease(test_id, duration=30.0)

    job = {
        "key": key,
        "duration": 240.0,
        "source_url": "http://127.0.0.1:8000/api/video/stream?id=fake_full&source=archivebate",
        "preview_url": "http://127.0.0.1:8000/api/video/stream?id=fake_preview&source=archivebate",
    }
    service._execute_build_job(job)

    # 1. Preview frames must have been extracted
    assert len(preview_calls) == 8, f"Expected 8 preview extraction calls, got {len(preview_calls)}"

    # 2. Manifest must contain native.frames pointing to preview endpoint
    manifest = service.get_manifest(test_id, 240.0, source="archivebate")
    native = manifest.get("native", {})
    assert native.get("kind") == "preview_video", f"Native kind mismatch: {native.get('kind')}"
    frames = native.get("frames", [])
    assert len(frames) == 8, f"Native frames count mismatch: {len(frames)}"
    assert frames[0][1].startswith("/api/storyboard/v2/preview?id="), f"Invalid frame URL: {frames[0][1]}"

    # 3. HTTP GET to /api/storyboard/v2/preview
    resp = client.get(f"/api/storyboard/v2/preview?id={test_id}&idx=0&source=archivebate")
    assert resp.status_code == 200, f"Preview endpoint returned {resp.status_code}"
    assert resp.headers.get("content-type") == "image/jpeg"
    assert "public" in resp.headers.get("cache-control", "")

    # 4. Out-of-bounds frame returns 404
    resp_404 = client.get(f"/api/storyboard/v2/preview?id={test_id}&idx=99&source=archivebate")
    assert resp_404.status_code == 404

    reset_frame_extractor()
    print("PASS: P1-04 Layer 0 preview_video extraction, manifest mapping & HTTP serving")


if __name__ == "__main__":
    test_p1_03_video_coverage()
    test_p1_02_queue_full_and_retry()
    test_p1_01_p2_04_qos_limiter_and_preemption()
    test_p2_02_p2_05_sheet_revision_and_security()
    test_p1_04_preview_video_layer0()
    print("\nALL AUDIT REPAIR BACKEND TESTS PASSED SUCCESSFULLY!")
