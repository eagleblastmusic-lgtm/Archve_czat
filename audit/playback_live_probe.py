"""Opt-in real playback probe. Run ONLY from a sanitized checkout."""
import os
import sys
from pathlib import Path
if os.environ.get("ARCHIVEBATE_AUDIT_ISOLATED") != "1":
    raise SystemExit("Requires sanitized checkout and ARCHIVEBATE_AUDIT_ISOLATED=1")
sys.path.insert(0, str(Path.cwd()))
import json
import logging
import threading
import time
import uvicorn
from playwright.sync_api import sync_playwright
logging.disable(logging.CRITICAL)
import runtime_app
server = uvicorn.Server(uvicorn.Config(runtime_app.app, host="127.0.0.1", port=18183,
    lifespan="off", access_log=False, log_level="critical"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
report = {}
try:
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.1)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)[:200]))
        def wait_condition(expression, timeout=45000):
            deadline = time.monotonic() + timeout / 1000
            while time.monotonic() < deadline:
                if page.evaluate(expression):
                    return
                page.wait_for_timeout(250)
            raise TimeoutError("Condition not reached")
        modal = "--modal" in sys.argv
        video_selector = "#modalVideo" if modal else "#mainPlayer"
        timeline_selector = "#modalTimelineContainer" if modal else "#timelineContainer"
        sprite_selector = "#modalTimelineSprite" if modal else "#timelineSprite"
        t = time.monotonic()
        if modal:
            page.route("**/api/feed**", lambda route: route.fulfill(json={"videos":[],"complete":True,"catalog_complete":True,"page_complete":True,"revision":1,"snapshot_id":"1","count":0}))
            page.goto("http://127.0.0.1:18183/",wait_until="domcontentloaded")
            page.evaluate("window.ArchivebateVideoModal.open({id:'16447670',source:'archivebate',username:'fixture'})")
        else:
            page.goto("http://127.0.0.1:18183/watch/16447670", wait_until="domcontentloaded")
        page.locator(video_selector).evaluate("v => {v.muted=true;v.play().catch(()=>{});}")
        try:
            wait_condition(f"document.querySelector({json.dumps(video_selector)}).currentTime > 1", timeout=45000)
            report["first_second_elapsed_s"] = round(time.monotonic()-t,3)
            report["playback"] = page.locator(video_selector).evaluate("v => ({currentTime:v.currentTime,readyState:v.readyState,error:v.error?.code || null})")
            timeline = page.locator(timeline_selector)
            if timeline.count():
                page.locator(video_selector).hover()
                page.wait_for_timeout(150)
                box = timeline.bounding_box()
                timeline.hover(position={"x":box['width']*.3,"y":box['height']/2})
                try:
                    wait_condition("window.ArchivebateYouTubeStoryboard.stats().segment_cache_entries > 0",timeout=30000)
                except Exception as exc:
                    report["storyboard_failure"] = str(exc)[:200]
                report["storyboard"] = page.evaluate("window.ArchivebateYouTubeStoryboard.stats()")
                report["timeline"] = page.locator(sprite_selector).evaluate("e=>({display:getComputedStyle(e).display,frame:e.dataset.frameIndex,images:e.querySelectorAll('img').length})")
                first_frame = report["timeline"]["frame"]
                duration = page.locator(video_selector).evaluate("v=>v.duration")
                next_time = int(duration*.3/30)*30 + (20 if int(first_frame or 0)<15 else 5)
                timeline.hover(position={"x":box['width']*next_time/duration,"y":box['height']/2})
                wait_condition(f"document.querySelector({json.dumps(sprite_selector)}).dataset.frameIndex !== " + json.dumps(first_frame), timeout=5000)
                report["timeline_changed"] = page.locator(sprite_selector).evaluate("e=>({display:getComputedStyle(e).display,frame:e.dataset.frameIndex})")
        except Exception as exc:
            report["playback_failure"] = str(exc)[:400]
            report["qos"] = page.evaluate("window.ArchivebatePlayerQoS?.snapshot(document.querySelector('#modalVideo')||document.querySelector('#mainPlayer'))")
            report["playback"] = page.locator(video_selector).evaluate("v => ({currentTime:v.currentTime,readyState:v.readyState,error:v.error?.code || null})")
        page.locator(video_selector).evaluate("v=>{v.pause();v.removeAttribute('src');v.load();}")
        page.goto("http://127.0.0.1:18183/watch/16444260",wait_until="domcontentloaded")
        try:
            wait_condition("document.body.innerText.includes('Nagranie zostało usunięte ze źródła')",timeout=45000)
            report["removed_message"] = True
            wait_condition("JSON.parse(localStorage.getItem('archivebate_unavailable_videos_v2') || '{}').entries?.['archivebate:id:16444260']",timeout=45000)
            report["removed_quarantined"] = True
        except Exception as exc:
            report["removed_failure"] = str(exc)[:400]
        report["page_errors"] = errors
        browser.close()
finally:
    server.should_exit = True
    thread.join(5)
print(json.dumps(report,ensure_ascii=False,indent=2))
Path("live_playback_result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
