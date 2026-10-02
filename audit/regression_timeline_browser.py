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


def reveal_controls(page, selector):
    # The paused player's center overlay intentionally intercepts its middle.
    # Move the real pointer within the wrapper without forcing hidden controls.
    player = page.locator(selector)
    player.scroll_into_view_if_needed()
    box = player.bounding_box()
    assert box
    page.mouse.move(box['x'] + 8, box['y'] + 8)
    controls_selector = '#modalControlsBar' if selector == '#modalVideo' else '#customControlsBar'
    page.wait_for_function(
        "selector => {const e=document.querySelector(selector); return !!e && !e.classList.contains('idle');}",
        arg=controls_selector, timeout=1500,
    )


def move_over_timeline(page, timeline, fraction):
    """Use the real pointer at a stable coordinate inside the timeline hit area."""
    box = page.locator(timeline).bounding_box()
    assert box
    page.mouse.move(box['x'] + box['width'] * fraction, box['y'] + box['height'] / 2)


def verify_browsing(browser, poster_file, report):
    """New failure paths: no-hover image retry, native tabs and visible removal checks."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
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
    assert len(removed_checks) == 1 and removed_checks[0]["force"] == "false", removed_checks
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
    popup.wait_for_url("**/watch/card_0", wait_until="domcontentloaded")
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

    # A player can discover removal after the visible-card probe returned a
    # resolved URL. Its stronger evidence must hide the tile immediately and
    # survive late metadata writes, reload and the standalone watch surface.
    proof = {"id": "card_0", "source": "archivebate", "username": "fixture_0",
             "availability": "unavailable", "availability_reason": "stream_file_not_found",
             "stream_missing_confirmations": 2, "checked_at": time.time(), "retryable": False}
    page.route("**/api/video/details?**", lambda route: route.fulfill(json=proof))
    page.evaluate("()=>ArchivebateVideoPrefetch.detailsCache.delete('archivebate:id:card_0')")
    page.evaluate("v=>ArchivebateVideoModal.open(v)", cards[0])
    page.wait_for_function("()=>document.querySelector('#videoLoader').textContent.includes('Usunięto je z kafelków.')")
    assert not page.locator('.video-card[data-video-id=card_0]').count(), page.evaluate("()=>({cards:[...document.querySelectorAll('.video-card[data-video-id=card_0]')].map(c=>c._videoData),hidden:ArchivebateVideoPrefetch.isKnownUnavailableVideo('card_0')})")
    page.evaluate("()=>ArchivebateVideoPrefetch.setVideoDetails('card_0',{availability:'available',direct_url:'https://fixture.invalid/dead.mp4'})")
    assert page.evaluate("()=>ArchivebateVideoPrefetch.isKnownUnavailableVideo('card_0')")
    watch = page.context.new_page()
    watch.route("**/api/video/details?**", lambda route: route.fulfill(json=proof))
    watch.goto("http://127.0.0.1:18184/watch/card_0", wait_until="domcontentloaded")
    watch.wait_for_function("()=>document.querySelector('#playerLoader').textContent.includes('Usunięto je z kafelków.')")
    watch.close()

    # Reproduce the reported generic player error: cached details still say
    # available, but the actual media request fails. Only the error handler's
    # forced stream probe can obtain removal evidence here.
    failed_probes = []
    for video_id in ('card_4', 'card_5'):
        failed_proof = {**proof, 'id': video_id}
        def probe_failed(route, _request, *, result=failed_proof):
            failed_probes.append(result['id'])
            route.fulfill(json=result)
        target = page if video_id == 'card_4' else context.new_page()
        target.route(f'**/api/video/details?id={video_id}', lambda route, _request, *, vid=video_id: route.fulfill(json={
            'id': vid, 'availability': 'available', 'source': 'archivebate', 'username': 'fixture',
            'direct_url': 'https://fixture.invalid/dead.mp4', 'proxy_stream_url': f'/api/video/stream?id={vid}'}))
        target.route(f'**/api/video/stream?id={video_id}**', lambda route: route.fulfill(status=410, body='missing file'))
        target.route(f'**/api/video/availability?id={video_id}&force=true', probe_failed)
        if video_id == 'card_4':
            page.evaluate('v=>ArchivebateVideoModal.open(v)', cards[4])
            target.wait_for_function("()=>document.querySelector('#videoLoader').textContent.includes('Usunięto je z kafelków.')")
            assert not page.locator('.video-card[data-video-id=card_4]').count()
        else:
            target.goto(f'http://127.0.0.1:18184/watch/{video_id}', wait_until='domcontentloaded')
            target.wait_for_function("()=>document.querySelector('#playerLoader').textContent.includes('Usunięto je z kafelków.')")
            target.close()
    assert failed_probes == ['card_4', 'card_5'], failed_probes
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("()=>!ArchivebateAppContext.state.isLoading && document.querySelector('[data-video-id=card_2]')")
    assert not page.locator('.video-card[data-video-id=card_0]').count()
    assert not page.locator('.video-card[data-video-id=card_4]').count()
    assert not page.locator('.video-card[data-video-id=card_5]').count()
    assert not errors, errors
    report['browsing'].update(player_removal_hides_tile=True, late_details_cannot_restore=True,
                              player_removal_survives_reload=True, watch_removal_message=True,
                              media_error_probes=failed_probes, media_error_removal_survives_reload=True)
    context.close()


def verify_audit_flows(browser, poster_file, report):
    """Exercise actual card/prefetch, view generations, toast and focus owners."""
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    errors, requests_seen, local_queries = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests_seen.append(request.url))
    context.route("https://**/*", lambda route: route.abort())
    cards = [{"id": f"local_{i}", "source": "archivebate", "username": f"fixture_{i}",
              "poster": f"https://fixture.invalid/{i}.jpg", "preview_video": f"https://fixture.invalid/{i}.mp4",
              "duration": "1:35", "tags": ["neutral"]} for i in range(240)]
    empty_feed = {"videos": [], "complete": True, "catalog_complete": True, "page_complete": True,
                  "revision": 1, "snapshot_id": "1", "count": 0, "preferences_version": 0}
    page.route("**/api/feed**", lambda route: route.fulfill(json=empty_feed))
    status = {"logged_in": False, "account_configured": False, "email": "", "last_synced": None,
              "favorites_count": 0, "history_count": 0, "following_count": 0, "preferences_version": 0, "favorite_authors": []}
    page.route("**/api/status", lambda route: route.fulfill(json=status))
    page.route("**/api/jobs", lambda route: route.fulfill(json={"status": "failed", "jobs": {
        "account_sync": {"status": "failed", "phase": "partial", "result": {"status": "partial", "error": "fixture partial fetch"}}}}))
    def local_search(route):
        args = parse_qs(urlsplit(route.request.url).query)
        local_queries.append(args)
        number = int(args.get("page", [1])[0])
        route.fulfill(json={"items": cards[:200] if number == 1 else cards[200:], "total": 240,
                            "page_count": 2, "catalog_revision": 1, "preferences_version": 0})
    page.route("**/api/search/local?**", local_search)
    cache_hits = {"value": False, "local": True}
    def local_thumbnail(route):
        if cache_hits["local"]:
            assert parse_qs(urlsplit(route.request.url).query).get("cache_only") == ["true"], route.request.url
        if cache_hits["value"]:
            route.fulfill(content_type="image/jpeg", body=poster_file.read_bytes())
        else:
            route.fulfill(status=404)
    page.route("**/api/thumb?**", local_thumbnail)
    page.goto("http://127.0.0.1:18184/", wait_until="domcontentloaded")
    page.wait_for_function("()=>!ArchivebateAppContext.state.isLoading")
    page.evaluate("()=>ArchivebateVideoViews.showSkeletons()")
    assert page.evaluate("()=>getComputedStyle(document.querySelector('.skeleton-card'),'::after').animationName") == 'none'
    start = len(requests_seen)
    page.select_option('#searchScopeSelect', 'local')
    page.fill('#searchInput', 'neutral')
    page.press('#searchInput', 'Enter')
    page.wait_for_function("()=>document.querySelectorAll('.video-card').length===200")
    page.locator('.video-card').first.hover()
    page.locator('.video-card .play-btn').first.focus()
    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(1000)
    assert page.locator('.video-card').first.inner_text().find('Brak lokalnej miniatury') >= 0
    page.locator('#nextPageBtnTop').click()
    page.wait_for_function("()=>ArchivebateAppContext.state.currentPage===2 && document.querySelectorAll('.video-card').length===40")
    cache_hits["value"] = True
    page.locator('#prevPageBtnTop').click()
    page.wait_for_function("()=>ArchivebateAppContext.state.currentPage===1 && document.querySelector('.thumbnail-img')?.naturalWidth>0")
    page.locator('.video-card').first.hover()
    page.wait_for_timeout(1000)
    local_requests = requests_seen[start:]
    forbidden = [url for url in local_requests if any(path in url for path in
                 ('/api/video/', '/api/storyboard', '/api/search/stream', '/api/search?', '/api/model/')) or url.startswith('https://')]
    assert not forbidden, forbidden
    assert local_queries[-2]["author_filter"] == ["all"] and local_queries[-2]["preferences_version"] == ["0"], local_queries
    report['local_scope'] = {"cold_cache": True, "warm_cache": True, "hover_focus_scroll_pagination": True,
                             "provider_requests": forbidden, "thumbnail_reads": len([u for u in local_requests if '/api/thumb?' in u])}
    cache_hits["local"] = False

    # Capture the real EventSource's callbacks, then deliver late data/error
    # after account navigation. The old generation has already been closed.
    page.unroute("**/api/feed**")
    page.route("**/api/feed/stream?**", lambda route: None)
    page.route("**/api/feed?**", lambda route: route.fulfill(json={**empty_feed, "complete": False,
               "catalog_complete": False, "page_complete": False, "videos": cards[:16], "count": 240}))
    page.evaluate("()=>ArchivebateVideoViews.preferencesChanged(1)")
    page.locator('#navHomeBtn').click()
    page.wait_for_function("()=>ArchivebateAppContext.state.activeSearchSource!==null")
    page.evaluate("()=>{window.auditOldSSE=ArchivebateAppContext.state.activeSearchSource; window.auditOldController=ArchivebateAppContext.state.viewController;}")
    page.locator('#navAccountBtn').click()
    assert page.evaluate("()=>auditOldSSE.readyState===2 && auditOldController.signal.aborted")
    page.evaluate("cards=>{auditOldSSE.onmessage({data:JSON.stringify({videos:cards,complete:true,catalog_complete:true})});auditOldSSE.onerror();}", cards)
    page.wait_for_timeout(250)
    assert page.locator('#accountPanelView').is_visible() and page.locator('.video-card').count() == 0
    assert page.locator('#jobSyncStatus').inner_text() == 'Niepełne pobranie danych'
    assert 'nie były jeszcze synchronizowane' in page.locator('#panelLastSync').inner_text()
    assert page.locator('#pageJumpInputTop').get_attribute('aria-label') and page.locator('#pageJumpInput').get_attribute('aria-label')
    report['account_generation'] = {"SSE_closed": True, "controller_aborted": True, "late_data_and_error_ignored": True,
                                    "partial_job_visible": True, "unsynchronized_label": True}

    blocked = ['fixture_0', 'fixture_1']
    page.route("**/api/blocked_models", lambda route: route.fulfill(json={"blocked_models": blocked.copy(), "blocked_videos_total": len(blocked)}))
    def block_mutation(route):
        name = urlsplit(route.request.url).path.split('/')[-2]
        if route.request.url.split('?')[0].endswith('/unblock'):
            if name in blocked: blocked.remove(name)
        elif name not in blocked:
            blocked.append(name)
        route.fulfill(json={"success": True, "preferences_version": 0, "hidden_videos": 1})
    page.route("**/api/model/*/block?**", block_mutation)
    page.route("**/api/model/*/unblock", block_mutation)
    trigger = page.locator('#navAccountBtn')
    trigger.focus()
    page.evaluate("()=>ArchivebateBlockedModels.showManager()")
    dialog = page.locator('[aria-modal=true]').filter(has=page.locator('#blockedModelsTitle'))
    dialog.locator('input').wait_for(state='visible')
    page.keyboard.press('Shift+Tab')
    assert page.evaluate("()=>document.activeElement.textContent==='Zamknij'")
    for _ in range(7):
        page.keyboard.press('Tab')
        assert dialog.evaluate('d=>d.contains(document.activeElement)')
    dialog.get_by_role('button', name='Odblokuj').first.click()
    page.wait_for_function("()=>document.activeElement.getAttribute('aria-label')==='Szukaj zablokowanego profilu'")
    page.keyboard.press('Escape')
    assert trigger.evaluate('e=>document.activeElement===e')
    report['keyboard'] = {"shift_tab_contained": True, "tab_contained": True, "row_removal_keeps_focus": True,
                          "escape_returns_focus": True, "reduced_motion": True, "page_labels": True}

    await_script = "async()=>{await ArchivebateBlockedModels.block('fixture_0');await ArchivebateBlockedModels.block('fixture_1');}"
    page.evaluate(await_script)
    undo = page.locator('.toast').filter(has_text='Możesz cofnąć')
    assert undo.count() == 2
    page.wait_for_timeout(5200)
    assert undo.count() == 2 and undo.first.get_by_role('button', name='Cofnij').is_enabled()
    undo.first.get_by_role('button', name='Cofnij').click()
    page.wait_for_function("()=>!ArchivebateAppContext.state.clientBlockedAuthors.has('fixture0')")
    assert blocked == ['fixture_1'], blocked
    page.wait_for_timeout(5100)
    assert page.locator('.toast-action').filter(has_text='Cofnij').count() == 0
    report['undo'] = {"available_after_four_seconds": True, "captured_first_action": True, "expires_after_ten_seconds": True}

    # Explicit playback is the scope boundary; history failure and retry remain
    # operable in the modal's focus trap and on the standalone watch page.
    history_attempts = []
    def history(route):
        ident = route.request.post_data_json['id']
        history_attempts.append(ident)
        route.fulfill(status=503 if history_attempts.count(ident) == 1 else 200,
                      json={"detail": "fixture disk failure"} if history_attempts.count(ident) == 1 else {"success": True, "total_history": 1})
    context.route("**/api/account/history/record", history)
    page.evaluate("v=>ArchivebateVideoModal.open(v)", {**cards[0], 'id': 'history_modal', '_mediaScope': 'local_catalog'})
    page.locator('#modalVideo').evaluate('v=>{v.muted=true;v.play().catch(()=>{});} ')
    failure = page.locator('.toast').filter(has_text='Nie zapisano obejrzenia')
    failure.wait_for(state='visible')
    assert failure.evaluate("e=>e.closest('[aria-modal=true]')!==null"), 'retry belongs to the active dialog'
    failure.get_by_role('button', name='Ponów zapis').click()
    page.locator('.toast').filter(has_text='Obejrzenie zapisane').wait_for(state='visible')
    page.keyboard.press('Escape')
    watch = context.new_page()
    watch.on('pageerror', lambda error: errors.append(str(error)))
    watch.route("**/api/account/favorites/toggle", lambda route: route.fulfill(json={
        "local_committed": True, "is_favorite": True, "remote_state": "failed", "total_favorites": 1}))
    watch.goto('http://127.0.0.1:18184/watch/history_watch', wait_until='domcontentloaded')
    watch.locator('#mainPlayer').evaluate('v=>{v.muted=true;v.play().catch(()=>{});} ')
    watch.locator('.toast').filter(has_text='Nie zapisano obejrzenia').get_by_role('button', name='Ponów zapis').click()
    watch.locator('.toast').filter(has_text='Obejrzenie zapisane').wait_for(state='visible')
    watch.locator('#favBtn').click()
    watch.wait_for_function("()=>document.querySelector('#favBtn').getAttribute('aria-pressed')==='true'")
    assert 'lokalnie' in watch.locator('#toastContainer').inner_text().lower(), watch.locator('#toastContainer').inner_text()
    assert 'zdaln' in watch.locator('#toastContainer').inner_text().lower()
    report['player_messages'] = {"modal_history_failure_retry": True, "watch_history_failure_retry": True,
                                "watch_local_success_remote_failure": True, "history_attempts": history_attempts}
    watch.screenshot(path='audit_ui_watch.png')
    page.emulate_media(forced_colors='active')
    page.screenshot(path='audit_ui_account_high_contrast.png')
    assert not errors, errors
    report['page_errors'] = errors
    context.close()


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
                    channel = os.environ.get("ARCHIVEBATE_BROWSER_CHANNEL") or ("msedge" if os.name == "nt" else None)
                    browser = playwright.chromium.launch(channel=channel, headless=True,
                        args=["--autoplay-policy=no-user-gesture-required"])
                    cases = () if '--audit-only' in sys.argv else ((False, "timeline_fixture"), (True, "timeline_fixture"),
                                            (False, "cw_999001"), (True, "cw_999001"),
                                            (True, "timeline_slow_source"), (True, "timeline_instant"))
                    for modal, video_id in cases:
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
                        # Reveal controls by a real pointer movement inside the player.
                        reveal_controls(page, selector)
                        box = page.locator(timeline).bounding_box()
                        assert box, "timeline must be visible"
                        move_over_timeline(page, timeline, 91.1 / 95)
                        status_selector = "#modalTimelinePreviewStatus" if modal else "#timelinePreviewStatus"
                        try:
                            page.wait_for_function("selector => {const e=document.querySelector(selector);return e.getBoundingClientRect().width>0 && getComputedStyle(e).display!=='none' && e.textContent.includes('bufor');}",
                                                   arg=status_selector, timeout=5000)
                        except Exception:
                            print(json.dumps({'buffer_case':[modal,video_id], 'diagnostic':page.evaluate("a=>{const v=document.querySelector(a.video),s=document.querySelector(a.status),t=document.querySelector(a.timeline);return {trace:window.timelinePointerTrace,video:{time:v.currentTime,paused:v.paused,buffered:v.buffered.length},status:s.textContent,statusDisplay:getComputedStyle(s).display,timeline:t.getBoundingClientRect().toJSON(),stats:ArchivebateYouTubeStoryboard.stats(),errors:window.auditErrors};}",
                                {'video':selector,'status':status_selector,'timeline':timeline})},indent=2),flush=True)
                            raise
                        page.mouse.move(0, 0)
                        page.locator(selector).evaluate("v => {delete v.buffered;v.pause();window.ArchivebatePlayerQoS?.report(v,'pause',true);}")
                        page.evaluate("delete document.hidden")
                        if video_id == "timeline_instant":
                            # Interrupt advance work with a real urgent hover.
                            # The interrupted part must be retried afterwards,
                            # leaving no permanent hole in the prepared axis.
                            page.wait_for_function("() => ArchivebateYouTubeStoryboard.stats().warm_inflight > 0",timeout=5000)
                            reveal_controls(page, selector)
                            move_over_timeline(page, timeline, 60.1 / 95)
                            page.wait_for_function("() => ArchivebateYouTubeStoryboard.getSegmentFromCache('timeline_instant',95,60)",timeout=10000)
                            page.mouse.move(0,0)
                            # All ten-second anchors must now exist before the
                            # subsequent fast sweep across the whole movie.
                            page.wait_for_function("() => [0,30,60,90].every(t=>ArchivebateYouTubeStoryboard.getSegmentFromCache('timeline_instant',95,t))",timeout=15000)
                        if modal and video_id.startswith("cw_"):
                            # Exercise the native-thumbnail bypass also in the modal.
                            page.evaluate("Object.assign(ArchivebateAppContext.state, {currentTimelinePrefix:'/fixture/180x135/',currentTimelineCount:15})")
                        reveal_controls(page, selector)
                        box = page.locator(timeline).bounding_box()
                        assert box, "timeline must be visible"
                        samples = []
                        streams_before = len(stream_requests)
                        requests_before = page.evaluate("ArchivebateYouTubeStoryboard.stats().requests")
                        for second in (60, 70, 80, 90):
                            move_over_timeline(page, timeline, (second + 0.1) / 95)
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
                    if '--audit-only' not in sys.argv:
                        verify_browsing(browser, poster_file, report)
                    verify_audit_flows(browser, poster_file, report)
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
