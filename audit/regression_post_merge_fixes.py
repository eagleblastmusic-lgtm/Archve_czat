"""Post-merge regression: bounded V2 disk cache, build admission, QoS IDs, CW streams.

Runs against real production functions in a disposable fixture; no account files
or real user data are modified.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from queue import Empty, Full
from types import SimpleNamespace
from unittest.mock import patch

import cache_store
import main
import player_qos_runtime as qos
import storyboard_service as legacy
import timeline_storyboard as ts
from timeline_storyboard import resolve_video_key


class FakeProcess:
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


def test_cache_v2_atomic_eviction():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        active = root / "v2" / "active"
        old = root / "v2" / "old"
        for folder in (active, old):
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_bytes(b"x" * 40)
            (folder / "sheet_0.jpg").write_bytes(b"y" * 140)

        result = cache_store.trim_storyboard_cache_directory(
            root, max_bytes=190, protected_dirs={"active"}
        )
        assert result["total_bytes"] == 360, result
        assert active.joinpath("manifest.json").exists()
        assert active.joinpath("sheet_0.jpg").exists()
        assert not old.exists(), "Entire idle video directory must be removed"
        assert result["remaining_bytes"] <= 190, result


def test_cache_live_lease_preserved():
    with tempfile.TemporaryDirectory() as temp:
        key = resolve_video_key("active_cache_guard", source="archivebate")
        active_name = cache_store.safe_cache_key(key.as_string())
        root = Path(temp)
        live = root / "v2" / active_name
        old = root / "v2" / "idle"
        for folder in (live, old):
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_bytes(b"x" * 100)
            (folder / "sheet_0.jpg").write_bytes(b"y" * 200)
        result = ts.lease_manager.acquire(key, consumer_id="cache_tester", session_id="cache_tester_s1")
        assert result.ok
        try:
            with patch.object(ts, "STORYBOARD_CACHE_DIR", root):
                ts.prune_storyboard_cache_safely(max_bytes=320)
            assert live.joinpath("manifest.json").exists()
            assert live.joinpath("sheet_0.jpg").exists()
            assert not old.exists(), "Inactive V2 folder must be removed"
        finally:
            ts.lease_manager.release(key, consumer_id="cache_tester", session_id="cache_tester_s1", reason="test_cleanup")


def test_quick_process_cancel_uses_legacy_hash():
    target = "legacy_quick_id"
    other = "unrelated_quick_id"
    proc = FakeProcess()
    other_proc = FakeProcess()
    key1, key2 = "test_quick_a", "test_quick_other"
    with legacy._active_process_lock:
        legacy._active_processes[key1] = {"proc": proc, "video_key": legacy._key(target)}
        legacy._active_processes[key2] = {"proc": other_proc, "video_key": legacy._key(other)}
    try:
        qos.hard_cancel(target, source="archivebate", clear_lease=False, reason="regression")
        assert proc.terminated, "Target QUICK process was not selected for cancellation"
        assert not other_proc.terminated, "Unrelated QUICK process was cancelled"
    finally:
        with legacy._active_process_lock:
            legacy._active_processes.pop(key1, None)
            legacy._active_processes.pop(key2, None)
            legacy._preempted_processes.discard(key1)
            legacy._preempted_processes.discard(key2)
        qos.clear_cancel_state(target, source="archivebate")


def test_camwhores_refresh_id():
    def capture(video_id, duration, **kwargs):
        return {"id": video_id, **kwargs}

    with patch.object(main, "_fetch_details_singleflight", return_value={
        "direct_url": "https://cdn.example/video.mp4", "embed_url": "https://camwhores.tv/watch/123"
    }) as fetch_details, patch.object(ts.progressive_storyboard_service, "request_build", side_effect=capture):
        response = main.storyboard_v2_build(
            id="123", duration=60.0, source="camwhores", consumer_id="test_cw",
            session_id="test_cw_s1", request=SimpleNamespace(base_url="http://127.0.0.1:8000/")
        )
    fetch_details.assert_called_once_with("cw_123")
    assert response["id"] == "123"
    assert "id=cw_123" in response["source_url"], response["source_url"]
    assert "id=123&" not in response["source_url"]


def test_queue_saturation_reports_error():
    class SaturatedQueue:
        def put_nowait(self, value):
            raise Full()

        def get(self, timeout=None):
            raise Empty()

        def task_done(self):
            pass

    full = SaturatedQueue()
    key = resolve_video_key("queue_full_regression", source="archivebate")
    with patch.object(ts, "_jobs_queue", full), patch.object(ts, "_active_builds", {}), \
         patch.object(ts.progressive_storyboard_service, "get_manifest",
                      return_value={"version": 12, "rev": 1, "complete": False, "frames": []}):
        result = ts.progressive_storyboard_service.request_build(
            key.provider_id, 60.0, "http://127.0.0.1:8000/api/video/stream?id=queue_full_regression",
            source="archivebate", consumer_id="queue_tester", session_id="queue_tester_s1"
        )
    assert result["build_status"] == "retryable_error", result
    assert result["error_code"] == "queue_full", result
    ts.lease_manager.release(key, consumer_id="queue_tester", session_id="queue_tester_s1", reason="test_cleanup")


if __name__ == "__main__":
    tests = [
        test_cache_v2_atomic_eviction, test_cache_live_lease_preserved,
        test_quick_process_cancel_uses_legacy_hash, test_camwhores_refresh_id,
        test_queue_saturation_reports_error,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"PASS: {len(tests)}/{len(tests)} post-merge regressions")
