import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Defensywne mocki przed importem main
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests, config

with patch.object(requests.Session, "request", side_effect=AssertionError("External HTTP disabled")), \
     patch.object(config, "get_archivebate_credentials", return_value=("", "")):
    import main
    import storyboard_service as story
    from fastapi.testclient import TestClient
    from PIL import Image, ImageDraw

client = TestClient(main.app)

print("--- ROZPOCZĘCIE TESTÓW PAKIETU D (BACKEND) ---")

# 1. Tworzenie neutralnego testowego wideo (10 sekund z numerami czasu)
with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp_dir:
    root = Path(tmp_dir)
    ffmpeg_exe = story.imageio_ffmpeg.get_ffmpeg_exe()

    for i in range(10):
        im = Image.new("RGB", (160, 90), (i * 20, 60, 200 - i * 15))
        draw = ImageDraw.Draw(im)
        draw.text((60, 30), str(i), fill="white")
        im.save(root / f"frame_{i:02d}.png")

    test_video = root / "neutral_test_10s.mp4"
    subprocess.run(
        [ffmpeg_exe, "-v", "error", "-framerate", "1", "-i", str(root / "frame_%02d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(test_video)],
        check=True
    )
    assert test_video.exists(), "Plik test_video nie został utworzony"

    # 2. Test pojedynczego wywołania FFmpeg dla całego segmentu
    with tempfile.TemporaryDirectory(dir=root) as seg_tmp:
        seg_frames_dir = Path(seg_tmp)
        frames = story._extract_segment_frames(ffmpeg_exe, str(test_video), 0.0, 10.0, seg_frames_dir)
        assert len(frames) == 10, f"Oczekiwano 10 klatek, otrzymano {len(frames)}"
        reds = []
        for frame_path in frames:
            with Image.open(frame_path).convert("RGB") as observed:
                reds.append(sum(px[0] for px in observed.resize((1, 1)).getdata()))
        assert all(reds[i] <= reds[i + 1] + 8 for i in range(len(reds) - 1)), reds
        print("PASS 1: FFmpeg wyodrębnił 10 uporządkowanych klatek segmentu 1 fps")

    # Non-zero, fractional seeks must match image content, including the slow
    # fallback. A manifest alone cannot prove that a frame is from that second.
    for seek_before_input in (True, False):
        with tempfile.TemporaryDirectory(dir=root) as seek_tmp:
            frames, times, exact = story._run_segment_extract(
                ffmpeg_exe, str(test_video), 3.3, 4.0, Path(seek_tmp), 25, seek_before_input
            )
            assert exact and len(frames) == 4, (seek_before_input, len(frames), times)
            assert all(abs(t - expected) < 0.01 for t, expected in zip(times, (4, 5, 6, 7))), times
            for frame, timestamp in zip(frames, times):
                with Image.open(frame) as observed:
                    red = observed.getpixel((10, 10))[0]
                assert abs(red - round(timestamp) * 20) < 10, (timestamp, red)
    print("PASS 1C: Input seek and fallback preserve actual seconds and frame content at non-zero offsets")

    gap_video = root / "neutral_gap.mp4"
    subprocess.run(
        [ffmpeg_exe, "-v", "error", "-framerate", "0.5", "-i", str(root / "frame_%02d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(gap_video)], check=True
    )
    with tempfile.TemporaryDirectory(dir=root) as gap_tmp:
        frames, times, exact = story._extract_segment_frames_with_times(
            ffmpeg_exe, str(gap_video), 0.0, 10.0, Path(gap_tmp)
        )
        assert exact and len(frames) == 5, (len(frames), times)
        assert times == [0.0, 2.0, 4.0, 6.0, 8.0], times
    print("PASS 1D: Missing seconds do not create duplicated frames with fabricated timestamps")

    # A remote source must be opened once, not once for each ten-second anchor.
    with tempfile.TemporaryDirectory(dir=root) as sampled_tmp:
        with patch.object(story, "_run_cancellable_process", wraps=story._run_cancellable_process) as processes:
            frames, times, exact = story._extract_preview_frames_with_times(
                ffmpeg_exe, str(gap_video), 0, 20, Path(sampled_tmp)
            )
        assert exact and times == [0.0, 10.0], times
        assert len(frames) == 2 and processes.call_count == 1, processes.call_count
        assert processes.call_args.kwargs["timeout"] > 8
        with Image.open(frames[0]) as first, Image.open(frames[1]) as second:
            assert abs(first.getpixel((10, 10))[0] - second.getpixel((10, 10))[0]) > 60
    print("PASS 1E: Ten-second previews decode distinct frames with one source open and seek")

    # Non-zero FFmpeg nie może zostać uznany za sukces nawet gdy zostawił poprawny plik JPG.
    with tempfile.TemporaryDirectory(dir=root) as failed_tmp:
        failed_dir = Path(failed_tmp)
        def fake_failed_process(cmd, **kwargs):
            output_pattern = next(str(part) for part in cmd if "frame_%03d.jpg" in str(part))
            Image.new("RGB", (160, 90), (20, 30, 40)).save(output_pattern.replace("%03d", "001"), "JPEG")
            return 1, "forced failure", False
        with patch.object(story, "_run_cancellable_process", side_effect=fake_failed_process):
            rejected = story._extract_segment_frames(ffmpeg_exe, str(test_video), 0.0, 2.0, failed_dir)
        assert rejected == [], "Partial frame left by FFmpeg rc!=0 must be rejected"
        print("PASS 1B: Partial/non-zero FFmpeg output is rejected")

    # 3. Budowa segmentu i weryfikacja manifestu. V4.2+ generuje tylko dla aktywnego konsumenta.
    with patch.object(story, "STORYBOARD_CACHE_DIR", root):
        story.demand("vid_pkg_d", "pkg-d", active=True)
        try:
            manifest = story._build_segment("vid_pkg_d", 10.0, 0, str(test_video))
        finally:
            story.demand("vid_pkg_d", "pkg-d", active=False)
        assert manifest["type"] == "segment"
        assert manifest["segment_index"] == 0
        assert manifest["frame_count"] == 1
        assert manifest["columns"] == 1
        assert manifest["rows"] == 1
        assert len(manifest["times"]) == 1
        assert manifest["sample_interval"] == 10
        for idx, t in enumerate(manifest["times"]):
            assert abs(t - idx) < 0.01, f"Błąd znacznika czasu klatki {idx}: {t}"
        assert manifest["approximate"] is False
        assert manifest["time_precision"] == "decoded_pts_10s"

        sprite_file = root / manifest["sprite_file"]
        assert sprite_file.exists(), "Plik sprite nie został zapisany na dysku"
        print("PASS 2: Manifest segmentu używa decoded PTS; kolejność klatek jest sprawdzana niezależnie")

    # 4. Testy endpointów FastAPI (/api/storyboard/segment oraz segment/image)
    with patch.object(story, "STORYBOARD_CACHE_DIR", root):
        res = client.get("/api/storyboard/segment?id=vid_pkg_d&duration=10&segment=0")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ready"
        assert "/api/storyboard/segment/image" in data["sprite_url"]

        img_res = client.get(data["sprite_url"])
        assert img_res.status_code == 200
        assert img_res.headers["content-type"] == "image/jpeg"
        assert "immutable" in img_res.headers["cache-control"]
        print("PASS 3: Endpointy /api/storyboard/segment i segment/image działają z nagłówkiem immutable")

        with patch.object(main, "_fetch_details_singleflight", side_effect=AssertionError("Cached sprite must not resolve video")):
            cached_post = client.post("/api/storyboard/segment?id=vid_pkg_d&duration=10&segment=0",
                                      headers={"X-Archivebate-Mutation-Token": main.LOCAL_MUTATION_TOKEN})
        assert cached_post.status_code == 200 and cached_post.json()["status"] == "ready"
        print("PASS 3B: POST also reuses a disk-cached segment without resolving video metadata")

    # 5. Sprawdzenie priorytetyzacji i usuwania zapotrzebowania (demand lease)
    segment_calls = []
    def fake_build_seg(video_id, duration, seg_idx, url, priority=0):
        segment_calls.append((video_id, seg_idx))
        return {"segment_index": seg_idx}

    with patch.object(story, "_cached_segment", return_value=None), \
         patch.object(story, "_build_segment", side_effect=fake_build_seg):

        story.start_segment("unleased_vid", 60.0, 0, "dummy_url")
        story._jobs.join()
        assert segment_calls == [], f"Zadanie bez dzierżawy nie powinno się wykonać: {segment_calls}"

        story.demand("leased_vid", "consumer_1", active=True)
        story.start_segment("leased_vid", 60.0, 1, "dummy_url", priority=0)
        story._jobs.join()
        assert segment_calls == [("leased_vid", 1)], f"Oczekiwano wykonania segmentu 1: {segment_calls}"
        story.demand("leased_vid", "consumer_1", active=False)
        print("PASS 4: Zadania segmentów bez aktywnej dzierżawy są ignorowane; kolejka priorytetowa działa")

    # 6. Brak niepotrzebnych zapytań strumienia przy gotowym segmencie
    with patch.object(main, "_fetch_details_singleflight", return_value={"direct_url": "dummy"}), \
         patch.object(story, "_cached_segment", return_value={"status": "ready", "times": [0, 1, 2], "sprite_file": "dummy.jpg", "created_at": 1}):
        res = client.get("/api/storyboard/segment?id=cached_vid&duration=30&segment=0")
        assert res.status_code == 200
        assert res.json()["status"] == "ready"
        print("PASS 5: Gotowy segment nie generuje zapytań do resolvera ani strumienia wideo")

print("\n--- WSZYSTKIE TESTY PAKIETU D (BACKEND) ZAKOŃCZONE SUKCESEM (PASS) ---")
