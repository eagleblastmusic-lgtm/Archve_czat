"""Real (not mocked) FFmpeg smoke: local clip extraction and process teardown.

This covers real subprocess lifecycle in CI. End-user hover-to-frame latency
on Windows with remote sources still requires manual/field measurements.
"""
import subprocess
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg

import player_qos_runtime as qos
import timeline_storyboard as ts
from timeline_storyboard import resolve_video_key


def test_real_ffmpeg_extract_and_cancel():
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        movie = base / "fixture.mp4"
        frame = base / "frame.jpg"
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
             "-t", "2", "-c:v", "mpeg4", "-q:v", "10", "-y", str(movie)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True, timeout=15,
        )
        started = time.monotonic()
        ok = ts._default_extract_frame(str(movie), 0.4, frame, timeout=8, fast=False)
        extraction_ms = (time.monotonic() - started) * 1000
        assert ok and frame.is_file() and frame.stat().st_size > 350, "Real FFmpeg frame extraction failed"

        key = resolve_video_key("postmerge_real_ffmpeg", source="archivebate")
        lease = ts.lease_manager.acquire(
            key, consumer_id="ffmpeg_fixture", session_id="ffmpeg_fixture_s1", duration=30
        )
        assert lease.ok
        process = subprocess.Popen(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-re", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
             "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pid = f"postmerge_real_ffmpeg_{time.monotonic()}"
        qos.register_v2_process(pid, process, key.provider_id, source=key.source, generation=lease.epoch)
        try:
            assert process.poll() is None, "FFmpeg should be alive before cancellation"
            was_present, remaining = ts.lease_manager.release(
                key, consumer_id="ffmpeg_fixture", session_id="ffmpeg_fixture_s1",
                reason="real_ffmpeg_test"
            )
            assert was_present and remaining == 0
            process.wait(timeout=3)
            assert process.poll() is not None, "Real FFmpeg process survived last lease release"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            qos.unregister_v2_process(pid)
        print(f"PASS: real FFmpeg extracted JPEG in {extraction_ms:.1f} ms and terminated on final release")


if __name__ == "__main__":
    test_real_ffmpeg_extract_and_cancel()
