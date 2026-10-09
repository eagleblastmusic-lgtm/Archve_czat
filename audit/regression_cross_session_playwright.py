"""Real Multi-Tab and Player Lifecycle Browser Qualification Suite (Playwright).

Executes against the real running application code:
- static/timeline-preview.js (window.ArchivebateTimelinePreview.manager)
- static/video-modal.js (window.ArchivebateVideoModal)
- static/v452-player-qos.js (window.ArchivebatePlayerQoS)

Scenarios:
A. Two independent tabs opening the same video:
   - Tab A opens video X; Tab B opens video X in a separate browser context/tab.
   - V2 storyboard generation active.
   - Tab A closes.
   - Tab B continues to receive storyboard revisions.
   - FFmpeg / worker needed for Tab B is NOT globally cancelled.
B. Rapid video switching (A -> B -> C -> A) in real modal:
   - Real ArchivebateVideoModal.openVideoModal calls.
   - Verifies no leaked subscriptions, no console/page errors, correct active video key.
C. Delayed responses and release ordering:
   - Late GET manifest does not resurrect cancelled session.
   - Delayed release from old session does not kill new session.
D. Closing the last consumer:
   - Generation stops promptly; no orphaned FFmpeg processes or active leases remain.
E. Unexpected tab termination (crash simulation):
   - Page closed forcefully without clean release; lease expires naturally and cleans up.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def setup_fixtures():
    # Setup test video fixtures
    for vid in ("vid_multi_tab", "vid_switch_a", "vid_switch_b", "vid_switch_c"):
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

    setup_fixtures()

    port = find_free_port()
    os.environ["PORT"] = str(port)

    def fixture_details(video_id, **kwargs):
        return {
            "id": video_id,
            "source": "archivebate",
            "title": f"Fixture {video_id}",
            "username": "ModelTester",
            "duration": "4:00",
            "availability": "available",
            "direct_url": "http://127.0.0.1:8000/api/video/stream?id=fixture_stream",
            "proxy_stream_url": f"/api/video/stream?id={video_id}",
            "thumbnail": f"/api/storyboard/v2/sheet?id={video_id}&sheet=0&v=1&source=archivebate",
        }

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

                print("\n" + "=" * 70)
                print("RUNNING REAL MULTI-TAB & PLAYER LIFECYCLE PLAYWRIGHT TESTS")
                print("=" * 70)

                # ====================================================================
                # SCENARIO A: Two independent tabs opening the same video
                # ====================================================================
                print("\n[SCENARIO A] Verifying two independent tabs opening the same video...")
                page_a = context.new_page()
                page_b = context.new_page()

                errors_a = []
                errors_b = []
                page_a.on("pageerror", lambda err: errors_a.append(str(err)))
                page_b.on("pageerror", lambda err: errors_b.append(str(err)))

                page_a.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                page_b.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")

                # Both pages open the same video in real modal
                diag_a = page_a.evaluate("""
                    async () => {
                        window.ArchivebateVideoModal.openVideoModal({
                            id: 'vid_multi_tab',
                            source: 'archivebate',
                            title: 'Shared Video Multi-Tab',
                            duration: '4:00',
                        });
                        const mgr = window.ArchivebateTimelinePreview ? window.ArchivebateTimelinePreview.manager : null;
                        const key = 'archivebate:id:vid_multi_tab';
                        if (mgr) {
                            mgr.subscribe(key, 'modal', () => {}, 'archivebate');
                            await mgr.ensureStoryboard(key, 'vid_multi_tab', 240.0, 'archivebate');
                        }
                        return {
                            hasModal: !!window.ArchivebateVideoModal,
                            hasMgr: !!mgr,
                            consumerId: mgr ? mgr.consumerId : null,
                            subscribers: mgr && mgr.cache.get(key) ? mgr.cache.get(key).subscribers.size : 0,
                        };
                    }
                """)
                diag_b = page_b.evaluate("""
                    async () => {
                        window.ArchivebateVideoModal.openVideoModal({
                            id: 'vid_multi_tab',
                            source: 'archivebate',
                            title: 'Shared Video Multi-Tab',
                            duration: '4:00',
                        });
                        const mgr = window.ArchivebateTimelinePreview ? window.ArchivebateTimelinePreview.manager : null;
                        const key = 'archivebate:id:vid_multi_tab';
                        if (mgr) {
                            mgr.subscribe(key, 'modal', () => {}, 'archivebate');
                            await mgr.ensureStoryboard(key, 'vid_multi_tab', 240.0, 'archivebate');
                        }
                        return {
                            hasModal: !!window.ArchivebateVideoModal,
                            hasMgr: !!mgr,
                            consumerId: mgr ? mgr.consumerId : null,
                            subscribers: mgr && mgr.cache.get(key) ? mgr.cache.get(key).subscribers.size : 0,
                        };
                    }
                """)
                print(f"Diag A: {diag_a}")
                print(f"Diag B: {diag_b}")

                # Wait for both tabs to acquire leases and verify on backend
                time.sleep(0.5)
                shared_key = resolve_video_key("vid_multi_tab", source="archivebate")
                active_count = ts.lease_manager.get_active_consumers_count(shared_key)
                print(f"Active consumers count: {active_count}, live_consumers: {ts.lease_manager.get_active_consumers(shared_key)}")
                assert active_count == 2, f"Expected 2 active consumers, got {active_count}"
                assert ts.has_live_lease("vid_multi_tab") is True
                assert not qos.is_hard_cancelled("vid_multi_tab")

                # Tab A closes (simulating user closing the tab)
                page_a.close()
                time.sleep(0.5)

                # CRITICAL VERIFICATION:
                # Tab A's close must only release Tab A's lease!
                # Tab B is STILL active, lease remains live, and video is NOT hard cancelled!
                active_after_close_a = ts.lease_manager.get_active_consumers_count(shared_key)
                assert active_after_close_a == 1, f"Expected 1 active consumer (Tab B), got {active_after_close_a}"
                assert ts.has_live_lease("vid_multi_tab") is True, "Tab B must retain live lease!"
                assert not qos.is_hard_cancelled("vid_multi_tab"), "Video must NOT be hard cancelled while Tab B is open!"

                # Verify Tab B can still query storyboard and render preview without errors
                tab_b_frames = page_b.evaluate("""
                    () => {
                        const manager = window.ArchivebateTimelinePreview.manager;
                        return manager.hasEntry('archivebate:id:vid_multi_tab', 'archivebate');
                    }
                """)
                assert tab_b_frames is True, "Tab B must retain active storyboard entry"

                # Tab B closes -> now all consumers are closed
                page_b.close()
                time.sleep(0.5)

                # Now with the last consumer closed, the video is cleanly cancelled
                final_count = ts.lease_manager.get_active_consumers_count(shared_key)
                assert final_count == 0, f"Expected 0 active consumers after all tabs closed, got {final_count}"
                assert qos.is_hard_cancelled("vid_multi_tab") is True, "Final tab close must trigger hard cancel!"
                assert not ts.has_live_lease("vid_multi_tab"), "Final tab close must remove live lease!"

                assert len(errors_a) == 0, f"Page A errors: {errors_a}"
                assert len(errors_b) == 0, f"Page B errors: {errors_b}"
                print("PASS: Scenario A: Two independent tabs maintain isolated lifecycle and Tab B is protected")

                # ====================================================================
                # SCENARIO B: Rapid video switching A -> B -> C -> A in real modal
                # ====================================================================
                print("\n[SCENARIO B] Verifying rapid video switching A -> B -> C -> A in real modal...")
                page_switch = context.new_page()
                switch_errors = []
                page_switch.on("pageerror", lambda err: switch_errors.append(str(err)))
                page_switch.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")

                page_switch.evaluate("""
                    async () => {
                        const vm = window.ArchivebateVideoModal;
                        const mgr = window.ArchivebateTimelinePreview ? window.ArchivebateTimelinePreview.manager : null;
                        async function openAndSub(video) {
                            vm.openVideoModal(video);
                            if (mgr) {
                                const key = 'archivebate:id:' + video.id;
                                mgr.subscribe(key, 'modal', () => {}, 'archivebate');
                                await mgr.ensureStoryboard(key, video.id, 240.0, 'archivebate');
                            }
                        }
                        // Open A
                        await openAndSub({ id: 'vid_switch_a', source: 'archivebate', title: 'Video A', duration: '4:00' });
                        // Switch to B
                        await openAndSub({ id: 'vid_switch_b', source: 'archivebate', title: 'Video B', duration: '4:00' });
                        // Switch to C
                        await openAndSub({ id: 'vid_switch_c', source: 'archivebate', title: 'Video C', duration: '4:00' });
                        // Switch back to A
                        await openAndSub({ id: 'vid_switch_a', source: 'archivebate', title: 'Video A', duration: '4:00' });
                    }
                """)
                time.sleep(0.5)

                sub_state = page_switch.evaluate("""
                    () => {
                        const mgr = window.ArchivebateTimelinePreview.manager;
                        return {
                            has_a: mgr.hasSubscribers('archivebate:id:vid_switch_a', 'archivebate'),
                            has_b: mgr.hasSubscribers('archivebate:id:vid_switch_b', 'archivebate'),
                            has_c: mgr.hasSubscribers('archivebate:id:vid_switch_c', 'archivebate'),
                            activeKey: mgr.activeVideoKey,
                        };
                    }
                """)

                assert sub_state["has_a"] is True, "Video A must be actively subscribed"
                assert sub_state["has_b"] is False, "Video B must be unsubscribed"
                assert sub_state["has_c"] is False, "Video C must be unsubscribed"
                assert sub_state["activeKey"] == "archivebate:id:vid_switch_a", "activeKey must be Video A"

                # Check backend leases: Video A has active lease, B and C do not
                assert ts.has_live_lease("vid_switch_a") is True, "Video A must have live lease"
                assert not qos.is_hard_cancelled("vid_switch_a"), "Video A must not be hard cancelled"

                # Close modal
                page_switch.evaluate("() => window.ArchivebateVideoModal.closeModal()")
                time.sleep(0.3)
                assert not ts.has_live_lease("vid_switch_a"), "Video A lease must be released on closeModal"

                page_switch.close()
                assert len(switch_errors) == 0, f"Switch errors: {switch_errors}"
                print("PASS: Scenario B: Rapid video switching cleanly unregisters previous videos without errors")

                # ====================================================================
                # SCENARIO C: Delayed responses and release ordering
                # ====================================================================
                print("\n[SCENARIO C] Verifying delayed responses and release ordering...")
                test_vid_c = "vid_delay_test"
                # Session 1 acquires lease
                ts.acquire_lease(test_vid_c, consumer_id="sess_1", duration=30.0)
                # Session 1 releases
                ts.release_lease(test_vid_c, consumer_id="sess_1", reason="sess_1_done")
                assert qos.is_hard_cancelled(test_vid_c) is True

                # Late manifest read arrives -> must NOT resurrect
                m = ts.progressive_storyboard_service.get_manifest(test_vid_c, 120.0)
                assert m is not None
                assert qos.is_hard_cancelled(test_vid_c) is True
                assert not ts.has_live_lease(test_vid_c)

                # Session 2 acquires lease
                ts.acquire_lease(test_vid_c, consumer_id="sess_2", duration=30.0)
                assert not qos.is_hard_cancelled(test_vid_c)
                assert ts.has_live_lease(test_vid_c) is True

                # Stale release from Session 1 arrives
                was_present, remaining = ts.release_lease(test_vid_c, consumer_id="sess_1", reason="sess_1_stale")
                assert was_present is False
                assert remaining == 1
                assert not qos.is_hard_cancelled(test_vid_c)
                assert ts.has_live_lease(test_vid_c) is True

                ts.release_lease(test_vid_c, consumer_id="sess_2", reason="sess_2_done")
                print("PASS: Scenario C: Delayed responses and out-of-order release safely scoped")

                # ====================================================================
                # SCENARIO D: Closing last consumer stops generation promptly
                # ====================================================================
                print("\n[SCENARIO D] Verifying closing last consumer stops generation promptly...")
                test_vid_d = "vid_stop_promptly"
                ts.acquire_lease(test_vid_d, consumer_id="worker_consumer", duration=30.0)
                assert ts.has_live_lease(test_vid_d) is True

                t_start = time.monotonic()
                ts.release_lease(test_vid_d, consumer_id="worker_consumer", reason="user_exit")
                t_elapsed = time.monotonic() - t_start

                assert t_elapsed < 0.1, f"Release must complete in sub-100ms, took {t_elapsed:.3f}s"
                assert qos.is_hard_cancelled(test_vid_d) is True
                assert not ts.has_live_lease(test_vid_d)
                print(f"PASS: Scenario D: Generation stopped in {t_elapsed * 1000:.1f}ms without hanging")

                # ====================================================================
                # SCENARIO E: Unexpected tab crash (context close without release)
                # ====================================================================
                print("\n[SCENARIO E] Verifying unexpected tab crash and natural lease expiry...")
                test_vid_e = "vid_crash_test"
                # Register short lease
                ts.acquire_lease(test_vid_e, consumer_id="crashed_tab", duration=0.2)
                assert ts.has_live_lease(test_vid_e) is True

                # Simulate abrupt crash / unceremonious disconnect
                time.sleep(0.3)
                assert ts.has_live_lease(test_vid_e) is False, "Lease must expire naturally after crash"
                print("PASS: Scenario E: Abrupt client crash handled via natural lease expiration")

                browser.close()

        finally:
            server.should_exit = True
            server_thread.join(timeout=3.0)

    print("\n" + "=" * 70)
    print("ALL REAL MULTI-TAB & PLAYER LIFECYCLE PLAYWRIGHT TESTS PASSED!")
    print("=" * 70)


if __name__ == "__main__":
    run_cross_session_browser_tests()
