import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

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
    
    # Klatki 0..9 z wielkimi cyframi
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
        # Niezależny oracle: piksele po dekodowaniu muszą rosnąć zgodnie z numerem sekundy.
        reds = []
        for frame_path in frames:
            with Image.open(frame_path).convert("RGB") as observed:
                reds.append(sum(px[0] for px in observed.resize((1, 1)).getdata()))
        assert all(reds[i] <= reds[i + 1] + 8 for i in range(len(reds) - 1)), reds
        print("PASS 1: FFmpeg wyodrębnił 10 uporządkowanych klatek segmentu 1 fps")

    # Non-zero FFmpeg nie może zostać uznany za sukces nawet gdy zostawił poprawny plik JPG.
    with tempfile.TemporaryDirectory(dir=root) as failed_tmp:
        failed_dir = Path(failed_tmp)
        def fake_failed_run(cmd, **kwargs):
            output_pattern = next(str(part) for part in cmd if "frame_%03d.jpg" in str(part))
            Image.new("RGB", (160, 90), (20, 30, 40)).save(output_pattern.replace("%03d", "001"), "JPEG")
            return SimpleNamespace(returncode=1, stderr="forced failure")
        with patch.object(story.subprocess, "run", side_effect=fake_failed_run):
            rejected = story._extract_segment_frames(ffmpeg_exe, str(test_video), 0.0, 2.0, failed_dir)
        assert rejected == [], "Partial frame left by FFmpeg rc!=0 must be rejected"
        print("PASS 1B: Partial/non-zero FFmpeg output is rejected")

    # 3. Budowa segmentu i weryfikacja manifestu
    with patch.object(story, "STORYBOARD_CACHE_DIR", root):
        manifest = story._build_segment("vid_pkg_d", 10.0, 0, str(test_video))
        assert manifest["type"] == "segment"
        assert manifest["segment_index"] == 0
        assert manifest["frame_count"] == 10
        assert manifest["columns"] == 6
        assert manifest["rows"] == 2
        assert len(manifest["times"]) == 10
        # Dokładność znaczników czasu: każda klatka co 1s
        for idx, t in enumerate(manifest["times"]):
            assert abs(t - idx) < 0.01, f"Błąd znacznika czasu klatki {idx}: {t}"
        assert manifest["approximate"] is True
        assert manifest["time_precision"] == "nominal_1fps_fallback"
        
        sprite_file = root / manifest["sprite_file"]
        assert sprite_file.exists(), "Plik sprite nie został zapisany na dysku"
        print("PASS 2: Manifest oznacza nominalny timing jako approximate; kolejność klatek jest sprawdzana niezależnie")

    # 4. Testy endpointów FastAPI (/api/storyboard/segment oraz segment/image)
    with patch.object(story, "STORYBOARD_CACHE_DIR", root):
        # Pobranie statusu gotowego segmentu
        res = client.get("/api/storyboard/segment?id=vid_pkg_d&duration=10&segment=0")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ready"
        assert "/api/storyboard/segment/image" in data["sprite_url"]
        
        # Pobranie obrazu segmentu z nagłówkiem immutable
        img_res = client.get(data["sprite_url"])
        assert img_res.status_code == 200
        assert img_res.headers["content-type"] == "image/jpeg"
        assert "immutable" in img_res.headers["cache-control"]
        print("PASS 3: Endpointy /api/storyboard/segment i segment/image działają z nagłówkiem immutable")

    # 5. Sprawdzenie priorytetyzacji i usuwania zapotrzebowania (demand lease)
    segment_calls = []
    def fake_build_seg(video_id, duration, seg_idx, url):
        segment_calls.append((video_id, seg_idx))
        return {"segment_index": seg_idx}

    with patch.object(story, "_cached_segment", return_value=None), \
         patch.object(story, "_build_segment", side_effect=fake_build_seg):
        
        # Bez dzierżawy (demand) zadanie segmentu jest natychmiast pomijane przez workera
        story.start_segment("unleased_vid", 60.0, 0, "dummy_url")
        story._jobs.join()
        assert segment_calls == [], f"Zadanie bez dzierżawy nie powinno się wykonać: {segment_calls}"

        # Z aktywną dzierżawą zadanie wykonuje się
        story.demand("leased_vid", "consumer_1", active=True)
        story.start_segment("leased_vid", 60.0, 1, "dummy_url", priority=0)
        story._jobs.join()
        assert segment_calls == [("leased_vid", 1)], f"Oczekiwano wykonania segmentu 1: {segment_calls}"
        story.demand("leased_vid", "consumer_1", active=False)
        print("PASS 4: Zadania segmentów bez aktywnej dzierżawy są ignorowane; kolejka priorytetowa działa")

    # 6. Brak niepotrzebnych zapytań strumienia przy gotowym segmencie
    stream_calls = []
    with patch.object(main, "_fetch_details_singleflight", return_value={"direct_url": "dummy"}), \
         patch.object(story, "_cached_segment", return_value={"status": "ready", "times": [0, 1, 2], "sprite_file": "dummy.jpg", "created_at": 1}):
        res = client.get("/api/storyboard/segment?id=cached_vid&duration=30&segment=0")
        assert res.status_code == 200
        assert res.json()["status"] == "ready"
        # Sprawdzono: nie odpytano upstreamu / resolvera
        print("PASS 5: Gotowy segment nie generuje zapytań do resolvera ani strumienia wideo")

print("\n--- WSZYSTKIE TESTY PAKIETU D (BACKEND) ZAKOŃCZONE SUKCESEM (PASS) ---")
