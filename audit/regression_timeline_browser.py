"""Real-browser timeline acceptance with neutral local media and no provider IO.

Run through implementation_runner.py to isolate all caches and user state.
"""
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from unittest.mock import patch

if os.environ.get("ARCHIVEBATE_AUDIT_ISOLATED") != "1":
    raise SystemExit("Run from a sanitized checkout with ARCHIVEBATE_AUDIT_ISOLATED=1")
sys.path.insert(0, str(Path.cwd()))
import config
import requests
import uvicorn
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

logging.disable(logging.CRITICAL)


def verify_browsing(browser, poster_file, report):
    """New failure paths: no-hover image retry, native tabs and visible removal checks."""
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors, availability_calls, feed_urls = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("https://**/*", lambda route: route.abort())
    cards = [{"id": f"card_{i}", "source": "archivebate", "username": f"fixture_{i}",
              "duration": "1:35", "poster": f"/fixture/poster_{i}.jpg",
              "poster_proxy": f"/api/thumb?url=fixture_{i}", "tags": []} for i in range(24)]
    cards[1]["id"] = "removed_card"
    cards[3]["preview_video"] = "https://cdn.freefile.io/thumbnails/fixture_3.mp4"

    def feed(route):
        feed_urls.append(route.request.url)
        grouped = "group_authors=1" in route.request.url
        videos = [{**card, "is_grouped": True, "group_count": 1, "grouped_videos": [card]} for card in cards] if grouped else cards
        route.fulfill(json={"videos": videos, "complete": True, "catalog_complete": True,
                            "page_complete": True, "revision": 1, "snapshot_id": "1", "count": len(videos),
                            "page_count": 1, "last_page": 1})
    page.route("**/api/feed**", feed)
    page.route("**/api/account/favorites**", lambda route: route.fulfill(json={"videos": [cards[0]], "last_page": 1, "total": 1}))
    image_attempts = {}
    def thumbnail(route):
        name = parse_qs(urlsplit(route.request.url).query).get("url", [""])[0]
        image_attempts[name] = image_attempts.get(name, 0) + 1
        if name == 'fixture_3' or (name == "fixture_2" and image_attempts[name] == 1):
            route.fulfill(status=502, body="transient image failure")
        else:
            route.fulfill(content_type="image/jpeg", body=poster_file.read_bytes())
    page.route("**/api/thumb?**", thumbnail)
    # Ensure the direct backup fails as well; only the automatic canonical retry can recover.
    page.route("**/fixture/poster_2.jpg", lambda route: route.fulfill(status=404))
    page.route("**/fixture/poster_3.jpg", lambda route: route.fulfill(status=404))
    def availability(route):
        args = parse_qs(urlsplit(route.request.url).query)
        video_id = args["id"][0]
        availability_calls.append({"id": video_id, "force": args.get("force", ["false"])[0]})
        if video_id == "removed_card":
            route.fulfill(json={"id": video_id, "source": "archivebate", "availability": "unavailable",
                "availability_reason": "stream_file_not_found", "stream_missing_confirmations": 2,
                "checked_at": time.time(), "retryable": False})
        else:
            route.fulfill(json={"id": video_id, "source": "archivebate", "availability": "available",
                "direct_url": "https://fixture.invalid/neutral.mp4", "proxy_stream_url": f"/api/video/stream?id={video_id}"})
    page.route("**/api/video/availability?**", availability)
    page.goto("http://127.0.0.1:18184/", wait_until="domcontentloaded")
    page.mouse.move(0, 0)
    page.wait_for_function("()=>{const i=document.querySelector('[data-video-id=card_2] .thumbnail-img');return i?.complete && i.naturalWidth>0;}", timeout=8000)
    assert image_attempts.get("fixture_2", 0) == 2, image_attempts
    page.wait_for_function("()=>{const i=document.querySelector('[data-video-id=card_3] .thumbnail-img');return i?.complete && i.naturalWidth===320 && i.src.startsWith('data:image/jpeg');}", timeout=10000)
    page.wait_for_function("()=>!document.querySelector('[data-video-id=removed_card]')", timeout=8000)
    removed_checks = [call for call in availability_calls if call["id"] == "removed_card"]
    assert len(removed_checks) == 3 and sum(call["force"] == "true" for call in removed_checks) == 2, removed_checks
    assert not any(int(call["id"].split('_')[-1]) > 11 for call in availability_calls if call["id"].startswith('card_')), availability_calls

    page.evaluate("()=>{window.middleTrace=[];for(const n of ['mousedown','mouseup','auxclick','click'])document.addEventListener(n,e=>{window.middleTrace.push({type:n,button:e.button,prevented:e.defaultPrevented,target:e.target.outerHTML?.slice(0,200)});});}")
    try:
        with page.context.expect_page(timeout=5000) as popup_info:
            page.locator('[data-video-id=card_0] .play-btn').click(button="middle")
    except Exception:
        print(json.dumps({'native_tab_failure': page.evaluate('()=>window.middleTrace'),
            'pages': [p.url for p in page.context.pages], 'errors': errors}), flush=True)
        raise
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded")
    assert popup.url.endswith('/watch/card_0'), popup.url
    assert page.url.endswith('/') and not page.locator('#videoModal').evaluate("e=>e.classList.contains('active')")
    popup.close()

    page.locator('#navProfilesBtn').click()
    page.wait_for_function("()=>ArchivebateAppContext.state.profileDirectory===true && !ArchivebateAppContext.state.isLoading")
    assert 'Profile' in page.locator('#viewTitle').inner_text()
    assert any('group_authors=1' in url for url in feed_urls), feed_urls
    assert page.locator('#navProfilesBtn').evaluate("e=>e.classList.contains('active')")
    page.locator('#navFavoritesBtn').click()
    page.wait_for_function("()=>ArchivebateAppContext.state.mode==='favorites' && document.querySelectorAll('.video-card').length===1")
    page.locator('#navHomeBtn').click()
    page.wait_for_function("()=>ArchivebateAppContext.state.mode==='home' && !ArchivebateAppContext.state.isLoading")
    assert not page.locator('[data-video-id=removed_card]').count(), 'confirmed removal must be filtered before DOM creation on return'
    assert 'group_authors=0' in feed_urls[-1], feed_urls
    assert not errors, errors
    report['browsing'] = {"thumbnail_recovers_without_hover": True, "missing_jpg_uses_tiny_clip_without_hover": True, "middle_click_native_tab": True,
        "top_tabs": ["Główna", "Ulubione", "Profile"], "removed_checks": removed_checks,
        "removed_stays_hidden_on_return": True, "visible_availability_checks": len(availability_calls), "page_errors": errors}
    page.close()


def run():
    with patch.object(requests.Session, "request", side_effect=AssertionError("Provider IO forbidden")), \
         patch.object(config, "get_archivebate_credentials", return_value=("", "")), \
         tempfile.TemporaryDirectory(prefix="timeline_browser_") as directory:
        import runtime_app
        import main
        import storyboard_service as story

        root = Path(directory)
        for second in range(95):
            frame = Image.new("RGB", (160, 90), (20 + second * 2, 30, 80))
            ImageDraw.Draw(frame).text((60, 35), str(second), fill="white")
            frame.save(root / f"frame_{second:02d}.png")
        video_file = root / "neutral.mp4"
        poster_file = root / "poster.jpg"
        Image.new("RGB", (160, 90), (210, 15, 15)).save(poster_file)
        subprocess.run([story.imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-framerate", "1",
                        "-i", str(root / "frame_%02d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-y", str(video_file)], check=True)

        def fixture_details(video_id, **kwargs):
            return {"id": video_id, "source": "camwhores" if video_id.startswith("cw_") else "archivebate",
                    "title": "Neutral timeline fixture", "username": "Model", "duration": "1:35",
                    "availability": "available", "direct_url": "https://fixture.invalid/neutral.mp4",
                    "thumbnail": "/fixture/180x135/1.jpg", "keywords": []}

        stream_requests = []

        def fixture_stream(**kwargs):
            stream_requests.append(kwargs.get("owner", "player"))
            # The old per-anchor extractor timed out after eight seconds even
            # when a healthy HTTP source eventually supplied valid frames.
            if kwargs.get("id") == "timeline_slow_source" and kwargs.get("owner") == "storyboard":
                time.sleep(9)
            if kwargs.get("id") == "timeline_instant" and kwargs.get("owner") == "storyboard":
                time.sleep(0.4)
            return FileResponse(video_file, media_type="video/mp4")

        for route in runtime_app.app.routes:
            if getattr(route, "path", "") == "/api/video/stream":
                route.dependant.call = fixture_stream

        @runtime_app.app.get("/fixture/{filename:path}")
        def fixture_image(filename: str):
            return FileResponse(poster_file, media_type="image/jpeg")

        server = uvicorn.Server(uvicorn.Config(runtime_app.app, host="127.0.0.1", port=18184,
                                              lifespan="off", access_log=False, log_level="critical"))
        thread = threading.Thread(target=server.run, daemon=True)
        report = {"fixture": "95 seconds, distinct encoded image per second", "cases": []}
        with patch.object(main, "_fetch_details_singleflight", side_effect=fixture_details):
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert server.started, "fixture server did not start"
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True,
                        args=["--autoplay-policy=no-user-gesture-required"])
                    for modal, video_id in ((False, "timeline_fixture"), (True, "timeline_fixture"),
                                            (False, "cw_999001"), (True, "cw_999001"),
                                            (True, "timeline_slow_source"), (True, "timeline_instant")):
                        page = browser.new_page(viewport={"width": 1280, "height": 900})
                        # Keep advance preparation inactive for the initial
                        # critical-buffer proof; release it after that boundary.
                        page.add_init_script("Object.defineProperty(document,'hidden',{value:true,configurable:true});")
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.route("https://**/*", lambda route: route.abort())
                        page.route("**/api/feed**", lambda route: route.fulfill(json={
                            "videos": [], "complete": True, "catalog_complete": True, "page_complete": True,
                            "revision": 1, "snapshot_id": "1", "count": 0}))
                        selector = "#modalVideo" if modal else "#mainPlayer"
                        timeline = "#modalTimelineContainer" if modal else "#timelineContainer"
                        sprite = "#modalTimelineSprite" if modal else "#timelineSprite"
                        started = time.monotonic()
                        if modal:
                            page.goto("http://127.0.0.1:18184/", wait_until="domcontentloaded")
                            page.wait_for_function("!!window.ArchivebateVideoModal")
                            page.evaluate("v => window.ArchivebateVideoModal.open(v)", fixture_details(video_id))
                        else:
                            page.goto(f"http://127.0.0.1:18184/watch/{video_id}", wait_until="domcontentloaded")
                        page.locator(selector).evaluate("v => {v.muted=true;v.play().catch(()=>{});}")
                        page.wait_for_function("selector => document.querySelector(selector).currentTime > 0.25", arg=selector,
                                               timeout=15000)
                        first_frame_seconds = round(time.monotonic() - started, 3)
                        page.evaluate("selector => {window.timelinePointerTrace=[];const t=document.querySelector(selector); for(const name of ['pointerenter','pointermove','pointerleave'])t.addEventListener(name,e=>{const r=t.getBoundingClientRect(),bar=t.parentElement,w=bar.parentElement;window.timelinePointerTrace.push({type:e.type,x:e.clientX,y:e.clientY,top:r.top,height:r.height,barHeight:bar.getBoundingClientRect().height,rowHeight:bar.querySelector('.controls-row')?.getBoundingClientRect().height,wrapperHeight:w.getBoundingClientRect().height,scrollY:window.scrollY,under:document.elementFromPoint(e.clientX,e.clientY)?.id});});}", timeline)
                        # Force an empty buffer to verify visible waiting UI in
                        # the real CSS cascade, without allowing cold decoder IO.
                        page.locator(selector).evaluate("v => Object.defineProperty(v,'buffered',{value:{length:0},configurable:true})")
                        box = page.locator(timeline).bounding_box()
                        assert box, "timeline must be visible"
                        page.locator(timeline).hover(position={"x": box["width"] * 91.1 / 95, "y": box["height"] / 2})
                        status_selector = "#modalTimelinePreviewStatus" if modal else "#timelinePreviewStatus"
                        page.wait_for_function("selector => {const e=document.querySelector(selector);return e.getBoundingClientRect().width>0 && getComputedStyle(e).display!=='none' && e.textContent.includes('bufor');}",
                                               arg=status_selector, timeout=5000)
                        page.mouse.move(0, 0)
                        page.locator(selector).evaluate("v => {delete v.buffered;v.pause();window.ArchivebatePlayerQoS?.report(v,'pause',true);}")
                        page.evaluate("delete document.hidden")
                        if video_id == "timeline_instant":
                            # Interrupt advance work with a real urgent hover.
                            # The interrupted part must be retried afterwards,
                            # leaving no permanent hole in the prepared axis.
                            page.wait_for_function("() => ArchivebateYouTubeStoryboard.stats().warm_inflight > 0",timeout=5000)
                            early_box = page.locator(timeline).bounding_box()
                            page.locator(timeline).hover(position={"x":early_box['width']*60.1/95,"y":early_box['height']/2})
                            page.wait_for_function("() => ArchivebateYouTubeStoryboard.getSegmentFromCache('timeline_instant',95,60)",timeout=10000)
                            page.mouse.move(0,0)
                            # All ten-second anchors must now exist before the
                            # subsequent fast sweep across the whole movie.
                            page.wait_for_function("() => [0,30,60,90].every(t=>ArchivebateYouTubeStoryboard.getSegmentFromCache('timeline_instant',95,t))",timeout=15000)
                        if modal and video_id.startswith("cw_"):
                            # Exercise the native-thumbnail bypass also in the modal.
                            page.evaluate("Object.assign(ArchivebateAppContext.state, {currentTimelinePrefix:'/fixture/180x135/',currentTimelineCount:15})")
                        box = page.locator(timeline).bounding_box()
                        assert box, "timeline must be visible"
                        samples = []
                        streams_before = len(stream_requests)
                        requests_before = page.evaluate("ArchivebateYouTubeStoryboard.stats().requests")
                        for second in (60, 70, 80, 90):
                            page.locator(timeline).hover(position={"x": box["width"] * (second + 0.1) / 95,
                                                                 "y": box["height"] / 2})
                            try:
                                page.wait_for_function("a => {const e=document.querySelector(a.selector); return e.getBoundingClientRect().width>0 && getComputedStyle(e).display!=='none' && Math.abs(Number(e.dataset.frameTime)-a.second)<0.05;}",
                                                       arg={"selector": sprite, "second": second},
                                                       timeout=1000 if video_id == "timeline_instant" else (30000 if video_id == "timeline_slow_source" else 15000))
                            except Exception:
                                diagnostic = page.evaluate("a => {const e=document.querySelector(a.sprite),v=document.querySelector(a.video),s=document.querySelector(a.status); return {trace:window.timelinePointerTrace,frame:e.dataset.frameTime,display:getComputedStyle(e).display,status:s.textContent,statusDisplay:getComputedStyle(s).display,video:{duration:v.duration,paused:v.paused,currentTime:v.currentTime,readyState:v.readyState},stats:ArchivebateYouTubeStoryboard.stats(),fallback:window.ArchivebateV43TimelineFallback?.stats()};}",
                                    {"sprite": sprite, "video": selector, "status": status_selector})
                                print(json.dumps({"failed_case": [modal, video_id, second], "diagnostic": diagnostic,
                                    "segment_status": story.get_segment_status(video_id, 95, 2), "stream_requests": stream_requests,
                                    "page_errors": errors}, indent=2), flush=True)
                                raise
                            # Element screenshots may scroll and move the hovered
                            # timeline underneath the cursor. Capture the viewport
                            # crop without modifying this active interaction.
                            sprite_box = page.locator(sprite).bounding_box()
                            timeline_box = page.locator(timeline).bounding_box()
                            assert abs(timeline_box["y"] - box["y"]) < 0.5, "control updates must not move the timeline under a stationary pointer"
                            viewport = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                            pixels = viewport.crop((round(sprite_box["x"]), round(sprite_box["y"]),
                                round(sprite_box["x"] + sprite_box["width"]), round(sprite_box["y"] + sprite_box["height"])))
                            red = pixels.getpixel((10, 10))[0]
                            if abs(red - (20 + second * 2)) >= 8:
                                pixels.save(Path(f"timeline_failed_{modal}_{video_id}_{second}.png"))
                                print(json.dumps({"pixel_failure": [modal, video_id, second, red],
                                    "box": sprite_box, "trace": page.evaluate("window.timelinePointerTrace"),
                                    "sprite": page.locator(sprite).evaluate("e=>({display:getComputedStyle(e).display,frame:e.dataset.frameIndex,time:e.dataset.frameTime,identity:e.dataset.boardIdentity,img:e.querySelector('img')&&{src:e.querySelector('img').src,transform:getComputedStyle(e.querySelector('img')).transform,width:getComputedStyle(e.querySelector('img')).width,height:getComputedStyle(e.querySelector('img')).height,complete:e.querySelector('img').complete}})")}), flush=True)
                            assert abs(red - (20 + second * 2)) < 8, (modal, video_id, second, red)
                            samples.append({"second": second, "red": red})
                        assert len({sample["red"] for sample in samples}) == 4, samples
                        assert not errors, errors
                        requests_after = page.evaluate("ArchivebateYouTubeStoryboard.stats().requests")
                        if video_id == "timeline_instant":
                            assert requests_after == requests_before, "prepared hover must never initiate source preparation"
                        report["cases"].append({"mode": "modal" if modal else "watch", "source": fixture_details(video_id)["source"],
                            "first_frame_seconds_local_fixture": first_frame_seconds, "samples": samples,
                            "storyboard_stream_requests": len(stream_requests) - streams_before,
                            "source_header_delay_seconds": 9 if video_id == "timeline_slow_source" else 0,
                            "hover_preparation_requests": requests_after - requests_before,
                            "buffer_wait_visible": True, "page_errors": errors})
                        page.close()
                    verify_browsing(browser, poster_file, report)
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(5)
                story.shutdown()
        output = Path("timeline_browser_result.json")
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        print("PASS: real Edge shows actual encoded seconds in modal/watch for both providers")


if __name__ == "__main__":
    run()
