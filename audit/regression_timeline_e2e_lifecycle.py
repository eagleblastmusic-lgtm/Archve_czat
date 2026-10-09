"""Real-browser End-to-End qualification for P0-1, P0-2, subscription lifecycle,
QoS hard-cancel reset, and timeline rendering latency in Chromium/Edge.
"""
import io
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

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def create_test_sprite(out_path: Path, rows: int = 10, cols: int = 10, w: int = 160, h: int = 90) -> None:
    img = Image.new("RGB", (cols * w, rows * h), (18, 18, 18))
    draw = ImageDraw.Draw(img)
    for r in range(rows):
        for c in range(cols):
            x = c * w
            y = r * h
            slot = r * cols + c
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=((slot * 2) % 255, (slot * 3) % 255, 120))
            draw.text((x + 10, y + 10), f"S{slot}", fill=(255, 255, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="JPEG", quality=80)

def run_e2e_tests():
    from timeline_storyboard import (
        resolve_video_key,
        get_manifest_path,
        get_sheet_path,
        record_lease,
        FRAME_WIDTH,
        FRAME_HEIGHT,
        STORYBOARD_V2_VERSION,
    )
    import main
    import runtime_app
    import player_qos_runtime as qos

    port = find_free_port()
    os.environ["PORT"] = str(port)

    # Neutral fixtures
    key1 = resolve_video_key("video_e2e_1", source="archivebate")
    sheet1_path = get_sheet_path(key1, 0, rev=2)
    sheet1_unversioned = get_sheet_path(key1, 0)
    create_test_sprite(sheet1_path)
    create_test_sprite(sheet1_unversioned)

    manifest1 = {
        "version": STORYBOARD_V2_VERSION,
        "video_key": key1.as_string(),
        "video_id": key1.provider_id,
        "source": key1.source,
        "duration": 300.0,
        "rev": 2,
        "complete": True,
        "frame_w": FRAME_WIDTH,
        "frame_h": FRAME_HEIGHT,
        "columns": 10,
        "rows": 10,
        "sheets": [{"index": 0, "url": f"/api/storyboard/v2/sheet?id={key1.provider_id}&sheet=0&v=2&source=archivebate"}],
        "frames": [[round(i * 3.0, 3), 0, i] for i in range(100)],
        "native": {},
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    manifest1_path = get_manifest_path(key1)
    manifest1_path.parent.mkdir(parents=True, exist_ok=True)
    manifest1_path.write_text(json.dumps(manifest1), encoding="utf-8")

    def fixture_details(video_id, **kwargs):
        return {
            "id": video_id,
            "source": "archivebate",
            "title": "E2E fixture",
            "username": "E2EModel",
            "duration": "5:00",
            "availability": "available",
            "direct_url": "https://fixture.invalid/stream.mp4",
            "proxy_stream_url": f"/api/video/stream?id={video_id}",
            "thumbnail": "/api/storyboard/v2/sheet?id=video_e2e_1&sheet=0&v=2&source=archivebate",
        }

    # Start uvicorn server with lifespan="off" to protect user credentials
    server_config = uvicorn.Config(
        runtime_app.app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        access_log=False,
        log_level="critical"
    )
    server = uvicorn.Server(server_config)
    server_thread = threading.Thread(target=server.run, daemon=True)

    with patch.object(main, "_fetch_details_singleflight", side_effect=fixture_details):
        server_thread.start()
        try:
            deadline = time.monotonic() + 8.0
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, "E2E server failed to start"

            channel = os.environ.get("ARCHIVEBATE_BROWSER_CHANNEL") or ("msedge" if os.name == "nt" else None)
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    channel=channel,
                    headless=True,
                    args=["--autoplay-policy=no-user-gesture-required"]
                )
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()

                page_errors = []
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                http_requests = []
                page.on("request", lambda req: http_requests.append(req.url))

                # Navigate to local Archivebite runtime
                page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                page.wait_for_selector("#videoModal", state="attached", timeout=5000)

                # --- TEST 1: P0-1 Recursion immunity and listener leakage ---
                print("[E2E TEST 1] Verifying timeline callback recursion immunity in browser...")
                eval_result = page.evaluate("""
                    () => {
                        const manager = window.ArchivebateTimelinePreview.manager;
                        const key = 'archivebate:id:video_e2e_1';
                        
                        let recursionDepth = 0;
                        let caughtRangeError = null;

                        // Simulate old recursive modal render pattern
                        function testRender() {
                            recursionDepth++;
                            if (recursionDepth > 3000) {
                                throw new RangeError("Maximum call stack size exceeded");
                            }
                            manager.pollManifest(key, 'video_e2e_1', 300.0, 'archivebate', () => {
                                testRender();
                            });
                        }

                        try {
                            testRender();
                        } catch (err) {
                            caughtRangeError = err.message;
                        }

                        const entry = manager.cache.get(key);
                        return {
                            recursionDepth,
                            caughtRangeError,
                            subscribersCount: entry ? entry.subscribers.size : 0,
                        };
                    }
                """)

                assert eval_result["caughtRangeError"] is None, f"RangeError occurred: {eval_result['caughtRangeError']}"
                assert eval_result["recursionDepth"] == 1, f"Expected recursion depth 1, got {eval_result['recursionDepth']}"
                print("PASS: P0-1 Callback recursion depth is 1 (immunity confirmed)")

                # --- TEST 2: P0-2 Uncontrolled polling on pointermove (0 redundant requests) ---
                print("[E2E TEST 2] Verifying zero redundant HTTP requests during pointermove on COMPLETE storyboard...")
                initial_req_count = len(http_requests)

                page.evaluate("""
                    async () => {
                        const manager = window.ArchivebateTimelinePreview.manager;
                        const key = 'archivebate:id:video_e2e_1';
                        
                        // Preload storyboard
                        await manager.ensureStoryboard(key, 'video_e2e_1', 300.0, 'archivebate');

                        // Simulate 500 rapid hover events
                        for (let i = 0; i < 500; i++) {
                            manager.render({
                                videoKey: key,
                                videoId: 'video_e2e_1',
                                targetTime: (i % 300),
                                duration: 300.0,
                                pos: (i % 300) / 300.0,
                            });
                            await manager.ensureStoryboard(key, 'video_e2e_1', 300.0, 'archivebate');
                        }
                    }
                """)

                new_reqs = [r for r in http_requests[initial_req_count:] if "/api/storyboard/v2" in r]
                build_reqs = [r for r in new_reqs if "/build" in r]
                assert len(build_reqs) == 0, f"Expected 0 build requests during hover, got {len(build_reqs)}: {build_reqs}"
                print("PASS: P0-2 500 rapid hover events generated exactly 0 redundant /build requests")

                # --- TEST 3: Multi-consumer subscription and unsubscribe lifecycle ---
                print("[E2E TEST 3] Verifying multi-consumer subscription and unsubscribe lifecycle...")
                sub_test = page.evaluate("""
                    () => {
                        const manager = window.ArchivebateTimelinePreview.manager;
                        const key = 'archivebate:id:video_e2e_multi';
                        
                        // Subscriber A (modal) and Subscriber B (watch) subscribe
                        manager.subscribe(key, 'modal', () => {}, 'archivebate');
                        manager.subscribe(key, 'watch', () => {}, 'archivebate');
                        
                        const entry = manager.cache.get(key);
                        const countBoth = entry.subscribers.size;

                        // Modal closes -> unsubscribes 'modal'
                        manager.unsubscribe(key, 'modal', 'archivebate');
                        const countWatchOnly = entry.subscribers.size;
                        const watchStillActive = entry.subscribers.has('watch');

                        // Watch unloads -> unsubscribes 'watch'
                        manager.unsubscribe(key, 'watch', 'archivebate');
                        const countNone = entry.subscribers.size;

                        return { countBoth, countWatchOnly, watchStillActive, countNone };
                    }
                """)

                assert sub_test["countBoth"] == 2, f"Expected 2 subscribers, got {sub_test['countBoth']}"
                assert sub_test["countWatchOnly"] == 1, f"Expected 1 subscriber remaining, got {sub_test['countWatchOnly']}"
                assert sub_test["watchStillActive"] is True, "Watch subscriber must remain active after modal unsubscribe"
                assert sub_test["countNone"] == 0, "No subscribers should remain after both unsubscribe"
                print("PASS: Multi-consumer subscription correctly protects active consumers")

                # --- TEST 4: QoS hard-cancel clearing on user revisit ---
                print("[E2E TEST 4] Verifying QoS hard-cancel clearing upon user revisit...")
                test_vid = "video_e2e_revisit"
                qos.hard_cancel(test_vid, reason="stalled")
                assert qos.is_hard_cancelled(test_vid) is True, "Must be hard cancelled"

                # User re-engages with video: record_lease is called
                record_lease(test_vid, duration=30.0, source="archivebate")
                assert qos.is_hard_cancelled(test_vid) is False, "Hard cancel state must be cleared on user revisit"
                print("PASS: QoS hard-cancel state is cleared cleanly upon user revisit")

                # --- TEST 5: Latency benchmark in real browser ---
                print("[E2E TEST 5] Measuring real browser render latency...")
                perf_stats = page.evaluate("""
                    () => {
                        const manager = window.ArchivebateTimelinePreview.manager;
                        const key = 'archivebate:id:video_e2e_1';
                        
                        const container = document.createElement('div');
                        container.style.width = '1000px';
                        container.style.height = '10px';
                        document.body.appendChild(container);

                        const spriteEl = document.createElement('div');
                        container.appendChild(spriteEl);

                        const timeTextEl = document.createElement('span');
                        container.appendChild(timeTextEl);

                        const latencies = [];
                        for (let i = 0; i < 200; i++) {
                            const t0 = performance.now();
                            manager.render({
                                videoKey: key,
                                videoId: 'video_e2e_1',
                                targetTime: (i * 1.5) % 300,
                                duration: 300.0,
                                pos: ((i * 1.5) % 300) / 300.0,
                                spriteEl,
                                timeTextEl,
                            });
                            latencies.push(performance.now() - t0);
                        }
                        
                        latencies.sort((a, b) => a - b);
                        const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length;
                        const p50 = latencies[Math.floor(latencies.length * 0.50)];
                        const p95 = latencies[Math.floor(latencies.length * 0.95)];
                        const p99 = latencies[Math.floor(latencies.length * 0.99)];
                        
                        return { avg, p50, p95, p99 };
                    }
                """)

                print(f"Browser Render Latency: avg={perf_stats['avg']:.3f}ms, p50={perf_stats['p50']:.3f}ms, p95={perf_stats['p95']:.3f}ms, p99={perf_stats['p99']:.3f}ms")
                assert perf_stats["p95"] < 1.0, f"p95 render latency must be under 1ms, got {perf_stats['p95']:.3f}ms"
                assert len(page_errors) == 0, f"Unexpected page errors: {page_errors}"

                browser.close()

        finally:
            server.should_exit = True
            server_thread.join(timeout=3.0)

    print("\nALL TIMELINE E2E LIFECYCLE TESTS PASSED IN REAL BROWSER!")

if __name__ == "__main__":
    run_e2e_tests()
