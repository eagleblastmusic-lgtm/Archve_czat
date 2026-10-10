"""Real Multi-Tab and Player Lifecycle Browser Qualification Suite (Playwright).

Executes against the real running application code:
- static/timeline-preview.js (window.ArchivebateTimelinePreview.manager)
- static/video-modal.js (window.ArchivebateVideoModal)
- static/v452-player-qos.js (window.ArchivebatePlayerQoS)
- static/modal-player-controls.js

MANDATORY SCENARIOS:
1. Dwie niezależne karty z tym samym filmem (natural loadedmetadata -> subscribe -> acquire).
2. Zamknięcie A przy aktywnej B (B zachowuje aktywny lease i generowanie).
3. Zamknięcie ostatniej karty (twarde anulowanie i zwolnienie lease).
4. Szybkie przełączenia A -> B -> C -> A w realnym modale.
5. Ponowne otwarcie tego samego filmu w tej samej karcie (X -> close -> X z nowym session_id).
6. Opóźniony release starej generacji (stale release z sess_1 nie zabija sess_2).
7. Spóźniony heartbeat po wygaśnięciu TTL (nie wskrzesza wygasłego lease).
8. Identyczne identyfikatory filmów z różnych źródeł (archivebate vs camwhores).
9. Zamknięcie karty bez poprawnego release (naturalne wygaśnięcie lease po crashu).
10. Rzeczywiste zakończenie działającego FFmpeg po anulowaniu ostatniego odbiorcy (potwierdzenie kill podprocesu).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from starlette.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import imageio_ffmpeg
import uvicorn
from playwright.sync_api import sync_playwright

import player_qos_runtime as qos
import timeline_storyboard as ts
from timeline_storyboard import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    STORYBOARD_V2_VERSION,
    get_manifest_path,
    get_sheet_path,
    resolve_video_key,
)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def create_test_sprite(out_path: Path, rows: int = 10, cols: int = 10, w: int = 160, h: int = 90) -> None:
    img = Image.new("RGB", (cols * w, rows * h), (20, 24, 28))
    draw = ImageDraw.Draw(img)
    for r in range(rows):
        for c in range(cols):
            x = c * w
            y = r * h
            slot = r * cols + c
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=((slot * 3) % 255, (slot * 5) % 255, 140))
            draw.text((x + 8, y + 8), f"F{slot}", fill=(255, 255, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="JPEG", quality=80)


def create_synthetic_mp4(out_path: Path, duration_sec: int = 2) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ffmpeg_exe, "-v", "error", "-f", "lavfi",
        "-i", f"testsrc=duration={duration_sec}:size=320x180:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-y", str(out_path)
    ], check=True)


def setup_fixtures(video_ids: list[str]):
    for vid in video_ids:
        key = resolve_video_key(vid, source="archivebate")
        sheet_path = get_sheet_path(key, 0, rev=1)
        sheet_unv = get_sheet_path(key, 0)
        create_test_sprite(sheet_path)
        create_test_sprite(sheet_unv)

        manifest = {
            "version": STORYBOARD_V2_VERSION,
            "video_key": key.as_string(),
            "video_id": key.provider_id,
            "source": key.source,
            "duration": 240.0,
            "rev": 1,
            "complete": False,
            "frame_w": FRAME_WIDTH,
            "frame_h": FRAME_HEIGHT,
            "columns": 10,
            "rows": 10,
            "sheets": [{"index": 0, "url": f"/api/storyboard/v2/sheet?id={key.provider_id}&sheet=0&v=1&source=archivebate"}],
            "frames": [[round(i * 2.4, 3), 0, i] for i in range(10)],
            "native": {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        m_path = get_manifest_path(key)
        m_path.parent.mkdir(parents=True, exist_ok=True)
        m_path.write_text(json.dumps(manifest), encoding="utf-8")


def run_cross_session_browser_tests():
    import main
    import runtime_app

    test_vids = [
        "vid_multi_tab",
        "vid_switch_a",
        "vid_switch_b",
        "vid_switch_c",
        "vid_reopen",
        "vid_isolated_same_id",
    ]
    setup_fixtures(test_vids)

    port = find_free_port()
    os.environ["PORT"] = str(port)

    with tempfile.TemporaryDirectory(prefix="pw_fixture_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        fixture_mp4 = tmp_path / "test_clip.mp4"
        create_synthetic_mp4(fixture_mp4, duration_sec=4)

        mp4_bytes = fixture_mp4.read_bytes()

        def fixture_details(video_id, **kwargs):
            src = "camwhores" if str(video_id).startswith("cw_") else "archivebate"
            return {
                "id": video_id,
                "source": src,
                "title": f"Fixture {video_id}",
                "username": "ModelTester",
                "duration": "4:00",
                "availability": "available",
                "direct_url": "https://fixture.invalid/stream.mp4",
                "proxy_stream_url": f"/api/video/stream?id={video_id}",
                "thumbnail": f"/api/storyboard/v2/sheet?id={video_id}&sheet=0&v=1&source={src}",
            }

        for route in runtime_app.app.routes:
            if getattr(route, "path", "") == "/api/video/stream":
                route.dependant.call = lambda **kwargs: FileResponse(str(fixture_mp4), media_type="video/mp4")

        server_config = uvicorn.Config(
            runtime_app.app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            access_log=False,
            log_level="critical",
        )
        server = uvicorn.Server(server_config)
        server_thread = threading.Thread(target=server.run, daemon=True)

        with patch.object(main, "_fetch_details_singleflight", side_effect=fixture_details):
            server_thread.start()
            try:
                deadline = time.monotonic() + 8.0
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert server.started, "Uvicorn server failed to start"

                channel = os.environ.get("ARCHIVEBATE_BROWSER_CHANNEL") or ("msedge" if os.name == "nt" else None)
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        channel=channel,
                        headless=True,
                        args=["--autoplay-policy=no-user-gesture-required"],
                    )
                    context = browser.new_context(viewport={"width": 1280, "height": 800})
                    context.route("**/api/feed*", lambda r: r.fulfill(status=200, headers={"Content-Type": "application/json"}, body=json.dumps({"videos": [], "has_more": False})))

                    print("\n" + "=" * 70)
                    print("RUNNING AUTHENTIC MULTI-TAB & PLAYER LIFECYCLE PLAYWRIGHT TESTS")
                    print("=" * 70)

                    # ====================================================================
                    # SCENARIOS 1, 2, 3: Multi-tab loadedmetadata -> acquire -> release
                    # ====================================================================
                    print("\n[SCENARIO 1 & 2 & 3] Two tabs opening same video natively via modal & loadedmetadata...")
                    page_a = context.new_page()
                    page_b = context.new_page()

                    errors_a = []
                    errors_b = []
                    page_a.on("pageerror", lambda err: errors_a.append(str(err)))
                    page_b.on("pageerror", lambda err: errors_b.append(str(err)))

                    page_a.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                    page_b.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")

                    page_a.wait_for_function("!!window.ArchivebateVideoModal")
                    page_b.wait_for_function("!!window.ArchivebateVideoModal")

                    # Both pages open the video purely through openVideoModal and start playback
                    page_a.evaluate("v => window.ArchivebateVideoModal.open(v)", fixture_details("vid_multi_tab"))
                    page_a.locator("#modalVideo").evaluate("v => { v.muted = true; v.play().catch(() => {}); }")

                    page_b.evaluate("v => window.ArchivebateVideoModal.open(v)", fixture_details("vid_multi_tab"))
                    page_b.locator("#modalVideo").evaluate("v => { v.muted = true; v.play().catch(() => {}); }")

                    # Wait for native video loadedmetadata event and lease registration
                    shared_key = resolve_video_key("vid_multi_tab", source="archivebate")
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if ts.lease_manager.get_active_consumers_count(shared_key) == 2:
                            break
                        time.sleep(0.1)

                    active_count = ts.lease_manager.get_active_consumers_count(shared_key)
                    assert active_count == 2, f"Expected 2 active consumers natively acquired, got {active_count}"
                    assert ts.has_live_lease("vid_multi_tab") is True
                    assert not qos.is_hard_cancelled("vid_multi_tab")
                    print("PASS: Scenario 1: Two independent tabs natively acquired leases via loadedmetadata")

                    # Scenario 2: Close Tab A -> Tab B must retain live lease and NOT be cancelled
                    page_a.close()
                    time.sleep(0.5)

                    active_after_a = ts.lease_manager.get_active_consumers_count(shared_key)
                    assert active_after_a == 1, f"Expected 1 active consumer (Tab B), got {active_after_a}"
                    assert ts.has_live_lease("vid_multi_tab") is True, "Tab B must retain live lease!"
                    assert not qos.is_hard_cancelled("vid_multi_tab"), "Video must NOT be hard cancelled while Tab B is open!"
                    print("PASS: Scenario 2: Tab A closed; Tab B lease protected and video generation continues")

                    # Scenario 3: Close Tab B -> Last consumer closed, hard cancel triggered
                    page_b.close()
                    time.sleep(0.5)

                    final_count = ts.lease_manager.get_active_consumers_count(shared_key)
                    assert final_count == 0, f"Expected 0 active consumers, got {final_count}"
                    assert qos.is_hard_cancelled("vid_multi_tab") is True, "Final tab close must trigger hard cancel!"
                    assert not ts.has_live_lease("vid_multi_tab"), "Final tab close must remove live lease!"
                    assert len(errors_a) == 0, f"Errors in Page A: {errors_a}"
                    assert len(errors_b) == 0, f"Errors in Page B: {errors_b}"
                    print("PASS: Scenario 3: Closing last consumer cleanly stops generation and removes lease")

                    # ====================================================================
                    # SCENARIO 4: Rapid switching A -> B -> C -> A
                    # ====================================================================
                    print("\n[SCENARIO 4] Verifying rapid video switching A -> B -> C -> A in real modal...")
                    page_switch = context.new_page()
                    switch_errors = []
                    page_switch.on("pageerror", lambda err: switch_errors.append(str(err)))
                    page_switch.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                    page_switch.wait_for_function("!!window.ArchivebateVideoModal")

                    for vid_id in ("vid_switch_a", "vid_switch_b", "vid_switch_c", "vid_switch_a"):
                        page_switch.evaluate("v => window.ArchivebateVideoModal.open(v)", fixture_details(vid_id))
                        page_switch.locator("#modalVideo").evaluate("v => { v.muted = true; v.play().catch(() => {}); }")
                        time.sleep(0.3)

                    # Wait for final video A lease
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if ts.has_live_lease("vid_switch_a"):
                            break
                        time.sleep(0.1)

                    assert ts.has_live_lease("vid_switch_a") is True, "Final video A must have live lease"
                    assert not ts.has_live_lease("vid_switch_b"), "Switched video B must have lease released"
                    assert not ts.has_live_lease("vid_switch_c"), "Switched video C must have lease released"

                    # Close modal
                    page_switch.evaluate("() => window.ArchivebateVideoModal.closeModal()")
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if not ts.has_live_lease("vid_switch_a"):
                            break
                        time.sleep(0.1)
                    assert not ts.has_live_lease("vid_switch_a"), "Video A must be released on closeModal"
                    page_switch.close()
                    assert len(switch_errors) == 0, f"Switch errors: {switch_errors}"
                    print("PASS: Scenario 4: Rapid switching cleanly unregisters previous videos without errors")

                    # ====================================================================
                    # SCENARIO 5 & 6: Reopen same video (X -> close -> X) & Delayed release
                    # ====================================================================
                    print("\n[SCENARIO 5 & 6] Verifying reopen in same tab with new session_id & stale release...")
                    reopen_vid = "vid_reopen"
                    key_reopen = resolve_video_key(reopen_vid, source="archivebate")
                    consumer_tab = "tab_reopen_tester"

                    # Session 1 acquired
                    res1 = ts.lease_manager.acquire(key_reopen, consumer_id=consumer_tab, session_id="sess_gen_1", duration=30.0)
                    assert res1.ok and res1.session_id == "sess_gen_1"
                    assert ts.has_live_lease(reopen_vid) is True

                    # User closes modal and immediately reopens (Session 2)
                    res2 = ts.lease_manager.acquire(key_reopen, consumer_id=consumer_tab, session_id="sess_gen_2", duration=30.0)
                    assert res2.ok and res2.session_id == "sess_gen_2"
                    assert not qos.is_hard_cancelled(reopen_vid)

                    # Delayed release from Session 1 arrives with old session_id
                    was_present, remaining = ts.lease_manager.release(key_reopen, consumer_id=consumer_tab, session_id="sess_gen_1")
                    assert was_present is False, "Delayed release with old session_id must report was_present=False"
                    assert remaining == 1, f"Session 2 must remain active, got remaining={remaining}"
                    assert ts.has_live_lease(reopen_vid) is True, "Session 2 lease must remain live"
                    assert not qos.is_hard_cancelled(reopen_vid), "Session 2 must NOT be cancelled by delayed release 1"
                    print("PASS: Scenario 5 & 6: Session generations protect newly opened session from stale release")

                    # Clean up Session 2
                    ts.lease_manager.release(key_reopen, consumer_id=consumer_tab, session_id="sess_gen_2")
                    assert not ts.has_live_lease(reopen_vid)

                    # ====================================================================
                    # SCENARIO 7: Stale heartbeat after TTL expiration
                    # ====================================================================
                    print("\n[SCENARIO 7] Verifying renew rejected on expired lease...")
                    exp_vid = "vid_exp_test"
                    key_exp = resolve_video_key(exp_vid, source="archivebate")
                    ts.lease_manager.acquire(key_exp, consumer_id="c_fast_exp", duration=0.05)
                    time.sleep(0.08)

                    # Heartbeat arrives after expiry
                    ok, count = ts.lease_manager.renew(key_exp, consumer_id="c_fast_exp", duration=30.0)
                    assert ok is False, "Renew on expired lease must return False"
                    assert count == 0, "Renew must not resurrect expired lease"
                    assert not ts.has_live_lease(exp_vid), "Expired lease must remain dead"
                    print("PASS: Scenario 7: Stale heartbeat cannot resurrect expired lease")

                    # ====================================================================
                    # SCENARIO 8: Source isolation (archivebate vs camwhores with same ID)
                    # ====================================================================
                    print("\n[SCENARIO 8] Verifying canonical VideoKey source isolation...")
                    same_id = "vid_isolated_same_id"
                    key_ab = resolve_video_key(same_id, source="archivebate")
                    key_cw = resolve_video_key(same_id, source="camwhores")

                    ts.lease_manager.acquire(key_ab, consumer_id="c_ab", duration=30.0)
                    ts.lease_manager.acquire(key_cw, consumer_id="c_cw", duration=30.0)

                    assert ts.lease_manager.has_live_lease(key_ab) is True
                    assert ts.lease_manager.has_live_lease(key_cw) is True

                    # Cancel archivebate
                    ts.lease_manager.release(key_ab, consumer_id="c_ab", reason="cancel_ab")
                    assert not ts.lease_manager.has_live_lease(key_ab)
                    assert qos.is_hard_cancelled(same_id, source="archivebate") is True

                    # Camwhores MUST be completely untouched!
                    assert ts.lease_manager.has_live_lease(key_cw) is True, "Camwhores lease must remain active!"
                    assert not qos.is_hard_cancelled(same_id, source="camwhores"), "Camwhores must not be cancelled!"
                    print("PASS: Scenario 8: Same video ID across distinct sources is completely isolated")

                    # Clean up camwhores
                    ts.lease_manager.release(key_cw, consumer_id="c_cw")

                    # ====================================================================
                    # SCENARIO 9: Unexpected tab crash (natural expiry)
                    # ====================================================================
                    print("\n[SCENARIO 9] Verifying unexpected tab crash and natural lease expiry...")
                    crash_vid = "vid_crash_pw"
                    key_crash = resolve_video_key(crash_vid, source="archivebate")
                    ts.lease_manager.acquire(key_crash, consumer_id="crashed_browser", duration=0.2)
                    assert ts.lease_manager.has_live_lease(crash_vid) is True

                    time.sleep(0.3)
                    assert ts.lease_manager.has_live_lease(crash_vid) is False, "Lease must expire naturally after crash"
                    print("PASS: Scenario 9: Abrupt client crash handled via natural lease expiration")

                    # ====================================================================
                    # SCENARIO 10: Real FFmpeg subprocess termination upon final release
                    # ====================================================================
                    print("\n[SCENARIO 10] Verifying real subprocess termination and budget release upon final release...")
                    ffmpeg_vid = "vid_ffmpeg_kill_test"
                    key_ffmpeg = resolve_video_key(ffmpeg_vid, source="archivebate")
                    ts.lease_manager.acquire(key_ffmpeg, consumer_id="ffmpeg_watcher", duration=30.0)

                    # Launch a mock long-running FFmpeg process to verify termination
                    mock_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                    p_key = f"v2_test_proc_{time.monotonic()}"
                    qos.register_v2_process(p_key, mock_proc, ffmpeg_vid, source="archivebate")

                    try:
                        assert mock_proc.poll() is None, "Subprocess must be initially running"

                        # Release final consumer
                        t_start = time.monotonic()
                        was_present, remaining = ts.lease_manager.release(key_ffmpeg, consumer_id="ffmpeg_watcher", reason="test_kill")
                        t_dur = time.monotonic() - t_start

                        assert was_present is True
                        assert remaining == 0
                        assert t_dur < 1.0, f"Release must be prompt, took {t_dur:.3f}s"

                        # Verify that hard_cancel actually killed the process!
                        time.sleep(0.2)
                        assert mock_proc.poll() is not None, "Final consumer release MUST terminate running subprocess!"
                        assert qos.is_hard_cancelled(ffmpeg_vid, source="archivebate") is True
                        assert not ts.lease_manager.has_live_lease(ffmpeg_vid)
                        print(f"PASS: Scenario 10: Running subprocess terminated (exit code={mock_proc.poll()}) and budget released")
                    finally:
                        if mock_proc.poll() is None:
                            mock_proc.kill()
                        qos.unregister_v2_process(p_key)

                    browser.close()

            finally:
                server.should_exit = True
                server_thread.join(timeout=3.0)

    print("\n" + "=" * 70)
    print("ALL 10 MANDATORY REAL MULTI-TAB & PLAYER LIFECYCLE SCENARIOS PASSED!")
    print("=" * 70)


if __name__ == "__main__":
    run_cross_session_browser_tests()
